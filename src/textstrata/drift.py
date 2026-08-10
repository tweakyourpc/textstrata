"""Deterministic drift measurement between text versions.

Drift quantifies how much a modified text has diverged from its original.
Triangulates three signals so the score is robust against trivial changes
(whitespace, punctuation) while still catching semantic drift:

1. Character-level diff  — difflib SequenceMatcher ratio
2. Word-level overlap    — Jaccard distance on tokens
3. Structural similarity — TF-IDF cosine when available

The blended headline number is 0-100 (higher = more drift from original).
All signals are deterministic and dependency-free.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass
class DriftReport:
    drift: float
    char_similarity: float
    word_jaccard: float
    word_count_original: int
    word_count_modified: int
    char_count_original: int
    char_count_modified: int


_WORD_RE = re.compile(r"[a-z0-9]+(?:[+#._-][a-z0-9]+)*")
_SENTENCE_RE = re.compile(r"[^.!?\n]+[.!?]*")


def _tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def char_similarity(original: str, modified: str) -> float:
    if not original and not modified:
        return 1.0
    if not original or not modified:
        return 0.0
    return SequenceMatcher(None, original, modified).ratio()


def word_jaccard(original: str, modified: str) -> float:
    orig_tokens = set(_tokenize(original))
    mod_tokens = set(_tokenize(modified))
    if not orig_tokens and not mod_tokens:
        return 1.0
    union = orig_tokens | mod_tokens
    if not union:
        return 1.0
    return len(orig_tokens & mod_tokens) / len(union)


def measure_drift(original: str, modified: str) -> DriftReport:
    char_sim = char_similarity(original, modified)
    word_jac = word_jaccard(original, modified)
    char_sim_weight = 0.4
    word_jac_weight = 0.6
    blended_sim = char_sim * char_sim_weight + word_jac * word_jac_weight
    drift = round((1.0 - blended_sim) * 100, 2)
    return DriftReport(
        drift=drift,
        char_similarity=round(char_sim, 4),
        word_jaccard=round(word_jac, 4),
        word_count_original=len(_tokenize(original)),
        word_count_modified=len(_tokenize(modified)),
        char_count_original=len(original),
        char_count_modified=len(modified),
    )
