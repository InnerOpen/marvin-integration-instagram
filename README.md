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
3. The integration **declares the content it needs** — two entry types, two collections and two
   scheduled tasks. Marvin offers them for review after install; apply them, then write a rule and
   publish it. The tasks arrive **disabled and in dry-run**: flip `args.dry_run` to `false` once the
   execution log shows the right `would_send`, then enable.

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
| **Action** | `post` — the shared **`social.post`** capability, see below |
| **Action** | `publish_media` — `{ "image_url" | "image_urls": [2–10], "caption"?, "dry_run": true }` → dry run `{ "would_publish", "quota" }`; real `{ "published", "media_id", "permalink", "images", "quota" }` (JPEG from a public https URL; approval-gated) |

### `post` — the shared `social.post` capability

Marvin's one "post this" shape, so a workflow step can post to every connected network. It is the same publishing as
`publish_media` (which keeps its own arguments), approval-gated, and mapped like this:

| `social.post` arg | Instagram |
|---|---|
| `text` (required) | the caption (up to 2,200 characters, text and link together) |
| `images` (0–10 public https URLs) | `image_url` for one picture, a carousel for 2–10. **At least one is required**: Instagram can't post text alone, so none is refused (`IntegrationError`, code `unsupported`). JPEG only |
| `alt` (one per image) | `alt_text` on each image's container (up to 1,000 characters) |
| `link` | appended to the caption after a blank line (Instagram captions don't make links clickable) unless the text already contains it |
| `dry_run` | reports `would_post` and the day's `quota`, calls nothing but the quota read. Default **false** (the approval step before the post is the gate; `publish_media` stays dry-run by default) |

Output: `{ "posted", "post_id", "url", "network": "instagram", "images", "quota" }`, and on a dry run
`{ "posted": false, "dry_run": true, "post_id": null, "url": null, "network", "would_post": { "images", "caption", "carousel", "alt"? }, "quota" }`.
The 25-posts-per-24-hours quota is checked as in `publish_media`.

**Verify:** `alt_text` on `POST /{ig-user-id}/media` is documented for images (single and carousel items), not for the
carousel container; check it against your `api_version` in Meta's content-publishing reference. If Meta rejects the parameter
on your version, drop `alt` from the step.

`auto_reply` returns `records` only for replies it actually sent; a dry run returns `would_send`
instead so nothing gets logged. A record is
`{ comment_id, media_id, username, user_id, commenter, keyword, reply, text, sent_at }` — Meta withholds
`username` for commenters with no role on the app, so `commenter` falls back to the user id and then the
comment id, giving the log entry a usable title either way. A failed send lands in `skipped` as `send_failed: HTTP n` — it never
aborts the run.

## What it teaches Marvin (0.6.0, SDK 0.9.0)

- **A guide** (`src/marvin_integration_instagram/guide.md`): connecting the Meta token, setting up and publishing
  rules, how keywords match, what each action returns, what workflows can and cannot run (replies need approval, so
  only the scheduled task sends them), and what each failure message means. Marvin's agents read it when building
  workflows where Instagram is connected, and Marvin's manual search finds it.
- **No recipes**, on purpose: the reply actions cannot run from a workflow, the only workflow-callable read
  (`list_recent_comments`) has no way to mark a comment as seen, and the rule/log types, collections and scheduled
  tasks are already declared as blueprints.

## Logo

`logo.png` is the gradient Instagram glyph from Meta's [Instagram brand asset pack](https://www.meta.com/brand/resources/instagram/instagram-brand/), scaled to 192×192 and otherwise unaltered. Meta's guidelines: don't alter it, don't imply partnership or endorsement, and don't make Instagram more prominent than Marvin; keep it at least 29×29 px with clear space around it.

## Develop

```bash
uv run --extra dev pytest
```

The provider depends only on `marvin-integration-sdk` — not on Marvin core — so tests run standalone.
`matching.py` is pure Python (no SDK import) and holds all the keyword logic; `content.py` declares
the workspace content the actions depend on, which the core offers for review rather than creating.
