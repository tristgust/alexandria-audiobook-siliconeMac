'use strict';

const UI = globalThis.AlexandriaUI;

export function openReviewedDifferenceDialog({ event, onConfirm }) {
  UI.dialog({
    title: 'Approve reviewed source differences?',
    body: 'This imported Script does not exactly match the prepared source or its speaker boundaries. Alexandria will preserve the exact reviewed Script, record the audit difference in the accepted-version receipt, and continue to Cast.',
    confirmLabel: 'Approve reviewed version',
    onConfirm,
  }).open(event?.currentTarget || document.activeElement);
}

export async function requestScriptAcceptance({
  api, signal, endpoint, lifecycle, reviewOverride, reviewedOverride,
}) {
  const fingerprints = lifecycle.fingerprints || {};
  const result = await api.post(endpoint, {
    expected_script_fingerprint: fingerprints.script,
    expected_metadata_fingerprint: fingerprints.metadata,
    expected_source_fingerprint: fingerprints.source,
    expected_state_fingerprint: lifecycle.state_fingerprint,
    allow_reviewed_source_differences: reviewedOverride,
    expected_audit_fingerprint: reviewedOverride
      ? reviewOverride?.auditFingerprint || null : null,
  }, { signal });
  if (signal.aborted) return { kind: 'aborted' };
  if (result.ok) return {
    kind: 'accepted',
    lifecycle: { ...lifecycle, ...result.data, accepted: true, state: 'accepted', blockers: [] },
  };

  const detail = result.data?.detail && typeof result.data.detail === 'object'
    ? result.data.detail : {};
  const blocking = detail.context?.blocking_issues;
  if (detail.code === 'script_acceptance_blocked' && Array.isArray(blocking)) {
    return {
      kind: 'review',
      auditIssues: blocking,
      reviewOverride: detail.context?.reviewed_override_available
        && detail.context?.audit_fingerprint ? {
          auditFingerprint: detail.context.audit_fingerprint,
          scriptFingerprint: fingerprints.script,
          sourceFingerprint: fingerprints.source,
        } : null,
    };
  }
  if (detail.code === 'script_review_override_stale') return { kind: 'stale' };
  return { kind: 'error', message: detail.message || result.error };
}
