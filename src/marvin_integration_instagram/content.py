"""The workspace content this integration offers, declared for the core.

Just the token-refresh task: Instagram's long-lived token lasts 60 days, and this extends it every 30. A provider cannot create
content itself (it never touches the database), so it declares it here and the workspace applies it after review. It starts
disabled; enable it once the integration card reads ok.

Until 0.8.0 this also declared the `ig-auto-reply` / `ig-reply-log` entry types, two "Social —" collections and an auto-reply
scheduled task. Marvin's Social now holds the rules and answers comments for every network, so none of that ships any more.
"""

from marvin_integration_sdk import ContentBlueprint

TOKEN_REFRESH_TASK = "instagram-token-refresh"

TOKEN_REFRESH_INTERVAL_SECONDS = 30 * 24 * 3600  # long-lived tokens last 60 days

CONTENT = (
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
