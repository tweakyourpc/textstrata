"""Deterministic vocabulary normalization for similarity scoring.

Two notes can describe the same concept in different words — "k8s" vs
"kubernetes", "idempotency" vs "idempotent", "configuring" vs "configuration".
Raw token overlap misses all of these, so the knowledge graph never draws an
edge between them. This module closes that gap **without embeddings, models,
or network calls**, keeping the whole pipeline reproducible: identical corpus
plus identical synonym map yields an identical graph, every run.

Three layers, cheapest first:

1. **Stemming** — a compact, self-contained Porter stemmer folds morphological
   variants ("configure/configuring/configured" -> "configur"). This mirrors
   the ``porter`` tokenizer already used by the FTS5 catalog, so graph scores
   and full-text search finally agree on what a word is.

2. **Synonyms** — a small, hand-curated ``term -> canonical`` map folds domain
   equivalents (abbreviations, jargon) that stemming can't reach. It is a
   plain dict literal: no data file to ship, no dependency to install. A
   per-store override file (``.fabric/synonyms.json``) extends or
   overrides it for a specific corpus.

3. **Inference** — :func:`infer_synonyms` proposes *new* mappings from the
   corpus itself using tag co-occurrence plus string closeness, so the map
   grows with the knowledge base. Proposals are suggestions only; they flow
   through the existing human review queue before taking effect.

The public entry point is :func:`canonical_tokens`, a drop-in replacement for
the old raw tokenizer in ``similarity.py``.
"""

from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
# Tokenization primitives (shared shape with similarity.tokenize)
# --------------------------------------------------------------------------- #

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#._-]*")
_CAMEL_ACRONYM_RE = re.compile(r"([A-Z]+)([A-Z][a-z])")
_CAMEL_BOUNDARY_RE = re.compile(r"([a-z0-9])([A-Z])")

_STOPWORDS = frozenset(
    """a an and are as at be but by for from has have if in into is it its of on
    or that the their then there these this to was were will with without you
    your we our can may should must not no also than then over under""".split()
)


# --------------------------------------------------------------------------- #
# Curated synonym map: term -> canonical form
# --------------------------------------------------------------------------- #
#
# Kept deliberately small and domain-focused (software / distributed systems /
# personal knowledge management — the corpora this tool actually serves). Each
# entry maps a variant to a *canonical* token. Canonical tokens are chosen to
# be the fuller, less ambiguous form; they are themselves stemmed downstream,
# so "kubernetes" and a future "kubernetes-native" still converge.
#
# Guidance for editing:
#   * LHS is always the variant, RHS the canonical form.
#   * Prefer widening recall for clearly-equivalent jargon; avoid folding terms
#     that are merely *related* unless the domain makes them interchangeable.
#   * Multi-word canonicals use hyphens so they survive tokenization as one
#     token.

_BASE_SYNONYMS: dict[str, str] = {
    # --- infrastructure / ops ------------------------------------------- #
    "k8s": "kubernetes",
    "kube": "kubernetes",
    "db": "database",
    "dbs": "database",
    "infra": "infrastructure",
    "config": "configuration",
    "configs": "configuration",
    "cfg": "configuration",
    "repo": "repository",
    "repos": "repository",
    "ci": "continuous-integration",
    "cd": "continuous-deployment",
    "vm": "virtual-machine",
    "vms": "virtual-machine",
    "lb": "load-balancer",
    # --- security / auth ------------------------------------------------- #
    "auth": "authentication",
    "authn": "authentication",
    "authz": "authorization",
    "creds": "credentials",
    "cred": "credentials",
    "pii": "personal-information",
    "csp": "content-security-policy",
    "ssrf": "server-side-request-forgery",
    "xss": "cross-site-scripting",
    # --- software engineering ------------------------------------------- #
    "async": "asynchronous",
    "sync": "synchronous",
    "fn": "function",
    "func": "function",
    "funcs": "function",
    "param": "parameter",
    "params": "parameter",
    "arg": "argument",
    "args": "argument",
    "dep": "dependency",
    "deps": "dependency",
    "lib": "library",
    "libs": "library",
    "pkg": "package",
    "pkgs": "package",
    "doc": "documentation",
    "docs": "documentation",
    "impl": "implementation",
    "spec": "specification",
    "specs": "specification",
    "regex": "regular-expression",
    "perf": "performance",
    # --- data / messaging ------------------------------------------------ #
    "tx": "transaction",
    "txn": "transaction",
    "txns": "transaction",
    "msg": "message",
    "msgs": "message",
    "q": "queue",
    "pubsub": "publish-subscribe",
    # --- distributed systems (interchangeable in this domain) ----------- #
    "microservice": "service",
    "microservices": "service",
    "sharding": "partitioning",
    "shard": "partitioning",
    "partition": "partitioning",
    "replication": "redundancy",
    "failover": "redundancy",
    "quorum": "consensus",
    # --- personal knowledge management ---------------------------------- #
    "zettel": "note",
    "zettels": "note",
    "evergreen": "note",
    "moc": "index",
    "toc": "index",
    "outline": "index",
    "kb": "knowledge-base",
    "pkm": "knowledge-base",
    # --- units / plurals the stemmer won't catch ------------------------ #
    "apis": "api",
    "uis": "ui",
    "urls": "url",
    "ips": "ip",
}


