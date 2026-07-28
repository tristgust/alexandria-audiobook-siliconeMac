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
    const states = ['absent', 'inactive', 'active', 'loading', 'playing', 'paused', 'failed'];
    const state = states.includes(options.state) ? options.state : 'paused';
    if (state === 'absent') return null;
    const unavailable = ['inactive', 'loading', 'failed'].includes(state);
    const root = mark(document.createElement('footer'), 'persistent-player', 'persistentPlayer');
    root.className = 'persistent-player persistent-player-host';
    root.dataset.state = state;
    root.setAttribute('aria-label', `Audiobook player, ${state}`);
    if (state === 'loading') root.setAttribute('aria-busy', 'true');

    const play = UI.iconButton({
      name: state === 'playing' ? 'pause' : state === 'loading' ? 'loader' : 'play',
      label: state === 'playing' ? 'Pause audiobook' : 'Play audiobook',
      disabled: unavailable,
      tooltip: state === 'playing' ? 'Pause' : 'Play',
      attributes: { 'data-player-control': 'play-pause' },
    });
    play.classList.add('persistent-player-play');

    const details = document.createElement('div');
    details.className = 'persistent-player__details persistent-player-meta';
    const title = document.createElement('strong');
    title.textContent = options.title || (state === 'inactive' ? 'No track selected' : state === 'loading' ? 'Loading chapter audio' : state === 'failed' ? 'Chapter audio unavailable' : 'Chapter 04 · The Crossing');
    const subtitle = document.createElement('span');
    subtitle.textContent = options.subtitle || (state === 'inactive' ? 'Choose a produced chunk or final audiobook' : state === 'failed' ? 'Retry when the model is ready' : 'Narrator · Take 2');
    details.append(title, subtitle);

    const back = UI.iconButton({
      name: 'rotate-left', label: 'Skip backward 10 seconds', disabled: unavailable,
      tooltip: 'Back 10 seconds', attributes: { 'data-player-control': 'skip-back' },
    });
    back.classList.add('persistent-player-skip');
    const forward = UI.iconButton({
      name: 'rotate-right', label: 'Skip forward 10 seconds', disabled: unavailable,
      tooltip: 'Forward 10 seconds', attributes: { 'data-player-control': 'skip-forward' },
    });
    forward.classList.add('persistent-player-skip');

    const timeline = document.createElement('div');
    timeline.className = 'persistent-player-timeline';
    const elapsed = document.createElement('time');
    elapsed.textContent = formatTime(options.value || 0);
    const seek = document.createElement('input');
    seek.type = 'range';
    seek.min = '0';
    seek.max = String(Math.max(1, Number(options.maximum) || 212));
    seek.value = String(Math.max(0, Number(options.value) || 0));
    seek.step = '1';
    seek.disabled = unavailable;
    seek.dataset.playerControl = 'timeline';
    seek.setAttribute('aria-label', 'Audiobook position');
    const duration = document.createElement('time');
    duration.textContent = formatTime(Number(seek.max));
    timeline.append(elapsed, seek, duration);

    const volume = document.createElement('label');
    volume.className = 'persistent-player-volume';
    volume.append(UI.icon('volume'));
    const volumeInput = document.createElement('input');
    volumeInput.type = 'range';
    volumeInput.min = '0';
    volumeInput.max = '1';
    volumeInput.step = '0.05';
    volumeInput.value = '1';
    volumeInput.dataset.playerControl = 'volume';
    volumeInput.setAttribute('aria-label', 'Volume');
    volume.append(volumeInput);

    const speed = document.createElement('select');
    speed.className = 'persistent-player-speed';
    speed.dataset.playerControl = 'speed';
    speed.setAttribute('aria-label', 'Playback speed');
    [['0.75', '0.75×'], ['1', '1×'], ['1.25', '1.25×'], ['1.5', '1.5×'], ['2', '2×']]
      .forEach(([value, label]) => {
        const option = document.createElement('option');
        option.value = value;
        option.textContent = label;
        option.selected = value === '1';
        speed.append(option);
      });

    const slot = document.createElement('span');
    slot.className = 'persistent-player-audio-slot';
    root.append(play, details, back, timeline, forward, volume, speed, slot);
    return root;
  };
})();
