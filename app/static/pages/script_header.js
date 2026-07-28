'use strict';

import { approvalState } from './script_review_model.js';
import { importCandidateState } from './script_import_candidate.js';

export function scriptHeaderState({
  model, issues, goToCast, applyImportedScript, confirmReviewedDifferences, approve,
  continueAttribute, approveAttribute, applyImportAttribute,
  approveLabel, reviewRequiredLabel,
}) {
  const lifecycle = model.lifecycle || {};
  const accepted = lifecycle.accepted || lifecycle.state === 'accepted';
  const { ready: importReady, invalid: importInvalid } = importCandidateState(model);
  const approval = approvalState(lifecycle, issues);
  const reviewedOverrideReady = Boolean(model.reviewOverride?.auditFingerprint);
  const primaryAction = accepted ? {
    label: 'Continue to Cast',
    attributes: { [continueAttribute]: '' },
    onClick: goToCast,
  } : importReady ? {
    label: 'Apply imported Script',
    attributes: { [applyImportAttribute]: '' },
    onClick: applyImportedScript,
  } : reviewedOverrideReady ? {
    label: 'Approve reviewed differences',
    description: 'Approve this exact imported Script after reviewing its recorded source differences.',
    attributes: { [approveAttribute]: '', 'data-script-reviewed-override': '' },
    onClick: confirmReviewedDifferences,
  } : {
    label: approveLabel,
    disabled: importInvalid || !approval.canApprove,
    description: importInvalid ? model.importCandidate.message : approval.reason,
    attributes: { [approveAttribute]: '' },
    onClick: approve,
  };
  return {
    accepted,
    primaryAction,
    status: {
      tone: accepted ? 'success'
        : importInvalid || issues.some((issue) => issue.blocking) ? 'warning' : 'information',
      label: accepted ? 'Approved'
        : importReady ? 'Import review'
          : importInvalid ? 'Import unavailable'
            : issues.length ? reviewRequiredLabel : 'Ready for approval',
    },
  };
}
