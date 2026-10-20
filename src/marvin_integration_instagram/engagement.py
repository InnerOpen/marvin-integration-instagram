"""Pure helpers for Marvin's shared social-engagement shape: normalised comment items and the poll cursor.

No SDK import and no HTTP here. The provider does the Graph calls and hands rows in.
"""

from __future__ import annotations

import base64
import binascii
import json
from datetime import UTC, datetime

NETWORK = "instagram"
KIND_COMMENT, KIND_REPLY, KIND_MENTION = "comment", "reply", "mention"
KINDS = (KIND_COMMENT, KIND_REPLY, KIND_MENTION)
DEFAULT_LIMIT = 50


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


def comment_url(post_url: str | None, comment_id: str, parent_id: str | None) -> str | None:
    """Where a comment lives on instagram.com, or None without the post's permalink.
    The `/c/<id>/` (and `/c/<parent>/r/<id>/`) shape is the web app's own; Meta does not document it (verify)."""
    if not post_url:
        return None
    base = post_url.rstrip("/")
    return f"{base}/c/{parent_id}/r/{comment_id}/" if parent_id else f"{base}/c/{comment_id}/"


def to_item(row: dict) -> dict | None:
    """One comment row (see ``InstagramProvider._list_comments``) as a ``social.comments`` item; None when it has no
    usable timestamp, since the cursor and the oldest→newest order both hang on it."""
    ts = row.get("timestamp")
    if ts is None:
        return None
    handle = row.get("username") or ""
    parent_id = row.get("parent_id")
    return {
        "id": row["comment_id"],
        "kind": KIND_REPLY if parent_id else KIND_COMMENT,
        "post_id": row["media_id"],
        "post_url": row.get("post_url"),
        # Instagram gives no display name for a commenter, so the handle stands in for it; `id` and `handle` may be
        # empty when Meta withholds them from commenters without a role on the app.
        "author": {"id": row.get("user_id") or "", "handle": handle, "name": handle},
        "text": row.get("text") or "",
        "created_at": _iso(ts),
        "url": comment_url(row.get("post_url"), row["comment_id"], parent_id),
        "network": NETWORK,
    }


def encode_cursor(newest: str | None, ids_at_newest: list[str]) -> str:
    """The cursor is the newest created_at handed out plus the ids sharing that exact second, so a comment that
    lands in the same second as the last one returned is neither repeated nor lost. Opaque to callers."""
    raw = json.dumps({"t": newest, "ids": sorted(ids_at_newest)}, separators=(",", ":"))
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str | None, set[str]]:
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        newest, ids = data["t"], data["ids"]
        if newest is not None:
            datetime.fromisoformat(newest)
        return newest, {str(i) for i in ids}
    except (binascii.Error, ValueError, KeyError, TypeError, UnicodeDecodeError) as e:
        raise ValueError("The 'since' cursor is not one this plugin returned.") from e


def page_after(items: list[dict], since: str | None, limit: int) -> tuple[list[dict], str]:
    """The items newer than the cursor, oldest→newest, at most `limit`, and the cursor to ask with next time.

    With a cursor the *oldest* `limit` new items are returned, so a backlog drains in order without gaps. Without one
    (the first poll) the *newest* `limit` are, since older history is not what a poller starting now wants.
    """
    items = sorted(items, key=lambda i: (i["created_at"], i["id"]))
    seen_at, seen_ids = decode_cursor(since) if since else (None, set())
    if since and seen_at:
        items = [i for i in items if i["created_at"] > seen_at or (i["created_at"] == seen_at and i["id"] not in seen_ids)]
    page = items[:limit] if since else items[-limit:]
    if not page:
        return [], since or encode_cursor(None, [])
    newest = page[-1]["created_at"]
    ids = [i["id"] for i in page if i["created_at"] == newest]
    if newest == seen_at:
        ids += list(seen_ids)
    return page, encode_cursor(newest, ids)
