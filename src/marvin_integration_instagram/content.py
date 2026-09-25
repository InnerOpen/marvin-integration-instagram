"""The workspace content this integration needs, declared for the core to offer.

`auto_reply` reads its rules from entries and writes a log entry per reply sent, and the scheduled
task is what calls it — so those types and that task are not optional extras, they are how the
integration works. A provider cannot create them (it never touches the database), so it declares
them here and the workspace applies them after review.

The names are this integration's own, which is why hardcoding them is fair: an Instagram reply log
is Instagram's business. Nothing here touches the workspace's own content model.
"""

from marvin_integration_sdk import ContentBlueprint

RULES_TYPE = "ig-auto-reply"
LOG_TYPE = "ig-reply-log"
AUTO_REPLY_TASK = "instagram-auto-reply"
TOKEN_REFRESH_TASK = "instagram-token-refresh"

POLL_INTERVAL_SECONDS = 120
TOKEN_REFRESH_INTERVAL_SECONDS = 30 * 24 * 3600  # long-lived tokens last 60 days

SOCIAL_COLOR = "#C13584"
RULES_ICON = "💬"
SENT_ICON = "📤"

# Internal content: never rendered, submitted or routed — the CMS is just the editor for it.
_INTERNAL = {"publishable": False, "submittable": False, "routable": False}

AUTO_REPLY_TASK_CONFIG = {
    "integration": "instagram",
    "action": "auto_reply",
    "args": {"dry_run": True, "max_age_days": 7},
    "inputs": {
        "rules": {"entry_type": RULES_TYPE, "status": "published", "as": "records"},
        "skip_comment_ids": {"entry_type": LOG_TYPE, "as": "field", "field": "comment_id"},
    },
    "outputs": {
        "records_entry_type": LOG_TYPE,
        "slug_prefix": "ig-reply-",
        "slug_field": "comment_id",
        "title_template": "{keyword} → {commenter}",
        "status": "published",
    },
}

CONTENT = (
    ContentBlueprint(
        kind="entry_type",
        slug=RULES_TYPE,
        name="IG Auto-Reply Rule",
        description="A keyword and the DM it should trigger. Only published rules fire; drafts are inert.",
        payload={
            "name": "IG Auto-Reply Rule",
            "icon": RULES_ICON,
            "color": SOCIAL_COLOR,
            "description": "Keyword → private-reply rule for Instagram comments. Only published rules apply.",
            "schema_json": {
                "fields": [
                    {"key": "post_id", "label": "Post ID", "type": "text", "required": False, "placeholder": "* for all posts"},
                    {"key": "keywords", "label": "Keywords (comma-separated)", "type": "text", "required": True},
                    {"key": "reply", "label": "Reply DM", "type": "textarea", "required": True},
                ]
            },
            "capabilities_json": _INTERNAL,
        },
    ),
    ContentBlueprint(
        kind="entry_type",
        slug=LOG_TYPE,
        name="IG Reply Log",
        description="One entry per DM sent. Its comment_id is what stops a comment being answered twice.",
        payload={
            "name": "IG Reply Log",
            "icon": SENT_ICON,
            "color": SOCIAL_COLOR,
            "description": "One entry per private reply sent. Its comment_id is the dedupe.",
            "schema_json": {
                "fields": [
                    {"key": "comment_id", "label": "Comment ID", "type": "text", "required": True, "readOnly": True},
                    {"key": "media_id", "label": "Post ID", "type": "text", "readOnly": True},
                    {"key": "commenter", "label": "Commenter", "type": "text", "readOnly": True},
                    {"key": "username", "label": "Username (if Meta shares it)", "type": "text", "readOnly": True},
                    {"key": "user_id", "label": "Instagram user ID", "type": "text", "readOnly": True},
                    {"key": "keyword", "label": "Matched keyword", "type": "text", "readOnly": True},
                    {"key": "reply", "label": "Reply sent", "type": "textarea", "readOnly": True},
                    {"key": "text", "label": "Comment text", "type": "textarea", "readOnly": True},
                    {"key": "sent_at", "label": "Sent at", "type": "text", "readOnly": True},
                ]
            },
            "capabilities_json": _INTERNAL,
        },
    ),
    ContentBlueprint(
        kind="collection",
        slug="social-auto-responses",
        name="Social — Auto-Responses",
        description="Your canned replies in one place. Named Social so other channels can join it later.",
        requires=(f"entry_type:{RULES_TYPE}",),
        payload={
            "name": "Social — Auto-Responses",
            "description": "Canned replies keyed by keyword. Draft rules are inert; published ones fire.",
            "icon": RULES_ICON,
            "color": SOCIAL_COLOR,
            "sort_order": 50,
            "is_smart": True,
            "is_public": False,
            "smart_rules": {"entry_types": [RULES_TYPE], "match": "all"},
        },
    ),
    ContentBlueprint(
        kind="collection",
        slug="social-sent",
        name="Social — Sent",
        description="Every automated reply actually sent, newest first.",
        requires=(f"entry_type:{LOG_TYPE}",),
        payload={
            "name": "Social — Sent",
            "description": "Every automated reply actually sent. One entry per comment answered.",
            "icon": SENT_ICON,
            "color": SOCIAL_COLOR,
            "sort_order": 51,
            "is_smart": True,
            "is_public": False,
            "smart_rules": {"entry_types": [LOG_TYPE], "match": "all"},
        },
    ),
    ContentBlueprint(
        kind="scheduled_task",
        slug=AUTO_REPLY_TASK,
        name="Instagram auto-reply",
        description="Polls comments every 2 minutes and sends matching replies. Starts disabled, in dry-run.",
        requires=(f"entry_type:{RULES_TYPE}", f"entry_type:{LOG_TYPE}"),
        payload={
            "name": "Instagram auto-reply",
            "description": "Match recent comments against published rules and DM the reply. Starts disabled, in dry-run.",
            "enabled": False,
            "schedule_type": "interval",
            "schedule_config": {"interval_seconds": POLL_INTERVAL_SECONDS},
            "task_type": "run_integration_action",
            "task_config": AUTO_REPLY_TASK_CONFIG,
        },
    ),
    ContentBlueprint(
        kind="scheduled_task",
        slug=TOKEN_REFRESH_TASK,
        name="Instagram token refresh",
        description="Extends the 60-day token every 30 days. Starts disabled; enable it once the card reads ok.",
        payload={
            "name": "Instagram token refresh",
            "description": "Extend the long-lived Instagram token (60-day expiry).",
            "enabled": False,
            "schedule_type": "interval",
            "schedule_config": {"interval_seconds": TOKEN_REFRESH_INTERVAL_SECONDS},
            "task_type": "run_integration_action",
            "task_config": {"integration": "instagram", "action": "refresh_token"},
        },
    ),
)
