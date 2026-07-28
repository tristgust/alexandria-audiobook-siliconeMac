# Multimodel expressive-clone blind review design

Status: Round 1 implemented incrementally; Round 2 pairwise comparison documented for later implementation.

## Primary review goal

The listener must be able to judge hundreds of generated speech samples without learning which model produced them, losing progress, or having to finish the entire round before partial results become useful.

The review is grouped by requested performance category and then by expected identity. It is never grouped by model. Candidate order is independently deterministic and blind for each identity/style combination, so a candidate number does not map to one model across pages.

## Round 1: independent sample scoring

Round 1 answers whether each individual output is usable evidence.

Required judgments:

- identity match, 1–5;
- delivery adherence, 1–5;
- naturalness, 1–5;
- artifact severity, 1–5, where 1 is clean;
- spoken text matches, yes/no;
- requested mode is clear, yes/no;
- retain for comparison, yes/no.

Optional inputs:

- focused notes;
- one follow-up flag when the numeric scores do not explain why a sample should be revisited.

Round 2 candidate preparation is derived from the scores, approvals, text accuracy, and notes after unblinding. Round 1 does not ask the listener to manually classify every sample into a complex promotion category.

### Cumulative partial-result workflow

Every style, group, and cumulative export uses stable blind sample IDs. The listener may send any completed export before the rest of Round 1 is finished.

`benchmarks/merge_multimodel_round1_review_results.py` merges those exports into three durable evidence files:

- `human-review/cumulative_results.json`, which remains blind and can be imported back into the review app;
- `human-review/unblinded_results.json`, which joins reviewed blind IDs to the internal model, identity, style, and control evidence;
- `human-review/round2_preparation.json`, which aggregates results by model, identity, and style and lists preliminary matched pair candidates.

The merge is cumulative rather than replace-all. Later files update only the blind sample rows they contain. Existing scores for every other sample remain intact.

Preliminary Round 2 preparation is derived automatically:

- a strong candidate requires text and mode confirmation, explicit retention, identity/delivery/naturalness of at least 4, and artifact severity no greater than 2;
- a comparison candidate uses the same confirmations with identity/delivery/naturalness of at least 3 and artifact severity no greater than 3;
- a follow-up flag or borderline scores create a targeted follow-up;
- failed text, unclear requested mode, or explicit non-retention create a rejection;
- incomplete rows remain pending.

These are preparation labels in internal evidence, not additional listener choices and not production promotion decisions. Notes and individual scores remain available for correction when a simple threshold does not represent the listening judgment well.

## Round 2: blind pairwise preference mode

Round 2 should add a separate pairwise comparison surface. It should not be inserted into the Round 1 sample-scoring card.

### Purpose

Pairwise review answers a different question:

> Between two otherwise matched outputs, which performance should survive?

It is useful when several candidates pass absolute scoring but the preferred model, reference, strength, seed, or control translation remains unclear.

### Pair construction

A valid pair must match on:

- expected identity;
- requested emotion or delivery mode;
- target text;
- language;
- evaluation round and corpus version.

Pairs may intentionally vary only one or a small number of recorded factors, such as:

- model;
- control mechanism;
- reference strategy;
- transfer strength;
- seed;
- decoding configuration.

Do not compare unrelated identities, texts, or requested modes in one preference decision.

### Blind presentation

- Randomize which candidate is shown as A or B for every pair.
- Store the hidden mapping only in the separate answer key.
- Do not reuse a stable left/right assignment across pairs.
- Show the same expected identity reference audio used for both candidates.
- Show the requested mode and exact target line.
- Allow replaying A, B, and the identity reference independently.
- Do not expose model names, filenames, paths, configurations, or answer-key-derived hints.

### Listener decision

The primary control should contain exactly three options:

- **A is better**
- **No meaningful preference**
- **B is better**

An optional note may explain the preference. Do not require separate confidence, reason, or promotion fields by default. More detailed reasons can be inferred from the Round 1 scores or written in the note when necessary.

A separate **Neither is acceptable** result may be introduced only when pair construction permits two failed candidates to reach Round 2. Prefer filtering obvious failures before pairwise review so the three-choice decision remains sufficient.

### Pairwise workflow

- One pair should be the visual center of the page.
- Previous pair, next pair, and next incomplete must remain visible.
- Autosave on every choice and note edit.
- Display pair, group, and cumulative completion counts.
- Support independent pair-set exports and cumulative exports.
- Support importing multiple partial exports and merging by stable pair ID.
- Preserve keyboard operation without hijacking focus from audio or form controls.
- Allow filtering by identity, performance mode, and incomplete state.

### Round 2 aggregation

After unblinding, calculate preference totals by:

- model;
- identity;
- emotion or delivery mode;
- control mechanism;
- matched opponent;
- reference strategy;
- decoding configuration.

Retain ties and no-preference outcomes. Do not force every pair into a winner.

Pairwise preference is supporting evidence, not automatic production promotion. License review, text accuracy, identity floors, artifacts, runtime constraints, and broader listening evidence remain independent gates.

## Future implementation boundary

Round 2 pairwise mode should reuse the current Round 1 cumulative review infrastructure:

- stable hidden IDs;
- separate answer keys;
- copied repository-local audio;
- browser autosave;
- partial import/merge;
- independent group exports;
- no production mutation.

It should be a distinct review route or application mode rather than additional controls added to every Round 1 sample card.
