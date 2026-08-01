# Alexandria voice and audiobook ecosystem audit — final decision synthesis

Status: **Research complete; implementation remains unauthorized by this document**  
Audit dates: 2026-07-30 through 2026-07-31, America/Chicago  
Supplied repositories reviewed: **111/111**  
Recursive linked projects reviewed: **145/145**  
Model-family records: **128**  
Evaluation-system records: **11**  
Machine-readable matrix: `.omo/evidence/alexandria-voice-audiobook-ecosystem-audit-20260730.json`  
Evidence index: `.omo/evidence/alexandria-voice-audiobook-ecosystem-audit-20260730/source-index.md`

## 1. Executive decision

Alexandria should remain one audiobook-production control plane.

No reviewed application, server, model host, queue, Voice store, project store, or evaluation suite justifies replacing or weakening:

```text
Project Home → Script → Cast → Produce → Export
```

The ecosystem's durable value is narrower:

- engine and model implementations behind Alexandria-owned adapters;
- exact artifact, cache, memory, cancellation, and lifecycle contracts;
- alignment, transcription, diarization, Voice evidence, enhancement, and pronunciation components;
- deterministic output gates and expected-set accounting;
- blinded human-listening workflows;
- negative fixtures proving how systems fail.

The practical conclusion is not “add more models.” It is:

> Finish exact-once generation and immutable Takes, then qualify a small number of capability-specific providers through one authoritative engine contract and one evidence ledger.

## 2. Scope and completion

The audit completed:

- substantive source review of all 111 supplied repositories;
- recursive review of 145 distinct linked projects;
- source-backed registration of 128 model families;
- source-backed registration of 11 evaluation systems;
- useful-reference sweeps for every completed linked review;
- exact artifact revision and digest capture where public metadata permitted it;
- Apple Silicon, runtime, lifecycle, security, long-form, and control-plane analysis;
- whole-project disposition independently from bounded concept harvesting.

Broad independent discovery is deferred by decision. The supplied and recursive network is already large enough to support the architecture and next provider screen. Reopen discovery only when:

- Alexandria has a concrete capability gap;
- a materially new Apple-native model family appears;
- an existing chosen provider becomes unavailable or fails qualification;
- a production defect requires a new technical comparison.

Do not maintain an open-ended engine-count race.

## 3. Product and ownership boundary

Alexandria remains authoritative for:

- project identity and storage;
- source ingestion and source fidelity;
- Script versions, review, and speaker labels;
- Cast identities and Voice assignment;
- reference recordings, transcripts, delivery evidence, and approval;
- model registry, cache identity, download and repair;
- provider capability truth;
- request admission, cancellation, progress, recovery, and receipts;
- raw Takes, processed versions, selection, retention, and cleanup;
- chapter order, final assembly, mastering, and Export;
- objective evidence and human-listening decisions.

External systems may return bounded results. They do not own Alexandria state.

## 4. Required production architecture

### 4.1 One authoritative engine record

Extend the existing `voice_backend_capabilities` and `model_registry`; do not create another registry.

Each engine record must bind:

- provider and adapter implementation revision;
- exact model, tokenizer, codec, vocoder, aligner, and auxiliary artifacts;
- supported platforms, devices, languages, sample rates, and Voice methods;
- reference-audio and reference-transcript requirements;
- whole-line and inline delivery controls;
- seed behavior and request-owned generator support;
- synthesis-window behavior: native long-form, safe internal split, or hard cap;
- streaming, cancellation, concurrency, timeout, and backpressure behavior;
- expected memory, residency group, load, release, and eviction behavior;
- offline readiness, packaging, acquisition, and repair state;
- known failure checks and qualification evidence.

Every UI, API, Cast rule, Produce action, Maintenance row, and scheduler decision must consume this same record.

### 4.2 Immutable request identity

One synthesis request identity must include:

```text
Script entry and text fingerprint
+ Cast and Voice identity
+ accepted Voice evidence set and order
+ exact reference transcripts
+ delivery direction and pronunciation guidance
+ engine, model, tokenizer/codec, and adapter revisions
+ preprocessing and synthesis-window contract
+ generation settings and effective seed
```

A dependency change creates a different request. It cannot resume, overwrite, or claim equivalence with the previous request.

### 4.3 Provider adapter boundary

Each provider adapter should expose the same narrow responsibilities:

