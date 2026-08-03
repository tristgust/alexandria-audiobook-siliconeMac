# Alexandria documentation

This is the canonical map for Alexandria documentation. The root `README.md`
is the product and installation overview. The detailed execution roadmap and
completion state live under the repository-local `.omo` control plane.

## Authorities

- **Master roadmap:** `.omo/plans/alexandria-apple-silicon-native-ollama-master-plan.md`
- **Completion ledger:** `.omo/state/ledger.md`
- **Live coordination:** `.omo/state/hello.md`
- **Machine-readable state:** `.omo/state/boulder.json`
- **Detailed proof:** `.omo/evidence/`
- **Canonical local folders:** [Canonical workspace](CANONICAL_WORKSPACE.md)

The roadmap stays detailed and authoritative. The ledger is a concise
chronology of completed work, commits, decisions and evidence; it does not
replace or summarize away roadmap requirements.

## Product and workflow

- [Project flow](PROJECT_FLOW.md)
- [Projects and Project Home](PROJECTS.md)
- [Script lifecycle](SCRIPT_LIFECYCLE.md)
- [ChatGPT Task Bundles](TASK_BUNDLES.md)
- [Cast aggregate](CAST_AGGREGATE.md)
- [Produce aggregate](PRODUCE_AGGREGATE.md)
- [Export aggregate](EXPORT_AGGREGATE.md)
- [Navigation routes](NAVIGATION_ROUTES.md)
- [Library](LIBRARY.md)
- [Settings](SETTINGS.md)
- [Templates](TEMPLATES.md)
- [Maintenance](MAINTENANCE.md)
- [Help Center](HELP_CENTER.md)
- [Interface design](INTERFACE_DESIGN.md)
- [Interface acceptance](INTERFACE_ACCEPTANCE.md)

## Script, identity and Voice systems

- [Source fidelity audit](FIDELITY_AUDIT.md)
- [Resumable Script generation](RESUMABLE_GENERATION.md)
- [Generation metadata](GENERATION_METADATA.md)
- [Character roster](CHARACTER_ROSTER.md)
- [Roster reconciliation](ROSTER_RECONCILIATION.md)
- [Speaker management](SPEAKER_MANAGEMENT.md)
- [Persona and visual references](PERSONA_AND_VISUAL_REFS.md)
- [Voice types](VOICE_TYPES.md)
- [Five recurring Voices](FIVE_RECURRING_VOICES.md)
- [Accent pipeline](ACCENT_PIPELINE.md)
- [Fish S2.1 cloud provider](FISH_S21_CLOUD_PROVIDER.md)

## Audio generation and integrity

- [Audio artifact integrity](AUDIO_ARTIFACTS.md)
- [Exact-once audio lifecycle](AUDIO_GENERATION_LIFECYCLE.md)
- [Immutable Takes](AUDIO_TAKES.md)
- [Approved adaptation audio](APPROVED_ADAPTATION_AUDIO.md)
- [Pronunciation provenance](PRONUNCIATION.md)
- [Synthesis windows and seams](SYNTHESIS_WINDOWS.md)

## Training and evaluation

- [Dataset Builder](DATASET_BUILDER.md)
- [Instruction dataset](INSTRUCTION_DATASET.md)
- [Instruction propagation](INSTRUCTION_PROPAGATION.md)
- [Voice training](VOICE_TRAINING.md)
- [LoRA on Apple Silicon](LORA_APPLE_SILICON.md)
- [Benchmarking](BENCHMARKING.md)

## Platform and operations

- [Apple Silicon](APPLE_SILICON.md)
- [Native Ollama](NATIVE_OLLAMA.md)
- [Project migration](MIGRATION.md)
- [Updating the fork](UPDATING_FORK.md)
- [Local archives](LOCAL_ARCHIVES.md)

## Generated user help

`help/` contains the versioned offline Help Center pages consumed by the app.
Their hashes are guarded by `help/manifest.json`; they are not duplicate
developer documentation.

## Research and history

- `research/` contains current technical decision synthesis that still informs
  roadmap work.
- `history/` contains completed acceptance contracts retained for traceability.
- Completed focused plans and invalid supplied references belong under
  `.omo/evidence/`, not beside the active master roadmap.

## Documentation rules

1. Put requirements and task status in the master roadmap, not in a new plan.
2. Put concise completed-task facts in the ledger; put full logs and matrices in
   evidence.
3. Do not create a second setup guide, interface specification, roadmap,
   Boulder or coordination file.
4. Update an existing focused document when its subject already has one.
5. Move completed acceptance-only material to `history/`; delete only when its
   useful content and evidence are already preserved elsewhere.
