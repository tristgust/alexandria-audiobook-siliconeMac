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
      const iconClass = options.icons?.[state];
      if (iconClass) {
        button.replaceChildren(UI.iconFromClass(iconClass, state === 'failed' ? 'refresh' : 'play'));
      } else {
        button.replaceChildren(UI.icon(state === 'loading' ? 'loader' : state === 'playing' ? 'pause' : 'play'));
      }
      const stateLabel = options.labels?.[state];
      button.setAttribute('aria-label', stateLabel || options.label || (state === 'playing' ? 'Pause preview' : state === 'failed' ? 'Retry preview' : state === 'loading' ? 'Preview loading' : 'Play preview'));
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
      slider.dispatchEvent(new Event('input', { bubbles: true }));
      options.onInput?.(value);
    });
    root.append(slider, output);
    render();
    return root;
  };

  function playerButton(name, label, control, disabled = false) {
    return UI.iconButton({
      name,
      label, size: 'compact', disabled, tooltip: '',
      attributes: { 'data-player-control': control },
    });
  }

  function playerTimeline({ value, maximum, disabled, onInput }) {
    let limit = Math.max(.01, Number(maximum) || 1);
    const root = document.createElement('div');
    root.className = 'persistent-player__timeline';
    const elapsed = document.createElement('time');
    elapsed.textContent = formatTime(value);
    const range = document.createElement('input');
    range.type = 'range';
    range.min = '0';
    range.max = String(limit);
    range.value = String(value);
    range.step = '0.01';
    range.disabled = disabled;
    range.setAttribute('aria-label', 'Audio position');
    const duration = document.createElement('time');
    duration.textContent = formatTime(limit);
    const syncValueText = () => {
      range.setAttribute('aria-valuetext', `${formatTime(range.value)} of ${formatTime(limit)}`);
    };
    const setValue = (nextValue, notify = false) => {
      const bounded = Math.max(0, Math.min(limit, Number(nextValue) || 0));
      range.value = String(bounded);
      elapsed.textContent = formatTime(bounded);
      syncValueText();
      if (notify) onInput?.(bounded);
      return bounded;
    };
    const setMaximum = (nextMaximum) => {
      limit = Math.max(.01, Number(nextMaximum) || 1);
      range.max = String(limit);
      duration.textContent = formatTime(limit);
      syncValueText();
      return limit;
    };
    range.addEventListener('input', () => setValue(range.value, true));
    syncValueText();
    root.append(elapsed, range, duration);
    return { root, range, setValue, setMaximum, getMaximum: () => limit };
  }

  UI.persistentPlayer = function persistentPlayer(options = {}) {
    const states = ['absent', 'inactive', 'active', 'loading', 'playing', 'paused', 'failed'];
    let state = states.includes(options.state) ? options.state : 'paused';
    if (state === 'absent') return null;
    const unavailable = ['inactive', 'loading', 'failed'].includes(state);
    const root = mark(document.createElement('section'), 'persistent-player', 'persistentPlayer');
    root.className = 'persistent-player';
    const contextTitle = options.title || (state === 'inactive'
      ? 'No track selected'
      : state === 'loading'
        ? 'Loading chapter audio'
        : state === 'failed'
          ? 'Chapter audio unavailable'
          : 'Chapter 04 · The Crossing');
    const emit = (action, detail = {}) => {
      const eventDetail = { action, state, ...detail };
      root.dispatchEvent(new CustomEvent('alexandriaplayerchange', { detail: eventDetail, bubbles: true }));
      options.onAction?.(eventDetail);
    };
    const syncState = () => {
      root.dataset.state = state;
      root.setAttribute('aria-label', `Audio player, ${state}: ${contextTitle}`);
      root.toggleAttribute('aria-busy', state === 'loading');
    };
    syncState();

    const transport = document.createElement('div');
    transport.className = 'persistent-player__transport';
    const back = playerButton('skip-back', 'Skip back ten seconds', 'skip-back', unavailable);
    const playPause = playerButton(
      state === 'playing' ? 'pause' : state === 'loading' ? 'loader' : 'play',
      state === 'playing' ? 'Pause' : 'Play', 'play-pause', unavailable,
    );
    const forward = playerButton('skip-forward', 'Skip forward ten seconds', 'skip-forward', unavailable);
    transport.append(back, playPause, forward);

    const source = String(options.src || '').trim();
    let media = null;
    const suppliedDuration = Number(options.duration);
    const suppliedPosition = Number(options.position);
    let maximum = Math.max(.01, Number.isFinite(suppliedDuration) && suppliedDuration > 0 ? suppliedDuration : 212);
    let value = state === 'loading' || state === 'inactive' ? 0
      : Math.max(0, Math.min(maximum, Number.isFinite(suppliedPosition) ? suppliedPosition : 48));
    const timeline = playerTimeline({
      value,
      maximum,
      disabled: unavailable,
      onInput: (nextValue) => {
        value = nextValue;
        if (media && Number.isFinite(media.duration)) media.currentTime = value;
        emit('seek', { position: value, duration: maximum });
      },
    });
    const renderPlayPause = () => {
      playPause.replaceChildren(UI.icon(state === 'playing' ? 'pause' : 'play'));
      playPause.setAttribute('aria-label', state === 'playing' ? 'Pause' : 'Play');
      playPause.dataset.tooltip = state === 'playing' ? 'Pause' : 'Play';
      syncState();
    };
    const handlePlayRejection = (error) => {
      const message = error?.message || 'Audio playback failed.';
      const blocked = error?.name === 'NotAllowedError' || /user (?:didn't|did not) interact|not allowed/i.test(message);
      state = blocked ? 'paused' : 'failed';
      renderPlayPause();
      emit(blocked ? 'autoplay-blocked' : 'error', { message, src: source || null });
    };
    back.addEventListener('click', () => {
      value = timeline.setValue(value - 10);
      if (media && Number.isFinite(media.duration)) media.currentTime = value;
      emit('skip-back', { position: value, duration: maximum });
    });
    playPause.addEventListener('click', () => {
      if (media) {
        if (media.paused) {
          media.play().catch(handlePlayRejection);
        } else media.pause();
        return;
      }
      state = state === 'playing' ? 'paused' : 'playing';
      renderPlayPause();
      emit(state === 'playing' ? 'play' : 'pause', { position: value, duration: maximum });
    });
    forward.addEventListener('click', () => {
      value = timeline.setValue(value + 10);
      if (media && Number.isFinite(media.duration)) media.currentTime = value;
      emit('skip-forward', { position: value, duration: maximum });
    });

    const details = document.createElement('div');
    details.className = 'persistent-player__details';
    const title = document.createElement('strong');
    title.textContent = contextTitle;
    const subtitle = document.createElement('div');
    subtitle.className = 'timecode';
    subtitle.textContent = options.subtitle || (state === 'inactive' ? 'Choose a produced chunk or final audiobook' : state === 'failed' ? 'Retry when the model is ready' : 'Narrator · Take 2');
    details.append(title, subtitle);

    const utility = document.createElement('div');
    utility.className = 'persistent-player__utility';
    const volumeLabel = document.createElement('label');
    volumeLabel.className = 'player-volume';
    const volumeIcon = document.createElement('span');
    volumeIcon.className = 'player-volume__icon';
    volumeIcon.setAttribute('aria-hidden', 'true');
    volumeIcon.append(UI.icon('volume'));
    volumeLabel.append(volumeIcon);
    const volume = document.createElement('input');
    volume.type = 'range';
    volume.min = '0';
    volume.max = '100';
    volume.value = String(Math.max(0, Math.min(100, Number(options.volume) || 72)));
    volume.disabled = unavailable;
    volume.dataset.playerControl = 'volume';
    volume.setAttribute('aria-label', 'Volume');
    const syncVolumeIcon = () => {
      const level = Number(volume.value);
      volumeIcon.replaceChildren(UI.icon(level <= 0 ? 'volume-off' : 'volume'));
      volumeIcon.dataset.level = level <= 0 ? 'muted' : level < 50 ? 'low' : 'high';
      volume.setAttribute('aria-valuetext', `${level} percent`);
    };
    volume.addEventListener('input', () => {
      syncVolumeIcon();
      if (media) media.volume = Number(volume.value) / 100;
      emit('volume', { volume: Number(volume.value) / 100 });
    });
    syncVolumeIcon();
    volumeLabel.append(volume);

    const queueButton = playerButton('queue', 'Open queue', 'queue', unavailable);
    const queue = Array.isArray(options.queue) ? options.queue : [];
    const queuePopover = UI.popover({
      opener: queueButton,
      label: 'Playback queue',
      items: queue.length ? queue.map((item, index) => ({
        label: item.label || item.title || `Queued item ${index + 1}`,
        onSelect: () => {
          emit('queue-select', { index, item });
          options.onQueueSelect?.(item, index);
        },
      })) : [{ label: 'No additional audio queued', disabled: true }],
    });
    queuePopover.classList.add('persistent-player__popover');

    let speed = Number(options.speed) || 1;
    if (source) {
      media = document.createElement('audio');
      media.className = 'persistent-player__media';
      media.hidden = true;
      media.preload = 'metadata';
      media.src = source;
      media.volume = Number(volume.value) / 100;
      media.playbackRate = speed;
      root.dataset.mediaSource = source;
      const updateFromMedia = () => {
        value = timeline.setValue(media.currentTime || 0);
      };
      media.addEventListener('loadedmetadata', () => {
        if (Number.isFinite(media.duration) && media.duration > 0) {
          maximum = timeline.setMaximum(media.duration);
          value = timeline.setValue(Math.min(value, maximum));
          if (value > 0) media.currentTime = value;
          emit('metadata', { position: value, duration: maximum, src: source });
        }
      });
      media.addEventListener('timeupdate', updateFromMedia);
      media.addEventListener('play', () => {
        state = 'playing';
        renderPlayPause();
        emit('play', { position: media.currentTime || value, duration: maximum, src: source });
      });
      media.addEventListener('pause', () => {
        if (media.ended) return;
        state = 'paused';
        renderPlayPause();
        emit('pause', { position: media.currentTime || value, duration: maximum, src: source });
      });
      media.addEventListener('ended', () => {
        value = timeline.setValue(maximum);
        state = 'paused';
        renderPlayPause();
        emit('ended', { position: value, duration: maximum, src: source });
      });
      media.addEventListener('error', () => {
        state = 'failed';
        renderPlayPause();
        emit('error', { message: 'Audio source could not be played.', src: source });
      });
    }
    const optionsButton = playerButton('more', `Player options, ${speed}× speed`, 'overflow', unavailable);
    const optionsPopover = UI.popover({
      opener: optionsButton,
      label: 'Player options',
      items: [0.75, 1, 1.25, 1.5, 2].map((value) => ({
        label: `${value}× speed${value === speed ? ' · Current' : ''}`,
        onSelect: () => {
          speed = value;
          if (media) media.playbackRate = speed;
          optionsButton.setAttribute('aria-label', `Player options, ${speed}× speed`);
          emit('speed', { speed });
        },
      })),
    });
    optionsPopover.classList.add('persistent-player__popover');
    utility.append(volumeLabel, queuePopover, optionsPopover);

    root.append(transport, timeline.root, details, utility);
    if (media) root.append(media);
    root.playerCleanup = () => {
      queuePopover.popoverCleanup?.();
      optionsPopover.popoverCleanup?.();
      if (media) {
        media.pause();
        media.removeAttribute('src');
        media.load();
      }
    };
    root.getPlayerState = () => ({
      state,
      position: media ? media.currentTime || value : value,
      duration: media && Number.isFinite(media.duration) ? media.duration : maximum,
      volume: Number(volume.value) / 100,
      speed,
      src: source || null,
      native: Boolean(media),
    });
    if (media && state === 'playing') media.play().catch(handlePlayRejection);
    return root;
  };
})();
