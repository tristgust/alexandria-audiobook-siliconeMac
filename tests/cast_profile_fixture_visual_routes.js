'use strict';

const { visualStatus } = require('./cast_profile_fixture_cast_payloads.js');

const json = (value) => JSON.stringify(value);

function handleVisualApi({ url, finish, control }) {
  if (url.pathname === '/api/character_visuals/status') {
    if (control.visual === 'running' && control.visualReads++ > 0) control.visual = 'complete';
    finish(200, json(visualStatus(control)), 'application/json');
    return true;
  }
  if (url.pathname === '/api/character_visuals/discover') {
    control.visual = 'running';
    control.visualReads = 0;
    finish(200, json({ status: 'started', started: true }), 'application/json');
    return true;
  }
  if (url.pathname === '/api/character_visuals/cancel') {
    control.visual = 'idle';
    finish(200, json({ status: 'cancelling' }), 'application/json');
    return true;
  }
  if (url.pathname.startsWith('/api/character_visuals/')) {
    finish(200, json({
      entry_id: control.selected, canonical_name: 'Clara Leighton', display_name: 'Clara Leighton',
      visual: {
        image_prompt_summary: 'Dark hair, practical dress, and a weathered travelling coat.',
        stable_traits: ['Dark hair', '<img src=x onerror="globalThis.fixtureInjection=true">'],
        variants: ['Travelling coat in exterior scenes'], conflicts: [],
      },
    }), 'application/json');
    return true;
  }
  return false;
}

module.exports = { handleVisualApi };
