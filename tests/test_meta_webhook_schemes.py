"""Meta's webhook schemes, for an incoming webhook in Marvin: the URL check (WebSub's hub.* GET) and the delivery
signature (X-Hub-Signature-256), declared here rather than in Marvin's core."""

from marvin_integration_instagram.provider import InstagramProvider


def test_meta_verification_names_the_hub_parameters():
    meta = InstagramProvider.verification_schemes["meta"]
    assert (meta["challenge_param"], meta["token_param"], meta["mode_param"], meta["mode_values"]) == (
        "hub.challenge",
        "hub.verify_token",
        "hub.mode",
        ["subscribe"],
    )


def test_meta_signs_deliveries_with_sha256_in_x_hub_signature_256():
    meta = InstagramProvider.signature_schemes["meta"]
    assert (meta["header"], meta["prefix"], meta["sender_issues_key"]) == ("X-Hub-Signature-256", "sha256=", True)


def test_the_offered_webhook_uses_the_meta_schemes_and_is_a_suggestion():
    from marvin_integration_instagram.content import APP_SECRET_REF, VERIFY_TOKEN_REF

    hook = {c.slug: c for c in InstagramProvider().content}["instagram-webhooks"]
    assert hook.kind == "incoming_webhook" and not hook.required  # Instagram works without webhooks
    assert (hook.payload["verification_scheme"], hook.payload["signature_scheme"]) == ("meta", "meta")
    assert (hook.payload["verify_token_ref"], hook.payload["signing_secret_ref"]) == (VERIFY_TOKEN_REF, APP_SECRET_REF)
    assert "meta" in InstagramProvider.verification_schemes and "meta" in InstagramProvider.signature_schemes  # its own schemes
