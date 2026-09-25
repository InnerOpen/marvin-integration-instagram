"""Pure keyword-rule matching — no SDK, no HTTP, no Marvin.

A *comment* is ``{comment_id, media_id, username, text, timestamp: datetime}``; a *rule* is
``{post_id: "*" | media id, keywords: [str], reply: str}``. Rules arrive from Marvin entries, so
``keywords`` may also be a comma-separated string — ``normalize_rule`` accepts both.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

ALL_POSTS = "*"

REASON_ALREADY_REPLIED = "already_replied"
REASON_TOO_OLD = "too_old"
REASON_OWN_COMMENT = "own_comment"
REASON_NO_MATCH = "no_match"


def normalize_rule(raw: dict) -> dict | None:
    """Lower/strip a raw rule into ``{post_id, keywords, reply}``; None if it has no usable keywords or reply."""
    if not isinstance(raw, dict):
        return None
    keywords = raw.get("keywords") or []
    if isinstance(keywords, str):
        keywords = keywords.split(",")
    cleaned = [str(k).strip().lower() for k in keywords if str(k).strip()]
    reply = str(raw.get("reply") or "").strip()
    if not cleaned or not reply:
        return None
    post_id = str(raw.get("post_id") or ALL_POSTS).strip() or ALL_POSTS
    return {"post_id": post_id, "keywords": cleaned, "reply": reply}


def keyword_hit(text: str, keywords: list[str]) -> str | None:
    """First keyword found as a whole word (case-insensitive) in ``text``; multi-word keywords work."""
    for kw in keywords:
        if re.search(rf"(?<!\w){re.escape(kw)}(?!\w)", text or "", re.IGNORECASE):
            return kw
    return None


def _rules_for(media_id: str, rules: list[dict]) -> list[dict]:
    """Rules to try for one post, in the order they should be tried.

    Post-specific rules beat the "*" catch-all; within each group the caller's order is kept. Meta
    allows exactly one private reply per comment, so when a comment matches several rules only the
    first can fire — which makes the caller's ordering load-bearing. Marvin hands them over
    oldest-first, so the rule you wrote first wins.
    """
    specific = [r for r in rules if r["post_id"] == media_id]
    catch_all = [r for r in rules if r["post_id"] == ALL_POSTS]
    return specific + catch_all


def match_rules(
    comments: list[dict],
    rules: list[dict],
    *,
    now: datetime,
    max_age_days: int = 7,
    skip_ids: frozenset[str] | set[str] = frozenset(),
    skip_usernames: frozenset[str] | set[str] = frozenset(),
) -> tuple[list[dict], list[dict]]:
    """Decide which comments get which reply. One reply per comment, first hit wins.

    Returns ``(matches, skipped)``: a match is the comment plus ``keyword`` and ``reply``; a skip is
    ``{comment_id, reason}`` with one of the ``REASON_*`` values.

    One reply per comment, always: Instagram permits a single private reply per comment, so a
    comment matching several rules takes the first (post-specific before ``*``, then caller order)
    and the rest are not considered.
    """
    normalized = [r for r in (normalize_rule(r) for r in rules) if r]
    cutoff = now - timedelta(days=max_age_days)
    skip_users = {u.lower() for u in skip_usernames if u}
    matches: list[dict] = []
    skipped: list[dict] = []

    for c in comments:
        cid = str(c.get("comment_id"))
        if cid in skip_ids:
            skipped.append({"comment_id": cid, "reason": REASON_ALREADY_REPLIED})
            continue
        ts = c.get("timestamp")
        if ts is not None and ts < cutoff:
            skipped.append({"comment_id": cid, "reason": REASON_TOO_OLD})
            continue
        if str(c.get("username") or "").lower() in skip_users:
            skipped.append({"comment_id": cid, "reason": REASON_OWN_COMMENT})
            continue

        hit = None
        for rule in _rules_for(str(c.get("media_id")), normalized):
            kw = keyword_hit(c.get("text") or "", rule["keywords"])
            if kw:
                hit = {**c, "keyword": kw, "reply": rule["reply"]}
                break
        if hit:
            matches.append(hit)
        else:
            skipped.append({"comment_id": cid, "reason": REASON_NO_MATCH})

    return matches, skipped