- report capability and readiness;
- prepare validated request-local inputs;
- synthesize one authoritative internal segment or whole line;
- surface progressive output only when it is real and ordered;
- accept cancellation and produce one terminal state;
- release request and model resources through explicit ownership;
- return raw output plus a complete receipt;
- never download, switch models, fall back, rewrite text, or substitute silence implicitly.

Foreign queues, caches, model selectors, and project stores stay behind the adapter or are rejected.

### 4.4 Exact-once work and recovery

Boundary 16 remains the prerequisite.

Alexandria must first provide:

- restart-safe segment receipts;
- duplicate suppression;
- cancellation and timeout terminal states;
- crash reconciliation around generation, join, canonical replacement, and metadata commit;
- no resubmission of completed identical work;
- no partial result promoted as complete;
- no survivor-only batch success.

Only then should Boundary 20 generalize the mechanism into one persistent resource-aware scheduler.

### 4.5 Immutable rendition lineage

The accepted relationship is:

```text
request identity → raw Take → optional processing/mastering child version
```

Regeneration creates a new Take. It never deletes the previous valid Take.

Every rendition retains:

- source request fingerprint;
- engine and artifact identity;
- seed and settings;
- raw byte hash and audio validation;
- parent version when processed;
- review, Keep/pin, and current-selection state;
- receipt and rollback dependencies.

This is the most important implementation prerequisite exposed by the audit. Adding providers before this lineage exists would multiply stale-audio and approval debt.

### 4.6 Production Voice evidence sets

A Production Voice may contain multiple accepted samples, not one path string.

Each sample records:

- content hash;
- exact transcript and language;
- source and review provenance;
- identity, delivery, and quality evidence;
- provider compatibility;
- preprocessing identity;
- restrictions and approved use.

Sample membership and order participate in the prompt fingerprint. Cached prompt material invalidates when any member, transcript, order, compatibility result, or preprocessing rule changes.

### 4.7 Model residency

One residency coordinator should eventually own:

- loaded model and revision;
- current memory pressure and headroom;
- job ownership and residency group;
- compatible concurrency;
- planned eviction or manual release;
- measured release result;
- one bounded release-and-retry path for recognized allocation failures.

An engine must not silently unload or race another engine.

## 5. Provider tiers

These tiers are engineering dispositions, not blanket production approval.

### Tier 0 — Current Alexandria routes

Retain and finish qualification around the routes already represented in Alexandria's registry and product contracts:

- MLX Qwen3-TTS Base supplied-clip cloning;
- MLX Qwen3-TTS CustomVoice;
- MLX Qwen3-TTS VoiceDesign;
- MLX VoxCPM2 controlled cloning where its capability is explicitly selected;
- project-local merged MLX Qwen SFT/LoRA artifacts as experimental and unassignable until accepted;
- approved direct source performances as locked content-bound Takes, not generated model output.

Existing production use remains speaker- and capability-specific. A route that passed one Voice or delivery mode is not globally approved.

### Tier 1 — Next bounded blind qualification

The next comparison should stay small and hypothesis-driven:

1. IndexTTS2 as the matched current control.
2. A materially reworked VoxCPM2 controllable-clone route.
3. Qwen3-TTS Base as the current-family clone/control baseline.
4. Chatterbox Multilingual V3 if exact clone-plus-control behavior and current MPS handling are proven.
5. MOSS-TTS v1.5 or MOSS Nano only through a credible exact Apple route and reviewed style evidence.
6. Fish S2 Pro and Higgs Audio V2.5 only when exact Apple feasibility and combined clone-plus-instruction support are demonstrated.

Supertonic 3 is a separate compact on-device fallback candidate, not a replacement for the expressive-clone round. Its value is portable fixed or imported Voice synthesis after request-owned randomness and long-text seam repair.

Do not add a candidate merely because it has more parameters, more languages, or a polished demo.

### Tier 2 — Supporting production components

#### Transcription and text fidelity

- Existing pinned MLX Whisper remains the primary local validator.
- Qwen3-ASR, VibeASR, FunASR, and Kyutai delayed-stream STT are comparison or specialized validators after exact manifests.
- SenseVoice emotion and event tags are advisory only.

#### Alignment

- Qwen forced alignment and MMS CTC forced alignment are strong timestamp-evidence candidates.
- WhisperX is useful for composed alignment and diarization, but interpolated timestamps must remain labelled as interpolation.
- SimulStreaming supplies partial/final and bounded streaming-transcript concepts; it is not an audiobook authority.

