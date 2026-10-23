"""Meta's Instagram webhook deliveries as Social inbox items. The payload shapes are the ones Meta sent on 2026-10-23
(ids invented): a DM, a shared post, and the dashboard's sample comment."""

from marvin_integration_instagram.provider import InstagramProvider
from marvin_integration_instagram.webhooks import inbox_items

ACCOUNT, PERSON = "17841400000000001", "1400000000000002"


def _messaging(*events):
    return {"object": "instagram", "entry": [{"time": 1792776187033, "id": ACCOUNT, "messaging": list(events)}]}


def _event(message=None, **extra):
    return {"sender": {"id": PERSON}, "recipient": {"id": ACCOUNT}, "timestamp": 1792776186475, **({"message": message} if message else {}), **extra}


def test_a_text_dm_is_a_message_from_its_sender():
    (item,) = inbox_items(_messaging(_event({"mid": "mid.1", "text": "Thos is a message"})))

    assert (item["id"], item["kind"], item["text"], item["author"]["id"]) == ("mid.1", "message", "Thos is a message", PERSON)
    assert item["created_at"].startswith("2026-10-23T") and item["network"] == "instagram"


def test_a_shared_post_is_a_message_whose_text_is_its_caption_with_the_picture_attached():
    shared = {
        "type": "ig_post",
        "payload": {"ig_post_media_id": "18050000000000003", "title": "Designers, tailors…", "url": "https://lookaside.example/p"},
    }

    (item,) = inbox_items(_messaging(_event({"mid": "mid.2", "attachments": [shared]})))

    assert (item["kind"], item["text"]) == ("message", "Designers, tailors…")
    assert item["attachments"] == [{"type": "ig_post", "url": "https://lookaside.example/p", "title": "Designers, tailors…"}]


def test_story_replies_and_story_mentions_are_their_own_kinds():
    reply = _event({"mid": "mid.3", "text": "So good", "reply_to": {"story": {"id": "s1", "url": "https://lookaside.example/s"}}})
    mention = _event({"mid": "mid.4", "attachments": [{"type": "story_mention", "payload": {"url": "https://lookaside.example/m"}}]})

    kinds = [i["kind"] for i in inbox_items(_messaging(reply, mention))]

    assert kinds == ["story_reply", "story_mention"]


def test_a_reaction_is_kept_and_an_unreact_adds_nothing():
    react = _event(reaction={"mid": "mid.1", "action": "react", "reaction": "love", "emoji": "❤️"})
    unreact = _event(reaction={"mid": "mid.1", "action": "unreact"})

    (item,) = inbox_items(_messaging(react, unreact))

    assert (item["kind"], item["text"], item["id"]) == ("reaction", "❤️", f"mid.1:reaction:{PERSON}")


def test_the_accounts_own_messages_read_receipts_and_unsends_are_not_items():
    echo = _event({"mid": "mid.5", "text": "our reply", "is_echo": True})
    deleted = _event({"mid": "mid.6", "is_deleted": True})
    seen = _event(read={"mid": "mid.1"})

    assert inbox_items(_messaging(echo, deleted, seen)) == []


def test_a_comment_change_is_a_comment_with_the_id_the_poll_gives_it():
    sample = {
        "entry": [{"id": "0", "time": 1792775851, "changes": [{"field": "comments", "value": {
            "from": {"id": "232323232", "username": "test"}, "media": {"id": "123123123", "media_product_type": "FEED"},
            "id": "17865799348089039", "parent_id": "1231231234", "text": "This is an example."}}]}],
        "object": "instagram",
    }  # fmt: skip

    (item,) = inbox_items(sample)

    assert (item["id"], item["kind"], item["text"], item["post_id"]) == ("17865799348089039", "reply", "This is an example.", "123123123")
    assert item["author"] == {"id": "232323232", "handle": "test", "name": "test"}


def test_live_comments_and_mentions():
    payload = {"object": "instagram", "entry": [{"id": ACCOUNT, "time": 1792775851, "changes": [
        {"field": "live_comments", "value": {"id": "c-live", "text": "hi", "from": {"id": "9", "username": "v"}, "media": {"id": "live1"}}},
        {"field": "mentions", "value": {"media_id": "m1", "comment_id": "c-men"}},
    ]}]}  # fmt: skip

    assert [(i["kind"], i["id"]) for i in inbox_items(payload)] == [("live_comment", "c-live"), ("mention", "c-men")]


def test_anything_that_isnt_an_instagram_delivery_gives_nothing():
    assert inbox_items({"object": "page", "entry": [{"messaging": [_event({"mid": "x", "text": "y"})]}]}) == []
    assert inbox_items({}) == [] and inbox_items([]) == []


def test_the_provider_hands_marvin_the_items():
    assert InstagramProvider().inbox_items_from_webhook(_messaging(_event({"mid": "mid.7", "text": "Hi"})), None)[0]["id"] == "mid.7"
