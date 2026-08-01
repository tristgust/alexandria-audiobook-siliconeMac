'use strict';

import {
  VOICE_GROUP_ORDER, applyVoicePayload, bindVoiceOptionKeyboard, ownerForVoices, text, voiceGroup, voiceMark,
  voiceName, voicePresentation, words,
} from './voices_model.js';
import { voicesLoading } from './supporting_page_loading.js';
import { createCommunityQwenPackController } from './community_qwen_packs.js';

const UI = globalThis.AlexandriaUI;
const STATES = Object.freeze(['loading', 'empty', 'error', 'success', 'dense']), VISIBLE_USAGE_LIMIT = 8;
let cachedVoices = null;
export async function mount({ root, route, shell, api, signal }) {
  shell.globalHeader.set({
    title: 'Voices',
    subtitle: 'Inspect reusable voice resources, preview readiness, and Cast usage.',
  });
  const owner = ownerForVoices(route);
  const toolbar = document.createElement('div');
  toolbar.className = 'page-toolbar';
  const search = UI.searchField({
    label: 'Search Voices', placeholder: 'Search voices',
    iconClass: 'fas fa-magnifying-glass',
  });
  const method = UI.field({
    kind: 'select', label: 'Method',
    options: [{ value: 'all', label: 'All methods' }],
    value: route.context.filter || 'all',
  });
  let load = async () => {};
  const controller = createCommunityQwenPackController({
    api, signal, shell, onLibraryChanged: () => load(),
  });
  const communityPacks = UI.button({
    label: 'Community Qwen packs', variant: 'secondary',
    attributes: { 'data-community-qwen-packs': '' },
    onClick: (event) => controller.open(event.currentTarget),
  });
  toolbar.append(search, method, communityPacks);
  const content = document.createElement('section');
  content.className = 'content-state';
  content.dataset.state = STATES[0];
  content.append(voicesLoading());
  owner.append(toolbar, content);
  root.replaceChildren(owner);
  shell.player.set({ state: 'inactive', title: 'No voice preview selected' });

  let disposed = false, voices = [], selected = null;
  let projectId = route.context.project || '';
  const cacheProjectId = shell.projectCatalog?.()?.current_project_id || projectId;

  const detailFor = (voice) => {
    const [methodLabel] = voicePresentation(voice);
    const capability = voice.capability || {};
    const preview = voice.preview || {};
    const scope = voice.technical_details?.scope;
    const stateTone = ['invalid', 'legacy_blocked'].includes(voice.state)
      ? 'error'
      : ['experimental_unaccepted', 'review_required', 'stale'].includes(voice.state)
        ? 'warning' : 'success';
    const detail = document.createElement('section');
    detail.className = 'supporting-detail';
    const identity = document.createElement('header');
    identity.className = 'supporting-detail__identity';
    const copy = document.createElement('div');
    copy.append(
      text('div', 'metadata', methodLabel),
      text('h2', 'section-title', voiceName(voice, voices)),
    );
    identity.append(voiceMark(voice, 'supporting-detail__mark'), copy);
    detail.append(
      identity,
      UI.status({
        tone: stateTone,
        label: words(voice.state, 'Available'),
      }),
      text('p', 'flat-section__body', voice.description
        || capability.message || capability.description || 'No description supplied.'),
    );
    const capabilityFacts = document.createElement('dl');
    capabilityFacts.className = 'fact-list';
    capabilityFacts.append(
      text('dt', 'metadata', 'Scope'),
      text('dd', '', scope === 'reusable' ? 'Every project'
        : scope === 'project' ? 'Current project only'
          : scope === 'built_in' ? 'Built in' : 'Current project'),
      text('dt', 'metadata', 'Production use'),
      text('dd', '', capability.production_supported === true ? 'Available' : 'Not approved'),
      text('dt', 'metadata', 'Preview sample'),
      text('dd', '', preview.available === true ? 'Ready' : 'Not generated'),
      text('dt', 'metadata', 'Instruction control'),
      text('dd', '', capability.instruction_supported === true ? 'Supported' : 'Not supported'),
    );
    detail.append(capabilityFacts);
    if (preview.available === true && preview.url) {
      detail.append(UI.button({
        label: 'Play sample',
        variant: 'secondary',
        attributes: { 'data-voice-preview': '' },
        onClick: () => shell.player.set({
          state: 'playing', src: preview.url, position: 0,
          title: voiceName(voice, voices), subtitle: methodLabel,
        }),
      }));
    }
    const usage = Array.isArray(voice.usage) ? voice.usage : [];
    const usageSection = document.createElement('section');
    usageSection.className = 'voice-usage';
    usageSection.append(text('h3', 'entity-title', usage.length ? 'Used by' : 'Not assigned'));
    if (usage.length) {
      const usageList = (items) => {
        const list = document.createElement('ul');
        list.className = 'divider-list';
        items.forEach((item) => list.append(text(
          'li', '', item.character_name || item.name || 'Character',
        )));
        return list;
      };
      usageSection.append(usageList(usage.slice(0, VISIBLE_USAGE_LIMIT)));
      if (usage.length > VISIBLE_USAGE_LIMIT) {
        usageSection.append(UI.disclosure({
          label: `Show ${usage.length - VISIBLE_USAGE_LIMIT} more uses`,
          content: usageList(usage.slice(VISIBLE_USAGE_LIMIT)),
        }));
      }
    } else {
      usageSection.append(text(
        'p', 'metadata',
        'Assign voices from Cast. Approved Voices already appear in the Existing Voice picker.',
      ));
    }
    detail.append(usageSection);
    if (usage.length) {
      const castContext = { project: projectId };
      const firstCharacter = usage[0]?.character_id;
      const firstCharacterName = usage[0]?.character_name || usage[0]?.name || 'character';
      if (firstCharacter) castContext.character = firstCharacter;
      detail.append(UI.button({
        label: `Open ${firstCharacterName} in Cast`,
        variant: 'primary',
        attributes: { title: 'Open usage in Cast' },
        onClick: () => shell.navigate(shell.routes.routeForPath('cast', castContext).hash),
      }));
    }
    return detail;
  };

  const render = () => {
    if (disposed || signal.aborted) return;
    const query = search.querySelector('input').value.trim().toLocaleLowerCase();
    const chosen = method.querySelector('select').value;
    const visible = voices.filter((voice) => (
      (chosen === 'all' || voice.method === chosen)
      && (!query || `${voiceName(voice, voices)} ${voice.description || ''} ${voice.method_label || ''}`.toLocaleLowerCase().includes(query))
    )).sort((left, right) => {
      const groupDelta = VOICE_GROUP_ORDER.indexOf(voiceGroup(left))
        - VOICE_GROUP_ORDER.indexOf(voiceGroup(right));
      if (groupDelta) return groupDelta;
      return voiceName(left, voices).localeCompare(voiceName(right, voices));
    });
    content.replaceChildren();
    content.dataset.state = visible.length > 25 ? STATES[4] : STATES[3];
    if (!visible.length) {
      content.dataset.state = STATES[1];
      content.append(UI.emptyState({
        iconClass: voices.length ? 'fas fa-filter-circle-xmark' : 'fas fa-microphone-lines',
        title: voices.length ? 'No voices match' : 'No voice resources',
        body: voices.length ? 'Clear the search or choose another method.' : 'Voice resources appear after local capabilities are discovered.',
      }));
      return;
    }
    if (!visible.includes(selected)) selected = visible[0];
    const list = document.createElement('ul');
    list.className = 'supporting-list';
    list.setAttribute('role', 'listbox');
    list.setAttribute('aria-label', 'Voice resources');
    let activeGroup = '';
    visible.forEach((voice) => {
      const group = voiceGroup(voice);
      if (group !== activeGroup) {
        activeGroup = group;
        const label = document.createElement('li');
        label.className = 'supporting-list__group-label';
        label.setAttribute('role', 'presentation');
        label.append(text('span', 'utility-heading', group));
        list.append(label);
      }
      const row = document.createElement('li');
      row.setAttribute('role', 'presentation');
      const button = document.createElement('button');
      button.type = 'button';
      button.setAttribute('role', 'option');
      button.className = 'supporting-list__button supporting-list__button--icon';
      button.setAttribute('aria-selected', String(voice === selected));
      button.tabIndex = voice === selected ? 0 : -1;
      const copy = document.createElement('span');
      copy.className = 'supporting-list__copy';
      copy.append(
        text('strong', 'entity-title', voiceName(voice, voices)),
        text('span', 'metadata', `${voice.method_label || words(voice.method, 'Voice')} · ${words(voice.state, 'Unknown')}`),
      );
      button.append(voiceMark(voice), copy);
      button.addEventListener('click', () => {
        selected = voice;
        list.querySelectorAll('.supporting-list__button').forEach((item) => {
          item.setAttribute('aria-selected', String(item === button));
          item.tabIndex = item === button ? 0 : -1;
        });
        content.querySelector('.supporting-detail')?.replaceWith(detailFor(selected));
        button.focus({ preventScroll: true });
        button.scrollIntoView({ block: 'nearest' });
      });
      bindVoiceOptionKeyboard(list, button);
      row.append(button);
      list.append(row);
    });
    const master = document.createElement('section');
    master.className = 'supporting-master';
    master.append(list);
    content.append(UI.masterDetail({ master, detail: detailFor(selected) }));
  };

  const applyPayload = (payload) => {
    ({ voices, projectId } = applyVoicePayload(
      payload, method.querySelector('select'), projectId,
    ));
    render();
  };

  load = async (background = false) => {
    const result = await api.get('/api/voice-library', { signal, timeout: 60000 });
    if (disposed || signal.aborted) return;
    if (!result.ok) {
      if (background) return;
      content.dataset.state = STATES[2];
      content.replaceChildren(UI.notice({
        tone: 'error', title: 'Voices could not load', body: result.error, live: true,
        action: UI.button({ label: 'Retry', onClick: load }),
      }));
      return;
    }
    const resolvedProjectId = result.data?.project_id || cacheProjectId;
    if (resolvedProjectId) cachedVoices = { projectId: resolvedProjectId, payload: result.data };
    applyPayload(result.data);
  };

  search.querySelector('input').addEventListener('input', render);
  method.querySelector('select').addEventListener('change', render);
  const cachedPayload = cachedVoices?.projectId === cacheProjectId
    ? cachedVoices.payload : null;
  if (cachedPayload) { applyPayload(cachedPayload); void load(true); }
  else await load();
  return () => {
    if (disposed) return;
    disposed = true;
    controller.cleanup();
  };
}
