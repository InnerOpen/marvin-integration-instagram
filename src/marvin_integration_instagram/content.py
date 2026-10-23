"""The workspace content this integration offers, declared for the core.

The token-refresh task: Instagram's long-lived token lasts 60 days, and this extends it every 30. And (0.10.0) an incoming
webhook ready for Meta's webhooks — DMs, comments, mentions — set to the `meta` verification and signature schemes this
provider declares, so Meta can check the URL and Marvin can verify each delivery.

A provider cannot create content itself (it never touches the database), so it declares it here and the workspace applies
it after review. Both start disabled: enable the task once the integration card reads ok, the webhook once it is set up.

Until 0.8.0 this also declared the `ig-auto-reply` / `ig-reply-log` entry types, two "Social —" collections and an auto-reply
scheduled task. Marvin's Social now holds the rules and answers comments for every network, so none of that ships any more.
"""

from marvin_integration_sdk import ContentBlueprint

TOKEN_REFRESH_TASK = "instagram-token-refresh"

TOKEN_REFRESH_INTERVAL_SECONDS = 30 * 24 * 3600  # long-lived tokens last 60 days

WEBHOOK_SLUG = "instagram-webhooks"
APP_SECRET_REF = "INSTAGRAM_APP_SECRET"  # the Meta app's secret (App settings → Basic): it signs every delivery
VERIFY_TOKEN_REF = "INSTAGRAM_WEBHOOK_VERIFY_TOKEN"  # the phrase typed into Meta's webhook settings

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
    ContentBlueprint(
        kind="incoming_webhook",
        slug=WEBHOOK_SLUG,
        name="Instagram webhooks",
        description=(
            "Where Meta sends Instagram's webhooks (a DM, a comment, a mention) for workflows to act on. After applying: mint its "
            f"token; under Verification, Generate phrase (stored as {VERIFY_TOKEN_REF}); under Signing, store the Meta app's "
            f"secret as {APP_SECRET_REF}. Then paste the URL and the phrase into the app's Configure webhooks, subscribe to the "
            "fields you want, and publish the app."
        ),
        payload={
            "name": "Instagram webhooks",
            "description": "Meta's Instagram webhooks: checked with Meta's verification request, deliveries signed (X-Hub-Signature-256).",
            "verification_scheme": "meta",
            "verify_token_ref": VERIFY_TOKEN_REF,
            "signature_scheme": "meta",
            "signing_secret_ref": APP_SECRET_REF,
        },
    ),
)
