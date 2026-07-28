'use strict';

const {
  rosterImportFocusedPayload,
  rosterImportFullPayload,
  rosterReconciliationPayload,
} = require('./cast_profile_fixture_roster.js');

const json = (value) => JSON.stringify(value);

function handleTaskApi(context) {
  const { url, request, receipt, finish, control } = context;
  if (url.pathname === '/api/tasks/registry' && request.method === 'GET') {
    finish(200, json({
      schema_version: 2,
      tasks: [
        { task_type: 'complete_cast_dossier', label: 'Create complete Cast dossier' },
        { task_type: 'roster_discovery', label: 'Discover full-Cast evidence' },
        { task_type: 'roster_reconciliation', label: 'Reconcile and enrich full Character roster' },
        { task_type: 'persona_catalog_generation', label: 'Create voice profiles for all speaking identities' },
      ],
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/tasks/export' && request.method === 'POST') {
    finish(200, json({
      task_id: `task-${receipt.body?.task_type || 'fixture'}`,
      task_type: receipt.body?.task_type,
      download_url: `/api/tasks/${receipt.body?.task_type || 'fixture'}/download`,
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/tasks/import' && request.method === 'POST') {
    control.taskImported = true;
    const focused = rosterImportFocusedPayload();
    finish(200, json({
      kind: 'structured_result', status: 'inspected',
      task_type: 'roster_discovery', task_label: 'Discover full-Cast evidence',
      candidate_id: focused.candidate_id,
      result_fingerprint: focused.result_fingerprint,
      reconciliation: focused,
      routing: {
        status: 'awaiting_reconciliation', native_destination: 'cast',
        tab: 'characters', target_id: 'cast:issues',
        message: 'One safe roster addition is ready for native review.',
      },
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/character_roster/import-reconciliation' && request.method === 'GET') {
    finish(200, json(
      control.taskImported ? rosterImportFullPayload() : { schema_version: 1, status: 'none' },
    ), 'application/json');
    return true;
  }
  if (url.pathname === '/api/character_roster/reconciliation' && request.method === 'GET') {
    finish(200, json(
      control.taskImported
        ? rosterReconciliationPayload(control)
        : {
          schema_version: 1, state: 'approved', pending_import: null,
          current: { kind: 'approved', draft_fingerprint: null, approved_fingerprint: 'c'.repeat(64), working_draft: false },
          safe_changes: [], issues: [], summary: { issue_count: 0, safe_change_count: 0, working_draft: false, approved: true },
          approval: { blocked: true, can_approve_resolved: false, can_approve_with_unresolved: false },
        },
    ), 'application/json');
    return true;
  }
  if (url.pathname === '/api/character_roster/reconciliation/apply' && request.method === 'POST') {
    control.rosterDraftApplied = true;
    finish(200, json({
      status: 'applied',
      draft: { draft_fingerprint: 'd'.repeat(64) },
      reconciliation: rosterReconciliationPayload(control),
      enrichment: {
        schema_version: 1, candidate_id: 'structured-roster-fixture',
        draft_fingerprint: 'd'.repeat(64), relationships_included: true,
        options: {
          create_designed_voice_profiles: receipt.body?.create_designed_voice_profiles !== false,
          discover_visual_details: receipt.body?.discover_visual_details !== false,
        },
        state: 'pending_roster_approval', plan_fingerprint: 'p0'.padEnd(64, '0'),
        steps: {
          relationships: { state: 'included_in_roster_draft', required: true },
          designed_voice_profiles: { state: 'pending_roster_approval' },
          visual_details: { state: 'pending_roster_approval' },
        },
      },
      routing: {
        status: 'review_ready', native_destination: 'cast', target_id: 'cast:issues',
        message: 'Relationships are included now. Selected Voice and visual enrichment will start after approval.',
      },
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/character_roster/reconciliation/approve' && request.method === 'POST') {
    control.rosterApproved = true;
    finish(200, json({
      status: 'replaced',
      approved: { roster_fingerprint: 'a'.repeat(64) },
      reconciliation: { ...rosterReconciliationPayload(control), state: 'approved' },
      enrichment: {
        schema_version: 1, candidate_id: 'structured-roster-fixture',
        draft_fingerprint: 'd'.repeat(64), relationships_included: true,
        options: {
          create_designed_voice_profiles: true,
          discover_visual_details: true,
        },
        state: 'ready', approved_roster_fingerprint: 'a'.repeat(64),
        plan_fingerprint: 'p1'.padEnd(64, '1'),
        steps: {
          relationships: { state: 'complete', required: true },
          designed_voice_profiles: { state: 'pending_roster_approval' },
          visual_details: { state: 'pending_roster_approval' },
        },
      },
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/character_roster/enrichment/start' && request.method === 'POST') {
    control.enrichmentStarted = true;
    control.enrichmentReads = 0;
    finish(200, json({
      status: 'started', relationships_included: true,
      options: {
        create_designed_voice_profiles: true,
        discover_visual_details: true,
      },
      entry_count: 2,
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/character_roster/enrichment' && request.method === 'GET') {
    control.enrichmentReads += 1;
    if (!control.enrichmentStarted) {
      finish(200, json({
        schema_version: 1, status: 'absent', running: false, stage: 'idle',
        logs: [], plan: null,
      }), 'application/json');
      return true;
    }
    finish(200, json({
      schema_version: 1, status: 'complete', running: false, stage: 'complete',
      logs: ['Designed Voice profiles completed.', 'Visual dossier discovery completed.'],
      plan: {
        state: 'complete', relationships_included: true,
        steps: {
          relationships: { state: 'complete' },
          designed_voice_profiles: { state: 'complete' },
          visual_details: { state: 'complete' },
        },
      },
    }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/speaker_management/status' && request.method === 'GET') {
    finish(200, json({
      available: true,
      script_fingerprint: 'fixture-script',
      entries: [{
        character_id: 'cast:clara', display_name: 'Clara Leighton',
        canonical_name: 'Clara Leighton', resolution_status: 'resolved', line_count: 18,
      }],
    }), 'application/json');
    return true;
  }
  return false;
}

module.exports = { handleTaskApi };
