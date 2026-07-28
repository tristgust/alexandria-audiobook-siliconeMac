'use strict';

import { resultMessage, textNode } from '/static/pages/more.js';

const UI = globalThis.AlexandriaUI;

function evidenceRow(label, detail, attributes = {}) {
  const row = document.createElement('div');
  row.className = 'support-list-row';
  Object.entries(attributes).forEach(([key, value]) => row.setAttribute(key, value));
  const copy = document.createElement('div');
  copy.append(
    textNode('strong', '', label),
    textNode('p', 'support-status-copy', detail),
  );
  row.append(copy);
  return row;
}

function scriptEvidence(recovery) {
  const section = document.createElement('div');
  section.className = 'support-list';
  const count = Number(recovery.line_count || 0);
  section.append(evidenceRow(
    'Script evidence',
    `${count.toLocaleString()} Script line${count === 1 ? '' : 's'} under ${recovery.script_speaker}.`,
  ));
  (recovery.sample_lines || []).forEach((line) => {
    section.append(evidenceRow(
      `Script line ${Number(line.index) + 1}`,
      String(line.text || 'No spoken text recorded.'),
      { 'data-speaker-recovery-sample': '' },
    ));
  });
  if (recovery.sample_lines_truncated) section.append(textNode(
    'p', 'metadata', 'Additional spoken lines remain available in Script.',
  ));
  return section;
}

function exclusionAudit(recovery) {
  const audit = document.createElement('details');
  audit.className = 'full-cast-identity-directory';
  audit.dataset.speakerRecoveryExclusionAudit = '';
  audit.open = true;
  const records = recovery.excluded_audit || [];
  audit.append(textNode(
    'summary', '', `Preserved exclusion audit (${records.length})`,
  ));
  const list = document.createElement('div');
  list.className = 'support-list';
  records.forEach((record) => {
    list.append(evidenceRow(
      record.name || recovery.display_name,
      record.reason || 'No exclusion reason was recorded.',
    ));
    (record.evidence || []).slice(0, 5).forEach((item) => list.append(evidenceRow(
      item.source_location || 'Recorded source evidence',
      item.source_quote || 'No source quote was recorded.',
    )));
  });
  if (!records.length) list.append(textNode(
    'p', 'metadata', 'No prior exclusion record matches this Script label.',
  ));
  audit.append(list);
  return audit;
}

function castAction({ recovery, route, shell }) {
  return UI.button({
    label: 'Open Voice editor in Cast',
    variant: 'primary',
    attributes: { 'data-speaker-recovery-open-cast': '' },
    onClick: () => shell.navigate(shell.routes.routeForPath('cast', {
      ...(route.context.project ? { project: route.context.project } : {}),
      character: recovery.active_character_id,
      return: route.hash,
    }).hash),
  });
}

function recoveryAction({ recovery, payload, api, signal, route, shell, feedback }) {
  let busy = false;
  const opener = UI.button({
    label: 'Review recovery',
    variant: 'primary',
    attributes: { 'data-speaker-recovery-review': '' },
  });
  UI.dialog({
    opener,
    title: `Recover ${recovery.display_name}`,
    body: `Create one active Cast identity from ${recovery.script_speaker} and its ${recovery.line_count} Script line${Number(recovery.line_count) === 1 ? '' : 's'}. No production Voice will be assigned.`,
    confirmLabel: 'Recover speaker',
    onConfirm: async () => {
      if (busy) return;
      busy = true;
      opener.disabled = true;
      const result = await api.post('/api/speaker_management/action', {
        operation: 'add',
        expected_script_fingerprint: payload.script_fingerprint,
        payload: {
          script_speaker: recovery.script_speaker,
          display_name: recovery.display_name,
          expected_roster_fingerprint: payload.roster_fingerprint,
          require_exclusion_audit: true,
        },
      }, { signal });
      if (signal.aborted) return;
      feedback.replaceChildren(UI.notice({
        tone: result.ok ? 'success' : 'error',
        title: result.ok ? 'Speaker recovered in Cast' : 'Speaker recovery was rejected',
        body: result.ok
          ? 'The Script-derived identity is active. Its production Voice is still unassigned.'
          : resultMessage(result, 'No identity or Voice changes were made.'),
        live: true,
      }));
      if (result.ok) {
        window.setTimeout(
          () => shell.navigate(route.hash, { historyMode: 'replace' }),
          180,
        );
      } else {
        busy = false;
        opener.disabled = false;
        opener.focus();
      }
    },
  });
  return opener;
}

export function createSpeakerRecovery(context) {
  const { payload, route } = context;
  const recovery = payload.speaker_recovery;
  if (route.context.mode !== 'speaker-recovery' || !recovery) return null;
  const active = Boolean(recovery.active_character_id);
  const panel = document.createElement('section');
  panel.className = 'specialist-section full-cast-task-workspace';
  panel.dataset.speakerRecovery = '';
  panel.dataset.speakerRecoveryState = recovery.state
    || (active ? 'active' : recovery.eligible ? 'eligible' : 'blocked');
  panel.append(
    textNode('h2', '', 'Recover Script speaker'),
    textNode(
      'p', 'support-status-copy',
      active
        ? 'This Script-derived identity is active in Cast. Assign and review its production Voice there.'
        : recovery.state === 'blocked_no_audit'
          ? 'This spoken Script label has no preserved exclusion audit. Review the current roster before adding an identity.'
          : 'Review the exact spoken evidence before restoring this label to the active Cast. Its exclusion audit remains unchanged.',
    ),
    UI.status({
      label: active ? 'Active in Cast' : recovery.eligible ? 'Ready to recover' : 'Recovery blocked',
      tone: active ? 'success' : recovery.eligible ? 'warning' : 'neutral',
    }),
    scriptEvidence(recovery),
    exclusionAudit(recovery),
  );
  const feedback = document.createElement('div');
  if (active) panel.append(castAction({ ...context, recovery }));
  else if (recovery.eligible) panel.append(recoveryAction({
    ...context, recovery, feedback,
  }));
  else panel.append(UI.notice({
    tone: 'warning',
    title: 'This speaker cannot be recovered yet',
    body: recovery.blocked_reason || 'Refresh the current roster and review this identity again.',
  }));
  panel.append(feedback);
  return panel;
}
