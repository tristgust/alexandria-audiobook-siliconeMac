'use strict';

const fs = require('fs');
const path = require('path');
const {
  BrowserSession, argsFrom, required, writeJson,
} = require('./b19_t06_bootstrap_red.js');
const { fixtureServer } = require('./produce_export_fixture_server.js');
const { setMode } = require('./produce_export_browser_helpers.js');

async function inspectReady(server, artifacts, width, height) {
  server.control.mode = 'produce-takes';
  server.control.takeState.currentId = 'take-newest';
  server.control.takeState.kept.clear();
  server.control.takeState.deleted.clear();
  const session = await BrowserSession.open({
    url: `${server.url}#/produce?chunk=current-1`,
    artifacts: path.join(artifacts, `${width}x${height}-ready`),
    width,
    height,
  });
  session.baseUrl = server.url;
  try {
    await session.waitFor(`document.body.dataset.shellState==='ready'`);
    await session.waitFor(`document.querySelectorAll('[data-produce-take]').length===4`);
    await session.evaluate(`document.querySelector('[data-audio-row][data-chunk-id="chunk:current-1"]')?.dispatchEvent(new MouseEvent('click',{bubbles:true,cancelable:true}))`);
    if (width <= 1180) await session.waitFor(`!document.querySelector('.produce-inspector')?.hidden`);
    const result = await session.evaluate(`(() => {
      const inspector=document.querySelector('.produce-inspector');
      const body=document.querySelector('.produce-inspector__body');
      const rect=(el)=>el?.getBoundingClientRect();
      const svgSizes=[...document.querySelectorAll('.produce-inspector svg')]
        .map((svg)=>Math.max(rect(svg).width,rect(svg).height));
      const actionHeights=[...document.querySelectorAll('.produce-inspector-actions > *')]
        .map((node)=>rect(node).height);
      const current=document.querySelector('[data-produce-take][data-current="true"]');
      const incompatible=document.querySelector('[data-produce-take="take-incompatible"]');
      const order=[
        '.produce-inspector-waveform',
        '.produce-inspector-actions',
        '.produce-takes-section',
        '.produce-inspector-section',
        '.produce-inspector-summary',
        '[data-produce-technical-details]',
        '[data-produce-history]',
      ].map((selector)=>rect(document.querySelector(selector))?.top||0);
      return {
        maxSvg:Math.max(0,...svgSizes),
        maxActionHeight:Math.max(0,...actionHeights),
        bodyHeight:rect(body)?.height||0,
        takesTop:rect(document.querySelector('.produce-takes-section'))?.top||0,
        inspectorBottom:rect(inspector)?.bottom||0,
        currentActionLabels:[...current.querySelectorAll('button')].map((button)=>button.textContent.trim()),
        incompatibleActionLabels:[...incompatible.querySelectorAll('button')].map((button)=>button.textContent.trim()),
        technicalClosed:Boolean(document.querySelector('[data-produce-technical-details]:not([open])')),
        historyClosed:Boolean(document.querySelector('[data-produce-history]:not([open])')),
        order,
        overflow:Math.max(0,document.documentElement.scrollWidth-innerWidth),
      };
    })()`);
    await session.screenshot('right-inspector-ready.png');
    const assertions = {
      svgScale: result.maxSvg <= 20,
      compactActions: result.maxActionHeight <= 44,
      currentActionsRelevant: JSON.stringify(result.currentActionLabels) === JSON.stringify(['Play', 'Keep']),
      incompatibleActionsRelevant: JSON.stringify(result.incompatibleActionLabels) === JSON.stringify(['Play', 'Keep', 'Delete']),
      technicalDetailsCollapsed: result.technicalClosed,
      generationHistoryCollapsed: result.historyClosed,
      decisionFirstOrder: result.order.every((value, index, values) => index === 0 || value > values[index - 1]),
      takesReachableEarly: result.takesTop < result.inspectorBottom,
      boundedDefaultHeight: result.bodyHeight <= 1350,
      noOverflow: result.overflow <= 1,
    };
    return { state: 'ready', viewport: `${width}x${height}`, assertions, result };
  } finally {
    await session.close();
  }
}

async function inspectLoading(server, artifacts) {
  server.control.mode = 'produce-mixed';
  const session = await BrowserSession.open({
    url: `${server.url}#/produce`,
    artifacts: path.join(artifacts, '1536x1024-loading'),
    width: 1536,
    height: 1024,
  });
  session.baseUrl = server.url;
  try {
    await session.waitFor(`document.body.dataset.shellState==='ready'`);
    await setMode(session, server.control, 'produce-mixed-loading', 'produce', true);
    await session.waitFor(`document.querySelector('.produce-inspector-loading .loading-state')`);
    const result = await session.evaluate(`(() => {
      const inspector=document.querySelector('.produce-inspector');
      const loading=document.querySelector('.produce-inspector-loading .loading-state');
      const spinner=document.querySelector('.produce-inspector-loading .loading-state__spinner');
      const a=inspector.getBoundingClientRect();
      const b=loading.getBoundingClientRect();
      const s=spinner.getBoundingClientRect();
      return {
        topClearance:b.top-a.top,
        inlineClearance:b.left-a.left,
        spinnerSize:Math.max(s.width,s.height),
        skeletonAbsent:!document.querySelector('.produce-inspector .skeleton'),
        overflow:Math.max(0,document.documentElement.scrollWidth-innerWidth),
      };
    })()`);
    await session.screenshot('right-inspector-loading.png');
    const assertions = {
      borderClearance: result.topClearance >= 36 && result.inlineClearance >= 18,
      compactSpinner: result.spinnerSize <= 20,
      noBorderLikeSkeleton: result.skeletonAbsent,
      noOverflow: result.overflow <= 1,
    };
    return { state: 'loading', viewport: '1536x1024', assertions, result };
  } finally {
    server.release();
    await session.close();
  }
}

async function main() {
  const args = argsFrom(process.argv.slice(2));
  const artifacts = path.resolve(required(args, 'artifacts'));
  fs.mkdirSync(artifacts, { recursive: true });
  const server = await fixtureServer();
  const results = [];
  try {
    results.push(await inspectReady(server, artifacts, 390, 844));
    results.push(await inspectReady(server, artifacts, 1536, 1024));
    results.push(await inspectLoading(server, artifacts));
  } finally {
    server.release();
    await server.close();
  }
  const report = {
    status: results.every((item) => Object.values(item.assertions).every(Boolean)) ? 'PASS' : 'FAIL',
    scenario: 'B16-T05R right content-inspector repair',
    results,
  };
  writeJson(path.join(artifacts, 'report.json'), report);
  process.stdout.write(`B16_T05R_RIGHT_INSPECTOR=${JSON.stringify(report)}\n`);
  if (report.status !== 'PASS') process.exitCode = 1;
}

main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 2;
});
