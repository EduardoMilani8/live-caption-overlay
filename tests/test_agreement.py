from live_caption.asr.agreement import LocalAgreement, Word


def words(*items):
    """words((0.0, 0.4, " Hello"), ...) -> list[Word]"""
    return [Word(*i) for i in items]


HELLO = (0.0, 0.4, " Hello")
THERE = (0.5, 0.9, " there,")
FRIEND = (1.0, 1.4, " friend.")


def test_first_hypothesis_is_only_tentative():
    la = LocalAgreement()
    assert la.update(words(HELLO, THERE)) == []
    assert [w.text for w in la.tentative] == [" Hello", " there,"]


def test_agreed_prefix_is_committed_and_tail_stays_tentative():
    la = LocalAgreement()
    la.update(words(HELLO, THERE, (1.0, 1.4, " fiend")))
    committed = la.update(words(HELLO, THERE, FRIEND))
    assert [w.text for w in committed] == [" Hello", " there,"]
    assert [w.text for w in la.tentative] == [" friend."]


def test_agreement_ignores_case_and_punctuation():
    la = LocalAgreement()
    la.update(words((0.0, 0.4, " hello")))
    assert [w.text for w in la.update(words(HELLO))] == [" Hello"]


def test_committed_words_are_never_emitted_twice():
    la = LocalAgreement()
    la.update(words(HELLO, THERE))
    la.update(words(HELLO, THERE))  # commits both
    la.update(words(HELLO, THERE, FRIEND))
    assert [w.text for w in la.update(words(HELLO, THERE, FRIEND))] == [" friend."]
    assert [w.key for w in la.committed] == ["hello", "there", "friend"]


def test_repeat_with_shifted_timestamp_is_deduplicated():
    la = LocalAgreement()
    la.update(words(HELLO, THERE))
    la.update(words(HELLO, THERE))
    # After a buffer trim Whisper re-emits "there," starting after the frontier
    shifted = words((0.95, 1.0, " there"), FRIEND)
    la.update(shifted)
    assert [w.text for w in la.update(shifted)] == [" friend."]


def test_flush_commits_the_tentative_tail():
    la = LocalAgreement()
    la.update(words(HELLO))
    assert [w.text for w in la.flush()] == [" Hello"]
    assert la.tentative == []
