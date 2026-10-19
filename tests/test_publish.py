"""publish_media: a container per picture, a wait until Meta has processed it, media_publish — one picture or a
carousel; dry-run by default with the day's quota; refusals before anything is sent."""

import json
import logging
from urllib.parse import parse_qs

import pytest
from marvin_integration_sdk import IntegrationContext, IntegrationError, Response

from marvin_integration_instagram import InstagramProvider
from marvin_integration_instagram.provider import PUBLISH_WAIT_SECONDS

_LOG = logging.getLogger("test")
BASE = "https://graph.instagram.com/v22.0"
IG = "17841400000000000"
PIC = "https://assets.example.test/hero.jpg"


class _PublishHttp:
    """GETs answered by URL substring (a list answers in turn, the last repeating); POSTs recorded, answered by path."""

    def __init__(self, gets=None, quota=(3, 25), publish_id="99001"):
        self.gets = dict(gets or {})
        self.get_log: list[str] = []
        self.posts: list[dict] = []
        self._containers = 0
        self.quota = quota
        self.publish_id = publish_id

    def get(self, url, *, headers=None, timeout=15):
        self.get_log.append(url)
        if "content_publishing_limit" in url:
            if self.quota is None:
                return Response(status_code=400, content=b'{"error":"no"}')
            used, total = self.quota
            return Response(status_code=200, content=json.dumps({"data": [{"quota_usage": used, "config": {"quota_total": total}}]}).encode())
        if "fields=permalink" in url:
            return Response(status_code=200, content=b'{"permalink":"https://www.instagram.com/p/abc/"}')
        for needle, payload in self.gets.items():
            if needle in url:
                if isinstance(payload, list):
                    payload = payload.pop(0) if len(payload) > 1 else payload[0]
                return Response(status_code=200, content=json.dumps(payload).encode())
        return Response(status_code=404, content=b'{"error":"no route"}')

    def post(self, url, *, json=None, data=None, headers=None, timeout=15):
        form = {k: v[0] for k, v in parse_qs((data or b"").decode()).items()}
        self.posts.append({"url": url, "form": form, "headers": headers})
        if url.endswith("/media_publish"):
            return Response(status_code=200, content=f'{{"id":"{self.publish_id}"}}'.encode())
        self._containers += 1
        return Response(status_code=200, content=f'{{"id":"c{self._containers}"}}'.encode())


def _ctx(http, token="tok"):
    return IntegrationContext(config={"ig_user_id": IG}, secret=token, http=http, logger=_LOG)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(InstagramProvider, "_sleep", staticmethod(lambda s: None))


def test_dry_run_reports_the_plan_and_quota_and_posts_nothing():
    http = _PublishHttp()
    out = InstagramProvider().run_action("publish_media", {"image_url": PIC, "caption": "Hello"}, _ctx(http))
    assert out == {
        "dry_run": True,
        "published": False,
        "would_publish": {"images": [PIC], "caption": "Hello", "carousel": False},
        "quota": {"used": 3, "limit": 25, "remaining": 22},
    }
    assert http.posts == []


def test_a_single_picture_is_containered_waited_for_and_published():
    http = _PublishHttp(gets={"/c1?fields=status_code": [{"status_code": "IN_PROGRESS"}, {"status_code": "FINISHED"}]})
    out = InstagramProvider().run_action("publish_media", {"image_url": PIC, "caption": "Hello #hi", "dry_run": False}, _ctx(http))

    assert [p["url"].rsplit("/", 1)[1] for p in http.posts] == ["media", "media_publish"]
    assert http.posts[0]["form"] == {"image_url": PIC, "caption": "Hello #hi"}
    assert http.posts[1]["form"] == {"creation_id": "c1"}
    assert http.posts[0]["headers"]["Authorization"].startswith("Bearer ")
    assert sum("/c1?fields=status_code" in u for u in http.get_log) == 2  # polled until FINISHED
    assert out["published"] and out["media_id"] == "99001" and out["permalink"] == "https://www.instagram.com/p/abc/" and out["images"] == 1