#### VAD and diarization

- Silero VAD is the preferred compact VAD candidate after request-local recurrent state and parity qualification.
- pyannote community is the strongest offline diarization evidence family.
- diart is the strongest reviewed streaming diarization state machine.
- VBx is a useful deterministic refinement stage.
- 3D-Speaker CAM++ and pyannote/Wespeaker embeddings provide speaker evidence.

All labels are anonymous recording-local clusters until Alexandria links them to Cast through reviewed evidence.

#### Pronunciation and normalization

- eSpeak NG, phonemizer, and Misaki remain useful existing/reference components.
- g2pW is a conditional Mandarin polyphone component when every character retains original-position and fallback provenance.
- NeMo text processing supplies grammar and regression fixtures, not a source-rewriting authority.

Every transformation needs a reversible source map or an explicit reviewed exception.

#### Enhancement and cleanup

- DeepFilterNet is the strongest general enhancement candidate.
- ClearerVoice contributes exact-length and overlap/discard contracts and broader enhancement/separation/SR/TSE research.
- Demucs remains a conditional separation provider for specific cleanup jobs.

Enhancement creates a child rendition. It never rewrites the raw Take.

#### Codecs and tokenizers

- Descript Audio Codec contributes the strongest self-describing exact-length codec-container contract, after replacing pickle exchange.
- S3Tokenizer is a strong future semantic-tokenizer component with exact artifact digests and parity tests, but heuristic long-audio token seams remain unacceptable for source-authoritative use.
- X-Codec, X-Codec 2.0, BigCodec, FunCodec, Pupu, and related codecs remain research or dependency candidates until exact-length and streaming contracts pass.

#### Provenance and watermarking

- C2PA can record cryptographic provenance but does not prove truth, Voice authorization, or Alexandria approval.
- SilentCipher may be considered only as an optional final-output presence marker after canonical assembly.
- Watermark failure must be visible; a shared unsigned payload is not sufficient provenance.

### Tier 3 — Research, comparison, or deferred providers

Keep these out of normal production integration until a concrete gap justifies work:

- Step-Audio 2 and its serving forks;
- LLaSA and GRPO training lineages;
- CosyVoice and FlashCosyVoice routes without an accepted Apple/provider contract;
- Svara's CUDA/vLLM serving stack;
- Moshi and Unmute dialogue/session systems;
- broad vLLM and SGLang control planes;
- dialogue, music, SFX, and generic speech-language models that do not solve a named audiobook requirement;
- CUDA-only or multi-service providers without a credible local Apple path.

Their bounded lifecycle, cache, streaming, or failure concepts remain useful evidence.

## 6. Evaluation and approval stack

No single metric can approve audio.

### Gate 0 — Expected-set and artifact validity

Every requested item must end as:

- complete;
- failed with code and evidence;
- cancelled;
- timed out;
- invalidated by dependency change.

No item disappears from the denominator.

Require:

- decodable non-empty audio;
- exact sample rate and channels;
- positive duration;
- byte hash and request fingerprint;
- expected source-span coverage;
- no substituted silence or fallback output;
- no stale file chosen by recency.

### Gate 1 — Spoken-content fidelity

Use matched expected text with:

- primary local ASR;
- optional second ASR for disagreement;
- forced alignment or word timing;
- omission, insertion, repetition, truncation, and continuation checks;
- visible treatment of punctuation, interjections, and permitted normalization.

### Gate 2 — Voice identity

Use more than one accepted reference when available and record:

- embedding model and artifact identity;
- per-reference and aggregate similarity;
- calibrated thresholds per Voice/provider/language;
- cross-sentence drift;
- failure to produce a valid embedding.

Speaker similarity is evidence, not approval.

### Gate 3 — Acoustic and boundary quality

Check:

- clipping and loudness;
- leading and trailing defects;
- silence and duration anomalies;
- chunk seams and reference-tail leakage;
- DNSMOS, WVMOS, or related learned quality signals with complete failure accounting;
- optional enhancement comparison without replacing the raw Take.

### Gate 4 — Delivery and consistency

Measure and review:

- pace, pitch, energy, pause, and duration changes;
- instruction flattening or overstatement;
- identity versus acting tradeoff;
- repeated-seed stability;
- chapter-scale drift;
- compatibility of each reference with each provider.

