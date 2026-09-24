# marvin-integration-instagram

A [Marvin](https://github.com/InnerOpen/marvin) integration that polls the comments on your recent
Instagram posts and sends **private replies** (DMs) when a comment matches a keyword rule.
Category: *destination*.

The rules are ordinary Marvin content: an `ig-auto-reply` entry holds a post id (or `*`), a list of
keywords and the reply text, and only `published` rules apply. Every real send is recorded as an
`ig-reply-log` entry, which is also what keeps a comment from being answered twice.

## Install

On a Marvin host:

```bash
uv pip install marvin-integration-instagram   # or add to your Marvin image
# restart Marvin → "Instagram" appears in Settings → Integrations
```

No changes to Marvin core or its frontend. Marvin discovers the provider through the
`marvin.integrations` entry point this package declares.

## Configure

1. In the [Meta App Dashboard](https://developers.facebook.com/), create a Business app with the
   **Instagram** product (Instagram Login), add your account as a tester, and generate a
   **long-lived user token** (60 days). Note the numeric **Instagram user id** of that account.
2. In Marvin: **Settings → Integrations → Instagram → Configure**, paste the token and fill in the
   user id. Save — the card should turn `ok`.
3. Seed the rule/log entry types and the scheduled task (`scripts/seed_instagram_auto_reply.py
   --workspace <slug>` in the Marvin repo), publish a rule, and run the task. It starts **disabled
   and in dry-run**; flip `args.dry_run` to `false` once the log shows the right `would_send`.

> Under Meta **Standard Access** a private reply only reaches accounts that hold a role on your app
> (admins/testers). Replying to the public needs Advanced Access (App Review + Business
> Verification). Replies are one per comment, within 7 days of the comment.

## Credential, config & actions

| | |
|---|---|
| **Credential** | `access_token` — long-lived Instagram Login user token |
| **Config** | `ig_user_id` (required), `api_version` (`v22.0`), `lookback_media` (`10` recent posts), `own_username` (never answered) |
| **Action** | `list_recent_comments` — `{ "limit"?: n }` → `{ "comments": [...] }` |
| **Action** | `send_private_reply` — `{ "comment_id", "text" }` → `{ "sent", "status", "response" }` |
| **Action** | `auto_reply` — `{ "rules", "dry_run": true, "skip_comment_ids"?, "max_age_days": 7 }` → `{ "checked", "matched", "sent", "dry_run", "would_send"? , "records", "skipped" }` |
| **Action** | `refresh_token` — `{}` → `{ "expires_in", "secret_update" }` (the core stores the new token) |

`auto_reply` returns `records` only for replies it actually sent; a dry run returns `would_send`
instead so nothing gets logged. A record is
`{ comment_id, media_id, username, user_id, commenter, keyword, reply, text, sent_at }` — Meta withholds
`username` for commenters with no role on the app, so `commenter` falls back to the user id and then the
comment id, giving the log entry a usable title either way. A failed send lands in `skipped` as `send_failed: HTTP n` — it never
aborts the run.

## Develop

```bash
uv run --extra dev pytest
```

The provider depends only on `marvin-integration-sdk` — not on Marvin core — so tests run standalone.
`matching.py` is pure Python (no SDK import) and holds all the keyword logic.
