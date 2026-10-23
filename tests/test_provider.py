"""Tests for the Instagram provider — Graph calls through a stub http helper."""

import json
import logging
from datetime import UTC, datetime, timedelta

import pytest
from marvin_integration_sdk import IntegrationContext, Response, load_guide
from marvin_integration_sdk.testing import IntegrationContract

from marvin_integration_instagram import InstagramProvider

_LOG = logging.getLogger("test")
BASE = "https://graph.instagram.com/v22.0"


# Relative to the real clock: the provider ages comments against datetime.now(UTC) (default max age
# 7 days), so a fixed date here would turn every comment "too_old" a week later.
NOW = datetime.now(UTC).replace(microsecond=0)


def _ts(days_ago=0):
    return (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S%z")


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


def test_check_ok_error_and_unconfigured():
    p = InstagramProvider()
    assert p.check(_ctx()) == ("ok", None)
    assert p.check(_ctx(http=_graph_http(get_status=400)))[0] == "error"
    assert p.check(_ctx(secret=None))[0] == "unconfigured"
    assert p.check(IntegrationContext(config={}, secret="tok", logger=_LOG, http=_graph_http()))[0] == "unconfigured"


def test_missing_secret_raises_and_unknown_action_raises():
    p = InstagramProvider()
    with pytest.raises(ValueError):
        p.run_action("comments", {}, _ctx(secret=None))
    with pytest.raises(NotImplementedError):
        p.run_action("nope", {}, _ctx())


def test_the_old_keyword_actions_are_gone():
    """0.8.0: Marvin's Social replaces auto_reply, list_recent_comments and send_private_reply (comments and reply do their jobs)."""
    keys = {a.key for a in InstagramProvider.actions}
    assert not keys & {"list_recent_comments", "send_private_reply", "auto_reply"}
    assert {"comments", "reply", "hide", "delete_comment", "post", "publish_media", "refresh_token"} <= keys
    for gone in ("list_recent_comments", "send_private_reply", "auto_reply"):
        with pytest.raises(NotImplementedError):
            InstagramProvider().run_action(gone, {}, _ctx())


def test_lookback_media_string_is_cast():
    http = _graph_http()
    InstagramProvider().run_action("comments", {}, _ctx(http=http, lookback_media="3"))
    assert "limit=3" in http.gets[0]["url"]
    assert http.gets[0]["headers"] == {"Authorization": "Bearer tok"}
    http = _graph_http()
    InstagramProvider().run_action("comments", {}, _ctx(http=http, lookback_media="bogus"))
    assert "limit=10" in http.gets[0]["url"]


def test_get_failure_raises_value_error():
    with pytest.raises(ValueError, match="HTTP 500"):
        InstagramProvider().run_action("comments", {}, _ctx(http=_graph_http(get_status=500)))


def test_refresh_token_returns_secret_update():
    http = _StubHttp(routes={"refresh_access_token": {"access_token": "new-tok", "token_type": "bearer", "expires_in": 5183944}})
    out = InstagramProvider().run_action("refresh_token", {}, _ctx(http=http))
    assert out == {"expires_in": 5183944, "secret_update": "new-tok"}
    assert "grant_type=ig_refresh_token" in http.gets[0]["url"]
    assert "access_token=" in http.gets[0]["url"]  # Meta's refresh endpoint takes the token as a parameter, not the header


def test_provider_declares_the_token_refresh_task_and_the_webhook():
    """0.8.0 stops shipping the ig-* entry types, the two Social collections and the auto-reply task; 0.10.0 offers a webhook."""
    content = {c.slug: c for c in InstagramProvider().content}
    assert set(content) == {"instagram-token-refresh", "instagram-webhooks"}
    task = content["instagram-token-refresh"]
    assert task.kind == "scheduled_task" and not task.required and not task.requires
    assert task.payload["task_config"] == {"integration": "instagram", "action": "refresh_token"}


def test_the_declared_task_starts_disabled():
    # Installing an integration must never start anything on its own.
    (task,) = [c for c in InstagramProvider().content if c.kind == "scheduled_task"]
    assert task.payload["enabled"] is False


# ── what Instagram teaches Marvin (SDK 0.9.0) ────────────────────────────────


class TestInstagramContract(IntegrationContract):
    @pytest.fixture
    def provider(self):
        return InstagramProvider()


def test_the_guide_ships_with_the_package():
    guide = load_guide(InstagramProvider)
    assert guide.startswith("# Instagram") and "Social › Instagram › Rules" in guide and "Publish a post" in guide
    assert "ig-reply-" not in guide.split("Moving from version 0.7", 1)[0] and "`auto_reply`" not in guide.split("## Actions", 1)[1]


def test_the_guide_tells_workflows_the_truth_about_which_actions_they_can_run():
    # The core runs an approval-gated action only right after an approved Approval step; the guide says so and names them.
    needs_approval = {a.key for a in InstagramProvider.actions if a.requires_approval}
    assert needs_approval == {"publish_media", "post", "reply", "delete_comment", "message"}
    workflows = load_guide(InstagramProvider).split("## In workflows", 1)[1].split("## ", 1)[0]
    assert "Approval" in workflows and all(
        label in workflows for label in ("Publish a post", "Post to Instagram", "Reply to a comment", "Delete a comment", "Send a direct message")
    )


def test_the_post_on_publish_recipe_asks_first_then_posts_the_main_picture():
    (recipe,) = InstagramProvider.recipes
    ask, jpeg, post, keep = recipe.definition["actions"]
    assert ask["kind"] == "approval" and post["when"] == {"field": "steps.ok.output.decision", "op": "eq", "value": "approved"}
    assert (jpeg["kind"], jpeg["op"], jpeg["format"], jpeg["source"], jpeg["when"]) == ("media", "convert", "jpeg", "${entry.image}", post["when"])
    assert (post["action"], post["integration"], post["args"]["image_url"], post["args"]["dry_run"]) == (
        "publish_media",
        "{{instagram_integration}}",
        "${steps.jpeg.output.asset.url}",
        False,
    )
    assert keep["metadata"]["instagram_permalink"] == "${steps.post.output.permalink}" and keep["when"]["field"] == "steps.post.output.published"
    assert {"field": "entry.image", "op": "exists"} in recipe.definition["conditions"]  # no picture, no ask
    assert set(recipe.variables) == {"entry_type", "instagram_integration", "hashtags"}
