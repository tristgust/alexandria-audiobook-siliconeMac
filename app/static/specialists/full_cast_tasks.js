'use strict';

import { resultMessage } from '/static/pages/more.js';
import { createTaskImportSurface } from '/static/components/task_import_surface.js';
import {
  renderRosterDraftApproval,
  renderRosterImportReview,
} from './roster_import_review.js';
import { renderCompletedCastAudit } from './full_cast_dossier_review.js';
import { renderFullCastImportedResult } from './full_cast_import_result.js';
import { createFullCastTaskExports } from './full_cast_task_exports.js';
import { isCompletedCastPackage } from '/static/cast_dossier_state.js';

const UI = globalThis.AlexandriaUI;

export async function createFullCastTasks({
  api, signal, shell, route, report, onReviewMode,
}) {
  const section = await createFullCastTaskExports({ api, signal, report });
  let importer;
  let reviewing = false;
  const hideFreshTaskEntry = () => {
    section.hidden = true;
    importer?.section.classList.add('task-import-surface--reviewing');
  };
  const enterCompletedMode = () => {
    section.replaceChildren();
    section.hidden = true;
    importer.section.replaceChildren(importer.resultHost);
    importer.section.classList.add('task-import-surface--reviewing');
  };
  const enterReviewMode = () => {
    reviewing = true;
    hideFreshTaskEntry();
    onReviewMode?.();
  };
  importer = createTaskImportSurface({
    api, signal,
    title: 'Import a completed ZIP',
    description: 'Drop in the ZIP ChatGPT returns. Alexandria validates it and opens the correct review.',
    report,
    onImported: async (data, host) => {
      if (data.task_type === 'roster_discovery' && data.candidate_id) {
        enterReviewMode();
        await renderRosterImportReview({ api, signal, candidate: data, host, report });
        return;
      }
      if (data.task_type === 'complete_cast_dossier'
        && isCompletedCastPackage(data.cast_dossier_package)) {
        enterCompletedMode();
      }
      await renderFullCastImportedResult({ data, host, shell, route, api, signal });
    },
  });
  const resume = await api.get('/api/character_roster/reconciliation', { signal });
  if (!resume.ok) {
    enterReviewMode();
    importer.status.textContent = 'Cast resume state could not be checked.';
    importer.resultHost.replaceChildren(UI.notice({
      tone: 'error',
      title: 'Full Cast tasks could not be resumed safely',
      body: resultMessage(resume, 'Reload and retry before creating or importing another Cast task.'),
      action: UI.button({
        label: 'Reload and retry',
        variant: 'secondary',
        onClick: () => window.location.reload(),
      }),
      live: true,
    }));
  } else if (resume.data?.pending_import?.candidate_id) {
    enterReviewMode();
    importer.status.textContent = 'A previously imported Cast result is waiting for review.';
    await renderRosterImportReview({
      api, signal,
      candidate: {
        candidate_id: resume.data.pending_import.candidate_id,
        task_type: 'roster_discovery',
        reconciliation: resume.data.pending_import,
        cast_dossier_package: resume.data.cast_dossier_package || null,
      },
      host: importer.resultHost,
      report,
    });
  } else if (
    isCompletedCastPackage(resume.data?.cast_dossier_package)
  ) {
    importer.resultHost.replaceChildren(renderCompletedCastAudit(resume.data));
    enterCompletedMode();
  } else if (
    resume.data?.current?.working_draft
    && resume.data?.approval?.draft_fingerprint
  ) {
    enterReviewMode();
    importer.status.textContent = 'Your roster decisions are saved and waiting for approval.';
    await renderRosterDraftApproval({
      api,
      signal,
      status: resume.data,
      host: importer.resultHost,
      report,
    });
  } else if (
    resume.data?.cast_dossier_package?.activation?.ready === true
  ) {
    enterReviewMode();
    importer.status.textContent = 'Your approved Cast dossier is ready for final identity review.';
    await renderFullCastImportedResult({
      data: {
        task_type: 'complete_cast_dossier',
        cast_dossier_package: resume.data.cast_dossier_package,
      },
      host: importer.resultHost,
      shell,
      route,
      api,
      signal,
    });
  }
  return { section, importer: importer.section, reviewing };
}
