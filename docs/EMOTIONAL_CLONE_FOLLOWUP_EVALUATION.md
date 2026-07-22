# Emotional supplied-voice follow-up evaluation

Status: OpenVoice V2, IndexTTS2, and CosyVoice 3 were run against Alexandria's actual `NARRATOR` supplied-recording clone on the target Apple Silicon Mac. Human model-selection review rejected OpenVoice and CosyVoice for narrator identity; IndexTTS2 is the sole current quality finalist. The durable five-lane experiment covers 22 emotion and performance styles across direct non-cloned Qwen Ryan, IndexTTS2 generic Ryan, Narrator, Benny, and Doctor. All 110 human rows are now complete, including the restricted Doctor-relief follow-up. The targeted salvage review selected five new acting references and three transfer strengths, rejected disgust at the acting-source stage, and rejected whisper and sarcasm at the transfer stage. A bounded 24-sample winner-validation pack across Narrator, Benny, and Doctor is generated and packaged with 24 exact transcripts. The bounded speed stack reaches aggregate throughput RTF 1.680 on two warm workers, and a trained GPT-2 block has numerical parity in MLX. Winner-validation listening, the custom license, the next multi-model blind round, and production integration remain open. No candidate is production-supported.

This follow-up was created because Fish S2 Pro, Chatterbox, Qwen, and VoxCPM2 produced no convincing emotion in manual listening despite measurable output differences. Those earlier candidates remain useful baselines but are not presumptive solutions.

## Test boundary

The test used the real project `NARRATOR` clone reference. It did not use:

- the Ryan synthetic QA fixture;
- the narrator R8 dataset;
- the `narrator_attention_r8_pilot` LoRA;
- the `ALEXANDER SHUTTLEWORTH` LoRA assignment;
- speech-to-speech voice conversion.

All candidates synthesized the same sentence from text. The saved repository evidence stores fingerprints rather than the source reference transcript or target sentence. The local blinded review page displays the exact expected sentence because a listener cannot score text accuracy without it.

Repository evidence: `benchmarks/results/20260721T182355Z_emotional_clone_followup_narrator.json`.

Local review page: `/tmp/alexandria-emotional-clone-followup-review/review.html`.

Separate answer key: `/tmp/alexandria-emotional-clone-followup-review/answer_key.json`.

## Result summary

| Candidate | Samples | Mean RTF | RTF range | Load | Peak RSS | Speaker cosine | Perfect transcripts |
|---|---:|---:|---:|---:|---:|---:|---:|
| OpenVoice V2 pipeline | 18 | 0.232 | 0.078–0.330 | 1.37 s | 3.18 GiB | 0.9666–0.9714 | 16/18 |
| IndexTTS2 | 8 | 6.993 | 5.577–11.012 | 19.63 s mean | 7.37 GiB | 0.9538–0.9796 | 8/8 |
| CosyVoice 3 hybrid MPS | 6 | 4.119 | 3.665–4.625 | 4.72 s mean | 6.78 GiB | 0.9871–0.9911 | 6/6 |

Real-time factor is generation time divided by output duration. Values below 1.0 are faster than real time. Values above 1.0 are slower than real time.

The speaker cosine evaluator is a comparison aid, not a human identity verdict. A higher value on this one reference and sentence does not prove that a model will preserve every narrator characteristic across a book.

## OpenVoice V2

### What was tested

The official OpenVoice V1 expressive English base generator produced the requested performance style, then the official OpenVoice V2 tone-color converter applied the narrator identity. This remains a text-to-speech workflow from Alexandria's perspective; no performed source line is supplied by the user.

Styles tested at two deterministic seeds:

- default;
- friendly;
- cheerful;
- excited;
- sad;
- angry;
- terrified;
- shouting;
- whispering.

### Measured result

OpenVoice is already fast enough for production throughput on this Mac. The complete pipeline averaged 0.232 RTF and never exceeded 0.330 RTF in this test.

Speaker cosine was stable but lower than CosyVoice. Sixteen of eighteen samples transcribed perfectly. The two shouting samples produced one or two word substitutions under Whisper Base, so that style requires particular scrutiny.

### Apple Silicon work

No performance port is required. The evaluation needed narrow integration work:

1. bypass the bundled speaker-extraction helper because it hardcodes CUDA Whisper;
2. call the converter's direct speaker-embedding API;
3. bypass an upstream converter constructor argument-forwarding defect;
4. use a SciPy-free audio compatibility path for the current macOS 27 environment.

These are bounded wrapper corrections, not a model rewrite.

