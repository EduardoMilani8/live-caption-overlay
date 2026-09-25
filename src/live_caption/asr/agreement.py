"""LocalAgreement-2: decide which transcribed words are stable enough to show.

Every pass re-transcribes the whole audio buffer, so the tail of the
hypothesis keeps changing ("I scream" -> "ice cream"). A word is committed
only once two consecutive hypotheses agree on it; everything after the
agreed prefix stays tentative. Policy from Macháček et al., "Turning
Whisper into Real-Time Transcription System" (2023).
"""

from __future__ import annotations

import string
from dataclasses import dataclass

# Word timestamps jitter between passes; a hypothesis word starting this much
# before the committed frontier is still treated as new.
FRONTIER_TOLERANCE_S = 0.1
# Only words starting this close to the frontier are checked against the
# committed tail for repeats.
OVERLAP_WINDOW_S = 1.0
MAX_OVERLAP_WORDS = 5

_PUNCT = str.maketrans("", "", string.punctuation + "¿¡…“”‘’")


@dataclass(frozen=True)
class Word:
    start: float  # seconds since the stream started
    end: float
    text: str  # as Whisper emits it, usually with a leading space

    @property
    def key(self) -> str:
        """Comparison form: Whisper flips case/punctuation between passes."""
        return self.text.translate(_PUNCT).strip().casefold()


class LocalAgreement:
    def __init__(self) -> None:
        self.committed: list[Word] = []
        self._tentative: list[Word] = []

    @property
    def frontier(self) -> float:
        return self.committed[-1].end if self.committed else 0.0

    @property
    def tentative(self) -> list[Word]:
        return list(self._tentative)

    def update(self, hypothesis: list[Word]) -> list[Word]:
        """Feed a new full-buffer hypothesis; return the newly committed words."""
        new = self._strip_already_committed(hypothesis)

        agreed = 0
        for old, fresh in zip(self._tentative, new):
            if old.key != fresh.key:
                break
            agreed += 1

        commit = new[:agreed]
        self.committed.extend(commit)
        self._tentative = new[agreed:]
        return commit

    def flush(self) -> list[Word]:
        """Commit whatever is tentative (speech ended / stream closed)."""
        commit, self._tentative = self._tentative, []
        self.committed.extend(commit)
        return commit

    def _strip_already_committed(self, hypothesis: list[Word]) -> list[Word]:
        frontier = self.frontier
        new = [w for w in hypothesis if w.start > frontier - FRONTIER_TOLERANCE_S]
        if not new or not self.committed or abs(new[0].start - frontier) > OVERLAP_WINDOW_S:
            return new
        # Timestamps alone miss repeats when Whisper shifts a word across the
        # frontier, so also drop the longest n-gram that duplicates the
        # committed tail ("... the end" + "end of" -> "of").
        max_n = min(MAX_OVERLAP_WORDS, len(new), len(self.committed))
        for n in range(max_n, 0, -1):
            tail = [w.key for w in self.committed[-n:]]
            if [w.key for w in new[:n]] == tail:
                return new[n:]
        return new
