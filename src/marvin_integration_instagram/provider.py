"""Instagram provider: Graph API calls for comments, replies, moderation and posting.

Uses the Instagram Login flavour of the Graph API (``graph.instagram.com``) with a long-lived user
token. A *private reply* is one DM per comment, allowed within 7 days of the comment; Meta rejects a
second reply to the same comment. Under Standard Access, replies only reach accounts that hold a
role on the Meta app — public commenters need Advanced Access (App Review + Business Verification).

The provider is pure with respect to Marvin: it gets ``config``, the resolved ``secret`` (the
token), a ``logger`` and a safe ``http`` client via ``ctx``, and returns dicts. Persistence — which
comments were already answered, the reply log — is the core's job and arrives as action arguments.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import ClassVar
from urllib.parse import urlencode

from marvin_integration_sdk import (
    CATEGORY_DESTINATION,
    CredentialField,
    IntegrationContext,
    IntegrationError,
    IntegrationProvider,
    ProviderAction,
    WorkflowRecipe,
    register_provider,
)

from .content import CONTENT
from .engagement import DEFAULT_LIMIT, KIND_COMMENT, KIND_MENTION, KIND_REPLY, KINDS, NETWORK, decode_cursor, page_after, to_item
from .webhooks import inbox_items

DEFAULT_API_VERSION = "v22.0"
DEFAULT_LOOKBACK_MEDIA = 10
DEFAULT_MAX_AGE_DAYS = 7
COMMENTS_PAGE_SIZE = 50
COMMENT_FIELDS = "id,text,username,timestamp,from"
# The shared `social.comments` read also wants each post's permalink and the replies under each comment.
MEDIA_FIELDS = "id,timestamp"
MEDIA_FIELDS_WITH_LINK = "id,timestamp,permalink"
COMMENT_FIELDS_WITH_REPLIES = f"{COMMENT_FIELDS},replies{{{COMMENT_FIELDS}}}"
# Publishing: a container is created, Meta fetches and processes the picture, then it is published.
PUBLISH_POLL_SECONDS = 2
PUBLISH_WAIT_SECONDS = 60
MAX_CAROUSEL = 10
CAPTION_MAX = 2200
ALT_MAX = 1000  # Meta's limit for an image's alt text (verify against the current docs)


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


def _flag(value, default: bool) -> bool:
    """A boolean argument. Workflow templates can hand over "false" as text, which bool() would read as true."""
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() not in ("", "false", "0", "no", "off")
    return bool(value)


def _unsupported(message: str) -> IntegrationError:
    return IntegrationError(message, code="unsupported")


def _refusal(urls: list[str], caption: str) -> str | None:
    """Why Instagram can't take this post (too many pictures, a non-https URL, a long caption), or None."""
    if len(urls) > MAX_CAROUSEL:
        return f"A carousel holds at most {MAX_CAROUSEL} pictures ({len(urls)} given)."
    if any(not u.startswith("https://") for u in urls):
        return "Instagram fetches the picture itself, so every image URL must be public https."
    if len(caption) > CAPTION_MAX:
        return f"The caption is {len(caption)} characters; Instagram allows {CAPTION_MAX}."
    return None