### Current decision

OpenVoice is rejected for this narrator. All 18 human-reviewed samples scored 1/5 for identity and none was approved. Its speed does not compensate for generating the wrong speaker.

## IndexTTS2

### What was tested

IndexTTS2 used the narrator recording only as the speaker prompt. Emotion was supplied through the model's eight-axis emotion vector and its text-described emotion path. No emotion-reference audio or speech-to-speech input was used.

Controls tested:

- calm;
- angry;
- afraid;
- happy;
- melancholic;
- sad;
- surprised;
- text-described frightened whisper.

### Measured result

The current official code and lockfile install and run natively on Apple MPS. Every line transcribed perfectly. Identity similarity was acceptable on average but varied more than the other candidates; angry and surprised were the lowest-scoring directions.

The model is not fast enough. Vector controls were generally about 5.6–6.2 times slower than real time. The text-described frightened whisper was 9.2 times slower than real time, and the first calm run reached 11.0 RTF.

### Apple Silicon work

The official implementation already recognizes `mps`, so basic compatibility does not require a port. Its upstream acceleration paths do not solve the measured problem:

- `use_accel` activates only under CUDA and depends on FlashAttention and Triton;
- its `torch.compile` option covers the secondary S2Mel stage and also requires Triton;
- the measured bottleneck is the 871-million-parameter autoregressive GPT generation stage.

A material Apple Silicon speedup therefore requires an MLX or equivalent Metal-native implementation of the GPT generation path, likely with careful quantization and cache handling. That is a substantial backend project rather than a wrapper patch.

Keeping the model resident would remove repeated 19-second cold loads but would not fix its several-times-real-time generation speed.

### License

IndexTTS2 uses the custom bilibili Model Use License Agreement, not MIT or Apache-2.0. Redistribution, derivative work, downstream terms, revenue/user thresholds, model-improvement restrictions, and Chinese-law arbitration requirements need legal review before Alexandria ships it.

### Current decision

IndexTTS2 is the sole quality finalist. It earned six approvals from eight model-selection samples and was the only candidate that repeatedly combined recognizable narrator identity with meaningful emotional delivery. The broader finalist review accepted clone identity and melancholic delivery but proved that the original single-axis sad and afraid controls were inadequate. Those vector results are retained as rejected evidence; the candidate path is now a reviewed emotion-reference bank that separates narrator identity from acting. It remains blocked by emotion-bank listening, speed-quality listening, the custom model license, and production integration.

### Finalist expansion

The finalist expansion generated 30 additional samples without touching Alexandria's production registry, Setup surface, Voice assignments, or live project audio:

- six speaker-reference candidates using different sentence-bounded excerpts from the real narrator recording;
- eight emotion-strength samples across sad, melancholic, happy, and afraid at strengths 0.45 and 0.70;
- four one-beam versus three-beam samples across identity and melancholic delivery;
- six one-beam seed-stability samples across sad and melancholic delivery at strength 0.55;
- three samples on an unseen short line;
- three samples on an unseen two-sentence passage.

All 30 outputs transcribed exactly under the pinned MLX Whisper evaluator. Speaker cosine ranged from 0.9757 to 0.9847, but these values remain diagnostic only. Human listening must choose the reference, emotion strength, and beam setting and must confirm seed repeatability and long-form identity.

The reference comparison does not produce a trustworthy automatic winner. The final sentence and job/desk excerpts had the two highest cosine values, but the spread among the top five was small and human identity review remains authoritative.

One beam is the only bounded speed improvement found without a decoder rewrite. On the matched identity pair it reduced RTF from 6.133 to 5.305 and GPT generation from 22.08 to 16.80 seconds. On matched melancholic delivery it reduced RTF from 6.360 to 4.374 and GPT generation from 20.77 to 13.34 seconds. All four lines transcribed exactly, and the one-beam melancholic sample had a higher speaker cosine than the three-beam sample. This setting is not accepted until the blind quality comparison is scored.

Across the complete expansion, GPT generation and forward occupied roughly 64% to 87% of measured stages. Longer text did not make the identity or afraid paths approach real time: the two-sentence samples remained near 4.9 RTF, and the slowest generated line took 50.27 seconds. Melancholic outputs were faster because the model emitted fewer acoustic tokens, not because the backend became faster.

Keeping IndexTTS2 resident can amortize the observed 18.9–23.1 second load. It cannot remove the autoregressive bottleneck. If finalist listening accepts quality, a deeper Metal/MLX-native GPT decoder, modernized cache handling, and optional quantization remain a separate engineering project.

