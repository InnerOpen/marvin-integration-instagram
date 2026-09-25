"""Tests for the Instagram provider — Graph calls through a stub http helper."""

import json
import logging
from datetime import UTC, datetime

import pytest
from marvin_integration_sdk import IntegrationContext, Response

from marvin_integration_instagram import InstagramProvider

_LOG = logging.getLogger("test")
BASE = "https://graph.instagram.com/v22.0"


def _ts(days_ago=0):
    dt = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)
    dt = dt.replace(day=dt.day - days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%S%z")


MEDIA = {"data": [{"id": "m1", "timestamp": _ts()}, {"id": "m2", "timestamp": _ts(1)}]}
COMMENTS = {
    "m1": {"data": [{"id": "c1", "text": "What SIZE is this?", "username": "fan", "from": {"id": "u1", "username": "fan"}, "timestamp": _ts()}]},
    "m2": {
        "data": [
            {"id": "c2", "text": "love it", "from": {"id": "u2"}, "timestamp": _ts()},
            {"id": "c3", "text": "size?", "username": "me", "timestamp": _ts()},
        ]
    },
}


class _StubHttp:
    """GETs are answered by URL substring; POSTs are recorded and answered with `post_status`."""

    def __init__(self, routes=None, post_status=200, get_status=200):
        self.routes = routes or {}
        self.post_status = post_status
        self.get_status = get_status
        self.gets: list[dict] = []
        self.posts: list[dict] = []

    def get(self, url, *, headers=None, timeout=15):
        self.gets.append({"url": url, "headers": headers})
        for needle, payload in self.routes.items():
            if needle in url:
                return Response(status_code=self.get_status, content=json.dumps(payload).encode())
        return Response(status_code=404, content=b'{"error":"no route"}')

    def post(self, url, *, json=None, data=None, headers=None, timeout=15):
        self.posts.append({"url": url, "json": json, "headers": headers})
        return Response(status_code=self.post_status, content=b'{"recipient_id":"x","message_id":"y"}')


def _graph_http(**kw):
    return _StubHttp(
        routes={"/me/media": MEDIA, "/m1/comments": COMMENTS["m1"], "/m2/comments": COMMENTS["m2"], "/me?": {"user_id": "1", "username": "me"}}, **kw
    )


def _ctx(secret="tok", http=None, **config):
    cfg = {"ig_user_id": "1789", **config}
    return IntegrationContext(config=cfg, secret=secret, logger=_LOG, http=http or _graph_http())


RULES = [{"post_id": "*", "keywords": "size, sizing", "reply": "Sizes S–XL, DM us for a chart."}]


def test_check_ok_error_and_unconfigured():
    p = InstagramProvider()
    assert p.check(_ctx()) == ("ok", None)
    assert p.check(_ctx(http=_graph_http(get_status=400)))[0] == "error"
    assert p.check(_ctx(secret=None))[0] == "unconfigured"
    assert p.check(IntegrationContext(config={}, secret="tok", logger=_LOG, http=_graph_http()))[0] == "unconfigured"


def test_missing_secret_raises_and_unknown_action_raises():
    p = InstagramProvider()
    with pytest.raises(ValueError):
        p.run_action("list_recent_comments", {}, _ctx(secret=None))
    with pytest.raises(NotImplementedError):
        p.run_action("nope", {}, _ctx())


def test_list_recent_comments_walks_media_and_normalizes():
    http = _graph_http()
    out = InstagramProvider().run_action("list_recent_comments", {}, _ctx(http=http))
    assert [c["comment_id"] for c in out["comments"]] == ["c1", "c2", "c3"]
    assert out["comments"][0]["media_id"] == "m1"
    assert out["comments"][1]["username"] == "" and out["comments"][1]["user_id"] == "u2"  # username withheld, from.id kept
    assert out["comments"][0]["timestamp"].startswith("2026-09-24")
    assert all(g["headers"] == {"Authorization": "Bearer tok"} for g in http.gets)
    assert http.gets[0]["url"].startswith(f"{BASE}/me/media?")


def test_lookback_media_string_is_cast():
    http = _graph_http()
    InstagramProvider().run_action("list_recent_comments", {}, _ctx(http=http, lookback_media="3"))
    assert "limit=3" in http.gets[0]["url"]
    http = _graph_http()
    InstagramProvider().run_action("list_recent_comments", {}, _ctx(http=http, lookback_media="bogus"))
    assert "limit=10" in http.gets[0]["url"]


def test_get_failure_raises_value_error():
    with pytest.raises(ValueError, match="HTTP 500"):
        InstagramProvider().run_action("list_recent_comments", {}, _ctx(http=_graph_http(get_status=500)))


def test_auto_reply_dry_run_posts_nothing_and_returns_no_records():
    http = _graph_http()
    out = InstagramProvider().run_action("auto_reply", {"rules": RULES}, _ctx(http=http, own_username="me"))
    assert http.posts == []
    assert out["dry_run"] is True
    assert out["records"] == []
    assert out["checked"] == 3 and out["matched"] == 1 and out["sent"] == 0
    assert [w["comment_id"] for w in out["would_send"]] == ["c1"]
    assert out["would_send"][0]["keyword"] == "size"
    assert {s["comment_id"]: s["reason"] for s in out["skipped"]} == {"c2": "no_match", "c3": "own_comment"}


def test_auto_reply_real_run_sends_and_returns_records():
    http = _graph_http()
    out = InstagramProvider().run_action("auto_reply", {"rules": RULES, "dry_run": False, "skip_comment_ids": ["c3"]}, _ctx(http=http))
    assert len(http.posts) == 1
    post = http.posts[0]
    assert post["url"] == f"{BASE}/1789/messages"
    assert post["json"] == {"recipient": {"comment_id": "c1"}, "message": {"text": RULES[0]["reply"]}}
    assert post["headers"] == {"Authorization": "Bearer tok"}
    assert out["sent"] == 1 and "would_send" not in out
    rec = out["records"][0]
    assert rec["comment_id"] == "c1" and rec["username"] == "fan" and rec["user_id"] == "u1" and rec["keyword"] == "size" and rec["sent_at"]
    assert rec["commenter"] == "@fan"
    assert {"comment_id": "c3", "reason": "already_replied"} in out["skipped"]


def test_auto_reply_send_failure_is_skipped_not_raised():
    http = _graph_http(post_status=400)
    out = InstagramProvider().run_action("auto_reply", {"rules": RULES, "dry_run": False}, _ctx(http=http))
    assert out["sent"] == 0 and out["records"] == []
    assert {"comment_id": "c1", "reason": "send_failed: HTTP 400"} in out["skipped"]


def test_send_private_reply_posts_once():
    http = _graph_http()
    out = InstagramProvider().run_action("send_private_reply", {"comment_id": "c1", "text": "hi"}, _ctx(http=http))
    assert out["sent"] is True and out["status"] == 200
    assert http.posts[0]["json"]["recipient"] == {"comment_id": "c1"}
    with pytest.raises(ValueError):
        InstagramProvider().run_action("send_private_reply", {"comment_id": "c1"}, _ctx())


def test_refresh_token_returns_secret_update():
    http = _StubHttp(routes={"refresh_access_token": {"access_token": "new-tok", "token_type": "bearer", "expires_in": 5183944}})
    out = InstagramProvider().run_action("refresh_token", {}, _ctx(http=http))
    assert out == {"expires_in": 5183944, "secret_update": "new-tok"}
    assert "grant_type=ig_refresh_token" in http.gets[0]["url"]


def test_record_commenter_falls_back_to_user_id_then_comment_id():
    from marvin_integration_instagram.provider import _record

    base = {"comment_id": "c9", "media_id": "m", "keyword": "k", "reply": "r", "text": "t"}
    assert _record({**base, "username": "", "user_id": "u9"})["commenter"] == "user u9"
    assert _record({**base, "username": "", "user_id": ""})["commenter"] == "comment c9"


def test_provider_declares_the_content_its_actions_depend_on():
    """auto_reply reads rules from entries and writes a log entry per send, and the task is what
    calls it — so these are how the integration works, not optional extras."""
    content = {c.slug: c for c in InstagramProvider().content}
    assert set(content) == {
        "ig-auto-reply",
        "ig-reply-log",
        "social-auto-responses",
        "social-sent",
        "instagram-auto-reply",
        "instagram-token-refresh",
    }
    assert {c.kind for c in content.values()} == {"entry_type", "collection", "scheduled_task"}


def test_declared_tasks_start_disabled_and_in_dry_run():
    # Installing an integration must never start sending DMs on its own.
    tasks = [c for c in InstagramProvider().content if c.kind == "scheduled_task"]
    assert tasks and all(t.payload["enabled"] is False for t in tasks)
    auto = next(t for t in tasks if t.slug == "instagram-auto-reply")
    assert auto.payload["task_config"]["args"]["dry_run"] is True


def test_declared_content_depends_only_on_this_integrations_own_types():
    # A provider owns its own names; it must not reach into the workspace's content model.
    own = {c.slug for c in InstagramProvider().content if c.kind == "entry_type"}
    for blueprint in InstagramProvider().content:
        for requirement in blueprint.requires:
            kind, _, slug = requirement.partition(":")
            assert kind == "entry_type" and slug in own, f"{blueprint.slug} requires foreign content {requirement}"


def test_the_log_type_carries_the_dedupe_field_the_task_reads():
    log = next(c for c in InstagramProvider().content if c.slug == "ig-reply-log")
    fields = [f["key"] for f in log.payload["schema_json"]["fields"]]
    assert "comment_id" in fields
    auto = next(c for c in InstagramProvider().content if c.slug == "instagram-auto-reply")
    assert auto.payload["task_config"]["inputs"]["skip_comment_ids"]["field"] == "comment_id"