@register_provider
class InstagramProvider(IntegrationProvider):
    slug = "instagram"
    name = "Instagram"
    description = "Post to Instagram, and read, reply to (privately or publicly), hide and delete the comments on your posts. Keyword auto-replies are Social's, in Marvin."
    category = CATEGORY_DESTINATION
    icon = "📸"
    # Meta's webhooks, for an incoming webhook in Marvin: it checks the URL with a GET (hub.mode / hub.challenge /
    # hub.verify_token — the WebSub handshake) before it delivers, then signs each delivery with the app secret.
    verification_schemes: ClassVar[dict[str, dict]] = {
        "meta": {
            "challenge_param": "hub.challenge",
            "token_param": "hub.verify_token",
            "mode_param": "hub.mode",
            "mode_values": ["subscribe"],
            "notes": "Meta (Instagram, Facebook, WhatsApp): the verify token you type into the app's webhook settings",
        }
    }
    signature_schemes: ClassVar[dict[str, dict]] = {
        "meta": {
            "header": "X-Hub-Signature-256",
            "prefix": "sha256=",
            # The key is the secret of the login setup the app uses: Instagram login signs with the Instagram app secret
            # (Use cases → Instagram API → API setup with Instagram login), Facebook login with App settings → Basic.
            "notes": "Meta: sha256=<hex> of the body, keyed with your app's secret — the Instagram app secret for Instagram login",
            "sender_issues_key": True,
        }
    }
    logo = "logo.png"

    # What Marvin learns about Instagram (SDK 0.9.0): a how-to its agents read when building workflows here, and one
    # ready-made workflow in the Workflow Library, offered once Instagram is connected: post the main picture on publish,
    # after an editor approves. (Comments and replies are Social's: Marvin's own rules and auto-reply use the `comments` and `reply` actions.)
    guide = "guide.md"
    recipes = (
        WorkflowRecipe(
            id="post-on-publish",
            title="Post to Instagram when published",
            outcome=(
                "When an entry with a main picture is published, an editor is asked whether to post it; once approved, a JPEG copy "
                "of the picture goes to the feed with the summary and your hashtags as the caption, and the post's link is kept on "
                "the entry."
            ),
            category="Social and community",
            tags=("instagram", "social", "publishing", "approval"),
            definition={
                "trigger": {"type": "event", "event": "entry_published"},
                "conditions": [
                    {"field": "entry.entry_type", "op": "eq", "value": "{{entry_type}}"},
                    {"field": "entry.image", "op": "exists"},
                ],
                "actions": [
                    {
                        "kind": "approval",
                        "id": "ok",
                        "message": "Post “${entry.title}” to Instagram with its main picture? Caption: ${entry.summary} {{hashtags}}",
                        "approvers": "editor",
                        "expires_in_hours": 48,
                    },
                    {
                        # The feed takes JPEG only: a JPEG copy of the main picture (a PNG's transparency flattened onto
                        # white; an existing JPEG re-encoded once and reused on a re-run), kept as a derived asset.
                        "kind": "media",
                        "id": "jpeg",
                        "op": "convert",
                        "source": "${entry.image}",
                        "format": "jpeg",
                        "quality": 90,
                        "when": {"field": "steps.ok.output.decision", "op": "eq", "value": "approved"},
                    },
                    {
                        "kind": "integration",
                        "id": "post",
                        "integration": "{{instagram_integration}}",
                        "action": "publish_media",
                        "args": {"image_url": "${steps.jpeg.output.asset.url}", "caption": "${entry.summary} {{hashtags}}", "dry_run": False},
                        "when": {"field": "steps.ok.output.decision", "op": "eq", "value": "approved"},
                    },
                    {
                        "kind": "entry",
                        "op": "set_metadata",
                        "metadata": {"instagram_media_id": "${steps.post.output.media_id}", "instagram_permalink": "${steps.post.output.permalink}"},
                        "when": {"field": "steps.post.output.published", "op": "eq", "value": True},
                    },
                ],
            },
            variables={
                "entry_type": {
                    "type": "entry_type_slug",
                    "description": "Entries of this type are offered to Instagram when published (only those with a main picture).",
                    "example": "artwork",
                },
                "instagram_integration": {
                    "type": "integration_slug",
                    "description": "The Instagram connection; its token needs the content-publishing permission.",
                    "example": "instagram",
                },
                "hashtags": {
                    "type": "text",
                    "description": "Appended to every caption (hashtags and mentions, as written).",
                    "example": "#handmade #studio",
                },
            },
            side_effects=(
                {"kind": "external_post", "description": "One Instagram feed post per approved run (Instagram allows 25 per 24 hours)."},
                {
                    "kind": "content_mutation",
                    "description": "Keeps a JPEG copy of the picture as a new asset, and saves the post's link on the entry.",
                },
            ),
        ),
    )

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
            key="refresh_token",
            label="Refresh access token",
            description="Extend the long-lived token by another 60 days; the core stores the new token.",
            input_schema={"type": "object", "properties": {}, "additionalProperties": False},
        ),
        ProviderAction(
            key="publish_media",
            label="Publish a post",
            description=(
                "Post one picture, or a carousel of 2–10, to the feed with a caption. Dry-run by default: reports what would "
                "go out and the day's remaining quota without posting."
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "image_url": {
                        "type": "string",
                        "description": "A public https URL of a JPEG (the feed takes JPEG only); an asset's URL works, e.g. ${entry.image}.",
                    },
                    "image_urls": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "2–10 public JPEG URLs for a carousel, instead of image_url.",
                    },
                    "caption": {"type": "string", "description": "Up to 2,200 characters; #hashtags and @mentions as written."},
                    "dry_run": {"type": "boolean", "default": True},
                },
                "additionalProperties": False,
            },
            cost_hint="free",
            requires_approval=True,
        ),
        ProviderAction(
            key="post",
            label="Post to Instagram",
            description=(
                "Marvin's shared social post: the text becomes the caption (a link is appended to it) and the pictures become "
                "one feed post, or a carousel of 2–10. Instagram can't post text alone, so at least one picture is required."
            ),
            capability="social.post",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": f"The caption, up to {CAPTION_MAX} characters including the link."},
                    "images": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                        "maxItems": MAX_CAROUSEL,
                        "description": "1–10 public https JPEG URLs (Instagram needs at least one); more than one makes a carousel.",
                    },
                    "alt": {"type": "array", "items": {"type": "string"}, "description": "Alt text, one per image, in order."},
                    "link": {"type": "string", "description": "Appended to the caption: Instagram captions do not make links clickable."},
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "Report what would be posted, and the day's quota, without posting.",
                    },
                },
                "required": ["text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "posted": {"type": "boolean"},
                    "post_id": {"type": ["string", "null"]},
                    "url": {"type": ["string", "null"], "description": "The post's permalink."},
                    "network": {"type": "string", "const": "instagram"},
                    "would_post": {"type": "object", "description": "A dry run only: what would be sent."},
                },
            },
            cost_hint="free",
            requires_approval=True,
        ),
        ProviderAction(
            key="comments",
            label="Read comments and replies",
            description=(
                "Marvin's shared social comments read: new comments and replies on your recent posts since a cursor, oldest first. "
                "Pass back the cursor from the last call as `since`."
            ),
            capability="social.comments",
            input_schema={
                "type": "object",
                "properties": {
                    "since": {"type": "string", "description": "The cursor this action returned last time (opaque); omit on the first call."},
                    "limit": {"type": "integer", "default": DEFAULT_LIMIT, "minimum": 1},
                    "kinds": {
                        "type": "array",
                        "items": {"type": "string", "enum": list(KINDS)},
                        "description": "comment, reply, mention. Instagram has no way to poll mentions, so that kind returns nothing.",
                    },
                },
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "id": {"type": "string"},
                                "kind": {"type": "string", "enum": [KIND_COMMENT, KIND_REPLY, KIND_MENTION]},
                                "post_id": {"type": "string"},
                                "post_url": {"type": ["string", "null"]},
                                "author": {
                                    "type": "object",
                                    "properties": {"id": {"type": "string"}, "handle": {"type": "string"}, "name": {"type": "string"}},
                                },
                                "text": {"type": "string"},
                                "created_at": {"type": "string", "description": "ISO 8601, UTC."},
                                "url": {"type": ["string", "null"]},
                                "network": {"type": "string", "const": NETWORK},
                            },
                        },
                    },
                    "cursor": {"type": "string"},
                },
            },
            cost_hint="free",
        ),
        ProviderAction(
            key="reply",
            label="Reply to a comment",
            description=(
                "Marvin's shared social reply: a private reply (a DM to the commenter, once per comment, within 7 days) by default, "
                "or a public reply under the comment."
            ),
            capability="social.reply",
            input_schema={
                "type": "object",
                "properties": {
                    "comment_id": {"type": "string"},
                    "text": {"type": "string"},
                    "private": {"type": "boolean", "default": True, "description": "true: DM the commenter; false: reply in the thread."},
                    "dry_run": {
                        "type": "boolean",
                        "default": False,
                        "description": "Check the reply and report what would be sent, sending nothing.",
                    },
                },
                "required": ["comment_id", "text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "replied": {"type": "boolean"},
                    "reply_id": {"type": ["string", "null"]},
                    "private": {"type": "boolean"},
                    "url": {"type": ["string", "null"]},
                    "network": {"type": "string", "const": NETWORK},
                    "would_reply": {"type": "object", "description": "A dry run only: what would be sent."},
                },
            },
            cost_hint="free",
            requires_approval=True,
        ),
        ProviderAction(
            key="hide",
            label="Hide or unhide a comment",
            description="Marvin's shared social hide: hide a comment under your own post (or show it again).",
            capability="social.hide",
            input_schema={
                "type": "object",
                "properties": {"comment_id": {"type": "string"}, "hidden": {"type": "boolean", "default": True}},
                "required": ["comment_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"hidden": {"type": "boolean"}, "network": {"type": "string", "const": NETWORK}},
            },
            cost_hint="free",
        ),
        ProviderAction(
            key="delete_comment",
            label="Delete a comment",
            description="Marvin's shared social delete: permanently delete a comment under your own post, or one of your own replies.",
            capability="social.delete_comment",
            input_schema={
                "type": "object",
                "properties": {"comment_id": {"type": "string"}},
                "required": ["comment_id"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {"deleted": {"type": "boolean"}, "network": {"type": "string", "const": NETWORK}},
            },
            cost_hint="free",
            requires_approval=True,
        ),
        ProviderAction(
            key="message",
            label="Send a direct message",
            description=(
                "Marvin's shared social message: an Instagram direct message to a person by their Instagram-scoped id — the "
                "answer to a DM, a story reply or a story mention in the Social inbox. Meta allows it only within 24 hours of the "
                "person's last message to the account; a handle alone can't be resolved, so it is refused."
            ),
            capability="social.message",
            input_schema={
                "type": "object",
                "properties": {
                    "to": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "The person's Instagram-scoped id (a DM's sender id)."},
                            "handle": {"type": "string", "description": "Not enough on its own: Instagram can't resolve a handle to an id."},
                        },
                        "additionalProperties": False,
                    },
                    "text": {"type": "string"},
                    "dry_run": {"type": "boolean", "default": False, "description": "Check the message and report it, sending nothing."},
                },
                "required": ["to", "text"],
                "additionalProperties": False,
            },
            output_schema={
                "type": "object",
                "properties": {
                    "sent": {"type": "boolean"},
                    "message_id": {"type": ["string", "null"]},
                    "network": {"type": "string", "const": NETWORK},
                    "would_send": {"type": "object", "description": "A dry run only: what would be sent."},
                },
            },
            cost_hint="free",
            requires_approval=True,
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

    @staticmethod
    def _json_or_raise(resp) -> dict:
        if not resp.ok:
            raise ValueError(f"Instagram API error: HTTP {resp.status_code}: {resp.text[:200]}")
        try:
            return resp.json()
        except ValueError:  # an empty or non-JSON body on a success status
            return {}

    def _get_json(self, ctx: IntegrationContext, url: str) -> dict:
        return self._json_or_raise(ctx.http.get(url, headers=self._auth(ctx)))

    # ---- Graph calls ------------------------------------------------------------------------

    def _list_media(self, ctx: IntegrationContext, fields: str = MEDIA_FIELDS) -> list[dict]:
        url = f"{self._base(ctx)}/me/media?fields={fields}&limit={self._lookback(ctx)}"
        return self._get_json(ctx, url).get("data") or []

    @staticmethod
    def _comment_row(c: dict, media_id: str, parent_id: str | None = None) -> dict:
        # Meta withholds `username` for commenters without a role on the app; `from.id` usually survives.
        row = {
            "comment_id": str(c.get("id")),
            "media_id": str(media_id),
            "username": c.get("username") or "",
            "user_id": str((c.get("from") or {}).get("id") or ""),
            "text": c.get("text") or "",
            "timestamp": _parse_ts(c.get("timestamp")),
        }
        return {**row, "parent_id": parent_id} if parent_id else row

    def _list_comments(self, ctx: IntegrationContext, media_id: str, *, replies: bool = False) -> list[dict]:
        """A post's comments. With `replies`, the replies under each comment follow it, marked with `parent_id`
        (only the first page of replies per comment, as with the comments themselves)."""
        fields = COMMENT_FIELDS_WITH_REPLIES if replies else COMMENT_FIELDS
        url = f"{self._base(ctx)}/{media_id}/comments?fields={fields}&limit={COMMENTS_PAGE_SIZE}"
        rows: list[dict] = []
        for c in self._get_json(ctx, url).get("data") or []:
            rows.append(self._comment_row(c, media_id))
            if replies:
                rows.extend(self._comment_row(r, media_id, str(c.get("id"))) for r in (c.get("replies") or {}).get("data") or [])
        return rows

    def _recent_comments(self, ctx: IntegrationContext, *, thread: bool = False) -> list[dict]:
        """Comments on the latest posts. With `thread`, replies come too and each row carries its post's permalink."""
        comments: list[dict] = []
        for media in self._list_media(ctx, MEDIA_FIELDS_WITH_LINK if thread else MEDIA_FIELDS):
            rows = self._list_comments(ctx, str(media.get("id")), replies=thread)
            if thread:
                rows = [{**r, "post_url": media.get("permalink")} for r in rows]
            comments.extend(rows)
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
            "refresh_token": self._action_refresh_token,
            "publish_media": self._action_publish_media,
            "post": self._action_post,
            "comments": self._action_comments,
            "reply": self._action_reply,
            "hide": self._action_hide,
            "delete_comment": self._action_delete_comment,
            "message": self._action_message,
        }.get(key)
        if handler is None:
            raise NotImplementedError(f"instagram has no action '{key}'")
        self._require_secret(ctx)
        return handler(args or {}, ctx)

    # ---- actions ----------------------------------------------------------------------------

    def _action_refresh_token(self, args: dict, ctx: IntegrationContext) -> dict:
        # Unlike the Graph calls, Meta's refresh endpoint ignores the Authorization header and wants the token as
        # a query parameter ("The parameter access_token is required" otherwise).
        from urllib.parse import quote

        data = self._get_json(
            ctx, f"{self._base(ctx)}/refresh_access_token?grant_type=ig_refresh_token&access_token={quote(ctx.secret or '', safe='')}"
        )
        token = data.get("access_token")
        if not token:
            raise ValueError("Instagram did not return a refreshed access token.")
        return {"expires_in": data.get("expires_in"), "secret_update": token}

    # ---- publishing ---------------------------------------------------------------------------
    # Meta's content publishing API: a container per picture (Meta fetches the URL and processes it), a wait until
    # it reads FINISHED, then media_publish. A carousel is one container per picture plus a CAROUSEL container over
    # them. The feed takes JPEG only and 25 posts per 24 hours; the quota endpoint says how many are used.
    _sleep = staticmethod(time.sleep)  # patched in tests

    def _post_form(self, ctx: IntegrationContext, url: str, form: dict) -> dict:
        headers = {**self._auth(ctx), "Content-Type": "application/x-www-form-urlencoded"}
        return self._json_or_raise(ctx.http.post(url, data=urlencode(form).encode(), headers=headers))

    def _create_container(self, ctx: IntegrationContext, form: dict) -> str:
        data = self._post_form(ctx, f"{self._base(ctx)}/{self._cfg(ctx).get('ig_user_id')}/media", form)
        if not data.get("id"):
            raise ValueError("Instagram did not return a media container id.")
        return str(data["id"])

    def _wait_ready(self, ctx: IntegrationContext, container_id: str) -> None:
        deadline = time.monotonic() + PUBLISH_WAIT_SECONDS
        while True:
            data = self._get_json(ctx, f"{self._base(ctx)}/{container_id}?fields=status_code,status")
            code = str(data.get("status_code") or "")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise ValueError(f"Instagram could not process the picture: {data.get('status') or code}")
            if time.monotonic() >= deadline:
                raise ValueError(f"Instagram is still processing the picture after {PUBLISH_WAIT_SECONDS} s; try again in a minute.")
            self._sleep(PUBLISH_POLL_SECONDS)

    def _quota(self, ctx: IntegrationContext) -> dict | None:
        """Posts used of the 24-hour quota, or None when Meta won't say (the answer is informational either way)."""
        try:
            data = self._get_json(ctx, f"{self._base(ctx)}/{self._cfg(ctx).get('ig_user_id')}/content_publishing_limit?fields=quota_usage,config")
        except Exception as e:  # noqa: BLE001
            ctx.logger.info("instagram: quota not readable: %s", e)
            return None
        row = (data.get("data") or [{}])[0]
        used, total = row.get("quota_usage"), (row.get("config") or {}).get("quota_total")
        if used is None or total is None:
            return None
        return {"used": int(used), "limit": int(total), "remaining": max(0, int(total) - int(used))}

    def _action_publish_media(self, args: dict, ctx: IntegrationContext) -> dict:
        urls = [str(u) for u in (args.get("image_urls") or []) if u] or ([str(args["image_url"])] if args.get("image_url") else [])
        if not urls:
            raise ValueError("publish_media needs 'image_url' (one picture) or 'image_urls' (2–10 for a carousel).")
        return self._publish(ctx, urls, str(args.get("caption") or ""), None, bool(args.get("dry_run", True)))

    def _publish(self, ctx: IntegrationContext, urls: list[str], caption: str, alts: list[str] | None, dry_run: bool) -> dict:
        """One picture or a carousel to the feed: what publish_media and post share. `alts` is one alt text per picture
        (or None); it rides on each picture's container. Checks everything before the first call."""
        if problem := _refusal(urls, caption):
            raise ValueError(problem)
        if not self._cfg(ctx).get("ig_user_id"):
            raise ValueError("Missing Instagram user ID.")
        alts = alts or [""] * len(urls)
        quota = self._quota(ctx)
        plan = {"images": urls, "caption": caption, "carousel": len(urls) > 1}
        if any(alts):
            plan["alt"] = alts
        if dry_run:
            return {"dry_run": True, "published": False, "would_publish": plan, "quota": quota}
        if quota and quota["remaining"] == 0:
            raise ValueError(f"Instagram's publishing limit is used up ({quota['limit']} posts per 24 hours); try again later.")

        def picture(url: str, alt: str, **extra) -> dict:
            return {"image_url": url, **({"alt_text": alt} if alt else {}), **extra}

        if len(urls) == 1:
            container = self._create_container(ctx, {**picture(urls[0], alts[0]), "caption": caption})
        else:
            children = [self._create_container(ctx, picture(u, a, is_carousel_item="true")) for u, a in zip(urls, alts, strict=True)]
            for child in children:
                self._wait_ready(ctx, child)
            container = self._create_container(ctx, {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption})
        self._wait_ready(ctx, container)
        media = self._post_form(ctx, f"{self._base(ctx)}/{self._cfg(ctx).get('ig_user_id')}/media_publish", {"creation_id": container})
        media_id = str(media.get("id") or "")
        if not media_id:
            raise ValueError("Instagram accepted the post but returned no media id.")
        permalink = None
        try:
            permalink = self._get_json(ctx, f"{self._base(ctx)}/{media_id}?fields=permalink").get("permalink")
        except Exception as e:  # noqa: BLE001 — the post is out; a missing link is not a failure
            ctx.logger.info("instagram: permalink not readable for %s: %s", media_id, e)
        return {"published": True, "dry_run": False, "media_id": media_id, "permalink": permalink, "images": len(urls), "quota": quota}

    def _action_post(self, args: dict, ctx: IntegrationContext) -> dict:
        """The `social.post` capability, mapped onto the same publishing as publish_media: text → caption, link appended to
        it (captions don't make links clickable), images → one picture or a carousel, alt → each picture's alt text.
        Unlike publish_media the real post is the default: the approval step before it is the gate."""
        text = str(args.get("text") or "").strip()
        if not text:
            raise IntegrationError("Instagram needs text for the caption.", code="unsupported")
        images = args.get("images") or []
        if not isinstance(images, list) or not all(isinstance(u, str) for u in images):
            raise IntegrationError("'images' must be a list of public image URLs.", code="unsupported")
        urls = [u.strip() for u in images if u.strip()]
        if not urls:
            raise IntegrationError("Instagram can't post text alone: it needs at least one image.", code="unsupported")
        alt = args.get("alt") or []
        if not isinstance(alt, list) or len(alt) > len(urls):
            raise IntegrationError(f"'alt' is one text per image ({len(urls)} image(s) given).", code="unsupported")
        alts = [str(a or "").strip() for a in alt] + [""] * (len(urls) - len(alt))
        if any(len(a) > ALT_MAX for a in alts):
            raise IntegrationError(f"Alt text is limited to {ALT_MAX} characters.", code="unsupported")
        link = str(args.get("link") or "").strip()
        caption = f"{text}\n\n{link}" if link and link not in text else text
        if problem := _refusal(urls, caption):
            raise IntegrationError(problem, code="unsupported")
        out = self._publish(ctx, urls, caption, alts, bool(args.get("dry_run", False)))
        if out["published"]:
            return {
                "posted": True,
                "post_id": out["media_id"],
                "url": out["permalink"],
                "network": "instagram",
                "images": out["images"],
                "quota": out["quota"],
            }
        return {
            "posted": False,
            "dry_run": True,
            "post_id": None,
            "url": None,
            "network": "instagram",
            "would_post": out["would_publish"],
            "quota": out["quota"],
        }

    # ---- shared engagement (social.comments / social.reply / social.hide / social.delete_comment) -------------------
    # Marvin's shared auto-reply engine reads and answers through these. They sit on the same Graph calls as the
    # actions above; Instagram has no way to like a comment or repost, so it declares neither social.like nor social.repost.
    # social.message answers someone who messaged the account (within Meta's 24-hour window).

    def _action_comments(self, args: dict, ctx: IntegrationContext) -> dict:
        """The `social.comments` capability: the comment polling, plus the replies under each comment, as a cursor feed."""
        limit = int(args.get("limit") or DEFAULT_LIMIT)
        if limit < 1:
            raise ValueError("'limit' must be at least 1.")
        kinds = args.get("kinds") or [KIND_COMMENT, KIND_REPLY]
        if unknown := [k for k in kinds if k not in KINDS]:
            raise _unsupported(f"Unknown comment kind {unknown[0]!r}; use comment, reply or mention.")
        # Instagram's Graph API reports @mentions in comments only by webhook, never to a poll (verify), so there is
        # nothing to return for that kind. Asking for nothing else is an error rather than a quiet empty feed.
        wanted = set(kinds) - {KIND_MENTION}
        if not wanted:
            raise _unsupported("Instagram can't list mentions by polling; ask for comment or reply.")
        since = args.get("since") or None
        if since:
            try:
                decode_cursor(str(since))
            except ValueError as e:
                raise IntegrationError(str(e), code="invalid_cursor") from e

        items = [i for row in self._recent_comments(ctx, thread=True) if (i := to_item(row)) and i["kind"] in wanted]
        page, cursor = page_after(items, str(since) if since else None, limit)
        return {"items": page, "cursor": cursor}

    def _check_private_window(self, ctx: IntegrationContext, comment_id: str) -> None:
        """A private reply needs a comment under 7 days old that isn't the account's own: refuse before sending."""
        comment = self._get_json(ctx, f"{self._base(ctx)}/{comment_id}?fields=id,timestamp,username")
        ts = _parse_ts(comment.get("timestamp"))
        if ts and datetime.now(UTC) - ts > timedelta(days=DEFAULT_MAX_AGE_DAYS):
            raise _unsupported(f"Instagram allows a private reply only within {DEFAULT_MAX_AGE_DAYS} days of the comment; this one is older.")
        own = str(self._cfg(ctx).get("own_username") or "").lower()
        if own and str(comment.get("username") or "").lower() == own:
            raise _unsupported("That comment is from your own account; there is nobody to send a private reply to.")

    def _action_reply(self, args: dict, ctx: IntegrationContext) -> dict:
        """The `social.reply` capability: a private reply (a DM to the commenter) or, with private=false, a
        public reply under the comment. The approval step before it is the gate, so a real send is the default."""
        comment_id, text = str(args.get("comment_id") or "").strip(), str(args.get("text") or "").strip()
        if not comment_id or not text:
            raise ValueError("reply needs 'comment_id' and 'text'.")
        private, dry_run = _flag(args.get("private"), True), _flag(args.get("dry_run"), False)
        if private and not self._cfg(ctx).get("ig_user_id"):
            raise ValueError("Missing Instagram user ID.")
        if private:
            self._check_private_window(ctx, comment_id)
        result = {"replied": False, "reply_id": None, "private": private, "url": None, "network": NETWORK}
        if dry_run:
            return {**result, "dry_run": True, "would_reply": {"comment_id": comment_id, "text": text, "private": private}}
        if private:
            data = self._json_or_raise(self._send(ctx, comment_id, text))
            reply_id = data.get("message_id")
        else:
            data = self._post_form(ctx, f"{self._base(ctx)}/{comment_id}/replies", {"message": text})
            reply_id = data.get("id")
        return {**result, "replied": True, "reply_id": str(reply_id) if reply_id else None}

    def _action_message(self, args: dict, ctx: IntegrationContext) -> dict:
        """The `social.message` capability: a direct message to a person by Instagram-scoped id. Meta allows it only within 24
        hours of their last message to the account; the plugin can't see that time, so a send outside it fails at Instagram."""
        to = args.get("to") if isinstance(args.get("to"), dict) else {}
        igsid, handle = str(to.get("id") or "").strip(), str(to.get("handle") or "").strip()
        text = str(args.get("text") or "").strip()
        if not text or not (igsid or handle):
            raise ValueError("message needs 'to.id' and 'text'.")
        if not igsid:
            raise _unsupported("Instagram can message a person only by their Instagram-scoped id (to.id); a handle can't be resolved.")
        if not self._cfg(ctx).get("ig_user_id"):
            raise ValueError("Missing Instagram user ID.")
        if _flag(args.get("dry_run"), False):
            return {"sent": False, "dry_run": True, "message_id": None, "network": NETWORK, "would_send": {"to": {"id": igsid}, "text": text}}
        url = f"{self._base(ctx)}/{self._cfg(ctx).get('ig_user_id')}/messages"
        data = self._json_or_raise(ctx.http.post(url, json={"recipient": {"id": igsid}, "message": {"text": text}}, headers=self._auth(ctx)))
        return {"sent": True, "message_id": str(data["message_id"]) if data.get("message_id") else None, "network": NETWORK}

    def inbox_items_from_webhook(self, payload: dict, ctx: IntegrationContext) -> list[dict]:
        """Meta's webhook deliveries to the Instagram webhooks incoming webhook, as Social inbox items (webhooks.py)."""
        return inbox_items(payload)

    def _action_hide(self, args: dict, ctx: IntegrationContext) -> dict:
        """The `social.hide` capability: hide a comment under the account's own post, or show it again."""
        comment_id = str(args.get("comment_id") or "").strip()
        if not comment_id:
            raise ValueError("hide needs 'comment_id'.")
        hidden = _flag(args.get("hidden"), True)
        data = self._post_form(ctx, f"{self._base(ctx)}/{comment_id}?hide={str(hidden).lower()}", {})
        if data.get("success") is False:
            raise ValueError("Instagram did not change the comment's visibility.")
        return {"hidden": hidden, "network": NETWORK}

    def _action_delete_comment(self, args: dict, ctx: IntegrationContext) -> dict:
        """The `social.delete_comment` capability: delete a comment under the account's own post (or its own reply)."""
        comment_id = str(args.get("comment_id") or "").strip()
        if not comment_id:
            raise ValueError("delete_comment needs 'comment_id'.")
        data = self._json_or_raise(ctx.http.delete(f"{self._base(ctx)}/{comment_id}", headers=self._auth(ctx)))
        if data.get("success") is False:
            raise ValueError("Instagram did not delete the comment.")
        return {"deleted": True, "network": NETWORK}
