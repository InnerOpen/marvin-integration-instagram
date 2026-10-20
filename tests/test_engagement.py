"""The shared engagement capabilities: social.comments / social.reply / social.hide / social.delete_comment,
through a fake http. Nothing here talks to Meta."""

import json as json_module
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from marvin_integration_sdk import IntegrationContext, IntegrationError, Response

from marvin_integration_instagram import InstagramProvider
from marvin_integration_instagram.engagement import decode_cursor, encode_cursor, page_after

_LOG = logging.getLogger("test")
BASE = "https://graph.instagram.com/v22.0"
NOW = datetime.now(UTC).replace(microsecond=0)


def _ts(days_ago=0, minutes_ago=0):
    return (NOW - timedelta(days=days_ago, minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%S%z")


def _iso(days_ago=0, minutes_ago=0):
    return (NOW - timedelta(days=days_ago, minutes=minutes_ago)).isoformat()


MEDIA = {"data": [{"id": "m1", "timestamp": _ts(), "permalink": "https://www.instagram.com/p/AAA/"}, {"id": "m2", "timestamp": _ts(1)}]}
COMMENTS = {
    "m1": {
        "data": [
            {
                "id": "c1",
                "text": "What SIZE is this?",
                "username": "fan",
                "from": {"id": "u1"},
                "timestamp": _ts(minutes_ago=30),
                "replies": {"data": [{"id": "r1", "text": "thanks!", "username": "me", "from": {"id": "9"}, "timestamp": _ts(minutes_ago=10)}]},
            }
        ]
    },
    "m2": {"data": [{"id": "c2", "text": "love it", "from": {"id": "u2"}, "timestamp": _ts(minutes_ago=20)}]},
}


class _Http:
    """GETs are answered by URL substring (first match wins); POSTs and DELETEs are recorded and answered by `status`."""

    def __init__(self, routes=None, status=200, post_body=None):
        self.routes = routes if routes is not None else {"/me/media": MEDIA, "/m1/comments": COMMENTS["m1"], "/m2/comments": COMMENTS["m2"]}
        self.status = status
        self.post_body = post_body if post_body is not None else {"id": "new1", "success": True}
        self.gets: list[str] = []
        self.posts: list[dict] = []
        self.deletes: list[dict] = []

    def get(self, url, *, headers=None, timeout=15):
        self.gets.append(url)
        for needle, payload in self.routes.items():
            if needle in url:
                return Response(status_code=200, content=json_module.dumps(payload).encode())
        return Response(status_code=404, content=b'{"error":"no route"}')

    def post(self, url, *, json=None, data=None, headers=None, timeout=15):
        self.posts.append({"url": url, "json": json, "data": data, "headers": headers})
        body = {"recipient_id": "x", "message_id": "mid.1"} if json is not None else self.post_body
        return Response(status_code=self.status, content=json_module.dumps(body).encode())

    def delete(self, url, *, headers=None, timeout=15):
        self.deletes.append({"url": url, "headers": headers})
        return Response(status_code=self.status, content=json_module.dumps({"success": True}).encode())


def _ctx(http=None, secret="tok", **config):
    return IntegrationContext(config={"ig_user_id": "1789", **config}, secret=secret, logger=_LOG, http=http or _Http())


def _run(key, args=None, **kw):
    return InstagramProvider().run_action(key, args or {}, _ctx(**kw))


def _comment_node(age_days):
    return {"/c1?": {"id": "c1", "timestamp": _ts(age_days), "username": "fan"}}


# ── declaration ────────────────────────────────────────────────────────────


def test_the_capabilities_are_declared_on_the_new_actions_with_the_right_gates():
    by_cap = {a.capability: a for a in InstagramProvider.actions if a.capability}
    assert {c: (a.key, a.requires_approval) for c, a in by_cap.items()} == {
        "social.post": ("post", True),
        "social.comments": ("comments", False),
        "social.reply": ("reply", True),
        "social.hide": ("hide", False),
        "social.delete_comment": ("delete_comment", True),
    }
    # Nothing Instagram can't do is claimed.
    assert not {"social.like", "social.repost", "social.message"} & set(by_cap)


def test_the_existing_actions_are_still_there_and_unchanged_in_approval():
    keys = {a.key: a.requires_approval for a in InstagramProvider.actions}
    assert keys["list_recent_comments"] is False and keys["send_private_reply"] is True and keys["auto_reply"] is True
    assert keys["refresh_token"] is False and keys["publish_media"] is True and keys["post"] is True


# ── social.comments ────────────────────────────────────────────────────────


def test_comments_returns_normalised_items_oldest_first_with_replies():
    out = _run("comments")
    assert [i["id"] for i in out["items"]] == ["c1", "c2", "r1"]
    assert [i["created_at"] for i in out["items"]] == sorted(i["created_at"] for i in out["items"])
    c1, c2, r1 = out["items"]
    assert c1 == {
        "id": "c1",
        "kind": "comment",
        "post_id": "m1",
        "post_url": "https://www.instagram.com/p/AAA/",
        "author": {"id": "u1", "handle": "fan", "name": "fan"},
        "text": "What SIZE is this?",
        "created_at": _iso(minutes_ago=30),
        "url": "https://www.instagram.com/p/AAA/c/c1/",
        "network": "instagram",
    }
    assert r1["kind"] == "reply" and r1["post_id"] == "m1" and r1["url"] == "https://www.instagram.com/p/AAA/c/c1/r/r1/"
    assert c2["author"] == {"id": "u2", "handle": "", "name": ""} and c2["post_url"] is None and c2["url"] is None  # Meta withheld both
    assert isinstance(out["cursor"], str) and out["cursor"]


def test_comments_asks_for_replies_and_the_permalink_with_the_bearer_token():
    http = _Http()
    _run("comments", http=http)
    assert "permalink" in http.gets[0] and "replies{" in http.gets[1]


def test_comments_since_the_cursor_returns_only_what_is_new():
    first = _run("comments")
    assert _run("comments", {"since": first["cursor"]})["items"] == []
    newer = {**COMMENTS["m2"], "data": [*COMMENTS["m2"]["data"], {"id": "c9", "text": "later", "from": {"id": "u3"}, "timestamp": _ts()}]}
    http = _Http(routes={"/me/media": MEDIA, "/m1/comments": COMMENTS["m1"], "/m2/comments": newer})
    out = _run("comments", {"since": first["cursor"]}, http=http)
    assert [i["id"] for i in out["items"]] == ["c9"]
    assert _run("comments", {"since": out["cursor"]}, http=http)["items"] == []


def test_comments_limit_drains_a_backlog_oldest_first_without_gaps():
    seen, since = [], encode_cursor(None, [])  # an empty cursor: start from the oldest rather than the newest
    for _ in range(5):
        out = _run("comments", {"limit": 1, "since": since})
        seen += [i["id"] for i in out["items"]]
        since = out["cursor"]
    assert seen == ["c1", "c2", "r1"]  # one at a time, nothing repeated, nothing lost


def test_comments_first_poll_with_a_limit_takes_the_newest():
    assert [i["id"] for i in _run("comments", {"limit": 2})["items"]] == ["c2", "r1"]


def test_comments_kinds_filter_and_mentions():
    assert [i["id"] for i in _run("comments", {"kinds": ["reply"]})["items"]] == ["r1"]
    assert [i["id"] for i in _run("comments", {"kinds": ["comment"]})["items"]] == ["c1", "c2"]
    # mention alongside the others is accepted (and yields nothing of its own) ...
    assert [i["id"] for i in _run("comments", {"kinds": ["comment", "reply", "mention"]})["items"]] == ["c1", "c2", "r1"]
    # ... but only mentions, or an unknown kind, is a clear refusal.
    for kinds in (["mention"], ["like"]):
        with pytest.raises(IntegrationError) as e:
            _run("comments", {"kinds": kinds})
        assert e.value.code == "unsupported"


def test_comments_a_bad_cursor_or_limit_is_refused_before_any_call():
    http = _Http()
    with pytest.raises(IntegrationError) as e:
        _run("comments", {"since": "not-a-cursor"}, http=http)
    assert e.value.code == "invalid_cursor" and http.gets == []
    with pytest.raises(ValueError, match="limit"):
        _run("comments", {"limit": -1}, http=http)


def test_comments_api_failure_raises():
    with pytest.raises(ValueError, match="HTTP 404"):
        _run("comments", http=_Http(routes={}))


def test_comments_skips_a_row_without_a_usable_timestamp():
    bad = {"data": [{"id": "c5", "text": "x", "from": {"id": "u"}, "timestamp": "garbage"}]}
    http = _Http(routes={"/me/media": {"data": [{"id": "m1"}]}, "/m1/comments": bad})
    assert _run("comments", http=http)["items"] == []


def test_the_cursor_keeps_comments_in_the_same_second_as_the_last_one():
    def item(i, t):
        return {"id": i, "created_at": t}

    items = [item("a", "2026-01-01T00:00:00+00:00"), item("b", "2026-01-01T00:00:00+00:00")]
    page, cursor = page_after(items[:1], None, 10)
    assert [i["id"] for i in page] == ["a"]
    page, cursor = page_after(items, cursor, 10)
    assert [i["id"] for i in page] == ["b"]  # same second, not yet seen
    assert page_after(items, cursor, 10)[0] == []
    assert decode_cursor(encode_cursor(None, [])) == (None, set())


# ── social.reply ───────────────────────────────────────────────────────────


def _reply_http(age_days=1, **kw):
    return _Http(routes=_comment_node(age_days), **kw)


def test_reply_is_private_by_default_and_reuses_the_dm_call():
    http = _reply_http()
    out = _run("reply", {"comment_id": "c1", "text": "Sizes S–XL"}, http=http)
    assert out == {"replied": True, "reply_id": "mid.1", "private": True, "url": None, "network": "instagram"}
    (post,) = http.posts
    assert post["url"] == f"{BASE}/1789/messages"
    assert post["json"] == {"recipient": {"comment_id": "c1"}, "message": {"text": "Sizes S–XL"}}
    assert post["headers"] == {"Authorization": "Bearer tok"}


def test_reply_public_posts_to_the_replies_edge():
    http = _Http()
    out = _run("reply", {"comment_id": "c1", "text": "Thank you!", "private": False}, http=http)
    assert out == {"replied": True, "reply_id": "new1", "private": False, "url": None, "network": "instagram"}
    (post,) = http.posts
    assert post["url"] == f"{BASE}/c1/replies"
    assert parse_qs(post["data"].decode()) == {"message": ["Thank you!"]}
    assert http.gets == []  # the 7-day window is for private replies only


def test_reply_private_dry_run_checks_the_comment_and_sends_nothing():
    http = _reply_http()
    out = _run("reply", {"comment_id": "c1", "text": "hi", "dry_run": True}, http=http)
    assert out["replied"] is False and out["dry_run"] is True and out["reply_id"] is None
    assert out["would_reply"] == {"comment_id": "c1", "text": "hi", "private": True}
    assert http.posts == [] and len(http.gets) == 1


def test_reply_public_dry_run_sends_and_reads_nothing():
    http = _Http()
    out = _run("reply", {"comment_id": "c1", "text": "hi", "private": False, "dry_run": True}, http=http)
    assert out["replied"] is False and out["private"] is False and out["would_reply"]["private"] is False
    assert http.posts == [] and http.gets == []


def test_reply_private_refuses_a_comment_past_the_seven_day_window_before_sending():
    http = _reply_http(age_days=8)
    with pytest.raises(IntegrationError, match="7 days") as e:
        _run("reply", {"comment_id": "c1", "text": "hi"}, http=http)
    assert e.value.code == "unsupported" and http.posts == []
    # the same refusal on a dry run, so the engine learns it without sending
    with pytest.raises(IntegrationError):
        _run("reply", {"comment_id": "c1", "text": "hi", "dry_run": True}, http=_reply_http(age_days=8))


def test_reply_private_at_six_days_still_goes_out_and_public_ignores_the_window():
    assert _run("reply", {"comment_id": "c1", "text": "hi"}, http=_reply_http(age_days=6))["replied"] is True
    http = _Http(routes=_comment_node(30))
    assert _run("reply", {"comment_id": "c1", "text": "hi", "private": False}, http=http)["replied"] is True


def test_reply_private_refuses_the_accounts_own_comment():
    http = _reply_http()
    with pytest.raises(IntegrationError, match="own account") as e:
        _run("reply", {"comment_id": "c1", "text": "hi"}, http=http, own_username="Fan")
    assert e.value.code == "unsupported" and http.posts == []


def test_reply_flags_given_as_text_are_read_as_text():
    http = _Http()
    _run("reply", {"comment_id": "c1", "text": "hi", "private": "false"}, http=http)
    assert http.posts[0]["url"].endswith("/c1/replies")
    out = _run("reply", {"comment_id": "c1", "text": "hi", "dry_run": "true"}, http=_reply_http())
    assert out["replied"] is False


def test_reply_needs_comment_id_and_text():
    for args in ({"text": "hi"}, {"comment_id": "c1"}, {"comment_id": " ", "text": "hi"}):
        with pytest.raises(ValueError, match="comment_id"):
            _run("reply", args)


def test_reply_meta_refusals_raise_instead_of_claiming_success():
    with pytest.raises(ValueError, match="HTTP 400"):
        _run("reply", {"comment_id": "c1", "text": "hi"}, http=_reply_http(status=400))
    with pytest.raises(ValueError, match="HTTP 400"):
        _run("reply", {"comment_id": "c1", "text": "hi", "private": False}, http=_Http(status=400))
    with pytest.raises(ValueError, match="HTTP 404"):  # the comment can't be read, so the window can't be checked
        _run("reply", {"comment_id": "gone", "text": "hi"}, http=_Http(routes={}))


# ── social.hide ────────────────────────────────────────────────────────────


def test_hide_hides_by_default_and_can_unhide():
    http = _Http()
    assert _run("hide", {"comment_id": "c1"}, http=http) == {"hidden": True, "network": "instagram"}
    assert http.posts[0]["url"] == f"{BASE}/c1?hide=true" and http.posts[0]["headers"]["Authorization"] == "Bearer tok"
    assert _run("hide", {"comment_id": "c1", "hidden": False}, http=http) == {"hidden": False, "network": "instagram"}
    assert urlparse(http.posts[1]["url"]).query == "hide=false"


def test_hide_errors():
    with pytest.raises(ValueError, match="comment_id"):
        _run("hide", {})
    with pytest.raises(ValueError, match="HTTP 400"):
        _run("hide", {"comment_id": "c1"}, http=_Http(status=400))
    with pytest.raises(ValueError, match="visibility"):
        _run("hide", {"comment_id": "c1"}, http=_Http(post_body={"success": False}))


# ── social.delete_comment ──────────────────────────────────────────────────


def test_delete_comment_deletes_the_comment():
    http = _Http()
    assert _run("delete_comment", {"comment_id": "c1"}, http=http) == {"deleted": True, "network": "instagram"}
    assert http.deletes == [{"url": f"{BASE}/c1", "headers": {"Authorization": "Bearer tok"}}]
    assert http.posts == []


def test_delete_comment_errors():
    with pytest.raises(ValueError, match="comment_id"):
        _run("delete_comment", {})
    with pytest.raises(ValueError, match="HTTP 403"):
        _run("delete_comment", {"comment_id": "c1"}, http=_Http(status=403))


def test_every_new_action_needs_the_token():
    for key, args in (
        ("comments", {}),
        ("reply", {"comment_id": "c", "text": "t"}),
        ("hide", {"comment_id": "c"}),
        ("delete_comment", {"comment_id": "c"}),
    ):
        with pytest.raises(ValueError, match="token"):
            _run(key, args, secret=None)
