'use strict';

const { press, snapshot } = require('./cast_profile_browser_helpers.js');

const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));

async function runStateMatrixScenario({ assertions, details, server, session }) {
  const discoverBefore = server.control.requests.filter((item) => item.path === '/api/character_visuals/discover').length;
  await session.evaluate(`[...document.querySelectorAll('.disclosure__trigger')].find(n=>n.textContent.trim()==='Appearance')?.click()`);
  await session.waitFor(`document.querySelector('[data-persona-state]')?.dataset.personaState==='idle'`);
  assertions.personaNoAutoStart = server.control.requests.filter((item) => item.path === '/api/character_visuals/discover').length === discoverBefore;
  await session.evaluate(`{const n=document.querySelector('[data-persona-enable]');n.click()}`);
  await session.evaluate(`document.querySelector('[data-persona-collect]').click()`);
  await session.waitFor(`document.querySelector('[data-persona-state]')?.dataset.personaState==='running'`);
  await session.evaluate(`document.querySelector('[data-persona-state]').scrollIntoView({block:'center'})`);
  await session.screenshot('persona-running.png');
  await session.waitFor(`document.querySelector('[data-persona-state]')?.dataset.personaState==='completed'`, 7000);
  assertions.personaCompleted = true;
  assertions.personaSafe = await session.evaluate(`!globalThis.fixtureInjection&&!document.querySelector('[data-persona-state] img')`);
  await session.evaluate(`document.querySelector('[data-persona-state]').scrollIntoView({block:'center'})`);
  await session.screenshot('persona-completed.png');
  await session.evaluate(`document.querySelector('[data-cast-more]').click()`);
  details.returnContext = await session.evaluate(`document.querySelector('.popover-controller')?.dataset.returnContext||''`);
  assertions.returnContext = details.returnContext.includes('#/cast') && details.returnContext.includes('character=');
  await press(session, 'Escape');
  server.control.mode = 'error';
  await session.client.send('Page.reload');
  await session.waitFor(`document.querySelector('[data-cast-page]')?.dataset.castState==='error'`);
  assertions.errorRetry = await session.evaluate(`document.querySelector('[data-cast-retry]')?.textContent.includes('Retry')`);
  await session.screenshot('cast-error.png');
  server.control.mode = 'empty';
  await session.client.send('Page.reload');
  await session.waitFor(`document.querySelector('[data-cast-page]')?.dataset.castState==='empty'`);
  assertions.emptyReviewScript = await session.evaluate(`document.querySelector('[data-cast-page]')?.innerText.includes('Review Script')`);
  await session.screenshot('cast-empty.png');
  server.control.mode = 'discovering';
  await session.client.send('Page.reload');
  await session.waitFor(`document.querySelector('[data-cast-page]')?.dataset.castState==='running'`);
  assertions.discoveryProgress = await session.evaluate(`(() => {
    const page=document.querySelector('[data-cast-page]');
    const bar=page?.querySelector('.cast-discovery-progress [role="progressbar"]');
    return page?.innerText.includes('approved Script already supplies the speaking-role labels')
      && page?.innerText.includes('2 of 42 source passages analyzed')
      && bar?.getAttribute('aria-valuenow')==='5'
      && Boolean(page?.querySelector('[data-cast-cancel-discovery]'));
  })()`);
  await session.screenshot('cast-discovery-progress.png');
  server.control.mode = 'dense';
  await session.client.send('Page.reload');
  await session.waitFor(`document.querySelector('[data-cast-page]')?.dataset.castState==='ready'`);
  const dense = await snapshot(session);
  assertions.denseRoster = await session.evaluate(`document.querySelectorAll('[role="option"]').length===24`);
  assertions.denseNoOverflow = dense.overflow <= 1 && !dense.injection;
  await session.screenshot('cast-dense.png');
  server.control.mode = 'normal';
  server.control.visual = 'error';
  await session.client.send('Page.reload');
  await session.waitFor(`document.querySelector('[data-persona-state]')?.dataset.personaState==='error'`);
  assertions.personaError = await session.evaluate(`document.querySelector('[data-persona-retry]')?.textContent.includes('Retry')`);
  await session.screenshot('persona-error.png');
  server.control.visual = 'disabled';
  await session.client.send('Page.reload');
  await session.waitFor(`document.querySelector('[data-persona-state]')?.dataset.personaState==='disabled'`);
  assertions.personaDisabled = await session.evaluate(`document.querySelector('[data-persona-state]')?.textContent.includes('unavailable')`);
  await session.screenshot('persona-disabled.png');
  server.control.mode = 'loading';
  server.control.pending.length = 0;
  const beforeAbort = server.control.aborted;
  await session.client.send('Page.reload');
  await session.waitFor(`document.querySelector('[data-cast-page]')?.dataset.castState==='loading'`);
  await session.evaluate(`location.hash='#/projects'`);
  await session.waitFor(`document.body.dataset.destination==='projects'`);
  const deadline = Date.now() + 3000;
  while (server.control.aborted === beforeAbort && Date.now() < deadline) await wait(25);
  assertions.routeAbort = server.control.aborted > beforeAbort;
  server.release();
}

module.exports = { runStateMatrixScenario };
