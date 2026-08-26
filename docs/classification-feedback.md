# Classification feedback

Ingestion classifies deterministically: `classify.py` detects a content type, suggests
tags, and suggests a handling and preservation policy, with no model involved. Those
suggestions are advice. Nothing applies them, and an author is free to declare something
else.

That leaves a question the store could not previously answer: **are the rules any good?**
If every `policy` item ends up with its handling rewritten by hand, the rule that
classifies policies is wrong, and the only way to see that is to compare what was
suggested against what was approved, across the whole corpus.

## What is recorded

At publish time, ingestion attaches one `EditRecord` to the item carrying the suggested
policy and the suggested tags. It travels in the published Markdown under
`extra.edited_by`:

```yaml
extra:
  edited_by:
  - origin: via_script
    timestamp: ''
    drift: 0.0
    description: automatic classification at ingest
    suggested_policy:
      handling: human_only
      preservation: preserve_exact
      rationale: normative material should be preserved exactly
```

Two details are deliberate.

**The record carries no timestamp.** `canonical_frontmatter` serializes `edited_by` into
`extra`, so a generated timestamp would land in the published file and identical input
would stop normalizing to identical bytes. Normalized output is a pure function of the
input bytes, and that is enforced by tests. `Provenance` solves the same problem the same
way, by emitting only a *declared* `ingested_at`.

**An earlier classification record is replaced, not appended to.** Updating an item
republishes it, so appending would add one copy per save and grow without bound. Edit
records from any other source are carried through untouched.

## Reading the result

```bash
python -m textstrata show-misclassifications
```

```
Found 1 misclassification(s):

  Item: policy.override
  Title: Overriding Policy
    Suggested policy:
      Handling: human_only
      Preservation: preserve_exact
      Rationale: normative material should be preserved exactly
    Approved policy:
      Handling: human_plus_ai
      Preservation: summarize_allowed

Scanned 42 item(s).
7 item(s) carry a suggestion but declared no policy of their own, so nothing was
overridden.
```

## What counts as a disagreement

The report is only useful if it does not cry wolf, and two distinctions do that work.

**Overridden versus never applied.** An item that declared no policy of its own did not
disagree with anything; the advice simply was not adopted. Those are counted in the
summary, not listed. Only a value the author actually declared, differing from the
suggestion, is reported.

Establishing what the author declared reads the **stored original**, whose bytes an
invariant guarantees are never rewritten. Normalized frontmatter cannot answer it:
`canonical_frontmatter` drops an unset `handling`, but always emits `preservation`,
because its default is a non-empty value. Presence there says nothing about whether
anyone chose it. With no original on disk, `handling` remains trustworthy for the same
reason and `preservation` is left undetermined rather than guessed.

**Dropped versus added tags.** Ingestion publishes `declared + suggested`, so comparing
the two sets whole would flag every item carrying a tag of its own. A suggestion was
rejected only when a suggested tag is **absent** from the final list. Tags the author
added are not a failure of the suggester.

## Using it

Read the report for repetition, not for individual rows. One overridden item is a
judgement call. The same override appearing across every item of one content type is a
rule to fix in `classify.py`.

Items published before this existed carry no record and never appear, so the report fills
in as the corpus turns over. A file that cannot be parsed is counted and named rather
than raised: one malformed item must not be able to abort the report.