def test_a_carousel_makes_a_child_per_picture_then_one_container_over_them():
    pics = [PIC, "https://assets.example.test/2.jpg", "https://assets.example.test/3.jpg"]
    http = _PublishHttp(gets={"?fields=status_code": {"status_code": "FINISHED"}})
    out = InstagramProvider().run_action("publish_media", {"image_urls": pics, "caption": "Three", "dry_run": False}, _ctx(http))

    forms = [p["form"] for p in http.posts]
    assert [f.get("is_carousel_item") for f in forms[:3]] == ["true"] * 3 and [f["image_url"] for f in forms[:3]] == pics
    assert forms[3] == {"media_type": "CAROUSEL", "children": "c1,c2,c3", "caption": "Three"}
    assert forms[4] == {"creation_id": "c4"} and out["images"] == 3


def test_a_picture_meta_cannot_process_fails_before_publishing():
    http = _PublishHttp(gets={"?fields=status_code": {"status_code": "ERROR", "status": "Media is too large"}})
    with pytest.raises(ValueError, match="could not process the picture: Media is too large"):
        InstagramProvider().run_action("publish_media", {"image_url": PIC, "dry_run": False}, _ctx(http))
    assert not any(p["url"].endswith("/media_publish") for p in http.posts)


def test_a_picture_still_processing_at_the_deadline_fails(monkeypatch):
    clock = iter([0, 0, PUBLISH_WAIT_SECONDS + 1, PUBLISH_WAIT_SECONDS + 2])
    monkeypatch.setattr("marvin_integration_instagram.provider.time.monotonic", lambda: next(clock))
    http = _PublishHttp(gets={"?fields=status_code": {"status_code": "IN_PROGRESS"}})
    with pytest.raises(ValueError, match="still processing"):
        InstagramProvider().run_action("publish_media", {"image_url": PIC, "dry_run": False}, _ctx(http))


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({}, "needs 'image_url'"),
        ({"image_urls": [f"https://x.test/{i}.jpg" for i in range(11)]}, "at most 10"),
        ({"image_url": "http://plain.test/a.jpg"}, "public https"),
        ({"image_url": PIC, "caption": "x" * 2201}, "allows 2200"),
    ],
)
def test_bad_input_is_refused_before_any_call(args, message):
    http = _PublishHttp()
    with pytest.raises(ValueError, match=message):
        InstagramProvider().run_action("publish_media", {**args, "dry_run": False}, _ctx(http))
    assert http.posts == [] and http.get_log == []


def test_a_used_up_quota_refuses_a_real_post_but_a_dry_run_still_reports():
    http = _PublishHttp(quota=(25, 25))
    assert InstagramProvider().run_action("publish_media", {"image_url": PIC}, _ctx(http))["quota"]["remaining"] == 0
    with pytest.raises(ValueError, match="limit is used up"):
        InstagramProvider().run_action("publish_media", {"image_url": PIC, "dry_run": False}, _ctx(http))
    assert http.posts == []


def test_an_unreadable_quota_is_none_and_does_not_block():
    http = _PublishHttp(quota=None, gets={"?fields=status_code": {"status_code": "FINISHED"}})
    out = InstagramProvider().run_action("publish_media", {"image_url": PIC, "dry_run": False}, _ctx(http))
    assert out["published"] and out["quota"] is None


def test_the_action_is_declared_approval_gated_and_free():
    action = next(a for a in InstagramProvider.actions if a.key == "publish_media")
    assert action.requires_approval and action.cost_hint == "free"
    assert set(action.input_schema["properties"]) == {"image_url", "image_urls", "caption", "dry_run"}


# ── social.post: the shared capability, mapped onto the same publishing ─────────


def test_post_declares_the_social_capability_approval_gated():
    action = next(a for a in InstagramProvider.actions if a.key == "post")
    assert action.capability == "social.post" and action.requires_approval
    assert set(action.input_schema["properties"]) == {"text", "images", "alt", "link", "dry_run"}
    assert action.input_schema["required"] == ["text"]
    assert {"posted", "post_id", "url", "network", "would_post"} <= set(action.output_schema["properties"])
    # publish_media keeps its own arguments and stays out of capability routing
    old = next(a for a in InstagramProvider.actions if a.key == "publish_media")
    assert old.capability is None and set(old.input_schema["properties"]) == {"image_url", "image_urls", "caption", "dry_run"}
    assert [a.key for a in InstagramProvider.actions if a.capability == "social.post"] == ["post"]


def test_post_one_image_publishes_text_as_the_caption_and_returns_the_contract_output():
    http = _PublishHttp(gets={"?fields=status_code": {"status_code": "FINISHED"}})
    out = InstagramProvider().run_action("post", {"text": "Hello #hi", "images": [PIC]}, _ctx(http))  # no dry_run: posts for real
    assert http.posts[0]["form"] == {"image_url": PIC, "caption": "Hello #hi"}
    assert out["posted"] is True and out["network"] == "instagram"
    assert out["post_id"] == "99001" and out["url"] == "https://www.instagram.com/p/abc/"


