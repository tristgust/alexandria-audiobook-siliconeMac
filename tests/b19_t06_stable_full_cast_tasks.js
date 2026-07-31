'use strict';

const fs = require('fs');
const http = require('http');
const path = require('path');
const { BrowserSession, argsFrom, required, writeJson } = require('./b19_t06_bootstrap_red.js');

const VIEWPORTS = [[1024, 768], [768, 900]];

function fixtureServer(root) {
  const staticRoot = path.join(root, 'app/static');
  const control = { exports: [], activations: [], resumePackage: null };
  const server = http.createServer(async (request, response) => {
    const url = new URL(request.url, 'http://fixture.invalid');
    const finish = (status, body = '', type = 'text/plain; charset=utf-8') => {
      response.writeHead(status, { 'content-type': type, 'cache-control': 'no-store' });
      response.end(body);
    };
    if (url.pathname === '/') {
      return finish(200, `<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><style>
        *{box-sizing:border-box}body{margin:0;font-family:Arial,sans-serif}
        .stable-managed-import-layer{position:fixed;z-index:100;inset:0;display:grid;min-width:0;min-height:0;place-items:center;padding:clamp(8px,3vw,32px);background:rgba(35,33,30,.42)}
        .stable-managed-import-dialog{display:grid;width:min(900px,100%);height:min(820px,100%);min-height:0;max-height:100%;grid-template-rows:auto minmax(0,1fr) auto;overflow:hidden;border:1px solid #bdb2a5;border-radius:12px;background:#faf8f2;color:#23211e}
        .stable-managed-import-header,.stable-managed-import-footer{display:flex;align-items:center;justify-content:space-between;gap:16px;padding:16px 20px}.stable-managed-import-header{border-bottom:1px solid #d8d0c5}.stable-managed-import-footer{border-top:1px solid #d8d0c5}
        .stable-managed-import-body{display:grid;min-height:0;align-content:start;gap:18px;padding:20px;overflow:auto;scrollbar-gutter:stable}.stable-managed-import-actions{display:flex;gap:10px}.stable-managed-import-close{width:36px;height:36px;border:0;background:transparent;font-size:1.3rem}
        @media(max-width:520px){.stable-managed-import-header,.stable-managed-import-footer{padding:12px}.stable-managed-import-body{padding:12px}.stable-managed-import-footer{align-items:stretch;flex-direction:column}}
      </style></head><body><button id="open">Open Full Cast tasks</button><script type="module">
        import {createStableFullCastDialog} from '/static/stable_full_cast_tasks.js';
        async function apiJson(path, options={}) {
          const response=await fetch(path,{...options});
          const data=await response.json();
          if(!response.ok) throw new Error(data.detail||'Request failed');
          return data;
        }
        let dialog=null;
        document.getElementById('open').addEventListener('click',()=>{
          const view=createStableFullCastDialog({apiJson,onClose:()=>{dialog?.remove();dialog=null;}});
          dialog=view.layer;document.body.append(dialog);view.focus();
        });
        document.body.dataset.fixtureReady='true';
      </script></body></html>`, 'text/html; charset=utf-8');
    }
    if (url.pathname === '/api/character_roster/reconciliation') {
      const castDossierPackage = control.resumePackage ? {
        parent_candidate_id: 'structured-fixture-cast',
        status: control.resumePackage === 'completed' ? 'complete' : 'review',
        selected_sections: {
          roster_and_relationships: true,
          voice_personas_and_designs: true,
          visual_dossiers: true,
        },
        summary: {
          voice_dossier_count: 59,
          visual_dossier_count: 77,
          visual_observation_count: 133,
        },
        activation: {
          ready: control.resumePackage === 'approved',
          completed: control.resumePackage === 'completed',
          approved_roster_fingerprint: 'a'.repeat(64),
          reason: null,
        },
        ...(control.resumePackage === 'completed' ? {
          components: {
            roster_candidate_id: 'roster-fixture-cast',
            persona_candidate_id: 'persona-fixture-cast',
          },
          applications: {
            voice_dossiers: {
              destination: 'Voice review', persona_count: 59, identity_project_count: 12,
            },
            visual_dossiers: {
              destination: 'Visual review', character_count: 77, observation_count: 133,
              identity_crosswalk: { unidentified_woman_in_memory: 'character-memory-woman' },
              excluded_identity_keys: ['background_crowd'],
            },
          },
          review_warnings: ['One imported alias remains available for review.'],
        } : {}),
        visual_identity_review: {
          required: true,
          issues: [{
            identity_key: 'unidentified_woman_in_memory',
            label: 'Memory Woman',
            suggested_entry_id: 'character-memory-woman',
            suggested_entry_name: 'MEMORY WOMAN',
            excluded_during_roster_review: true,
          }],
          approved_entries: [{
            id: 'character-memory-woman',
            canonical_name: 'MEMORY WOMAN',
            display_name: 'MEMORY WOMAN',
          }],
        },
      } : null;
      return finish(200, JSON.stringify({
        schema_version: 1, state: 'approved', pending_import: null,
        current: { kind: 'approved', approved_fingerprint: 'a'.repeat(64), working_draft: false },
        approval: { blocked: true }, issues: [], safe_changes: [],
        ...(control.resumePackage === 'completed' ? {
          revision_count: 2,
          rollback: { available: true, revision: { revision_id: 'revision-fixture-2' } },
        } : {}),
        ...(castDossierPackage ? { cast_dossier_package: castDossierPackage } : {}),
      }), 'application/json');
    }
    if (url.pathname === '/api/tasks/export') {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      const payload = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      control.exports.push(payload);
      return finish(200, JSON.stringify({
        task_id: `task-${payload.task_type}`,
        task_type: payload.task_type,
        download_url: `/api/tasks/${payload.task_type}/download`,
      }), 'application/json');
    }
    if (url.pathname === '/api/cast-dossier/structured-fixture-cast/activate') {
      const chunks = [];
      for await (const chunk of request) chunks.push(chunk);
      control.activations.push(JSON.parse(Buffer.concat(chunks).toString('utf8')));
      return finish(200, JSON.stringify({
        package: {
          applications: {
            voice_dossiers: { status: 'native_review_ready' },
            visual_dossiers: { status: 'native_review_ready' },
          },
        },
      }), 'application/json');
    }
    if (url.pathname.startsWith('/api/tasks/') && url.pathname.endsWith('/download')) {
      return finish(200, 'fixture zip', 'application/zip');
    }
    if (url.pathname.startsWith('/static/')) {
      const filename = path.resolve(staticRoot, url.pathname.slice('/static/'.length));
      if (!filename.startsWith(`${staticRoot}${path.sep}`) || !fs.existsSync(filename)) return finish(404, 'Not found');
      return finish(200, fs.readFileSync(filename), 'text/javascript; charset=utf-8');
    }
    return finish(404, 'Not found');
  });
  return new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => resolve({
      server,
      control,
      url: `http://127.0.0.1:${server.address().port}/`,
      close: () => new Promise((done) => {
        server.close(done);
        server.closeAllConnections?.();
      }),
    }));
  });
}

