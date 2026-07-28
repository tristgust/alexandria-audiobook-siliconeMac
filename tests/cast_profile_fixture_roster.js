'use strict';

function rosterImportFullPayload() {
  return {
    schema_version: 1,
    status: 'pending',
    candidate_id: 'structured-roster-fixture',
    result_fingerprint: 'r'.repeat(64),
    task_id: 'task-roster-fixture',
    task_label: 'Discover full-Cast evidence',
    current_kind: 'approved',
    current_fingerprint: 'c'.repeat(64),
    current_entries: [{
      id: 'character_current', canonical_name: 'Edmund Fairfax',
      display_name: 'Edmund Fairfax', entity_kind: 'person',
      speaking_status: 'speaker', resolution_status: 'resolved',
      aliases: [], nicknames: [], confidence: 0.98,
    }],
    observations: [{
      import_id: 'import_clara', index: 0, identity_seed: 'clara-leighton',
      canonical_name: 'Clara Leighton', display_name: 'Clara Leighton',
      entity_kind: 'person', speaking_status: 'speaker', confidence: 0.95,
      aliases: ['C. Leighton'], nicknames: [], resolution_status: 'resolved',
      voice_clues: ['Measured alto', 'Restrained warmth'],
      sample_lines: ['I knew the letter would arrive before dusk.'],
      proposed_action: 'add', proposed_current_entry_id: null,
      proposal_reason: 'No current roster identity shares its names or aliases.',
      current_matches: [], matched_labels: [], native_semantic_status: 'valid',
      repaired_evidence_count: 0, invalid_evidence_count: 0,
      entry: {
        id: 'character_import_clara', canonical_name: 'Clara Leighton',
        display_name: 'Clara Leighton', entity_kind: 'person',
        speaking_status: 'speaker', titles: ['Dr.'], aliases: ['C. Leighton'],
        nicknames: [], pronouns: ['she/her'], species: ['human'],
        relationships: ['Sister of Edmund Fairfax'], confidence: 0.95,
        resolution_status: 'resolved', possible_duplicate_ids: [],
        mistaken_merge_risk: false, unresolved_questions: [],
        voice_clues: ['Measured alto', 'Restrained warmth'],
        sample_lines: ['I knew the letter would arrive before dusk.'],
        first_evidence_location: 'source offset 10', additional_evidence_locations: [],
        evidence: [{
          category: 'appearance', source_quote: 'her dark hair',
          source_location: 'source offset 10', start_char: 10, end_char: 23,
          confidence: 0.95, basis: 'explicit',
        }],
      },
    }],
    warnings: [],
    summary: {
      current_entries: 1, imported_observations: 1,
      proposed_merges: 0, proposed_additions: 1,
      proposed_exclusions: 0, unresolved: 0, groups: 0,
      aliases: 1, relationships: 1, voice_clues: 2,
      appearance_evidence: 1, speaking_identities: 1,
      evidence_repairs: 0, evidence_issues: 0, semantic_invalid: 0,
    },
  };
}

function rosterImportFocusedPayload() {
  const full = rosterImportFullPayload();
  return {
    schema_version: 1, status: 'pending',
    candidate_id: full.candidate_id,
    result_fingerprint: full.result_fingerprint,
    task_id: full.task_id, task_label: full.task_label,
    current_kind: full.current_kind,
    current_fingerprint: full.current_fingerprint,
    current_entries: full.current_entries,
    safe_changes: [{
      import_id: 'import_clara', action: 'add', current_entry_id: null,
      canonical_name: 'Clara Leighton', display_name: 'Clara Leighton',
      reason: 'No current roster identity shares its names or aliases.',
    }],
    safe_decisions: [{ import_id: 'import_clara', action: 'add', current_entry_id: null }],
    issues: [], warnings: [],
    summary: {
      ...full.summary, safe_change_count: 1, safe_merge_count: 0,
      safe_addition_count: 1, issue_count: 0,
      repaired_evidence_issue_count: 0, duplicate_issue_count: 0,
      unresolved_issue_count: 0,
    },
    apply_ready: true,
  };
}

function rosterReconciliationPayload(control) {
  const focused = rosterImportFocusedPayload();
  if (!control.rosterDraftApplied) {
    return {
      schema_version: 1, state: 'import_ready',
      current: {
        kind: 'approved', draft_fingerprint: null,
        approved_fingerprint: 'c'.repeat(64), working_draft: false,
      },
      pending_import: focused, safe_changes: focused.safe_changes,
      issues: [], summary: {
        issue_count: 0, blocking_issue_count: 0,
        unresolved_acknowledgement_count: 0,
        safe_change_count: 1, working_draft: false, approved: true,
      },
      approval: {
        blocked: true, mode: 'replacement', draft_fingerprint: null,
        expected_approved_fingerprint: 'c'.repeat(64),
        requires_unresolved_acknowledgement: false,
        can_approve_resolved: false, can_approve_with_unresolved: false,
      },
    };
  }
  return {
    schema_version: 1, state: 'ready_to_approve', pending_import: null,
    safe_changes: [], issues: [],
    current: {
      kind: 'draft', draft_fingerprint: 'd'.repeat(64),
      approved_fingerprint: 'c'.repeat(64), working_draft: true,
    },
    summary: {
      issue_count: 0, blocking_issue_count: 0,
      unresolved_acknowledgement_count: 0,
      safe_change_count: 0, working_draft: true, approved: true,
    },
    approval: {
      blocked: false, mode: 'replacement', draft_fingerprint: 'd'.repeat(64),
      expected_approved_fingerprint: 'c'.repeat(64),
      requires_unresolved_acknowledgement: false,
      can_approve_resolved: true, can_approve_with_unresolved: true,
    },
  };
}

module.exports = {
  rosterImportFocusedPayload,
  rosterImportFullPayload,
  rosterReconciliationPayload,
};