# --------------------------------------------------------------------------- #
# Porter stemmer (self-contained, deterministic, stdlib-only)
# --------------------------------------------------------------------------- #
#
# A compact implementation of the classic Porter algorithm (Porter, 1980).
# It intentionally under-stems rather than over-stems: short words and words
# without a vowel-consonant measure are left alone, so "fox" stays "fox" and
# "ssrf" stays "ssrf". Only alphabetic tokens are stemmed; anything containing
# a digit or symbol (versions, ids, hyphenated canonicals) is returned as-is.

_VOWELS = frozenset("aeiou")


def _is_consonant(word: str, i: int) -> bool:
    ch = word[i]
    if ch in _VOWELS:
        return False
    if ch == "y":
        # 'y' is a consonant only when preceded by a vowel (or at the start).
        return i == 0 or not _is_consonant(word, i - 1)
    return True


def _measure(stem: str) -> int:
    """Count vowel-consonant sequences (the Porter 'm' measure)."""
    form = "".join("c" if _is_consonant(stem, i) else "v" for i in range(len(stem)))
    return form.count("vc")


def _contains_vowel(stem: str) -> bool:
    return any(not _is_consonant(stem, i) for i in range(len(stem)))


def _ends_double_consonant(word: str) -> bool:
    return (
        len(word) >= 2
        and word[-1] == word[-2]
        and _is_consonant(word, len(word) - 1)
    )


def _cvc(word: str) -> bool:
    """True if word ends consonant-vowel-consonant and the last isn't w/x/y."""
    if len(word) < 3:
        return False
    if not (_is_consonant(word, len(word) - 3)
            and not _is_consonant(word, len(word) - 2)
            and _is_consonant(word, len(word) - 1)):
        return False
    return word[-1] not in "wxy"