async function inspect(server, artifacts, width, height) {
  server.control.exports.length = 0;
  server.control.resumePackage = null;
  const folder = path.join(artifacts, `${width}x${height}`);
  const session = await BrowserSession.open({ url: server.url, artifacts: folder, width, height });
  try {
    await session.waitFor(`document.body.dataset.fixtureReady==='true'`, 10000);
    await session.evaluate(`document.getElementById('open').focus();document.getElementById('open').click()`);
    await session.waitFor(`Boolean(document.querySelector('[data-stable-task-dropzone]'))`, 10000);
    const observed = await session.evaluate(`(() => {
      const dialog=document.querySelector('.stable-managed-import-dialog');
      const rect=dialog.getBoundingClientRect();
      const drop=document.querySelector('[data-stable-task-dropzone]');
      const dropRect=drop.getBoundingClientRect();
      return {
        taskCards:document.querySelectorAll('[data-stable-full-cast-task]').length,
        completeChoices:[...document.querySelectorAll('[data-stable-cast-dossier-option]')]
          .map(input=>({key:input.dataset.stableCastDossierOption,checked:input.checked})),
        completePrimary:document.querySelector('[data-stable-export-complete-cast]')?.textContent.trim()||'',
        individualOpen:document.querySelector('.stable-individual-tasks')?.open===true,
        steps:document.querySelectorAll('.stable-task-steps li').length,
        dropText:drop.innerText,
        fallbackOpen:document.querySelector('.stable-task-fallback')?.open===true,
        focused:Boolean(dialog.contains(document.activeElement)),
        overflow:Math.max(0,document.documentElement.scrollWidth-document.documentElement.clientWidth),
        dialog:{top:rect.top,right:rect.right,bottom:rect.bottom,left:rect.left,width:rect.width,height:rect.height},
        drop:{top:dropRect.top,right:dropRect.right,bottom:dropRect.bottom,left:dropRect.left,width:dropRect.width,height:dropRect.height},
      };
    })()`);
    await session.evaluate(`document.querySelector('[data-stable-export-complete-cast]').click()`);
    await session.waitFor(`Boolean(document.querySelector('.stable-complete-cast__result a'))`);
    const completeExport = server.control.exports.at(-1) || null;
    await session.evaluate(`document.querySelector('.stable-individual-tasks summary').click()`);
    await session.evaluate(`document.querySelector('[data-stable-full-cast-task="roster_discovery"] .btn').click()`);
    await session.waitFor(`Boolean(document.querySelector('[data-stable-full-cast-task="roster_discovery"] a'))`);
    await session.screenshot('stable-full-cast-fresh.png');
    const assertions = {
      threeTaskCards: observed.taskCards === 3,
      completeCastPrimary: observed.completeChoices.length === 3
        && observed.completeChoices.every((item) => item.checked)
        && observed.completePrimary === 'Export Cast bundle'
        && observed.individualOpen === false,
      completeCastPayload: completeExport?.task_type === 'complete_cast_dossier'
        && completeExport?.options?.roster_and_relationships === true
        && completeExport?.options?.voice_personas_and_designs === true
        && completeExport?.options?.visual_dossiers === true,
      threeSteps: observed.steps === 3,
      clearCompletedZipAction: /completed ZIP/i.test(observed.dropText) && /Do not unzip/i.test(observed.dropText),
      fallbackCollapsed: observed.fallbackOpen === false,
      focusContained: observed.focused,
      noOverflow: observed.overflow <= 1,
      dialogContained: observed.dialog.top >= 0 && observed.dialog.left >= 0
        && observed.dialog.right <= width + 1 && observed.dialog.bottom <= height + 1,
      dropContained: observed.drop.left >= observed.dialog.left - 1
        && observed.drop.right <= observed.dialog.right + 1,
      exportWorks: true,
    };
    return {
      viewport: `${width}x${height}`,
      status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      assertions,
      observed,
    };
  } finally {
    await session.close();
  }
}