### Gate 5 — Blinded human listening

Use P.808/P.835-inspired controls:

- qualification and hearing/device checks;
- training, trapping, and gold clips;
- randomized hidden model identity;
- full-playback evidence;
- expected vote counts;
- per-vote inclusion/exclusion reason;
- confidence intervals;
- immutable study and result manifests.

Final decisions remain per provider, Voice, language, and capability:

- production approved;
- approved with restrictions;
- return to preparation or training;
- reject.

## 7. Hard rejection rules

Reject or fail closed on:

- a second Alexandria project, Script, Cast, Voice, queue, cache, or output authority;
- silent provider or model switching;
- moving model snapshots in ordinary runtime;
- runtime network download or remote model code;
- global mutable RNG without an effective request seed;
- unbounded queues or unowned background tasks;
- cancellation that does not produce a terminal receipt;
- partial batch success presented as complete;
- missing outputs removed from evaluation denominators;
- broad `except` paths that substitute silence, ground truth, or successful-looking defaults;
- pickle or unrestricted deserialization across untrusted artifact boundaries;
- telemetry enabled for production manuscripts or audio;
- anonymous diarization clusters promoted directly to Cast;
- source normalization without a source map;
- generated audio overwritten or deleted on regeneration;
- metric-only approval or marketing capability claims;
- generic model marketplace, DAW, novelty-effect, dictation, personality, or voice-studio expansion.

## 8. Implementation sequence

The audit does not reorder the current release dependencies.

### Step 0 — Reconcile the live product

Complete `B19-T06` acceptance and `B19-T10` integration first. Do not implement this synthesis against one of the current dirty divergent trees.

### Step 1 — Finish Boundary 16

Complete:

- shared invalidation;
- synthesis-window contract;
- exact-once receipts;
- immutable generated Takes;
- crash reconciliation;
- orphan reconciliation;
- real-project regeneration and listening.

### Step 2 — Execute `B20-T01` and `B20-T07`

First implementation work from this audit should be:

- one authoritative engine capability record;
- one reusable engine-qualification matrix and evidence contract.

These tasks reduce risk without adding a new provider.

### Step 3 — Finish the bounded expressive-provider decision

Complete the outstanding B17 human review and the small multi-model blind round. Do not acquire or generate with every shortlisted model.

### Step 4 — Formalize Production Voice evidence

Execute `B20-T03` after capability truth and B17 disposition. Generalize the already proven adaptation/reference work into reviewed multi-sample Voice evidence sets.

### Step 5 — Generalize exact-once work and residency

After Boundary 16 and clean integration:

- execute `B20-T02` persistent scheduling;
- execute `B20-T04` residency and memory ownership.

### Step 6 — Final Listen and mastering

Implement `B20-T05` and `B20-T06` only over immutable Takes and child versions. Keep the surface constrained to audiobook assembly and publication-safe processing.

### Step 7 — Supplemental automation

Implement `B20-T08` last. Task Bundles remain primary. REST is loopback-authenticated and narrowly scoped. MCP remains optional and separately gated.

### Training lane

`B18-T03` and later training/listening tasks remain explicitly user-gated. The audit does not authorize training, checkpoint creation, or production assignment.

## 9. Immediate actionable recommendation

The next safe engineering task remains product integration and Boundary 16, not another model audit or model installation.

When a clean Boundary 20 lane is available, begin with `B20-T01`:

1. inventory every existing capability decision in `voice_backend_capabilities`, `model_registry`, Cast validation, Produce dispatch, Settings, Maintenance, and APIs;
2. define one versioned authoritative engine-record schema;
3. migrate current Qwen and VoxCPM2 declarations without changing behavior;
4. add drift tests proving every consumer uses the same truth;
5. add the reusable `B20-T07` qualification manifest and expected-set ledger;
6. only then admit one evaluation-only provider candidate.

## 10. Final disposition

The audit is complete.

It authorizes no product mutation by itself. It closes the research queue and supplies the decision basis for the existing master-plan boundaries.

The durable outcome is:

- preserve Alexandria's control plane;
- complete exact-once and Takes first;
- consolidate provider truth rather than multiply registries;
- qualify capabilities, not brands;
- use supporting models as evidence providers;
- keep every expected result in the ledger;
- require blinded human listening for production approval;
- add providers slowly and only for named audiobook needs.