Repository evidence: `benchmarks/results/20260721T220419Z_indextts2_finalist_expansion.json`.

Blinded review hub: `/private/tmp/alexandria-indextts2-finalist/review.html`.

### Finalist human result

Five exported score files cover 24 of the 30 finalist samples. The dedicated six-reference export was not received, so the six reference clips cannot be ranked directly by ear. The final-sentence reference remains provisionally accepted because it was used for all 24 scored downstream samples and achieved mean narrator identity 4.7604/5.

Direction-level human results:

- **Identity:** accepted, 3/4 approvals, mean identity 4.25/5. One one-beam short-line seed drifted, but unseen and long-form one-beam identity were approved at 4/5 and 5/5 identity.
- **Melancholic:** accepted, 8/9 approvals, mean identity 4.92/5, delivery 3.79/5, and naturalness 5/5. Strength 0.55 with one beam is the selected configuration.
- **Happy:** strength 0.45 was accepted on the known line at identity 5/5, delivery 4/5, and naturalness 5/5. Strength 0.70 was rejected as robotic. Happy remains restricted pending unseen and long-form confirmation.
- **Sad:** rejected as a reliable single-axis control. Only 1/5 samples was approved, and even that sample was described as only almost sad.
- **Afraid:** rejected. All four scored samples retained identity but failed delivery; none sounded afraid.

One beam is accepted for melancholic and remains the provisional general default with validation and retry. Three beams are retained only as a quality fallback because they can recover a weak short-line identity seed but are materially slower.

Human-review evidence: `benchmarks/results/20260721T231229Z_indextts2_finalist_human_review.json`.

### Sad and afraid salvage

A bounded salvage pass tested the model's natural-language emotion classifier and custom blended vectors.

The isolated Qwen classifier mapped the grief description almost entirely to the already failed sad axis: sad 0.98, melancholic 0.01, and calm 0.01. The fear description mapped primarily to afraid 0.85 with angry 0.10 and only trace values elsewhere. Live `use_emo_text` generation inside the resident TTS process failed to produce the first WAV before the five-minute ceiling.

Fixed text-derived grief, a sad/melancholic blend, and an isolated afraid/surprised blend also failed to produce a WAV within bounded runs. The fear blend remained incomplete after 3.5 minutes despite one beam and `max_mel_tokens` 500. These controls are rejected as generation-unstable on the current MPS path, not merely as weak performances.

Sad and afraid are therefore unsupported for this narrator under the current IndexTTS2 integration. More vector experimentation is not justified without a model or decoder change.

### Happy generalization

Three additional happy 0.45 samples were generated:

- unseen short line, one beam: RTF 5.774, speaker cosine 0.9806;
- unseen short line, three beams: RTF 12.425, speaker cosine 0.9825;
- two-sentence passage, one beam: RTF 6.726, speaker cosine 0.9812.

All three transcripts are exact. Three beams more than doubles unseen-line runtime for a small embedding increase, so one beam is the only practical setting unless listening finds a clear quality advantage.

Happy review pages:

- `/private/tmp/alexandria-indextts2-finalist/happy-unseen-review/review.html`
- `/private/tmp/alexandria-indextts2-finalist/happy-long-review/review.html`

Objective evidence: `benchmarks/results/20260721T231229Z_indextts2_salvage_happy_objective.json`.

### Reviewed emotion-reference bank

IndexTTS2 officially supports separate speaker and emotion reference audio. The follow-up now uses that separation directly: the narrator excerpt remains the sole identity source, while a reviewed expressive performance supplies delivery. This is not speech-to-speech conversion; the target line is still synthesized from text.

Twelve bank samples cover:

- sad with two independent references;
- fear with two independent references;
- anger with two independent references;
- happy and excited;
- friendly;
- surprised;
- whisper;
- shout.

All 12 samples transcribed exactly. Objective narrator cosine ranges from 0.9493 to 0.9820 and RTF ranges from 1.663 to 3.349. The shout sample is the most identity-stressing mode and must be judged especially carefully. Same-narrator references generally preserve identity most strongly; expressive base references often emit fewer acoustic tokens and run faster. These metrics do not establish acting quality.

#### Narrator bank human result

The completed 16-sample human review selected greedy eight-step generation as the speed default. It scored 5/5 for identity, delivery, and naturalness with artifact severity 1, while the sampled 25-, 12-, and 8-step variants were all rejected as somewhat robotic.

Narrator emotion dispositions:

- friendly and shout are accepted;
- whisper is accepted with a restriction because it sounds more like reduced volume than a true whisper;
- sad is accepted but still under-expressive, with the same-narrator reference preferred;
- anger is accepted only with the prior IndexTTS2 performance reference;
- fear, surprise, happy, and excited are rejected for insufficient delivery despite generally strong identity;
- one duplicated sad score export was deduplicated and did not affect counts.

Human evidence: `benchmarks/results/20260722T_narrator_emotion_bank_speed_human_review.json`.

The reviewed emotion-reference bank replaces the failed assumption that eight scalar axes can cover audiobook performance. Alexandria should treat emotion as a versioned, human-reviewed reference library with named intent, provenance, compatible speakers/languages, strength, listening status, and fallback behavior. New emotions can be added by reviewing new references rather than changing the narrator clone or retraining the whole model.

#### Durable five-lane capability expansion

The earlier ten-mode Benny/Doctor pass was superseded by a durable matched experiment that includes both the original narrator and a same-model control.

The five lanes are:

- direct non-cloned Qwen Ryan, which tests whether the written instruction itself produces convincing acting;
- IndexTTS2 generic Ryan, which uses the same Ryan identity and acting references and therefore acts as the same-model upper bound;
- IndexTTS2 Narrator;
- IndexTTS2 Benny;
- IndexTTS2 Doctor.

Each lane covers 22 styles:

- core emotion: neutral, happy, sad, angry, fear, surprise, disgust;
- adjacent or intensity states: excited, grief, panic, relief, contempt;
- social and dramatic delivery: friendly, tender, pleading, sarcastic, calm, urgent, exhausted, authoritative;
- vocal mode: whisper and shout.

This design distinguishes failures that originate in the acting reference from failures introduced by IndexTTS2 transfer or by a specific supplied identity. The direct Qwen lane and generic Ryan lane share the same person, while Narrator, Benny, and Doctor test cross-identity transfer from those exact performances.

All 110 samples produced valid audio. The pinned transcription evaluator completed all rows, with 105 exact transcripts and five minor non-zero-WER cases. Generic Ryan and Doctor were exact on all 22 lines; Narrator and Benny each had one small surprise-line variation; direct Qwen had minor wording or contraction variation in happy, whisper, and disgust. Human text confirmation remains visible on every review page.

Objective lane summaries:

- direct Qwen Ryan: mean RTF 0.312;
- IndexTTS2 generic Ryan: mean RTF 1.937;
- IndexTTS2 Narrator: uncontended full-matrix mean RTF 2.437;
- IndexTTS2 Benny: mean RTF 1.926;
- IndexTTS2 Doctor: mean RTF 1.918.

The review is self-contained under `.omo/evidence/b17-t05-four-voice-emotion-matrix/review/`. It has 22 independently autosaved pages, five hidden synthesis lanes per page, visible expected identity, copied identity references, separate answer keys, and no `/tmp`, model-cache, or symlink dependency.

Objective evidence: `.omo/evidence/b17-t05-four-voice-emotion-matrix/objective_summary.json`.

Durable review alias: `.omo/evidence/b17-t05-four-voice-emotion-matrix/review.html`.

#### Five-lane human result

The uploaded review archive contained all 22 expected exports and 110 rows. The original archive left Doctor relief blank; the separate one-sample follow-up closed that row. The completed human disposition therefore covers all 110 samples:

- 83 approved for continued comparison;
- 27 rejected;
- 110/110 human-confirmed text matches;
- no remaining five-lane score.

Lane-level results:

- direct Qwen Ryan: 19/22 approved, mean delivery 4.207/5;
- IndexTTS2 generic Ryan: 12/22 approved, mean delivery 3.202/5;
- IndexTTS2 Narrator: 16/22 approved, mean delivery 3.373/5;
- IndexTTS2 Benny: 17/22 approved, mean delivery 3.609/5;
- IndexTTS2 Doctor: 19/22 approved, mean delivery 3.984/5.

The same-voice generic Ryan lane did not behave as an upper bound. It had the lowest approval rate among the IndexTTS2 lanes, proving that some acting information is flattened inside IndexTTS2 even when speaker and emotion references originate from the same voice.

Strong cross-identity capabilities are neutral, tender, happy, authoritative, surprised, and angry. Sad, exhausted, and excited are usable but vary in emotional intensity or quality. Several styles require speaker compatibility rather than a universal bank entry:

- Doctor was strongest for whisper and sarcasm;
- Benny was the only production identity approved for urgent delivery;
- Narrator, Benny, and Doctor preserved calm better than generic Ryan;
- Narrator grief drifted away from the target identity while Benny and Doctor remained acceptable;
- shout transferred to the three production identities but not to generic Ryan.

Some comparison approvals do not establish the requested capability. Contempt was repeatedly described as not unmistakably contemptuous. Fear retained identity, but none of the original transferred performances sounded convincingly fearful. Panic failed all five original lanes. Disgust was only marginal in direct Qwen and failed every IndexTTS2 transfer. Relief passed in direct Qwen and received a restricted Doctor pass at 4/5 identity, 4/5 delivery, and 5/5 naturalness; generic Ryan, Narrator, and Benny still failed relief transfer.

The targeted salvage work then separated the causes directly: new acting-source candidates were reviewed for fear, panic, disgust, contempt, relief, and urgent, while generic-Ryan strength sweeps tested calm, pleading, whisper, sarcasm, and shout. Only human-selected winners were carried into the bounded production-identity validation pack.

Human evidence: `.omo/evidence/b17-t05-four-voice-emotion-matrix/human_review_summary.json`.

Doctor relief follow-up: `.omo/evidence/b17-t05-four-voice-emotion-matrix/doctor-relief-followup.html`.

#### Targeted reference and transfer salvage

The human classification enabled a bounded follow-up rather than another broad cross-speaker matrix.

**Acting-reference selection** generated three new direct Qwen Ryan prompt/seed candidates for each of six weak or ambiguous source performances:

- fear;
- panic;
- disgust;
- contempt;
- relief;
- urgent.

All 18 candidates generated at mean RTF 0.299. Thirteen transcribed exactly. The five visible text variations include minor inflection or wording changes and one material panic failure: `panic_acting_v3_5306` inserted repeated laughter before the line. That candidate cannot become a reference unless listening explicitly accepts the nonlexical performance and text behavior.

**Transfer-strength selection** used five acting references that were already convincing in direct Qwen but weakened during IndexTTS2 transfer:

- calm;
- pleading;
- whisper;
- sarcastic;
- shout.

For each style, generic Ryan was synthesized at hidden emotion-reference strengths 0.70, 0.85, and 1.00 using the accepted greedy eight-step runtime. All 15 samples transcribed exactly at mean RTF 1.968. This review determines whether increasing transfer strength restores delivery before repeating the winner across Narrator, Benny, and Doctor.

The combined follow-up contains:

- one Doctor-relief score;
- six acting-reference pages with 18 samples;
- five transfer-strength pages with 15 samples;
- 12 pages and 34 samples total.

Targeted objective evidence: `.omo/evidence/b17-t05-reference-transfer-salvage/objective_summary.json`.

Targeted review hub: `.omo/evidence/b17-t05-reference-transfer-salvage/review.html`.

#### Targeted salvage human result

All 34 uploaded rows were matched by blind `sample_id` against repository answer keys; filenames were not used to infer page identity.

Selected acting references:

- fear: `fear_acting_v2_5302`, prompt variant 2, seed 5302; variants 3 and 1 remain approved alternatives;
- panic: `panic_acting_v2_5305`, prompt variant 2, seed 5305; clear 5/5 identity, delivery, and naturalness winner;
- disgust: no winner; all three candidates scored only 1.0–1.2/5 delivery, establishing an acting-source failure before IndexTTS2;
- contempt: `contempt_acting_v3_5312`, prompt variant 3, seed 5312; the automatic transcript inflected “believed” as “believe,” while the listener confirmed the spoken text;
- relief: `relief_acting_v2_5314`, prompt variant 2, seed 5314; strong result with a slight excessive-laughter limitation, and variant 3 retained as an alternative;
- urgent: `urgent_acting_v3_5318`, prompt variant 3, seed 5318.

`panic_acting_v3_5306` is explicitly rejected as a production reference. It inserted repeated laughter/nonverbal material, produced WER about 0.47, and scored 3 identity / 2 delivery / 3 naturalness. It remains a research example of nonverbal generation only.

Selected generic-Ryan transfer strengths:

- calm: 0.70; all three strengths were perfect, so the least aggressive equivalent strength wins;
- pleading: 1.00; highest delivery score at 4.2/5;
- whisper: no accepted strength; every sample was quiet speech rather than whispering;
- sarcastic: no accepted strength; every sample remained ambiguous between enthusiasm and sarcasm;
- shout: 1.00; 0.85 remains an accepted alternative, while 0.70 was not approved.

Durable human evidence: `.omo/evidence/b17-t05-reference-transfer-salvage/human_review_summary.json`.

