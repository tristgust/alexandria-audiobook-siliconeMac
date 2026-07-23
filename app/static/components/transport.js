'use strict';

(() => {
  const UI = globalThis.AlexandriaUI ||= {};
  const LEVELS = [24, 58, 36, 82, 46, 68, 32, 74, 52, 88, 40, 64, 30, 76, 48, 70];

  function formatTime(value) {
    const seconds = Math.max(0, Math.round(Number(value) || 0));
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
  }

  UI.compactPlay = function compactPlay(options = {}) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'compact-play';
    button.dataset.primitive = 'compact-play';
    button.dataset.state = options.state || 'idle';
    button.setAttribute('aria-label', options.label || 'Play preview');
    button.textContent = options.state === 'playing' ? '❚❚' : '▶';
    button.addEventListener('click', () => {
      const playing = button.dataset.state === 'playing';
      button.dataset.state = playing ? 'idle' : 'playing';
      button.textContent = playing ? '▶' : '❚❚';
      button.setAttribute('aria-label', playing ? 'Play preview' : 'Pause preview');
    });
    return button;
  };

  UI.waveform = function waveform(options = {}) {
    const maximum = Number(options.maximum) || 180;
    let value = Math.max(0, Math.min(maximum, Number(options.value) || 0));
    const root = document.createElement('div');
    root.className = 'waveform';
    root.dataset.primitive = 'waveform';
    const slider = document.createElement('div');
    slider.className = 'waveform__slider';
    slider.tabIndex = 0;
    slider.setAttribute('role', 'slider');
    slider.setAttribute('aria-label', options.label || 'Audio position');
    slider.setAttribute('aria-valuemin', '0');
    slider.setAttribute('aria-valuemax', String(maximum));
    const output = document.createElement('output');
    output.className = 'waveform__output';
    if (options.testId) {
      slider.dataset.test = options.testId;
      output.dataset.test = `${options.testId.replace(/-slider$/, '')}-output`;
    }
    function render() {
      slider.setAttribute('aria-valuenow', String(value));
      slider.setAttribute('aria-valuetext', `${formatTime(value)} of ${formatTime(maximum)}`);
      output.textContent = `${formatTime(value)} / ${formatTime(maximum)}`;
    }
    LEVELS.forEach((level) => {
      const bar = document.createElement('span');
      bar.className = 'waveform__bar';
      bar.style.setProperty('--waveform-level', `${level}%`);
      bar.setAttribute('aria-hidden', 'true');
      slider.append(bar);
    });
    slider.addEventListener('keydown', (event) => {
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

  UI.persistentPlayer = function persistentPlayer(options = {}) {
    const state = options.state || 'active';
    const root = document.createElement('section');
    root.className = 'persistent-player';
    root.dataset.primitive = 'persistent-player';
    root.dataset.state = state;
    root.setAttribute('aria-label', 'Audio player');
    const details = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = options.title || (state === 'inactive' ? 'Nothing playing' : 'Chapter 04 · The Crossing');
    const subtitle = document.createElement('div');
    subtitle.className = 'timecode';
    subtitle.textContent = options.subtitle || (state === 'inactive' ? 'Choose a take to preview' : 'Narrator · Take 2');
    details.append(title, subtitle);
    if (state === 'inactive') {
      root.append(details, UI.button({ label: 'Open queue', variant: 'quiet', size: 'compact' }));
      return root;
    }
    const play = document.createElement('button');
    play.type = 'button';
    play.className = 'persistent-player__play';
    play.setAttribute('aria-label', 'Play chapter');
    play.textContent = '▶';
    root.append(play, details, UI.waveform({ value: 48, maximum: 212, label: 'Chapter position' }));
    return root;
  };
})();
