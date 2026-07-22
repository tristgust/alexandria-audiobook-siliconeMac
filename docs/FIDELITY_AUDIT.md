# Source Fidelity Audits

Alexandria treats source wording as data, not as material the LLM may freely rewrite. Script generation and Review use separate deterministic audits after structural JSON validation.

## Script audit

The Script audit compares a generated chunk against the exact normalized source chunk. It verifies that every source segment is represented in order and that dialogue/narration boundaries are not collapsed. Dialogue boundaries recognize straight and curly single and double quotation marks. ASCII contractions, possessives, and unpaired elisions remain narration rather than being misread as quote delimiters.

Blocking findings include:

- missing source text;
- added text;
- wording or order changes;
- dialogue merged across intervening narration;
- missing attribution narration;
- narrator text assigned as dialogue;
- dialogue left inside a narrator entry;
- duplicated source segments.

A small deterministic source-segment scaffold can repair structure when the model returned the exact source text but grouped it incorrectly. The scaffold may split or relabel entries; it does not paraphrase text.

Before chunking, Alexandria removes U+FFFD Unicode replacement markers produced by failed ebook-image decoding. This normalization is applied to the working source snapshot and all dependent fingerprints/audits without mutating the uploaded file or altering authored wording.

The only permitted non-exact cases are narrowly classified TTS-safe conversions or attribution clarifications defined by the audit contract. They are counted and recorded rather than hidden.

## Review audit

Review may restructure speaker boundaries and change `speaker` or `instruct`, but the combined `text` stream must remain exactly the same and in the same order.

The Review audit compares the original batch and candidate output as normalized text streams. It blocks omission, addition, rewording, and reordering. In contextual mode, whole context-only entries accidentally copied into the result may be removed only when the remaining target subsequence exactly matches the original batch.

If final Review output fails, Alexandria keeps the original batch instead of saving a partially trusted correction.

## Retry order

1. Request structured JSON.
2. Validate the JSON contract.
3. Normalize exact source segmentation where deterministic and safe.
4. Run the Script or Review audit.
5. If the audit blocks and retries remain, send a correction suffix naming the exact finding.
6. Re-audit the complete replacement output.
7. Preserve checkpoint/original data if the final candidate still fails.

Schema correction and fidelity correction are independent. Structurally valid JSON is not automatically faithful.

## Character roster enforcement

An approved character roster may canonicalize a `speaker` label after the fidelity audit passes. It never authorizes changes to `text`, punctuation, order, or entry quantity.

Ambiguous aliases remain unchanged. Unresolved identities remain separate. Named non-speakers are not promoted into dialogue speakers.

## Evidence and logs

Audit summaries include:

- pass/block verdict;
- source/output segment or entry counts;
- exact match count;
- permitted conversion count;
- finding category and bounded source/output previews;
- attempt and stage metadata;
- elapsed time.

Raw response logs are written under `logs/`, which is runtime data and should not be committed.

## Production benchmark

The committed Qwen 3.5 readiness suite includes 17 cases and three runs per case. The current readiness evidence records:

- schema success: 1.0;
- Script audit pass: 1.0;
- Review audit pass: 1.0;
- punctuation accuracy: 1.0;
- narrator/dialogue accuracy: 1.0.

See [Benchmarking](BENCHMARKING.md).

## Regression commands

```bash
PYTHONPATH=app:tests ./app/env/bin/python -m unittest \
  tests.test_script_audit \
  tests.test_review_audit \
  tests.test_generate_script_fidelity \
  tests.test_review_fidelity \
  tests.test_benchmark_corpus \
  tests.test_user_test_repairs
```

Any change to prompts, segmentation, roster canonicalization, JSON schemas, or review recovery must rerun these tests and the complete offline suite.
