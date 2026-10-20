# Instagram

Posts to Instagram, and reads, answers, hides and deletes the comments on your posts. It talks to the Instagram Login
flavour of the Graph API (`graph.instagram.com`). It posts pictures to the feed (and carousels), and it takes part in
Marvin's shared **social engagement**: read comments and replies, reply privately or publicly, hide and delete comments
(see the actions below). It cannot like a comment, repost, or message someone who has not commented.

**Keyword rules and auto-reply are Marvin's Social.** Write the rules under **Social › Instagram › Rules** and switch the
auto-reply (off, rehearsal, on) on that page: Marvin's engine reads comments through this plugin's `comments` action and
answers through `reply`. This plugin has no rules, reply log or auto-reply task of its own since 0.8.0 (version 0.7 had
them, with the `list_recent_comments`, `send_private_reply` and `auto_reply` actions; those are gone too).

## Connect it

1. In the Meta App Dashboard (developers.facebook.com), create a **Business** app with the **Instagram** product
   (Instagram Login), add your Instagram account as a **tester**, and generate a **long-lived user token**
   (valid 60 days). Note the account's numeric **Instagram user ID**.
2. In Marvin: **Settings → Integrations → Instagram → Configure**. Paste the token as the **Access token** and set
   **Instagram user ID**. Save: the card should turn `ok` (Marvin checks the token by reading `/me`).
3. Optional settings: **Own username** (comments by this account are never answered), **Posts to scan** (how many of
   your latest posts to read comments from; default 10), **Graph API version** (default `v22.0`).
4. The integration declares one piece of content: the **Instagram token refresh** scheduled task. Apply it from the
   integration card; it arrives **disabled**. Enable it once the card reads `ok`: it runs every 30 days and Marvin
   stores the new token (the token expires after 60 days if it is never refreshed).

**Reach.** Under Meta *Standard Access* a private reply only reaches accounts that hold a role on your Meta app
(admins, developers, testers). Replying to the public needs *Advanced Access* (App Review and Business
Verification). Test with a tester account first.

**Token permissions** (Instagram Login scopes; verify the exact names in the Meta App Dashboard): reading comments needs
`instagram_business_basic` and `instagram_business_manage_comments`; a public reply, hide and delete need
`instagram_business_manage_comments`; a private reply (a DM) also needs `instagram_business_manage_messages`;
posting needs `instagram_business_content_publish`. A token generated before an action was added may lack its scope:
generate a new one with all of them.

## Set up auto-reply

In Marvin, not here: open **Social › Instagram**, write and publish a rule under **Rules** (keywords, the reply, private
or public), then set the network's auto-reply to **rehearsal** (it records what it would send) and, when that looks
right, **on**. The Social page of Marvin's manual has the matching rules and the limits. Under Meta Standard Access a private reply
only reaches accounts that hold a role on your Meta app (see **Reach**), and one DM per comment is all Meta allows,
within 7 days of the comment.

**Moving from version 0.7:** the old **IG Auto-Reply Rule** / **IG Reply Log** entries, the two "Social —" collections and
the **Instagram auto-reply** task were removed from Marvin workspaces by a Marvin migration, and Marvin no longer reads
them. Write the rules again under Social › Instagram › Rules.

## Actions

- **Read comments and replies** (`comments`, capability `social.comments`): `since` (the `cursor` from the last call,
  opaque; leave it out the first time), `limit` (default 50), `kinds` (any of `comment`, `reply`, `mention`).
  Returns `{"items": [...], "cursor": "..."}`, items oldest first, each `{id, kind, post_id, post_url, author:
  {id, handle, name}, text, created_at (ISO 8601, UTC), url, network: "instagram"}`. Pass the returned `cursor` back
  as `since` and only newer items come back. It reads the latest *Posts to scan* posts, at most 50 comments per post
  and the first page of replies under each comment. Instagram has no display name for a commenter, so `name` repeats
  `handle`; both and `id` may be empty when Meta withholds them. **Mentions can't be polled** on Instagram: that kind
  returns nothing, and asking for nothing else is refused (`unsupported`). With no `since` the newest `limit` items
  come back; with one, the oldest `limit` new items, so a backlog drains in order.
- **Reply to a comment** (`reply`, capability `social.reply`, needs approval): `comment_id`, `text`, `private`
  (default `true`: a DM to the commenter; `false`: a public reply under the comment), `dry_run` (default `false`).
  Returns `{replied, reply_id, private, url, network}` (`url` is null: Instagram gives a reply no link), and on a dry
  run `replied: false` with `would_reply`. A private reply keeps the rules above: once per comment, within 7 days,
  never to the account's own comment; it is checked **before** sending and refused with `unsupported` when it fails,
  also on a dry run. A refusal from Meta is raised, not returned as `replied: false`.
- **Hide or unhide a comment** (`hide`, capability `social.hide`): `comment_id`, `hidden` (default `true`). Returns
  `{hidden, network}`. No approval needed (easily undone).