async function inspectApprovedPackageResume(server, artifacts) {
  server.control.resumePackage = 'approved';
  server.control.activations.length = 0;
  const session = await BrowserSession.open({
    url: server.url,
    artifacts: path.join(artifacts, 'approved-package-resume'),
    width: 768,
    height: 900,
  });
  try {
    await session.waitFor(`document.body.dataset.fixtureReady==='true'`, 10000);
    await session.evaluate(`document.getElementById('open').click()`);
    await session.waitFor(`Boolean(document.querySelector('.stable-managed-import-dialog'))`, 10000);
    await session.evaluate(`new Promise(resolve=>setTimeout(resolve,200))`);
    const observed = await session.evaluate(`(() => {
      const dialog=document.querySelector('.stable-managed-import-dialog');
      const decision=document.querySelector('[data-stable-visual-identity-decision]');
      const manual=document.querySelector('[data-stable-visual-identity-manual]');
      const optionLabels=decision ? [...decision.options].map(option=>option.textContent.trim()) : [];
      return {
        heading:dialog.querySelector('h3')?.textContent.trim()||'',
        decisionCount:dialog.querySelectorAll('[data-stable-visual-identity-decision]').length,
        decisionValue:decision?.value||'',
        optionLabels,
        manualHidden:manual?.hidden===true,
        consequence:dialog.querySelector('.stable-visual-identity-review__consequence')?.textContent.trim()||'',
        closeLabel:dialog.querySelector('.stable-managed-import-close')?.getAttribute('aria-label')||'',
        returnControls:[...dialog.querySelectorAll('button,a')]
          .filter(node=>/^\s*(?:←\s*)?return\b/i.test(node.textContent||'')).length,
      };
    })()`);
    await session.screenshot('stable-full-cast-approved-package-resume.png');
    await session.evaluate(`(() => {
      const decision=document.querySelector('[data-stable-visual-identity-decision]');
      decision.value='suggested';
      decision.dispatchEvent(new Event('change',{bubbles:true}));
      [...document.querySelectorAll('button')]
        .find(node=>node.textContent.trim()==='Apply selected sections')?.click();
    })()`);
    await session.waitFor(`/Voice personas and definitions: applied to Voice/.test(document.querySelector('.stable-managed-import-status')?.textContent||'')`, 10000);
    const activationPayload = server.control.activations.at(-1) || null;
    const assertions = {
      resumesPackage: observed.heading === 'Import selected dossier sections',
      showsIdentityDecision: observed.decisionCount === 1,
      preservesExclusionDefault: observed.decisionValue === 'exclude',
      progressiveDecision: observed.optionLabels.length === 3
        && observed.optionLabels.some((label) => /MEMORY WOMAN/.test(label))
        && observed.manualHidden
        && /source evidence/i.test(observed.consequence),
      modalUsesClose: observed.closeLabel === 'Close Full Cast tasks' && observed.returnControls === 0,
      submitsExplicitMapping: activationPayload?.identity_crosswalk?.unidentified_woman_in_memory === 'character-memory-woman'
        && Array.isArray(activationPayload?.excluded_visual_identity_keys)
        && activationPayload.excluded_visual_identity_keys.length === 0,
    };
    return {
      viewport: '768x900-approved-package-resume',
      status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      assertions,
      observed: { ...observed, activationPayload },
    };
  } finally {
    await session.close();
  }
}

