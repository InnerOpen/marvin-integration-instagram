"""Tests for the pure keyword-rule matcher."""

from datetime import UTC, datetime, timedelta

from marvin_integration_instagram.matching import keyword_hit, match_rules, normalize_rule

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


def _comment(cid="c1", text="what size is this?", media_id="m1", username="fan", age_days=0):
    return {"comment_id": cid, "media_id": media_id, "username": username, "text": text, "timestamp": NOW - timedelta(days=age_days)}


def test_keyword_hit_matches_whole_words_only():
    assert keyword_hit("ship it please", ["ship"]) == "ship"
    assert keyword_hit("what about shipping?", ["ship"]) is None


def test_keyword_hit_is_case_insensitive():
    assert keyword_hit("SIZE?", ["size"]) == "size"


def test_keyword_hit_supports_multi_word_keywords():
    assert keyword_hit("do you have a size chart", ["size chart"]) == "size chart"


def test_normalize_rule_accepts_comma_separated_keywords():
    rule = normalize_rule({"post_id": "", "keywords": " Size, SIZING ,,link", "reply": "Hi"})
    assert rule == {"post_id": "*", "keywords": ["size", "sizing", "link"], "reply": "Hi"}


def test_normalize_rule_drops_rules_without_keywords_or_reply():
    assert normalize_rule({"keywords": "", "reply": "x"}) is None
    assert normalize_rule({"keywords": ["a"], "reply": "  "}) is None


def test_match_rules_prefers_post_specific_rule_over_catch_all():
    rules = [
        {"post_id": "*", "keywords": ["size"], "reply": "generic"},
        {"post_id": "m1", "keywords": ["size"], "reply": "specific"},
    ]
    matches, _ = match_rules([_comment()], rules, now=NOW)
    assert [m["reply"] for m in matches] == ["specific"]


def test_match_rules_falls_back_to_catch_all_for_other_posts():
    rules = [{"post_id": "m1", "keywords": ["size"], "reply": "specific"}, {"post_id": "*", "keywords": ["size"], "reply": "generic"}]
    matches, _ = match_rules([_comment(media_id="m9")], rules, now=NOW)
    assert [m["reply"] for m in matches] == ["generic"]


def test_match_rules_skips_comments_older_than_max_age():
    _, skipped = match_rules([_comment(age_days=8)], [{"keywords": ["size"], "reply": "r"}], now=NOW, max_age_days=7)
    assert skipped == [{"comment_id": "c1", "reason": "too_old"}]


def test_match_rules_skips_already_replied_ids():
    _, skipped = match_rules([_comment()], [{"keywords": ["size"], "reply": "r"}], now=NOW, skip_ids={"c1"})
    assert skipped == [{"comment_id": "c1", "reason": "already_replied"}]


def test_match_rules_skips_own_username_case_insensitively():
    _, skipped = match_rules([_comment(username="MashAndBurn")], [{"keywords": ["size"], "reply": "r"}], now=NOW, skip_usernames={"mashandburn"})
    assert skipped == [{"comment_id": "c1", "reason": "own_comment"}]


def test_match_rules_sends_one_reply_per_comment_first_hit_wins():
    rules = [{"keywords": ["size"], "reply": "first"}, {"keywords": ["size", "this"], "reply": "second"}]
    matches, skipped = match_rules([_comment()], rules, now=NOW)
    assert len(matches) == 1
    assert matches[0]["reply"] == "first"
    assert matches[0]["keyword"] == "size"
    assert skipped == []


def test_match_rules_reports_no_match():
    _, skipped = match_rules([_comment(text="lovely")], [{"keywords": ["size"], "reply": "r"}], now=NOW)
    assert skipped == [{"comment_id": "c1", "reason": "no_match"}]


def test_overlapping_rules_send_exactly_one_reply_the_first_in_order():
    """Instagram allows one private reply per comment, so rule order decides which fires. Marvin
    hands rules over oldest-first, making this "the rule you wrote first wins"."""
    comment = _comment(text="what size, and do you have a link?")
    rules = [
        {"keywords": ["size"], "reply": "sizing answer"},
        {"keywords": ["link"], "reply": "link answer"},
    ]
    matches, skipped = match_rules([comment], rules, now=NOW)
    assert [m["reply"] for m in matches] == ["sizing answer"]
    assert skipped == []

    # reverse the order and the other one wins — the behaviour is ordering, not luck
    matches, _ = match_rules([comment], list(reversed(rules)), now=NOW)
    assert [m["reply"] for m in matches] == ["link answer"]


def test_a_post_specific_rule_beats_an_earlier_catch_all():
    comment = _comment(text="what size?", media_id="m1")
    rules = [
        {"post_id": "*", "keywords": ["size"], "reply": "generic"},
        {"post_id": "m1", "keywords": ["size"], "reply": "this post only"},
    ]
    matches, _ = match_rules([comment], rules, now=NOW)
    assert [m["reply"] for m in matches] == ["this post only"]