def test_post_several_images_make_a_carousel_with_alt_text_on_each_picture():
    pics = [PIC, "https://assets.example.test/2.jpg"]
    http = _PublishHttp(gets={"?fields=status_code": {"status_code": "FINISHED"}})
    InstagramProvider().run_action("post", {"text": "Two", "images": pics, "alt": ["A cat", "A dog"]}, _ctx(http))
    forms = [p["form"] for p in http.posts]
    assert forms[0] == {"image_url": PIC, "alt_text": "A cat", "is_carousel_item": "true"}
    assert forms[1] == {"image_url": pics[1], "alt_text": "A dog", "is_carousel_item": "true"}
    assert forms[2] == {"media_type": "CAROUSEL", "children": "c1,c2", "caption": "Two"}


def test_post_alt_may_be_shorter_than_the_images_and_single_alt_rides_on_the_container():
    http = _PublishHttp(gets={"?fields=status_code": {"status_code": "FINISHED"}})
    InstagramProvider().run_action("post", {"text": "x", "images": [PIC], "alt": ["A cat"]}, _ctx(http))
    assert http.posts[0]["form"] == {"image_url": PIC, "alt_text": "A cat", "caption": "x"}


def test_post_appends_the_link_to_the_caption_once():
    http = _PublishHttp()
    provider = InstagramProvider()
    out = provider.run_action("post", {"text": "New", "images": [PIC], "link": "https://example.test/p", "dry_run": True}, _ctx(http))
    assert out["would_post"]["caption"] == "New\n\nhttps://example.test/p"
    out = provider.run_action(
        "post", {"text": "See https://example.test/p", "images": [PIC], "link": "https://example.test/p", "dry_run": True}, _ctx(http)
    )
    assert out["would_post"]["caption"] == "See https://example.test/p"


def test_post_dry_run_reports_would_post_and_quota_and_sends_nothing():
    http = _PublishHttp()
    out = InstagramProvider().run_action("post", {"text": "Hi", "images": [PIC], "alt": ["Cat"], "dry_run": True}, _ctx(http))
    assert out == {
        "posted": False,
        "dry_run": True,
        "post_id": None,
        "url": None,
        "network": "instagram",
        "would_post": {"images": [PIC], "caption": "Hi", "carousel": False, "alt": ["Cat"]},
        "quota": {"used": 3, "limit": 25, "remaining": 22},
    }
    assert http.posts == []


def test_post_without_an_image_is_refused_with_a_clear_error():
    http = _PublishHttp()
    for args in ({"text": "just words"}, {"text": "just words", "images": []}):
        with pytest.raises(IntegrationError, match="can't post text alone") as exc:
            InstagramProvider().run_action("post", args, _ctx(http))
        assert exc.value.code == "unsupported" and isinstance(exc.value, ValueError)
    assert http.posts == [] and http.get_log == []


@pytest.mark.parametrize(
    ("args", "message"),
    [
        ({"images": [PIC]}, "needs text"),
        ({"text": "x", "images": [f"https://x.test/{i}.jpg" for i in range(11)]}, "at most 10"),
        ({"text": "x", "images": ["http://plain.test/a.jpg"]}, "public https"),
        ({"text": "x" * 2201, "images": [PIC]}, "allows 2200"),
        ({"text": "x" * 2190, "images": [PIC], "link": "https://example.test/long-path"}, "text and link|allows 2200"),
        ({"text": "x", "images": [PIC], "alt": ["a", "b"]}, "one text per image"),
        ({"text": "x", "images": [PIC], "alt": ["a" * 1001]}, "Alt text is limited"),
    ],
)
def test_post_refusals_happen_before_any_call(args, message):
    http = _PublishHttp()
    with pytest.raises(IntegrationError, match=message):
        InstagramProvider().run_action("post", {**args, "dry_run": False}, _ctx(http))
    assert http.posts == [] and http.get_log == []


def test_post_still_respects_the_quota():
    http = _PublishHttp(quota=(25, 25))
    with pytest.raises(ValueError, match="limit is used up"):
        InstagramProvider().run_action("post", {"text": "x", "images": [PIC]}, _ctx(http))
    assert http.posts == []
