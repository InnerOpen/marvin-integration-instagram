# Instagram

Reads the comments on your recent Instagram posts and answers matching ones with a **private reply** (a DM to the
commenter). Keyword rules are ordinary Marvin entries; a scheduled task polls every 2 minutes and sends the replies.
It talks to the Instagram Login flavour of the Graph API (`graph.instagram.com`). It cannot post, delete or moderate
anything.

## Connect it

1. In the Meta App Dashboard (developers.facebook.com), create a **Business** app with the **Instagram** product
   (Instagram Login), add your Instagram account as a **tester**, and generate a **long-lived user token**
   (valid 60 days). Note the account's numeric **Instagram user ID**.
2. In Marvin: **Settings → Integrations → Instagram → Configure**. Paste the token as the **Access token** and set
   **Instagram user ID**. Save: the card should turn `ok` (Marvin checks the token by reading `/me`).
3. Optional settings: **Own username** (comments by this account are never answered), **Posts to scan** (how many of
   your latest posts to read comments from; default 10), **Graph API version** (default `v22.0`).
4. The integration declares the content it needs: two entry types (**IG Auto-Reply Rule**, **IG Reply Log**), two
   smart collections (**Social — Auto-Responses**, **Social — Sent**) and two scheduled tasks (**Instagram
   auto-reply**, **Instagram token refresh**). Apply them from the integration card. Both tasks arrive **disabled**,
   and the auto-reply task arrives in **dry-run**.

**Reach.** Under Meta *Standard Access* a private reply only reaches accounts that hold a role on your Meta app
(admins, developers, testers). Replying to the public needs *Advanced Access* (App Review and Business
Verification). Test with a tester account first.

## Set up auto-reply

1. Create an **IG Auto-Reply Rule** entry: **Keywords** (comma-separated), **Reply DM**, and optionally **Post ID**
   (a media id; leave it empty or `*` for every post). **Publish** it: draft rules do nothing.
2. Enable the **Instagram auto-reply** task. In dry-run it sends nothing and logs nothing: its run history reports what
   matched. When that looks right, set the task's `args.dry_run` to `false`.
3. Every real send creates an **IG Reply Log** entry (slug `ig-reply-<comment id>`). The log is the memory: the task
   passes the logged comment ids back as `skip_comment_ids`, so a comment is never answered twice. Deleting a log entry
   makes its comment eligible again (until it is older than 7 days).
4. Enable **Instagram token refresh** once the card reads `ok`. It runs every 30 days and Marvin stores the new
   token. The token expires after 60 days if it is never refreshed.

### How rules match

- A keyword matches as a **whole word**, case-insensitively; multi-word keywords work (`price list`). `size` matches
  "What SIZE is this?" but not "sizes".
- **One reply per comment** (Meta allows one). If a comment matches several rules: a rule for that specific post beats
  a `*` rule, and otherwise the rule written first wins.
- A rule without keywords or without a reply is ignored.
- Skipped comments are reported with a reason: `already_replied` (in the log), `too_old` (older than `max_age_days`,
  default 7, the window Meta allows), `own_comment` (the **Own username** setting), `no_match`.
- Meta withholds `username` for commenters who have no role on the app. The log's title then falls back to the user
  id, then the comment id.

## Actions

- **List recent comments** (`list_recent_comments`): `limit` (optional). Returns `{"comments": [...]}`, each with
  `comment_id`, `media_id`, `username` (may be empty), `user_id`, `text` and `timestamp` (ISO 8601, or null). It reads
  the latest *Posts to scan* posts, at most 50 comments per post (no further pages), and `limit` trims the combined
  list afterwards.
- **Send private reply** (`send_private_reply`): `comment_id`, `text`. DMs the author of one comment, once per
  comment, within 7 days of it. Returns `{"sent": bool, "status": <HTTP status>, "response": <Meta's reply, first 500
  characters>}`. A refusal from Meta comes back as `sent: false`, it is not raised.
- **Auto-reply to keyword comments** (`auto_reply`): `rules` (required: a list of `{post_id, keywords, reply}`),
  `dry_run` (default `true`), `skip_comment_ids`, `max_age_days` (default 7). Returns `checked`, `matched`, `sent`,
  `dry_run`, `skipped` and, for real sends, `records`; a dry run returns `would_send` and no `records`. In practice
  the scheduled task supplies `rules` from the published rule entries and `skip_comment_ids` from the log. A send
  that Meta refuses is listed in `skipped` as `send_failed: HTTP <n>` and does not stop the run. It is not logged,
  so it is tried again on the next run until the comment is too old.
- **Refresh access token** (`refresh_token`): extends the token by another 60 days. Returns `expires_in` and
  `secret_update`; only the **Instagram token refresh** scheduled task stores that new token.

## In workflows

- **Send private reply** and **Auto-reply** require approval, so a workflow's **Run integration** step refuses them
  ("requires approval and cannot run from a workflow"), and they are not offered to workflow builders. Replies go
  out only through the scheduled task, which an admin creates and enables. Don't build a workflow that tries to
  reply to comments.
- A workflow can call **List recent comments**, for example to read comments on a schedule. The result is
  `$steps.<id>.output.comments`. Every run re-reads the same recent comments (nothing marks one as seen), so
  de-duplicate on `comment_id` if the workflow acts on them.
- Don't call **Refresh access token** from a workflow: the new token would not be stored. Use the scheduled task.
- The integration emits no events and receives no webhooks. Marvin finds new comments only by polling.

## What it can't do

- Reply publicly to a comment, like, hide or delete comments, or post media.
- Send a second DM to the same comment, or reply to a comment older than 7 days.
- Send DMs to people who commented but are not allowed under your app's access level (see **Reach**).
- React instantly: new comments are picked up on the next poll (every 2 minutes).
- Read more than the latest *Posts to scan* posts, or more than 50 comments of one post.

## When it fails

This integration does not declare error codes or an error policy, so a failed action carries the code `unknown` and
nothing is retried automatically. The messages:

- **"No Instagram access token configured."**: the credential is empty. Paste the token on the integration card.
- **"Instagram API error: HTTP <n>: …"**: Meta refused a read (comments, media, refresh); the start of Meta's
  response follows. If it is about the token, generate a new long-lived token and paste it on the integration card.
- **"Instagram did not return a refreshed access token."**: the refresh call succeeded but carried no token.
- **`send_failed: HTTP <n>`** in `skipped`, or `sent: false`: Meta refused one DM (for example a comment that already
  has a reply, one past the 7-day window, or an account without a role on the app under Standard Access).
- The card shows **unconfigured** when the token or the Instagram user ID is missing, and **error** with the HTTP
  status when `/me` is refused or unreachable.
