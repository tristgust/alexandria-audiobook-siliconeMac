'use strict';

import { isCompletedCastPackage } from './cast_dossier_state.js';
import { renderStableCompletedCast } from './stable_cast_dossier_activation.js';
import { renderDirectDossierActivation } from './stable_cast_dossier_direct_activation.js';
import { button, text } from './stable_full_cast_dom.js';
import { completeCastPanel, taskCard, TASKS } from './stable_full_cast_exports.js';
import { buildImportSurface } from './stable_full_cast_import.js';

let skipPendingResumeOnce = false;

function installStyles() {
  if (document.querySelector('style[data-stable-full-cast-style]')) return;
  const style = document.createElement('style');
  style.dataset.stableFullCastStyle = '';
  style.textContent = `
    .stable-task-workspace,.stable-task-section,.stable-task-intro,
    .stable-task-card__copy,.stable-task-result,.stable-roster-review-header,
    .stable-roster-row__identity,.stable-roster-row__facts,
    .stable-roster-row__decision,.stable-roster-row__facts>div,
    .stable-roster-enrichment,.stable-roster-approval-card,.stable-roster-feedback {
      display:grid;min-width:0;gap:8px
    }
    .stable-task-workspace{gap:24px}.stable-task-section{gap:16px}
    .stable-task-eyebrow{color:#176b6b;font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
    .stable-task-muted{margin:0;color:#68635d;font-size:.88rem;line-height:1.45;overflow-wrap:anywhere}
    .stable-task-section h3,.stable-task-intro h3,.stable-roster-review-header h3,
    .stable-roster-enrichment h3,.stable-roster-approval-card h3,.stable-task-card h4 {
      margin:0;font-family:Georgia,serif;color:#23211e
    }
    .stable-task-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}
    .stable-task-card{display:grid;min-width:0;grid-template-columns:36px minmax(0,1fr);align-content:start;gap:12px;padding:16px;border:1px solid #d8d0c5;border-radius:10px;background:#fffdf9}
    .stable-task-card--audit{grid-template-columns:minmax(0,1fr);gap:6px}
    .stable-task-card--audit>strong,.stable-task-card--audit>span{min-width:0;overflow-wrap:anywhere}
    .stable-task-card__step,.stable-task-step__number{display:grid;width:36px;height:36px;place-items:center;border-radius:50%;background:#e4f0ed;color:#176b6b;font-weight:700}
    .stable-task-card>.btn,.stable-task-card__result{grid-column:2;justify-self:start}
    .stable-task-card__result{gap:8px}
    .stable-task-divider{height:1px;background:#d8d0c5}
    .stable-task-import{display:grid;gap:14px;padding-block:20px 0;border-top:1px solid #d8d0c5;background:transparent}
    .stable-task-steps{display:flex;min-width:0;align-items:baseline;flex-wrap:wrap;gap:6px 12px;padding:0;margin:0;color:#68635d;list-style:none}
    .stable-task-steps li{display:flex;min-width:0;align-items:baseline;gap:4px;padding:0;border:0}.stable-task-steps li+li:before{content:'→';margin-right:8px;color:#9d9285}.stable-task-steps li>span:last-child:before{content:'· '}
    .stable-task-steps .stable-task-step__number{display:inline;width:auto;height:auto;border-radius:0;background:transparent;color:#8a837b;font-size:.78rem;font-weight:600}.stable-task-steps li strong,.stable-task-steps li>span:last-child{font-size:.78rem;line-height:1.35}
    .stable-task-dropzone{display:grid;width:100%;min-height:72px;grid-template-columns:36px minmax(0,1fr) auto;align-items:center;gap:12px;padding:12px 8px;border:0;border-block:1px solid #d8d0c5;border-radius:0;background:transparent;color:#23211e;cursor:pointer;font:inherit;text-align:left}
    .stable-task-dropzone:hover,.stable-task-dropzone[data-dragging=true]{background:#f3f7f5}
    .stable-task-dropzone:focus-visible{outline:3px solid rgba(23,107,107,.3);outline-offset:2px}
    .stable-task-dropzone__icon{display:grid;width:36px;height:36px;place-items:center;color:#176b6b;font-size:1.05rem}
    .stable-task-dropzone__copy{display:grid;gap:4px}.stable-task-dropzone__action{color:#176b6b;font-weight:700}
    .stable-task-selected{display:grid;grid-template-columns:36px minmax(0,1fr) auto;align-items:center;gap:12px;padding:12px 8px;border-block:1px solid #d8d0c5;background:transparent}
    .stable-task-selected[hidden],.stable-task-dropzone[hidden]{display:none}
    .stable-task-selected__copy{display:grid;min-width:0;gap:3px}
    .stable-task-fallback{padding-top:10px;border-top:1px solid #d8d0c5}
    .stable-task-fallback summary{cursor:pointer;color:#68635d;font-weight:600}.stable-task-fallback__body{display:grid;gap:10px;padding:12px 0 0}
    .stable-task-fallback label{display:grid;gap:6px}.stable-task-status{display:inline-flex;width:max-content;max-width:100%;padding:3px 8px;border-radius:999px;font-size:.76rem;font-weight:700}
    .stable-task-status--success{background:#e8f3e8;color:#2d6335}.stable-task-status--warning{background:#f5ead6;color:#75541e}
    .stable-task-error{margin:0;color:#8b3131;font-weight:600}
    .stable-roster-repair-note{padding-block:8px;border-block:1px solid #d8d0c5}
    .stable-roster-metrics{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));border-block:1px solid #d8d0c5}.stable-roster-approval-summary{grid-template-columns:repeat(3,minmax(0,1fr))}.stable-roster-approval-summary .stable-roster-metric:nth-child(3){border-right:0}
    .stable-roster-metric{display:grid;gap:2px;padding:10px;border-right:1px solid #d8d0c5}.stable-roster-metric:last-child{border-right:0}.stable-roster-metric span{color:#68635d;font-size:.72rem;text-transform:uppercase}.stable-roster-metric strong{font-family:Georgia,serif;font-size:1.3rem}
    .stable-roster-list{display:grid;max-height:min(610px,54vh);overflow-y:auto;border-block:1px solid #d8d0c5;scrollbar-gutter:stable}.stable-roster-list--decisions{max-height:min(390px,44vh)}
    .stable-roster-decision-header{display:grid;gap:4px}.stable-roster-decision-header h3{margin:0;font-family:Georgia,serif}.stable-roster-decision-help{max-width:720px;padding-left:12px;border-left:3px solid #b9afa2;line-height:1.45}
    .stable-roster-row{display:grid;grid-template-columns:minmax(160px,.7fr) minmax(270px,1.3fr) minmax(180px,.8fr);gap:16px;padding:16px 0;border-bottom:1px solid #d8d0c5}.stable-roster-row:last-child{border-bottom:0}.stable-roster-row--incomplete{background:#f8efdf;outline:3px solid rgba(139,92,28,.28);outline-offset:-3px}
    .stable-roster-row__facts{grid-template-columns:repeat(3,minmax(0,1fr))}.stable-task-label{color:#68635d;font-size:.72rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
    .stable-roster-tags{display:flex;flex-wrap:wrap;gap:4px}.stable-roster-tags>span:not(.stable-task-muted){max-width:100%;padding:2px 7px;border-radius:999px;background:#e4f0ed;font-size:.76rem;overflow-wrap:anywhere}
    .stable-roster-safe-changes,.stable-roster-dossier-attachment{padding-block:8px;border-block:1px solid #d8d0c5}.stable-roster-safe-changes>summary,.stable-roster-dossier-attachment>summary{display:flex;min-height:36px;align-items:center;justify-content:space-between;gap:12px;cursor:pointer}.stable-roster-safe-changes>summary strong,.stable-roster-dossier-attachment>summary strong{font-family:Georgia,serif}.stable-roster-safe-changes__list{display:grid;max-height:220px;grid-template-columns:repeat(2,minmax(0,1fr));gap:0 20px;overflow-y:auto;padding-block:8px}.stable-roster-safe-change{display:flex;min-width:0;align-items:baseline;justify-content:space-between;gap:10px;padding-block:7px;border-bottom:1px solid #d8d0c5}.stable-roster-safe-change>strong{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.stable-roster-dossier-attachment__body{display:grid;gap:12px;padding-block:12px}.stable-roster-dossier-preview{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));border-block:1px solid #d8d0c5}.stable-roster-dossier-preview__item{display:grid;gap:4px;padding:12px;border-right:1px solid #d8d0c5;border-bottom:1px solid #d8d0c5}.stable-roster-dossier-preview__item:nth-child(2n){border-right:0}.stable-roster-dossier-preview__item:nth-last-child(-n+2){border-bottom:0}.stable-roster-dossier-preview__item p{margin:0}.stable-roster-dossier-preview__definition{margin:0;color:#68635d;font-size:.8rem;line-height:1.45}
    .stable-roster-enrichment{padding-block:12px;border-block:1px solid #d8d0c5;background:transparent}.stable-roster-enrichment-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:10px}
    .stable-roster-enrichment-card{display:grid;grid-template-columns:22px minmax(0,1fr);align-items:start;gap:10px;padding:12px;border-left:3px solid #176b6b;background:#f4f0e8}.stable-roster-enrichment-card>span{display:grid;gap:4px}.stable-roster-enrichment-card input{width:18px;height:18px;margin-top:2px}
    .stable-roster-enrichment--attached{display:grid;gap:8px}.stable-roster-enrichment--attached>p{margin:0}.stable-roster-enrichment-grid--attached{grid-template-columns:repeat(2,minmax(0,1fr));gap:0;border-block:1px solid #d8d0c5}.stable-roster-enrichment-card--attached{border-left:0;border-right:1px solid #d8d0c5;background:transparent}.stable-roster-enrichment-card--attached:last-child{border-right:0}
    .stable-roster-approval-card{padding:16px;border:1px solid #aac6ac;border-radius:10px;background:#edf6ed}.stable-roster-approval-card .btn{justify-self:start}
    .stable-complete-cast{display:grid;border-bottom:1px solid #d8d0c5;background:transparent}
    .stable-complete-cast__choice-copy,.stable-complete-cast__result{display:grid;min-width:0}.stable-complete-cast__legend{padding:0 8px 0 0;color:#68635d;font-size:.72rem;font-weight:700;letter-spacing:.08em;text-transform:uppercase}
    .stable-complete-cast__choices{padding:0;margin:0;border:0;border-top:1px solid #d8d0c5}.stable-complete-cast__choice{display:grid;min-width:0;grid-template-columns:20px minmax(0,1fr) max-content;align-items:start;gap:16px;padding:14px 8px;border-bottom:1px solid #d8d0c5;cursor:pointer}.stable-complete-cast__choice:hover{background:#f3f7f5}.stable-complete-cast__choice:focus-within{background:#edf6f3}.stable-complete-cast__choice input{width:18px;height:18px;margin:2px 0 0;accent-color:#176b6b}.stable-complete-cast__choice-copy{gap:4px}.stable-complete-cast__choice-copy>strong{font-size:.94rem}.stable-complete-cast__destination{align-self:center;color:#68635d;font-size:.78rem;font-weight:600;white-space:nowrap}
    .stable-visual-identity-review{display:grid;gap:8px;padding-block:16px;border-block:1px solid #d8d0c5}.stable-visual-identity-review h4{margin:0;font-family:Georgia,serif}.stable-visual-identity-review__list{display:grid;max-height:min(360px,38vh);overflow-y:auto}.stable-visual-identity-review__row{display:grid;grid-template-columns:minmax(160px,.7fr) minmax(260px,1.3fr);align-items:start;gap:16px;padding:12px 0;border-bottom:1px solid #d8d0c5}.stable-visual-identity-review__row:last-child{border-bottom:0}.stable-visual-identity-review__identity,.stable-visual-identity-review__decision{display:grid;min-width:0;gap:6px}.stable-visual-identity-review__identity strong,.stable-visual-identity-review__identity span{overflow-wrap:anywhere}.stable-visual-identity-review__consequence{padding-left:10px;border-left:3px solid #b9afa2}
    .stable-complete-cast__actions{display:flex;align-items:center;justify-content:space-between;gap:16px;padding-block:16px}.stable-complete-cast__result{gap:8px;justify-items:start;padding-bottom:16px}.stable-individual-tasks{padding-block:10px;border-block:1px solid #d8d0c5}.stable-individual-tasks>summary{min-height:36px;cursor:pointer;font-weight:700}
    @media(max-width:900px){.stable-task-grid,.stable-roster-enrichment-grid,.stable-roster-safe-changes__list,.stable-roster-dossier-preview{grid-template-columns:1fr}.stable-roster-enrichment-grid--attached{grid-template-columns:1fr}.stable-roster-enrichment-card--attached{border-right:0;border-bottom:1px solid #d8d0c5}.stable-roster-enrichment-card--attached:last-child{border-bottom:0}.stable-roster-dossier-preview__item{border-right:0}.stable-roster-dossier-preview__item:nth-last-child(-n+2){border-bottom:1px solid #d8d0c5}.stable-roster-dossier-preview__item:last-child{border-bottom:0}.stable-roster-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}.stable-roster-row,.stable-visual-identity-review__row{grid-template-columns:1fr}}
    @media(max-width:520px){.stable-roster-metrics,.stable-roster-row__facts,.stable-roster-approval-summary{grid-template-columns:1fr}.stable-task-steps{align-items:flex-start;flex-direction:column;gap:4px}.stable-task-steps li+li:before{content:none}.stable-roster-metric{border-right:0;border-bottom:1px solid #d8d0c5}.stable-roster-metric:last-child{border-bottom:0}.stable-task-dropzone{min-height:0;grid-template-columns:36px minmax(0,1fr);padding:12px 8px}.stable-task-dropzone__action{grid-column:2}.stable-task-selected{grid-template-columns:36px minmax(0,1fr)}.stable-task-selected .btn{grid-column:2;justify-self:start}.stable-complete-cast__choice{grid-template-columns:20px minmax(0,1fr);gap:12px}.stable-complete-cast__destination{grid-column:2;justify-self:start}.stable-complete-cast__actions{align-items:stretch;flex-direction:column}.stable-complete-cast__actions .btn{width:100%}}
  `;
  document.head.append(style);
}

