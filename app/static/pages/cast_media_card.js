'use strict';

import { castText } from './cast_model.js';

const UI = globalThis.AlexandriaUI;
const MEDIA_LEVELS = [42, 68, 34, 78, 50, 88, 46, 72, 38, 82, 54, 66, 32, 76, 48, 70];

const formatMediaTime = (value) => {
  const seconds = Math.max(0, Math.round(Number(value) || 0));
  return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
};

export function createCastMediaCard({ shell }) {
  const connectPlayer = (play, playerOptions) => {
    const player = shell.player.set(playerOptions);
    const sync = (state) => play.setPlaybackState(
      state === 'playing' ? 'playing' : state === 'failed' ? 'failed' : 'paused',
    );
    sync(player?.dataset.state || 'paused');
    player?.addEventListener('alexandriaplayerchange', (event) => {
      if (play.isConnected) sync(event.detail?.state || player?.dataset.state || 'paused');
    });
  };

  return function mediaCard({
    src, label, source, playerTitle, playerSubtitle, dataKey, unavailableCopy,
  }) {
    let duration = 0;
    const playable = Boolean(src);
    const card = document.createElement('div');
    card.className = 'cast-profile__media-card';
    card.dataset.mediaState = playable ? 'ready' : 'unavailable';
    const play = UI.compactPlay({
      state: playable ? 'ready' : 'disabled',
      labels: {
        ready: `Play ${label.toLowerCase()}`, paused: `Play ${label.toLowerCase()}`,
        playing: `Pause ${label.toLowerCase()}`, failed: `Retry ${label.toLowerCase()}`,
        disabled: `${label} unavailable`,
      },
      icons: {
        ready: 'fas fa-play', paused: 'fas fa-play', playing: 'fas fa-pause',
        failed: 'fas fa-rotate-right', disabled: 'fas fa-play',
      },
    });
    play.classList.add('cast-profile__media-play');
    if (dataKey) play.dataset[dataKey] = '';
    const main = document.createElement('div');
    main.className = 'cast-profile__media-main';
    const heading = document.createElement('div');
    heading.className = 'cast-profile__media-heading';
    heading.append(castText('strong', '', label),
      castText('span', 'metadata', source || unavailableCopy || 'Audio unavailable'));
    const graphic = document.createElement('div');
    graphic.className = 'cast-profile__media-graphic';
    graphic.setAttribute('aria-hidden', 'true');
    MEDIA_LEVELS.forEach((level) => {
      const bar = document.createElement('span');
      bar.style.setProperty('--cast-wave-level', `${level}%`);
      graphic.append(bar);
    });
    const time = castText('time', 'timecode cast-profile__media-time', playable ? '0:00 / --:--' : 'Not available');
    const timeline = document.createElement('div');
    timeline.className = 'cast-profile__media-timeline';
    timeline.append(graphic, time);
    main.append(heading, timeline);
    card.append(play, main);
    if (!playable) return card;
    const probe = document.createElement('audio');
    probe.preload = 'metadata';
    probe.src = src;
    probe.hidden = true;
    probe.addEventListener('loadedmetadata', () => {
      duration = Number.isFinite(probe.duration) ? probe.duration : 0;
      time.textContent = `0:00 / ${duration ? formatMediaTime(duration) : '--:--'}`;
    });
    probe.addEventListener('error', () => {
      card.dataset.mediaState = 'failed';
      time.textContent = 'Could not load';
    });
    card.append(probe);
    play.addEventListener('click', () => connectPlayer(play, {
      state: 'playing', src, position: 0, ...(duration ? { duration } : {}),
      title: playerTitle, subtitle: playerSubtitle,
    }));
    return card;
  };
}
