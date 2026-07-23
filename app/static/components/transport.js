'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  const LEVELS = [24, 58, 36, 82, 46, 68, 32, 74, 52, 88, 40, 64, 30, 76, 48, 70];
  const mark = (node, primitive, factory) => {
    node.dataset.primitive = primitive;
    node.dataset.productionFactory = factory;
    return node;
  };
  const formatTime = (value) => {
    const seconds = Math.max(0, Math.round(Number(value) || 0));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  };

  UI.compactPlay = function compactPlay(options = {}) {
    const allowed = ['loading', 'ready', 'playing', 'paused', 'failed', 'disabled'];
    let state = allowed.includes(options.state) ? options.state : 'ready';
    const button = mark(document.createElement('button'), 'compact-play', 'compactPlay');
    button.type = 'button';
    button.className = 'compact-play';
    const render = () => {
      button.dataset.state = state;
      button.disabled = state === 'loading' || state === 'disabled';
      button.replaceChildren(UI.icon(state === 'loading' ? 'loader' : state === 'playing' ? 'pause' : 'play'));
      button.setAttribute('aria-label', options.label || (state === 'playing' ? 'Pause preview' : state === 'failed' ? 'Retry preview' : state === 'loading' ? 'Preview loading' : 'Play preview'));
      if (state === 'loading') button.setAttribute('aria-busy', 'true'); else button.removeAttribute('aria-busy');
    };
    button.addEventListener('click', () => {
      state = state === 'playing' ? 'paused' : state === 'failed' ? 'ready' : 'playing';
      render();
    });
    button.setPlaybackState = (nextState) => { if (allowed.includes(nextState)) { state = nextState; render(); } };
    render();
    return button;
  };

  UI.waveform = function waveform(options = {}) {
    const maximum = Number(options.maximum) || 180;
    let value = Math.max(0, Math.min(maximum, Number(options.value) || 0));
    const root = mark(document.createElement('div'), 'waveform', 'waveform');
    root.className = 'waveform';
    const slider = document.createElement('div');
    slider.className = 'waveform__slider';
    slider.tabIndex = options.disabled ? -1 : 0;
    slider.setAttribute('role', 'slider');
    slider.setAttribute('aria-label', options.label || 'Audio position');
    slider.setAttribute('aria-valuemin', '0');
    slider.setAttribute('aria-valuemax', String(maximum));
    if (options.disabled) slider.setAttribute('aria-disabled', 'true');
    const output = document.createElement('output');
    output.className = 'waveform__output';
    if (options.testId) {
      slider.dataset.test = options.testId;
      output.dataset.test = `${options.testId.replace(/-slider$/, '')}-output`;
    }
    const render = () => {
      slider.setAttribute('aria-valuenow', String(value));
      slider.setAttribute('aria-valuetext', `${formatTime(value)} of ${formatTime(maximum)}`);
      output.textContent = `${formatTime(value)} / ${formatTime(maximum)}`;
    };
    LEVELS.forEach((level) => {
      const bar = document.createElement('span');
      bar.className = 'waveform__bar';
      bar.style.setProperty('--waveform-level', `${level}%`);
      bar.setAttribute('aria-hidden', 'true');
      slider.append(bar);
    });
    slider.addEventListener('keydown', (event) => {
      if (options.disabled) return;
      const keys = { ArrowLeft: -5, ArrowDown: -5, ArrowRight: 5, ArrowUp: 5 };
      if (event.key === 'Home') value = 0;
      else if (event.key === 'End') value = maximum;
      else if (keys[event.key]) value = Math.max(0, Math.min(maximum, value + keys[event.key]));
      else return;
      event.preventDefault();
      render();
    });
    root.append(slider, output);
    render();
    return root;
  };

  function playerButton(name, label, control, disabled = false) {
    return UI.iconButton({ name, label, size: 'compact', disabled, tooltip: '', attributes: { 'data-player-control': control } });
  }

  UI.persistentPlayer = function persistentPlayer(options = {}) {
    const state = options.state || 'paused';
    const unavailable = state === 'loading' || state === 'failed';
    const root = mark(document.createElement('section'), 'persistent-player', 'persistentPlayer');
    root.className = 'persistent-player';
    root.dataset.state = state;
    root.setAttribute('aria-label', `Audio player, ${state}`);
    if (state === 'loading') root.setAttribute('aria-busy', 'true');
    const details = document.createElement('div');
    details.className = 'persistent-player__details';
    const title = document.createElement('strong');
    title.textContent = options.title || (state === 'loading' ? 'Loading chapter audio' : state === 'failed' ? 'Chapter audio unavailable' : 'Chapter 04 · The Crossing');
    const subtitle = document.createElement('div');
    subtitle.className = 'timecode';
    subtitle.textContent = options.subtitle || (state === 'failed' ? 'Retry when the model is ready' : 'Narrator · Take 2');
    details.append(title, subtitle);
    const transport = document.createElement('div');
    transport.className = 'persistent-player__transport';
    transport.append(
      playerButton('previous', 'Previous take', 'previous', unavailable),
      playerButton('skip-back', 'Skip back', 'skip-back', unavailable),
      playerButton(state === 'playing' ? 'pause' : state === 'loading' ? 'loader' : 'play', state === 'playing' ? 'Pause' : 'Play', 'play-pause', unavailable),
      playerButton('skip-forward', 'Skip forward', 'skip-forward', unavailable),
      playerButton('next', 'Next take', 'next', unavailable),
    );
    const timeline = UI.waveform({ value: state === 'loading' ? 0 : 48, maximum: 212, label: 'Chapter position', disabled: unavailable });
    const utility = document.createElement('div');
    utility.className = 'persistent-player__utility';
    const volumeLabel = document.createElement('label');
    volumeLabel.className = 'player-volume';
    volumeLabel.append(UI.icon('volume'));
    const volume = document.createElement('input');
    volume.type = 'range';
    volume.min = '0';
    volume.max = '100';
    volume.value = '72';
    volume.disabled = unavailable;
    volume.dataset.playerControl = 'volume';
    volume.setAttribute('aria-label', 'Volume');
    volumeLabel.append(volume);
    utility.append(volumeLabel, playerButton('queue', 'Open queue', 'queue'), playerButton('more', 'Player options', 'overflow'));
    root.append(details, transport, timeline, utility);
    return root;
  };
})();
