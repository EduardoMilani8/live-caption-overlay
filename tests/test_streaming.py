import numpy as np

from live_caption.asr.agreement import Word
from live_caption.asr.streaming import StreamingTranscriber
from live_caption.audio.capture import SAMPLE_RATE


def speech(n_words, word_s=0.4, gap_s=0.1, sentence_every=6):
    """Ground truth: evenly spaced words, a sentence end every few words."""
    out, t = [], 0.0
    for i in range(n_words):
        text = f" w{i}" + ("." if (i + 1) % sentence_every == 0 else "")
        out.append(Word(t, t + word_s, text))
        t += word_s + gap_s
    return out


class FakeEngine:
    """Returns the ground-truth words fully inside the buffer.

    Test audio stores each sample's absolute index, so the engine can tell
    which stretch of the stream it was given. With `wobble`, the last word
    is misheard on every other pass, like Whisper's unstable tail.
    """

    def __init__(self, truth, wobble=False):
        self.truth, self.wobble, self.calls, self.max_seen_s = truth, wobble, 0, 0.0

    def transcribe(self, audio, prompt):
        self.calls += 1
        self.max_seen_s = max(self.max_seen_s, audio.size / SAMPLE_RATE)
        if audio.size == 0:
            return []
        start = audio[0] / SAMPLE_RATE
        end = start + audio.size / SAMPLE_RATE
        seen = [Word(w.start - start, w.end - start, w.text) for w in self.truth if w.start >= start - 1e-6 and w.end <= end]
        if self.wobble and seen and self.calls % 2:
            last = seen[-1]
            seen[-1] = Word(last.start, last.end, last.text + "x")
        return seen


def stream(engine, total_s, step_s=1.0, max_buffer_s=5.0, finish=True):
    st = StreamingTranscriber(engine, max_buffer_s=max_buffer_s)
    committed, step = [], int(step_s * SAMPLE_RATE)
    for i in range(0, int(total_s * SAMPLE_RATE), step):
        st.add_audio(np.arange(i, i + step, dtype=np.float32))
        committed += st.process().committed
    if finish:
        committed += st.finish().committed
    return committed, st


def test_every_word_is_committed_exactly_once_in_order():
    truth = speech(40)  # 20 s of "speech"
    committed, _ = stream(FakeEngine(truth), total_s=21)
    assert [w.text for w in committed] == [w.text for w in truth]


def test_unstable_tail_never_leaks_into_committed_text():
    truth = speech(40)
    # finish() flushes the last tentative words as-is (best effort at stream
    # end), so check only what the agreement itself committed.
    committed, _ = stream(FakeEngine(truth, wobble=True), total_s=21, finish=False)
    assert not any(w.text.endswith("x") for w in committed)
    assert [w.key for w in committed] == [w.key for w in truth[: len(committed)]]
    assert len(committed) >= len(truth) - 2  # only the very tail is still pending


def test_buffer_stays_bounded():
    engine = FakeEngine(speech(80))
    stream(engine, total_s=41, max_buffer_s=5.0)
    assert engine.max_seen_s <= 5.0 + 1.0  # at most one step over the cap


def test_silence_does_not_grow_the_buffer():
    engine = FakeEngine([])
    _, st = stream(engine, total_s=30)
    assert st.buffer_s <= 1.0 + 1.0