#### Bounded winner validation

The selected five acting references—fear, panic, contempt, relief, and urgent—and three accepted transfer settings—calm 0.70, pleading 1.00, and shout 1.00—were applied through IndexTTS2 to Narrator, Benny, and Doctor only. Generic Ryan was not regenerated.

The validation used the accepted FP32 MPS profile with fast math, Metal-preferred matrix multiplication, one beam, greedy decoding, eight diffusion steps, and two persistent workers. It produced 24/24 valid WAVs with 24 exact transcripts. The review is self-contained, copies every WAV, separates answer keys, uses eight unique autosave keys, saves on input, shows completion counts, jumps to the next incomplete control, and blocks incomplete exports.

Objective evidence: `.omo/evidence/b17-t05-reference-transfer-salvage/winner-validation/objective_summary.json`.

Blinded review hub: `.omo/evidence/b17-t05-reference-transfer-salvage/winner-validation/review/index.html`.

Per-speaker capability remains pending human review. Mechanical generation and speaker cosine do not establish fear, panic, contempt, relief, urgency, calm, pleading, or shout support.

#### Next multi-model blind round

IndexTTS2 will not be the only model in the next blind comparison. VoxCPM2 controllable cloning is mandatory and must be reworked rather than represented by the prior flat baseline. The bounded setup probe must use the actual reference-audio-plus-control-instruction path, transcript-aligned sentence-bounded references, exact preprocessing hashes, strong contrasting instructions, and explicit checks for startup chirps, reference-tail leakage, identity drift, and instruction flattening.

The current high-priority screen also includes:

- Fish Audio S2 Pro with its current inline free-form control contract, not the prior unchanged configuration;
- MOSS-TTS Local Transformer v1.5 through its Apple-Silicon/MLX path, subject to the separate large-model acquisition decision;
- Higgs Audio V2.5 after exact weight, license, clone-API, memory, and Apple-Silicon screening;
- Chatterbox Multilingual V3 after confirming its actual clone-control surface and local runtime;
- Qwen3-TTS Base only if an upstream-supported or trained clone-plus-instruction path is proven; Alexandria's untrained instruction embedding remains a comparison baseline.

Apple-Silicon LoRA/SFT remains a separate experimental lane. It enters a blind test only after real training produces a durable checkpoint, valid held-out WAVs, and proof that the exact instruction participates in both training and inference. Identity-only and instruction-conditioned adapters remain distinct and unassignable.

Research screen: `.omo/evidence/b17-t05-next-multimodel-screen/model_screen.json`.

No candidate may generate blind-test audio until its official repository/model documentation has been reviewed and converted into a model-specific handling contract. The contract records the real clone mode, reference and transcript requirements, instruction or tag syntax, sampling controls, language/device requirements, unsupported combinations, and known failure checks. A generic translation shared across models is forbidden.

The documentation review already constrains the lineup materially:

- IndexTTS2 uses separate speaker and emotion references with `emo_alpha`; random sampling is avoided because upstream warns it reduces clone fidelity.
- VoxCPM2 must use Controllable Cloning; Ultimate Cloning disables Control Instruction and tests a different capability.
- Qwen3-TTS Base officially supports rapid voice cloning with reference audio and transcript, but the released Base model is not marked instruction-controlled. It enters the emotion blind test only after a supported combined path or trained adapter is proven.
- Fish S2 Pro uses inline free-form control tags at exact text positions, not a generic external instruction field.
- Higgs Audio V2.5 remains blocked until its exact V2.5 weights, tokenizer, style-control API, license, and Apple-Silicon path are resolved; V2 examples cannot be mislabeled V2.5.
- MOSS-TTS v1.5 documents voice cloning, language tags, duration, pronunciation, and `[pause X.Ys]`; arbitrary emotion control must be verified from the exact v1.5/MLX API rather than inferred.
- Chatterbox Multilingual V3 must be loaded explicitly with `t3_model="v3"`, cloned with `audio_prompt_path`, and given `language_id`; Original Chatterbox CFG/exaggeration guidance is not assumed to apply to V3 without source confirmation.

Model-specific contracts: `.omo/evidence/b17-t05-next-multimodel-screen/model_handling_contracts.json`.

### Apple Silicon speed expansion

The bounded runtime stack tested FP32, FP16, MPS fast math, Metal-preferred matrix multiplication, one versus three beams, 25/12/8 diffusion steps, greedy generation, classifier-free guidance, and one/two/three warm workers.

Measured findings:

- FP16 is slower on this MPS path and is rejected.
- MPS fast math plus Metal-preferred matmul provides a small improvement.
- Eight diffusion steps outperform 25, subject to listening quality.
- Greedy generation reduces the fixed-line RTF from 3.685 to 3.045 and removes random-seed variability, subject to listening quality.
- CFG-off is slower on the matched line and is rejected.
- Two persistent workers are the throughput optimum on the M2 Max; three workers oversaturate the shared GPU.
- Sequential resident throughput is aggregate RTF 2.718.
- Two workers with 12 steps reach 2.072.
- Two workers with eight steps reach 1.916.
- Two workers with greedy eight-step decoding reach 1.773.
- Two workers with greedy eight-step emotion-reference generation reach **aggregate throughput RTF 1.680**.

The selected provisional production profile is therefore FP32, MPS fast math, Metal-preferred matmul, one beam, greedy decoding, eight diffusion steps, and two persistent workers. Every quality-affecting change remains behind the new blind speed-quality review.

### MLX block parity

Alexandria already contains MLX 0.32.0 and MLX-LM 0.31.3. IndexTTS2's bottleneck is a 24-layer, 1,280-dimensional, 20-head GPT-2 transformer with custom prefix and mel-token embeddings. A trained first transformer block was mapped from the official PyTorch checkpoint into a minimal MLX implementation and evaluated on the same input.

MLX block parity passed:

- maximum absolute error: 0.0000801;
- mean absolute error: 0.000000346;
- RMSE: 0.000001343;
- cosine similarity: 0.999999999999644.

This proves the trained transformer computation and weights map correctly. The next increment is a 24-layer MLX decoder with custom prefix embeddings, mel embedding, learned mel positions, final normalization, mel head, and a greedy KV-cached token loop. Existing PyTorch speaker extraction, emotion conditioning, S2Mel, and BigVGAN can remain unchanged during that port.

Repository evidence: `benchmarks/results/20260722T004820Z_indextts2_emotion_bank_speed_expansion.json`.

Blinded review hub: `benchmarks/results/20260721T_indextts2_emotion_speed_review_hub.html`.

## CosyVoice 3

### What was tested

CosyVoice 3 used instructed zero-shot cloning with the real narrator reference. Instructions tested:

- neutral suspense;
- urgent delivery;
- controlled anger;
- terrified whisper;
- quiet grief;
- excitement.

The 35-second narrator source was deterministically trimmed to 20 seconds because CosyVoice rejects prompts longer than 30 seconds.

### Measured result

CosyVoice produced the highest automated speaker-cosine values in the initial comparison, 0.9871–0.9911, and all six lines transcribed perfectly. The blinded human review rejected all six samples, however: every sample received narrator identity 1/5 and none was approved. This proves that the current speaker-cosine evaluator does not capture the perceptual identity traits that matter for this narrator and must not be used as an acceptance gate.

Its working hybrid MPS path averaged 4.119 RTF. That was materially better than the full CPU neutral baseline, which measured 5.930 RTF and used more memory, but it is still not fast enough for interactive or high-throughput audiobook generation.

### Apple Silicon work

The official wrapper selects CUDA or CPU but not MPS. The evaluation-only runner applies a small, explicit porting layer:

1. route the PyTorch LLM, flow model, frontend, and neural vocoder to MPS;
2. disable optional WeText normalization so simple English input does not trigger an undocumented ModelScope download;
3. append CosyVoice 3's required `<|endofprompt|>` control token when normalization is bypassed;
4. keep the causal F0 predictor in float32 because MPS has no float64 support;
5. run only the final ISTFT on CPU because the pinned PyTorch 2.3 MPS backend lacks `aten::unfold_backward`;
6. keep the speech-tokenizer ONNX graph on CPU.

Moving the speech tokenizer to CoreML was counterproductive. Neutral generation worsened from 4.018 to 6.481 RTF, load time increased from 4.48 to 7.25 seconds, and memory rose from 6.78 to 8.32 GiB.

The next speed investigation, only if listening quality warrants it, should test a newer compatible PyTorch runtime, persistent model residency, LLM quantization, and a Metal-native LLM path. The existing hybrid port is already narrow enough to integrate experimentally; reaching real time is the unresolved work.

### Corrected identity and persistent-description rerun

The initial instructed test did not first establish a true exact-transcript zero-shot identity baseline and did not include a persistent narrator profile. A corrected rerun therefore used a 5.9-second sentence-bounded excerpt whose exact transcript was independently verified by the pinned MLX Whisper evaluator.