- **Delete a comment** (`delete_comment`, capability `social.delete_comment`, needs approval): `comment_id`. Returns
  `{deleted, network}`. Permanent. Works on comments under your own posts and on your own replies.
- **Refresh access token** (`refresh_token`): extends the token by another 60 days. Returns `expires_in` and
  `secret_update`; only the **Instagram token refresh** scheduled task stores that new token.
- **Publish a post** (`publish_media`): `image_url` (one picture) or `image_urls` (2–10, a carousel), `caption` (up to
  2,200 characters), `dry_run` (default `true`). The feed takes **JPEG only**, fetched by Instagram from a public
  https URL — an entry's main picture is `${entry.image}`, any asset's URL works, and a `media` step's `convert`
  (`format: jpeg`) makes one from a PNG. A dry run returns `would_publish` (the pictures and caption) and `quota`
  (`used`, `limit`, `remaining` of the 25 posts per 24 hours) and posts nothing. A real run returns `published`,
  `media_id`, `permalink` (null if Meta withholds it) and `images`; a picture Meta can't process, or a used-up quota,
  fails the step before anything is posted. Needs the `instagram_business_content_publish` permission on the token.
- **Post to Instagram** (`post`): Marvin's shared **social post** (capability `social.post`), the same publishing as
  *Publish a post* under the common arguments: `text` (required; the caption), `images` (1–10 public https JPEG URLs;
  more than one is a carousel), `alt` (one alt text per image, optional), `link` (appended to the caption on its own
  line, since Instagram captions don't make links clickable), `dry_run` (default `false`: the approval step before it
  is the gate). Instagram can't post text alone: no image is refused with a clear error, as are an over-long caption
  (text and link together, 2,200 characters) and a non-https URL. Returns `posted`, `post_id`, `url` (the permalink),
  `network` (`instagram`), `images` and `quota`; a dry run returns `posted: false` and `would_post` instead, and posts
  nothing. A workflow step that names the capability `social.post` instead of an action posts through every connected
  network, so it needs the pictures resolved to JPEG URLs first (a `media` `convert` step).

## In workflows

- **Publish a post**, **Post to Instagram**, **Reply to a comment** and
  **Delete a comment** require approval: a workflow may run them only right
  after an **Approval** step that was approved (the step refuses otherwise). The usual post: trigger
  `entry_published` → Approval "Post “${entry.title}” to Instagram?" → a `media` step `convert` to `jpeg` with
  `source: ${entry.image}` (the feed takes JPEG only; the copy is kept as a derived asset) → Run integration
  `publish_media` with `image_url: ${steps.<media id>.output.asset.url}`, `caption: ${entry.summary}`,
  `dry_run: false` → an entry step that sets `instagram_permalink` from `${steps.<id>.output.permalink}`. The
  plugin ships this as the **Post to Instagram when published** recipe. Keyword replies are Social's auto-reply: don't
  build a workflow that polls comments to reply to the same keywords, or a comment could be answered twice.
- **Read comments and replies** gives a workflow new comments without repeats: keep the `cursor` from one run and pass
  it as `since` in the next (Marvin's Social engine does exactly this). **Hide a comment** needs no approval, so a workflow
  can hide spam after an AI classification; **Reply to a comment** and **Delete a comment** come after an Approval step.
- Don't call **Refresh access token** from a workflow: the new token would not be stored. Use the scheduled task.
- The integration emits no events and receives no webhooks. Marvin finds new comments only by polling.

## What it can't do

- Like a comment (the API has none), repost, or send a DM that is not a reply to a comment.
- List @mentions (webhook-only on Instagram).
- Post a video, a reel or a story, or a PNG/WebP (convert to JPEG first); schedule a post for later (use a workflow
  wait).
- Send a second DM to the same comment, or reply to a comment older than 7 days.
- Send DMs to people who commented but are not allowed under your app's access level (see **Reach**).
- React instantly: new comments are picked up on Marvin's next poll (every 5 minutes).
- Read more than the latest *Posts to scan* posts, or more than 50 comments of one post.

## When it fails

This integration does not declare error codes or an error policy, so a failed action carries the code `unknown` and
nothing is retried automatically. The messages:

- **"No Instagram access token configured."**: the credential is empty. Paste the token on the integration card.
- **"Instagram API error: HTTP <n>: …"**: Meta refused a read (comments, media, refresh); the start of Meta's
  response follows. If it is about the token, generate a new long-lived token and paste it on the integration card.
- **`unsupported`** (a refusal before anything is sent): a private reply to a comment older than 7 days or to your own
  comment, only `mention` asked of **Read comments and replies**, or an unknown comment kind.
- **`invalid_cursor`**: `since` is not a cursor this plugin returned; start again without it.
- **"Instagram did not return a refreshed access token."**: the refresh call succeeded but carried no token.
- **A private `reply` Meta refuses** is raised with Meta's message (for example a comment that already has a reply, one past
  the 7-day window, or an account without a role on the app under Standard Access).
- The card shows **unconfigured** when the token or the Instagram user ID is missing, and **error** with the HTTP
  status when `/me` is refused or unreachable.
