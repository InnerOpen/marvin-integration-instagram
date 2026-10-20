# marvin-integration-instagram

A [Marvin](https://github.com/InnerOpen/marvin) integration that polls the comments on your recent
Instagram posts and sends **private replies** (DMs) when a comment matches a keyword rule.
Category: *destination*. It also implements Marvin's shared **social engagement** capabilities (read comments, reply
privately or publicly, hide, delete), which the shared auto-reply engine in Marvin core uses on every network.

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
| **Action** | `comments`, `reply`, `hide`, `delete_comment` — the shared **`social.comments`**, **`social.reply`**, **`social.hide`**, **`social.delete_comment`** capabilities, see [Shared engagement](#shared-engagement-capabilities) |
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

## Shared engagement capabilities

Marvin core's shared auto-reply engine reads comments and replies through these capabilities, on every connected
network. Each sits on one new action and reuses the plugin's existing Graph calls; the existing actions
(`list_recent_comments`, `send_private_reply`, `auto_reply`, `publish_media`, `post`, `refresh_token`) and the
blueprints are unchanged.

| Capability | Action | Gated | Arguments → output | Graph call |
|---|---|---|---|---|
| `social.comments` | `comments` | no | `{ since?, limit?: 50, kinds?: ["comment","reply","mention"] }` → `{ items, cursor }` | `GET /me/media?fields=id,timestamp,permalink`, then `GET /{media-id}/comments?fields=…,replies{…}` per post |
| `social.reply` | `reply` | **yes** | `{ comment_id, text, private: true, dry_run: false }` → `{ replied, reply_id, private, url, network }` (+ `would_reply` on a dry run) | private: `GET /{comment-id}?fields=id,timestamp,username` (window check), then `POST /{ig-user-id}/messages` with `recipient.comment_id`; public: `POST /{comment-id}/replies` with `message` |
| `social.hide` | `hide` | no | `{ comment_id, hidden: true }` → `{ hidden, network }` | `POST /{comment-id}?hide=true\|false` |
| `social.delete_comment` | `delete_comment` | **yes** | `{ comment_id }` → `{ deleted, network }` | `DELETE /{comment-id}` |

Not declared, because Instagram's API has no such call: `social.like` (no way to like a comment), `social.repost`, and
`social.message` (a DM is possible only as a reply to a comment, which is `social.reply` with `private: true`).

**`social.comments`.** An item is `{ id, kind, post_id, post_url, author: { id, handle, name }, text, created_at (ISO
UTC), url, network: "instagram" }`, oldest to newest. `kind` is `comment` for a top-level comment and `reply` for a
reply under one (the first page of replies per comment). `name` repeats `handle`: Instagram gives a commenter no
display name; `id` and `handle` can be empty when Meta withholds them from commenters without a role on the app.
The `cursor` is opaque (it encodes the newest `created_at` returned plus the ids sharing that second); pass it back as
`since` and only newer items come back. With no `since` the newest `limit` items return; with one, the oldest `limit`
new items, so a backlog drains in order and nothing is skipped. A bad cursor raises `invalid_cursor`.
**Mentions are skipped:** the Graph API reports @mentions in comments only through webhooks, which Marvin does not
receive, so `kinds` containing `mention` is accepted and yields nothing for it, and asking for *only* mentions is
refused (`unsupported`).

**`social.reply`.** `private: true` (the default) is the existing private reply, sent by the same code as
`send_private_reply`, with the checks it implies plus the window now enforced up front: a comment older than 7 days,
or from the account's own `own_username`, is refused with `unsupported` before anything is sent (also on a dry run).
`private: false` is a public reply in the thread (no window). Unlike `send_private_reply`, a failure from Meta is
raised, never returned as `replied: false`. `url` is null: the API gives a reply no link. `dry_run` checks the same
things and returns `replied: false` and `would_reply`; the real send is the default, since the approval step before it
is the gate. Meta's own one-reply-per-comment limit is not pre-checked; Meta's rejection is raised.

Refusals the plugin knows about use `IntegrationError` with code `unsupported`.

**The plugin's own auto-reply.** Once the shared engine is switched on for Instagram in Marvin, it replaces this
plugin's **Instagram auto-reply** scheduled task, which is then turned off (not deleted). Until then the task, the
`auto_reply` action, the `ig-auto-reply` rules and the `ig-reply-log` entries keep working exactly as before; the
shared engine reads the same rules and log, so nothing is answered twice.

### Token permissions

Instagram Login scopes (verify the exact names and which combination each call needs in the Meta App Dashboard):

| Call | Scope |
|---|---|
| list posts, read the account (`check`) | `instagram_business_basic` |
| read comments and replies (`comments`, `list_recent_comments`) | `instagram_business_basic`, `instagram_business_manage_comments` |
| public reply, hide, delete comment | `instagram_business_manage_comments` |
| private reply (DM) | `instagram_business_manage_messages` (and `instagram_business_manage_comments` to read the comment) |
| `post`, `publish_media` | `instagram_business_content_publish` |

A token made for 0.6.0 may lack `instagram_business_manage_comments` or `instagram_business_manage_messages`; generate
a new one with all of the above. The Standard Access limits (accounts with a role on your app only) apply to replies.

### Verify against Meta's docs

These are written from the Graph API reference as remembered, with no live call made; check them against your
`api_version` before relying on them:

- The scope names above, and exactly which scope each call needs (especially the private reply and the `hide` call).
- `POST /{comment-id}?hide=true|false` and `DELETE /{comment-id}` on `graph.instagram.com` with an Instagram Login
  token, the `{ "success": true }` response, and that deleting works on your own replies as well as on comments under
  your posts.
- `POST /{comment-id}/replies` takes `message` as a form parameter and returns `{ "id" }`.
- The `replies{…}` field expansion on `/{media-id}/comments`, and that a reply node has the same fields (`username`,
  `from`, `timestamp`) as a comment.
- The `url` of an item is built as `<post permalink>c/<id>/` (and `c/<parent>/r/<id>/` for a reply): that is the
  instagram.com web shape, not documented by Meta. It is null when Meta gives no `permalink`.
- That mentions in comments are webhook-only (no polling edge); `GET /{ig-user-id}/tags` lists posts you are tagged
  in, which is a different thing.
- Whether Meta limits a private reply to top-level comments only (replies to a reply are not pre-checked), and the
  length limits on a comment and on a DM (not pre-checked; Meta's rejection is raised).

## What it teaches Marvin (0.7.0, SDK 0.9.0)

- **A guide** (`src/marvin_integration_instagram/guide.md`): connecting the Meta token, setting up and publishing
  rules, how keywords match, what each action returns, what workflows can and cannot run (replies need approval, so
  only the scheduled task sends them), and what each failure message means. Marvin's agents read it when building
  workflows where Instagram is connected, and Marvin's manual search finds it.
- **No new recipes**, on purpose: the shared engagement flows (read, auto-reply, moderate) belong to Marvin core's
  shared engine and its `social.*` workflow steps, and the rule/log types, collections and scheduled tasks are already
  declared as blueprints. The one recipe is the post-on-publish workflow.

## Logo

`logo.png` is the gradient Instagram glyph from Meta's [Instagram brand asset pack](https://www.meta.com/brand/resources/instagram/instagram-brand/), scaled to 192×192 and otherwise unaltered. Meta's guidelines: don't alter it, don't imply partnership or endorsement, and don't make Instagram more prominent than Marvin; keep it at least 29×29 px with clear space around it.

## Develop

```bash
uv run --extra dev pytest
```

The provider depends only on `marvin-integration-sdk` — not on Marvin core — so tests run standalone.
`matching.py` is pure Python (no SDK import) and holds all the keyword logic; `content.py` declares
the workspace content the actions depend on, which the core offers for review rather than creating.