The corrected runner now supports two explicit modes:

- `zero_shot`, which binds exact reference audio to its exact transcript;
- `instruct`, which may prepend a persistent narrator description to the line direction.

The full user-authored narrator description was too long for reliable instructed generation. On MPS, zero of four outputs preserved the target text; on CPU, only one of four did. The failures repeated the line, leaked instruction fragments, or produced gibberish. The full description is therefore rejected as a direct per-line CosyVoice prompt.

A concise persistent description was stable on the hybrid MPS path. Neutral, controlled anger, grief, and fear all transcribed exactly, with mean RTF 3.088. Exact-transcript zero-shot on MPS also transcribed exactly at 3.027 RTF. These technical results do not establish narrator identity by ear.

Evidence: `benchmarks/results/20260721T201257Z_cosyvoice3_corrected_identity_followup.json`.

Focused listening review: `/tmp/alexandria-cosyvoice3-corrected-review-focused/review.html`.

### Focused human result and final decision

The focused six-sample review rejected CosyVoice for this narrator. The exact-transcript zero-shot MPS sample was the closest attempt, scoring 2.75/5 for identity, 5/5 for delivery and naturalness, and 1/5 artifact severity, but the listener still identified both the accent and voice as incorrect. No sample reached the required 3/5 identity floor.

The concise persistent profile made identity worse rather than better: four samples averaged 1.0625/5 identity and 2.0/5 delivery, with no approvals. The one text-valid full-profile CPU sample scored 1/5 identity despite strong neutral delivery. CosyVoice is therefore rejected as a finalist for this narrator. The persistent description remains useful as Alexandria Voice metadata and as an optional short delivery constraint, but it is not a substitute for clone identity and must not be treated as one.

Human-review evidence: `benchmarks/results/20260721T204043Z_cosyvoice3_corrected_identity_human_review.json`.

## Initial model-selection review contract

The combined local review includes 32 randomized samples:

- 18 OpenVoice samples;
- 8 IndexTTS2 samples;
- 6 CosyVoice samples.

For every sample, the HTML page shows:

- an audio player;
- the requested direction;
- the exact expected sentence;
- the automatic Whisper transcript;
- word error rate;
- narrator-identity score;
- delivery-adherence score;
- naturalness score;
- artifact-severity score;
- spoken-text confirmation;
- comparison approval;
- notes.

Candidate names remain hidden until the separate answer key is opened. Ratings save in the browser and can be exported as JSON.

## IndexTTS2 finalist review contract

The finalist hub presents six reviews in order:

1. narrator-reference selection — 6 samples;
2. emotion-strength selection — 8 samples;
3. one-beam versus three-beam quality — 4 samples;
4. seed stability — 6 samples;
5. unseen short text — 3 samples;
6. unseen two-sentence text — 3 samples.

Five pages are scored. The direct reference-selection export is missing, but the selected final-sentence reference is provisionally accepted from the 24 downstream human scores. Reference identity, emotion strength, beam count, and seed remain hidden from visible headings and appear only in the answer keys. Each review page uses a unique browser-storage key.

The earlier happy-only gate has been superseded by the broader emotion-reference and speed-quality review. The current hub contains nine independently autosaved pages and 16 samples covering broad delivery modes plus four hidden runtime variants.

## Final ranking after model-selection listening

1. **IndexTTS2 — sole quality finalist.** Clone identity and melancholic delivery are accepted. Broad emotion generation now uses a reviewed emotion-reference bank rather than the failed scalar-vector assumption. The bank and accelerated runtime remain listening-gated; the custom model license remains a separate blocker.
2. **CosyVoice 3 — rejected for this narrator.** Exact-transcript zero-shot improved identity to 2.75/5 but still had the wrong accent and voice. Persistent descriptions reduced identity and, when long, caused prompt leakage or gibberish.
3. **OpenVoice V2 — rejected for this narrator.** All 18 samples scored 1/5 identity despite fast generation and some usable emotional style.

The automated speaker-cosine ranking is not an acceptance signal for this narrator. Human identity and delivery scores are authoritative.

## Promotion gate

No candidate is production-supported yet. Promotion requires:

1. completed blinded review showing clear, repeatable emotional differences;
2. narrator identity accepted by ear, not merely by cosine score;
3. naturalness and artifact ratings acceptable for audiobook use;
4. text accuracy confirmed from both transcript evidence and listening;
5. license approval;
6. a separate implementation boundary for model registry, Setup, runtime isolation, Voice assignment, migration, and failure recovery.
