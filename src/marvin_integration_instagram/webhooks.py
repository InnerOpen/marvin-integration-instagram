"""Meta's Instagram webhook deliveries as Marvin Social inbox items (``inbox_items_from_webhook``, SDK 0.18.0).

Pure: no SDK import, no HTTP. A delivery is ``{"object": "instagram", "entry": [...]}``; each entry carries ``messaging``
events (direct messages, story replies and mentions, reactions) or ``changes`` (comments, live comments, mentions). Each
becomes an item in Marvin's shared shape — the same ``id`` a ``social.comments`` read gives a comment, so the inbox keeps it
once whichever way it arrives. What isn't an inbox item (the account's own messages, read receipts, postbacks, story
insights, an unsend or an un-react) gives nothing.
"""

from __future__ import annotations

from datetime import UTC, datetime

from .engagement import KIND_COMMENT, KIND_MENTION, KIND_REPLY, NETWORK, comment_url

KIND_MESSAGE, KIND_STORY_REPLY, KIND_STORY_MENTION, KIND_REACTION, KIND_LIVE_COMMENT = (
    "message",
    "story_reply",
    "story_mention",
    "reaction",
    "live_comment",
)
SHARED_MEDIA = ("ig_post", "ig_reel", "reel", "share")  # a post or reel someone sent: its caption rides as the title


def _iso(value) -> str | None:
    """Meta's times: messaging in milliseconds, changes in seconds (and an entry's own `time` in either)."""
    if not isinstance(value, int | float):
        return None
    seconds = value / 1000 if value > 10**11 else value
    return datetime.fromtimestamp(seconds, UTC).isoformat()


def _attachments(message: dict) -> list[dict]:
    out = []
    for a in message.get("attachments") or []:
        if not isinstance(a, dict):
            continue
        payload = a.get("payload") if isinstance(a.get("payload"), dict) else {}
        out.append({k: v for k, v in (("type", a.get("type")), ("url", payload.get("url")), ("title", payload.get("title"))) if v})
    return [a for a in out if a]


def _message_item(event: dict, when) -> dict | None:
    message = event.get("message") if isinstance(event.get("message"), dict) else {}
    if not message.get("mid") or message.get("is_echo") or message.get("is_deleted"):
        return None
    attachments = _attachments(message)
    types = {a.get("type") for a in attachments}
    reply_to = message.get("reply_to") if isinstance(message.get("reply_to"), dict) else {}
    if "story_mention" in types:
        kind = KIND_STORY_MENTION
    elif isinstance(reply_to.get("story"), dict):
        kind = KIND_STORY_REPLY
        story = reply_to["story"]
        attachments.append({k: v for k, v in (("type", "story"), ("url", story.get("url"))) if v})
    else:
        kind = KIND_MESSAGE
    shared = next((a for a in attachments if a.get("type") in SHARED_MEDIA and a.get("title")), None)
    text = message.get("text") or (shared or {}).get("title") or ""
    return {
        "id": message["mid"],
        "kind": kind,
        "author": {"id": str((event.get("sender") or {}).get("id") or ""), "handle": "", "name": ""},
        "text": text,
        "created_at": _iso(event.get("timestamp")) or when,
        "attachments": attachments,
        "network": NETWORK,
    }


def _reaction_item(event: dict, when) -> dict | None:
    reaction = event.get("reaction") if isinstance(event.get("reaction"), dict) else {}
    if not reaction.get("mid") or reaction.get("action") != "react":
        return None  # an un-react takes nothing back from the inbox; it just adds nothing
    sender = str((event.get("sender") or {}).get("id") or "")
    return {
        "id": f"{reaction['mid']}:reaction:{sender}",
        "kind": KIND_REACTION,
        "author": {"id": sender, "handle": "", "name": ""},
        "text": reaction.get("emoji") or reaction.get("reaction") or "",
        "created_at": _iso(event.get("timestamp")) or when,
        "network": NETWORK,
    }


def _change_item(change: dict, when) -> dict | None:
    field, value = change.get("field"), change.get("value") if isinstance(change.get("value"), dict) else {}
    if field in ("comments", "live_comments") and value.get("id"):
        author = value.get("from") if isinstance(value.get("from"), dict) else {}
        media = value.get("media") if isinstance(value.get("media"), dict) else {}
        handle = author.get("username") or ""
        parent = value.get("parent_id")
        kind = KIND_LIVE_COMMENT if field == "live_comments" else (KIND_REPLY if parent else KIND_COMMENT)
        return {
            "id": str(value["id"]),
            "kind": kind,
            "post_id": str(media["id"]) if media.get("id") else None,
            "author": {"id": str(author.get("id") or ""), "handle": handle, "name": handle},
            "text": value.get("text") or "",
            "created_at": when,
            "url": comment_url(None, str(value["id"]), parent),
            "network": NETWORK,
        }
    if field == "mentions" and (value.get("comment_id") or value.get("media_id")):
        # Meta sends only the ids; the text needs a Graph call this hook mustn't make, so the inbox shows the mention bare.
        return {
            "id": str(value.get("comment_id") or value.get("media_id")),
            "kind": KIND_MENTION,
            "post_id": str(value["media_id"]) if value.get("media_id") else None,
            "author": {"id": "", "handle": "", "name": ""},
            "text": "",
            "created_at": when,
            "network": NETWORK,
        }
    return None


def inbox_items(payload: dict) -> list[dict]:
    """Every inbox item in one delivery, in order."""
    if not isinstance(payload, dict) or payload.get("object") != "instagram":
        return []
    items: list[dict] = []
    for entry in payload.get("entry") or []:
        if not isinstance(entry, dict):
            continue
        when = _iso(entry.get("time")) or datetime.now(UTC).isoformat()
        for event in entry.get("messaging") or []:
            if isinstance(event, dict):
                item = _reaction_item(event, when) if "reaction" in event else _message_item(event, when) if "message" in event else None
                if item:
                    items.append(item)
        for change in entry.get("changes") or []:
            if isinstance(change, dict) and (item := _change_item(change, when)):
                items.append(item)
    return items