export function createStableFullCastDialog({ apiJson, onClose }) {
  installStyles();
  const previouslyFocused = document.activeElement;
  const layer = document.createElement('div');
  layer.className = 'stable-managed-import-layer';
  layer.setAttribute('role', 'dialog');
  layer.setAttribute('aria-modal', 'true');
  layer.setAttribute('aria-labelledby', 'stable-full-cast-title');
  const dialog = document.createElement('section');
  dialog.className = 'stable-managed-import-dialog';
  const header = document.createElement('header');
  header.className = 'stable-managed-import-header';
  const heading = text('h2', 'Full Cast tasks');
  heading.id = 'stable-full-cast-title';
  const close = button('×', 'stable-managed-import-close');
  close.setAttribute('aria-label', 'Close Full Cast tasks');
  header.append(heading, close);
  const body = document.createElement('div');
  body.className = 'stable-managed-import-body stable-task-workspace';
  const intro = document.createElement('header');
  intro.className = 'stable-task-intro';
  intro.append(
    text('span', 'Whole-book workflow', 'stable-task-eyebrow'),
    text('h3', 'Complete the Cast'),
    text('p', 'Choose the work to include, send one ZIP to ChatGPT, and review each section in Alexandria. Individual task exports remain below.', 'stable-task-muted'),
  );
  const cards = document.createElement('div');
  cards.className = 'stable-task-grid';
  const resultHost = document.createElement('div');
  const footer = document.createElement('footer');
  footer.className = 'stable-managed-import-footer';
  const footerStatus = text('div', 'Choose a task or import a completed ZIP.', 'stable-managed-import-status');
  footerStatus.setAttribute('role', 'status');
  footerStatus.setAttribute('aria-live', 'polite');
  const footerActions = document.createElement('div');
  footerActions.className = 'stable-managed-import-actions';
  const footerClose = button('Close', 'btn btn-outline-secondary');
  footerActions.append(footerClose);
  footer.append(footerStatus, footerActions);
  TASKS.forEach((task) => cards.append(taskCard({
    task, apiJson, status: footerStatus, resultHost,
  })));
  const complete = completeCastPanel(apiJson, footerStatus, resultHost);
  const individual = document.createElement('details');
  individual.className = 'stable-individual-tasks';
  individual.append(text('summary', 'Individual task exports'), cards);
  const importer = buildImportSurface({ apiJson, body, footerStatus, footerActions });
  body.append(intro, complete, individual, text('div', '', 'stable-task-divider'), importer.section, resultHost);
  dialog.append(header, body, footer);
  layer.append(dialog);

  let closed = false;
  const closeDialog = () => {
    if (closed) return;
    closed = true;
    onClose?.();
    previouslyFocused?.focus?.();
    window.requestAnimationFrame(() => previouslyFocused?.focus?.());
  };
  close.addEventListener('click', closeDialog);
  footerClose.addEventListener('click', closeDialog);
  layer.addEventListener('mousedown', (event) => { if (event.target === layer) closeDialog(); });
  layer.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') { event.preventDefault(); closeDialog(); }
    if (event.key !== 'Tab') return;
    const focusable = [...layer.querySelectorAll(
      'button:not([disabled]),a[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])',
    )].filter((node) => node.offsetParent !== null);
    if (!focusable.length) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault(); last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault(); first.focus();
    }
  });

  const resumePending = !skipPendingResumeOnce;
  skipPendingResumeOnce = false;
  window.setTimeout(async () => {
    if (!resumePending) return;
    try {
      const reconciliation = await apiJson('/api/character_roster/reconciliation');
      if (reconciliation.pending_import?.candidate_id) {
        const module = await import('/static/stable_roster_import_review.js');
        await module.renderStableRosterImportReview({
          apiJson,
          candidate: {
            candidate_id: reconciliation.pending_import.candidate_id,
            task_type: 'roster_discovery',
            reconciliation: reconciliation.pending_import,
            cast_dossier_package: reconciliation.cast_dossier_package || null,
          },
          body, footerStatus, footerActions,
          onCreateNewBundle: () => {
            skipPendingResumeOnce = true;
            onClose?.();
            window.setTimeout(() => {
              document.querySelector('[data-stable-full-cast-tasks]')?.click();
            }, 0);
          },
        });
      } else if (
        isCompletedCastPackage(reconciliation.cast_dossier_package)
      ) {
        importer.resultHost.replaceChildren(renderStableCompletedCast({ reconciliation, text }));
        body.replaceChildren(importer.resultHost);
        footerStatus.textContent = 'The completed Cast dossier is available as an audit record.';
        footerActions.replaceChildren(footerClose);
        footerClose.focus({ preventScroll: true });
      } else if (
        reconciliation.current?.working_draft
        && reconciliation.approval?.draft_fingerprint
      ) {
        const module = await import('/static/stable_roster_import_review.js');
        await module.renderStableRosterDraftApproval({
          apiJson,
          status: reconciliation,
          body,
          footerStatus,
          footerActions,
        });
      } else if (
        reconciliation.cast_dossier_package?.activation?.ready === true
      ) {
        renderDirectDossierActivation({
          apiJson,
          response: {
            task_type: 'complete_cast_dossier',
            cast_dossier_package: reconciliation.cast_dossier_package,
          },
          resultHost: importer.resultHost,
          footerStatus,
          footerActions,
        });
        body.replaceChildren(importer.resultHost);
      }
    } catch (error) {
      body.replaceChildren(
        text('span', 'Resume check failed', 'stable-task-eyebrow'),
        text('h3', 'Full Cast tasks could not be resumed safely'),
        text('p', error.message || 'Reload and retry before creating or importing another Cast task.', 'stable-task-error'),
      );
      const retry = button('Reload and retry', 'btn btn-outline-secondary');
      retry.addEventListener('click', () => window.location.reload());
      footerStatus.textContent = 'No Cast task was created or imported.';
      footerActions.replaceChildren(retry);
    }
  }, 0);

  return {
    layer,
    focus() { body.querySelector('[data-stable-export-complete-cast]')?.focus(); },
  };
}