def stem(word: str) -> str:
    """Return the Porter stem of a single lowercase alphabetic token.

    Tokens shorter than 3 chars, or containing non-alphabetic characters, are
    returned unchanged — those are ids/abbreviations/versions where stemming
    would only lose information.
    """
    if len(word) <= 2 or not word.isalpha():
        return word

    w = word

    # --- Step 1a: plurals -------------------------------------------------
    if w.endswith("sses"):
        w = w[:-2]
    elif w.endswith("ies"):
        w = w[:-2]
    elif w.endswith("ss"):
        pass
    elif w.endswith("s"):
        w = w[:-1]

    # --- Step 1b: -ed / -ing ---------------------------------------------
    step1b_flag = False
    if w.endswith("eed"):
        if _measure(w[:-3]) > 0:
            w = w[:-1]
    elif w.endswith("ed") and _contains_vowel(w[:-2]):
        w = w[:-2]
        step1b_flag = True
    elif w.endswith("ing") and _contains_vowel(w[:-3]):
        w = w[:-3]
        step1b_flag = True

    if step1b_flag:
        if w.endswith(("at", "bl", "iz")):
            w += "e"
        elif _ends_double_consonant(w) and w[-1] not in "lsz":
            w = w[:-1]
        elif _measure(w) == 1 and _cvc(w):
            w += "e"

    # --- Step 1c: y -> i --------------------------------------------------
    if w.endswith("y") and _contains_vowel(w[:-1]):
        w = w[:-1] + "i"

    # --- Step 2: paired suffixes (measure > 0) ---------------------------
    _step2 = [
        ("ational", "ate"), ("tional", "tion"), ("enci", "ence"),
        ("anci", "ance"), ("izer", "ize"), ("abli", "able"),
        ("alli", "al"), ("entli", "ent"), ("eli", "e"), ("ousli", "ous"),
        ("ization", "ize"), ("ation", "ate"), ("ator", "ate"),
        ("alism", "al"), ("iveness", "ive"), ("fulness", "ful"),
        ("ousness", "ous"), ("aliti", "al"), ("iviti", "ive"),
        ("biliti", "ble"),
    ]
    for suf, rep in _step2:
        if w.endswith(suf):
            if _measure(w[: -len(suf)]) > 0:
                w = w[: -len(suf)] + rep
            break

    # --- Step 3 -----------------------------------------------------------
    _step3 = [
        ("icate", "ic"), ("ative", ""), ("alize", "al"), ("iciti", "ic"),
        ("ical", "ic"), ("ful", ""), ("ness", ""),
    ]
    for suf, rep in _step3:
        if w.endswith(suf):
            if _measure(w[: -len(suf)]) > 0:
                w = w[: -len(suf)] + rep
            break

    # --- Step 4: remove suffix when measure > 1 --------------------------
    _step4 = [
        "al", "ance", "ence", "er", "ic", "able", "ible", "ant", "ement",
        "ment", "ent", "ou", "ism", "ate", "iti", "ous", "ive", "ize",
    ]
    for suf in _step4:
        if w.endswith(suf):
            stemmed = w[: -len(suf)]
            if suf == "ion":
                if _measure(stemmed) > 1 and stemmed and stemmed[-1] in "st":
                    w = stemmed
            elif _measure(stemmed) > 1:
                w = stemmed
            break
    else:
        if w.endswith("ion") and _measure(w[:-3]) > 1 and w[-4:-3] in ("s", "t"):
            w = w[:-3]

    # --- Step 5a: strip final e ------------------------------------------
    if w.endswith("e"):
        m = _measure(w[:-1])
        if m > 1 or (m == 1 and not _cvc(w[:-1])):
            w = w[:-1]

    # --- Step 5b: collapse double l --------------------------------------
    if _measure(w) > 1 and _ends_double_consonant(w) and w.endswith("l"):
        w = w[:-1]

    return w


# --------------------------------------------------------------------------- #
# Synonym maps: base + per-store override
# --------------------------------------------------------------------------- #


def load_synonyms(store_root=None) -> dict[str, str]:
    """Return the effective synonym map: base overlaid with a store override.

    A store may keep a ``.fabric/synonyms.json`` file (a flat
    ``{variant: canonical}`` object). Entries there override or extend the base map, so a
    corpus can teach the graph its own vocabulary without code changes.
    """
    merged = dict(_BASE_SYNONYMS)
    if store_root is None:
        return merged
    try:
        from pathlib import Path

        path = Path(store_root) / ".fabric" / "synonyms.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(k, str) and isinstance(v, str) and k and v:
                    merged[k.lower()] = v.lower()
    except (OSError, json.JSONDecodeError):
        pass
    return merged


def save_synonyms(store_root, mapping: dict[str, str]) -> None:
    """Persist (merge) confirmed synonym mappings to the store override file."""
    from pathlib import Path

    path = Path(store_root) / ".fabric" / "synonyms.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    existing: dict[str, str] = {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            existing = {str(k): str(v) for k, v in loaded.items()}
    except (OSError, json.JSONDecodeError):
        pass
    for k, v in mapping.items():
        existing[k.lower()] = v.lower()
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


# --------------------------------------------------------------------------- #
# The canonical pipeline: raw text -> canonical tokens
# --------------------------------------------------------------------------- #


def canonical_token(token: str, synonyms: dict[str, str]) -> str:
    """Map one already-lowercased token to its canonical, stemmed form.

    Order matters: synonyms first (so "k8s" becomes "kubernetes" *before*
    stemming, and multi-word canonicals stay intact), then stem the result.
    A synonym whose canonical is multi-word (hyphenated) is returned without
    stemming, since it is already a deliberate canonical form.
    """
    mapped = synonyms.get(token, token)
    if "-" in mapped or not mapped.isalpha():
        return mapped
    return stem(mapped)


