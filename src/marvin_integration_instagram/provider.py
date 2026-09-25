"""Instagram provider: Graph API calls for comments and private replies.

Uses the Instagram Login flavour of the Graph API (``graph.instagram.com``) with a long-lived user
token. A *private reply* is one DM per comment, allowed within 7 days of the comment; Meta rejects a
second reply to the same comment. Under Standard Access, replies only reach accounts that hold a
role on the Meta app — public commenters need Advanced Access (App Review + Business Verification).

The provider is pure with respect to Marvin: it gets ``config``, the resolved ``secret`` (the
token), a ``logger`` and a safe ``http`` client via ``ctx``, and returns dicts. Persistence — which
comments were already answered, the reply log — is the core's job and arrives as action arguments.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import ClassVar

from marvin_integration_sdk import (
    CATEGORY_DESTINATION,
    CredentialField,
    IntegrationContext,
    IntegrationProvider,
    ProviderAction,
    register_provider,
)

from .content import CONTENT
from .matching import match_rules

DEFAULT_API_VERSION = "v22.0"
DEFAULT_LOOKBACK_MEDIA = 10
DEFAULT_MAX_AGE_DAYS = 7
COMMENTS_PAGE_SIZE = 50
COMMENT_FIELDS = "id,text,username,timestamp,from"


def _parse_ts(value: str | None) -> datetime | None:
    # Graph timestamps look like "2026-09-24T18:03:11+0000" — not quite ISO 8601 for fromisoformat.
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None


def _record(match: dict) -> dict:
    """The persisted shape of one match. `commenter` is the best available handle: the username
    when Meta gives it, otherwise the user id, otherwise the comment id."""
    username, user_id = match.get("username") or "", match.get("user_id") or ""
    commenter = f"@{username}" if username else (f"user {user_id}" if user_id else f"comment {match['comment_id']}")
    return {
        "comment_id": match["comment_id"],
        "media_id": match["media_id"],
        "username": username,
        "user_id": user_id,
        "commenter": commenter,
        "keyword": match["keyword"],
        "reply": match["reply"],
        "text": match["text"],
    }


@register_provider
class InstagramProvider(IntegrationProvider):
    slug = "instagram"
    name = "Instagram"
    description = "Poll comments on your recent posts and send keyword-triggered private replies (DMs)."
    category = CATEGORY_DESTINATION

    content = CONTENT
    """Entry types, collections and tasks this integration needs — offered for review on install."""

    credentials = (
        CredentialField(
            key="access_token",
            label="Access token",
            help="Long-lived Instagram Login user token (60 days; use the refresh_token action to extend).",
        ),
    )
    config_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "ig_user_id": {"type": "string", "title": "Instagram user ID", "description": "The numeric IG user id the token belongs to."},
            "api_version": {"type": "string", "title": "Graph API version", "default": DEFAULT_API_VERSION},
            "lookback_media": {
                "type": "string",
                "title": "Posts to scan",
                "default": str(DEFAULT_LOOKBACK_MEDIA),
                "description": "How many of your most recent posts to check for comments.",
            },
            "own_username": {"type": "string", "title": "Own username", "description": "Comments by this account are never answered."},
        },
        "required": ["ig_user_id"],
        "additionalProperties": False,
    }

    actions = (
        ProviderAction(
            key="list_recent_comments",
            label="List recent comments",
            description="Comments on your most recent posts.",
            input_schema={"type": "object", "properties": {"limit": {"type": "integer"}}, "additionalProperties": False},
        ),
        ProviderAction(
            key="send_private_reply",
            label="Send private reply",
            description="DM the author of one comment (once per comment, within 7 days).",
            input_schema={
                "type": "object",
                "properties": {"comment_id": {"type": "string"}, "text": {"type": "string"}},
                "required": ["comment_id", "text"],
                "additionalProperties": False,
            },
            requires_approval=True,
        ),
        ProviderAction(
            key="auto_reply",
            label="Auto-reply to keyword comments",
            description="Match recent comments against keyword rules and send the configured reply (dry-run by default).",
            input_schema={
                "type": "object",
                "properties": {
                    "rules": {"type": "array", "items": {"type": "object"}},
                    "dry_run": {"type": "boolean", "default": True},
                    "skip_comment_ids": {"type": "array", "items": {"type": "string"}},
                    "max_age_days": {"type": "integer", "default": DEFAULT_MAX_AGE_DAYS},
                },
                "required": ["rules"],
                "additionalProperties": False,
            },
            cost_hint="free",
            requires_approval=True,
        ),
        ProviderAction(
            key="refresh_token",
            label="Refresh access token",
            description="Extend the long-lived token by another 60 days; the core stores the new token.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
    )

    # ---- plumbing ---------------------------------------------------------------------------

    @staticmethod
    def _cfg(ctx: IntegrationContext) -> dict:
        return ctx.config or {}

    def _base(self, ctx: IntegrationContext) -> str:
        return f"https://graph.instagram.com/{self._cfg(ctx).get('api_version') or DEFAULT_API_VERSION}"

    @staticmethod
    def _auth(ctx: IntegrationContext) -> dict[str, str]:
        return {"Authorization": f"Bearer {ctx.secret}"}

    def _lookback(self, ctx: IntegrationContext) -> int:
        # The settings form renders flat text inputs, so this arrives as a string.
        try:
            return max(1, int(self._cfg(ctx).get("lookback_media") or DEFAULT_LOOKBACK_MEDIA))
        except (TypeError, ValueError):
            return DEFAULT_LOOKBACK_MEDIA

    def _require_secret(self, ctx: IntegrationContext) -> None:
        if not ctx.secret:
            raise ValueError("No Instagram access token configured.")

    def _get_json(self, ctx: IntegrationContext, url: str) -> dict:
        resp = ctx.http.get(url, headers=self._auth(ctx))
        if not resp.ok:
            raise ValueError(f"Instagram API error: HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    # ---- Graph calls ------------------------------------------------------------------------

    def _list_media(self, ctx: IntegrationContext) -> list[dict]:
        url = f"{self._base(ctx)}/me/media?fields=id,timestamp&limit={self._lookback(ctx)}"
        return self._get_json(ctx, url).get("data") or []

    def _list_comments(self, ctx: IntegrationContext, media_id: str) -> list[dict]:
        url = f"{self._base(ctx)}/{media_id}/comments?fields={COMMENT_FIELDS}&limit={COMMENTS_PAGE_SIZE}"
        # Meta withholds `username` for commenters without a role on the app; `from.id` usually survives.
        return [
            {
                "comment_id": str(c.get("id")),
                "media_id": str(media_id),
                "username": c.get("username") or "",
                "user_id": str((c.get("from") or {}).get("id") or ""),
                "text": c.get("text") or "",
                "timestamp": _parse_ts(c.get("timestamp")),
            }
            for c in self._get_json(ctx, url).get("data") or []
        ]

    def _recent_comments(self, ctx: IntegrationContext) -> list[dict]:
        comments: list[dict] = []
        for media in self._list_media(ctx):
            comments.extend(self._list_comments(ctx, str(media.get("id"))))
        return comments

    def _send(self, ctx: IntegrationContext, comment_id: str, text: str):
        url = f"{self._base(ctx)}/{self._cfg(ctx).get('ig_user_id')}/messages"
        body = {"recipient": {"comment_id": comment_id}, "message": {"text": text}}
        return ctx.http.post(url, json=body, headers=self._auth(ctx))

    # ---- lifecycle --------------------------------------------------------------------------

    def check(self, ctx: IntegrationContext) -> tuple[str, str | None]:
        if not ctx.secret:
            return ("unconfigured", "Missing access token.")
        if not self._cfg(ctx).get("ig_user_id"):
            return ("unconfigured", "Missing Instagram user ID.")
        try:
            resp = ctx.http.get(f"{self._base(ctx)}/me?fields=user_id,username", headers=self._auth(ctx))
        except Exception as e:  # noqa: BLE001
            return ("error", str(e))
        return ("ok", None) if resp.ok else ("error", f"HTTP {resp.status_code}: {resp.text[:200]}")

    def run_action(self, key: str, args: dict, ctx: IntegrationContext) -> dict:
        handler = {
            "list_recent_comments": self._action_list_recent_comments,
            "send_private_reply": self._action_send_private_reply,
            "auto_reply": self._action_auto_reply,
            "refresh_token": self._action_refresh_token,
        }.get(key)
        if handler is None:
            raise NotImplementedError(f"instagram has no action '{key}'")
        self._require_secret(ctx)
        return handler(args or {}, ctx)

    # ---- actions ----------------------------------------------------------------------------

    def _action_list_recent_comments(self, args: dict, ctx: IntegrationContext) -> dict:
        comments = self._recent_comments(ctx)
        limit = args.get("limit")
        if limit:
            comments = comments[: int(limit)]
        return {"comments": [{**c, "timestamp": c["timestamp"].isoformat() if c["timestamp"] else None} for c in comments]}

    def _action_send_private_reply(self, args: dict, ctx: IntegrationContext) -> dict:
        comment_id, text = args.get("comment_id"), args.get("text")
        if not comment_id or not text:
            raise ValueError("send_private_reply needs 'comment_id' and 'text'.")
        resp = self._send(ctx, str(comment_id), str(text))
        return {"sent": resp.ok, "status": resp.status_code, "response": resp.text[:500]}

    def _action_auto_reply(self, args: dict, ctx: IntegrationContext) -> dict:
        dry_run = bool(args.get("dry_run", True))
        rules = args.get("rules") or []
        skip_ids = {str(i) for i in (args.get("skip_comment_ids") or []) if i}
        own = self._cfg(ctx).get("own_username")
        comments = self._recent_comments(ctx)
        matches, skipped = match_rules(
            comments,
            rules,
            now=datetime.now(UTC),
            max_age_days=int(args.get("max_age_days") or DEFAULT_MAX_AGE_DAYS),
            skip_ids=skip_ids,
            skip_usernames={own} if own else set(),
        )

        result = {"checked": len(comments), "matched": len(matches), "sent": 0, "dry_run": dry_run, "records": [], "skipped": skipped}
        if dry_run:
            # Nothing persisted on a dry run: `records` stays empty so the core never logs a reply
            # that was not actually sent (a logged comment id blocks a real send later).
            result["would_send"] = [_record(m) for m in matches]
            return result

        for m in matches:
            resp = self._send(ctx, m["comment_id"], m["reply"])
            if not resp.ok:
                # A failed send is reported, never raised — one bad comment must not abort the run.
                ctx.logger.warning("instagram: private reply to %s failed: HTTP %s %s", m["comment_id"], resp.status_code, resp.text[:200])
                skipped.append({"comment_id": m["comment_id"], "reason": f"send_failed: HTTP {resp.status_code}"})
                continue
            result["sent"] += 1
            result["records"].append({**_record(m), "sent_at": datetime.now(UTC).isoformat()})
        return result

    def _action_refresh_token(self, args: dict, ctx: IntegrationContext) -> dict:
        data = self._get_json(ctx, f"{self._base(ctx)}/refresh_access_token?grant_type=ig_refresh_token")
        token = data.get("access_token")
        if not token:
            raise ValueError("Instagram did not return a refreshed access token.")
        return {"expires_in": data.get("expires_in"), "secret_update": token}