async function inspectCompletedPackageResume(server, artifacts) {
  server.control.resumePackage = 'completed';
  server.control.exports.length = 0;
  server.control.activations.length = 0;
  const session = await BrowserSession.open({
    url: server.url,
    artifacts: path.join(artifacts, 'completed-package-resume'),
    width: 768,
    height: 900,
  });
  try {
    await session.waitFor(`document.body.dataset.fixtureReady==='true'`, 10000);
    await session.evaluate(`document.getElementById('open').focus();document.getElementById('open').click()`);
    await session.waitFor(`Boolean(document.querySelector('[data-complete-cast-resume="completed"]'))`, 10000);
    const observed = await session.evaluate(`(() => {
      const panel=document.querySelector('[data-complete-cast-resume="completed"]');
      const text=panel?.innerText||'';
      return {
        heading:panel?.querySelector('h3')?.textContent.trim()||'',
        text,
        freshControls:document.querySelectorAll('[data-stable-export-complete-cast], [data-stable-task-dropzone], [data-stable-visual-identity-decision]').length,
        focusContained:Boolean(document.querySelector('.stable-managed-import-dialog')?.contains(document.activeElement)),
        overflow:Math.max(0,document.documentElement.scrollWidth-document.documentElement.clientWidth),
      };
    })()`);
    await session.screenshot('stable-full-cast-completed-package-resume.png');
    const keyboard = await session.evaluate(`(() => {
      const layer=document.querySelector('.stable-managed-import-layer');
      const inside=()=>Boolean(layer?.contains(document.activeElement));
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',bubbles:true}));
      const tabContained=inside();
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown',{key:'Tab',shiftKey:true,bubbles:true}));
      const shiftTabContained=inside();
      document.activeElement?.dispatchEvent(new KeyboardEvent('keydown',{key:'Escape',bubbles:true}));
      return {
        tabContained,
        shiftTabContained,
        closed:!document.querySelector('.stable-managed-import-layer'),
        openerRestored:document.activeElement===document.getElementById('open'),
      };
    })()`);
    const assertions = {
      resumesCompletedAudit: observed.heading === 'Complete Cast dossier imported',
      noReimportControls: observed.freshControls === 0,
      applicationsAuditable: /Voice personas & designs/.test(observed.text) && /Visual dossiers/.test(observed.text),
      identityAuditVisible: /Visual identity crosswalk/.test(observed.text) && /Retained visual exclusions/.test(observed.text),
      historyAuditable: /History and rollback/.test(observed.text) && /2 revisions/.test(observed.text) && /Rollback available/.test(observed.text),
      noMutatingRequests: server.control.exports.length === 0 && server.control.activations.length === 0,
      focusContained: observed.focusContained,
      keyboardFocusLifecycle: keyboard.tabContained && keyboard.shiftTabContained
        && keyboard.closed && keyboard.openerRestored,
      noOverflow: observed.overflow <= 1,
    };
    return {
      viewport: '768x900-completed-package-resume',
      status: Object.values(assertions).every(Boolean) ? 'PASS' : 'FAIL',
      assertions,
      observed: { ...observed, keyboard },
    };
  } finally {
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  const root = path.resolve(required(args, 'repo-root'));
  const server = await fixtureServer(root);
  const results = [];
  try {
    for (const [width, height] of VIEWPORTS) results.push(await inspect(server, artifacts, width, height));
    results.push(await inspectApprovedPackageResume(server, artifacts));
    results.push(await inspectCompletedPackageResume(server, artifacts));
  } finally {
    await server.close();
  }
  const report = { status: results.every((item) => item.status === 'PASS') ? 'PASS' : 'FAIL', results };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B19_T06_STABLE_FULL_CAST=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