def canonical_tokens(text: str, synonyms: dict[str, str] | None = None) -> list[str]:
    """Tokenize *text* into canonical tokens (stopword-filtered, synonym-folded,
    stemmed). Drop-in replacement for the raw tokenizer, but vocabulary-aware.
    """
    if synonyms is None:
        synonyms = _BASE_SYNONYMS
    # NFKC folds compatibility characters (for example full-width Latin)
    # while the two case-sensitive substitutions expose code-style words.
    text = unicodedata.normalize("NFKC", text)
    text = _CAMEL_ACRONYM_RE.sub(r"\1 \2", text)
    text = _CAMEL_BOUNDARY_RE.sub(r"\1 \2", text).casefold()
    out: list[str] = []
    for t in _TOKEN_RE.findall(text):
        # The token pattern permits internal . _ - + # (for terms like
        # "v1.2", "user_id", "c++", "c#"), but that also traps trailing
        # sentence punctuation ("service." , "config,"). Strip leading and
        # trailing separators so "service" and "service." fold together,
        # while genuinely internal ones survive.
        t = t.strip("._-")
        if len(t) <= 1 or t in _STOPWORDS:
            continue
        out.append(canonical_token(t, synonyms))
    return out


# --------------------------------------------------------------------------- #
# Corpus-derived synonym inference (no ML)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SynonymProposal:
    """A suggested ``variant -> canonical`` mapping learned from the corpus."""

    variant: str
    canonical: str
    cooccurrences: int
    reason: str

    def as_dict(self) -> dict[str, object]:
        return {
            "variant": self.variant,
            "canonical": self.canonical,
            "cooccurrences": self.cooccurrences,
            "reason": self.reason,
        }


def _edit_distance(a: str, b: str, *, cap: int = 3) -> int:
    """Bounded Levenshtein distance; returns ``cap + 1`` once it exceeds cap."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        best = i
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            val = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            cur.append(val)
            best = min(best, val)
        if best > cap:
            return cap + 1
        prev = cur
    return prev[-1]


def _close_forms(a: str, b: str) -> str | None:
    """Return a reason string if *a* and *b* are plausibly the same concept.

    Cheap, deterministic string heuristics only: shared stem, prefix/acronym
    relationship, or small edit distance. Returns ``None`` when unrelated.
    """
    if a == b:
        return None
    if stem(a) == stem(b):
        return "shared stem"
    # One is an abbreviation/prefix of the other (min length guard avoids noise)
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) >= 2 and longer.startswith(shorter):
        return "prefix abbreviation"
    if len(shorter) >= 3 and _edit_distance(a, b, cap=2) <= 2:
        return "near-identical spelling"
    return None


def infer_synonyms(
    items,
    *,
    existing: dict[str, str] | None = None,
    min_cooccurrence: int = 3,
) -> list[SynonymProposal]:
    """Propose new synonym mappings from tag co-occurrence + string closeness.

    Intuition: tags that repeatedly appear together on the same notes and are
    *also* close in spelling (shared stem, abbreviation, or a one-typo gap) are
    very likely the same concept under two names. We fold the rarer tag into
    the more common one and propose it for human review.

    Fully deterministic: proposals are sorted, and the direction of each
    mapping (variant -> canonical) is fixed by corpus frequency then by string
    length then lexically. Nothing here mutates state — the caller decides
    what to persist.
    """
    existing = _BASE_SYNONYMS if existing is None else existing
    tag_freq: Counter = Counter()
    cooccur: Counter = Counter()

    for it in items:
        tags = sorted({t.lower() for t in getattr(it, "tags", []) if t})
        tag_freq.update(tags)
        for i in range(len(tags)):
            for j in range(i + 1, len(tags)):
                cooccur[(tags[i], tags[j])] += 1

    proposals: list[SynonymProposal] = []
    seen: set[tuple[str, str]] = set()
    for (a, b), count in cooccur.items():
        if count < min_cooccurrence:
            continue
        reason = _close_forms(a, b)
        if reason is None:
            continue
        # Direction: the canonical form is the more established one — higher
        # corpus frequency first, then the LONGER (fuller, less abbreviated)
        # spelling, then lexical order as a final deterministic tie-break. The
        # other term becomes the variant that folds into it. This makes
        # abbreviations fold into full words ("kube" -> "kubernetes"), not the
        # reverse.
        fa, fb = tag_freq[a], tag_freq[b]
        if (fa, len(a), b) >= (fb, len(b), a):
            canonical, variant = a, b
        else:
            canonical, variant = b, a
        # Skip if already mapped (to this or anything) or self-referential.
        if variant in existing or variant == canonical:
            continue
        key = (variant, canonical)
        if key in seen:
            continue
        seen.add(key)
        proposals.append(
            SynonymProposal(
                variant=variant,
                canonical=canonical,
                cooccurrences=count,
                reason=f"{reason}; co-tagged {count}×",
            )
        )

    proposals.sort(key=lambda p: (-p.cooccurrences, p.variant, p.canonical))
    return proposals
