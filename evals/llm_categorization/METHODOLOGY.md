# LLM Categorization Evaluation Methodology

Evaluate prompt candidates as structured classifiers, not as free-form
assistants. Each prompt must return controlled JSON assignments that can be
validated, scored, compared, and reviewed.

## Principles

- The goal is correct, conservative, valid, and reviewable assignments.
- Do not optimize only for fewer `UNKNOWN` assignments or higher average
  confidence.
- A high-confidence wrong answer is worse than `UNKNOWN` or
  `needs_review: true`.
- Treat `needs_review: true` as a safety mechanism, not as a failure by
  default. A correct review flag is preferred when evidence is ambiguous,
  conflicting, weak, or outside the prompt's available context.
- False confident assignments are more serious than conservative review because
  they can silently corrupt downstream analytics, rules, reimbursement tracking,
  tax review, and user trust.
- The best prompt assigns correct taxonomy when evidence is clear, defers or
  flags ambiguous cases, avoids hallucinated IDs, and uses calibrated
  confidence.
- Prompt failures must be separated from taxonomy, rule, and data-quality
  failures.

## Headline Metrics

- Valid JSON rate.
- Schema-valid rate.
- Valid taxonomy ID rate.
- Category accuracy.
- Known-category accuracy excluding expected `UNKNOWN`.
- Exact taxonomy match.
- Tag micro precision, recall, and F1.
- Tag macro precision, recall, and F1 when possible.
- `UNKNOWN` precision and recall.
- False `UNKNOWN` rate.
- Missed `UNKNOWN` rate.
- `needs_review` precision, recall, and F1.
- Unsafe auto-assignment rate.
- High-confidence wrong rate.
- Confidence calibration by bands.
- Failure-mode counts.

## Main Safety Metric

```text
unsafe_auto_assignment =
  predicted taxonomy is wrong AND predicted needs_review is false
```

This metric should be reviewed before broad accuracy metrics. A prompt that
silently auto-assigns wrong taxonomy is less acceptable than a prompt that
returns `UNKNOWN` or asks for review.

## Confidence Calibration

Use these initial confidence bands when reporting calibration:

```text
0.00-0.49
0.50-0.69
0.70-0.84
0.85-0.94
0.95-1.00
```

Within each band, compare average confidence with empirical correctness. Review
high-confidence wrong assignments separately because they are the most dangerous
failure mode.

For small datasets or sparse runs where too few confidence bands are populated,
the initial scorer uses a simple proxy based on high-confidence exact-taxonomy
correctness when possible. If no high-confidence outputs exist, it falls back to
the overall exact-taxonomy match rate. This proxy is only a rough inspection aid
and must not be treated as a robust calibration estimate.

## Failure Separation

Classify failures by source before changing prompts:

- Prompt failures: invalid JSON, schema violations, hallucinated IDs, poor
  confidence calibration, unsafe no-review assignments, missed ambiguity, or
  wrong interpretation of supplied evidence.
- Taxonomy failures: missing categories or tags, unclear category instructions,
  overlapping semantics, or built-in behavior that is not represented clearly.
- Rule failures: existing manual or automatic rules that conflict with curated
  labels, merchants that always map to the same category, or cases where amount,
  direction, account, or history should drive a deterministic rule.
- Data-quality failures: noisy merchant normalization, ambiguous descriptions,
  missing direction context, duplicate examples, or labels that require external
  knowledge unavailable to the prompt.

Prompt scoring should report these separately so prompt revisions do not mask a
taxonomy or dataset problem.

Use representative failures to revise prompts. Do not encode isolated
merchant-specific fixes in the prompt when a taxonomy instruction, rule, history
record, or manual adjudication is the cleaner fix.

## Initial Experiment

- Use 100 to 120 manually curated examples.
- Compare 3 to 5 prompt candidates.
- Use temperature 0.
- Use one fixed model.
- Use the development set for prompt design and iteration.
- Use the validation set for prompt selection.
- Use the held-out test set only after prompt selection.

Do not tune prompts directly against the held-out test set. Once the held-out
test set has influenced a prompt, it is no longer a clean final estimate.

## Split Usage

- Development set: inspect examples, render prompts, iterate on wording, and
  debug output-format issues.
- Validation set: choose among prompt candidates after development iterations
  are complete.
- Held-out test set: estimate final performance after prompt selection only.

## Privacy Guidance

- Do not commit raw `finscope.db`, statement uploads, backups, logs, API keys,
  or other runtime financial data.
- Review and redact datasets before committing them. Preserve classification
  cues, but remove names, addresses, account numbers, exact counterparties, and
  other unnecessary personal details.
- Mark every example with `privacy_level`: `raw_real`, `redacted_real`, or
  `synthetic`.
- Keep synthetic examples clearly separate from real examples. Use filenames,
  notes, or source metadata to avoid mixing synthetic variants with real
  observations during leakage checks.
- Prefer redacted real examples for benchmark estimates. Use synthetic examples
  for targeted edge cases, prompt debugging, or taxonomy instruction tests, and
  report that distinction in summaries.
