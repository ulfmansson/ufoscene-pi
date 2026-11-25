// The phone remote: buttons become commands for the TV page on the Pi.

import * as spotify from './spotify.js';
import { egg } from './eggs.js';

const $ = (id) => document.getElementById(id);

async function send(cmd, arg) {
  if (navigator.vibrate) navigator.vibrate(15);
  try {
    const r = await fetch('/api/cmd', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(arg === undefined ? { cmd } : { cmd, arg }),
    });
    if (!r.ok) throw new Error((await r.json()).error || r.status);
  } catch (e) {
    status(`Could not reach the Pi (${e.message})`, 'bad');
  }
  setTimeout(refresh, 600);
}

for (const b of document.querySelectorAll('[data-cmd]')) b.addEventListener('click', () => send(b.dataset.cmd));
for (const b of document.querySelectorAll('[data-audio]')) {
  b.setAttribute('role', 'radio');
  b.addEventListener('click', () => send('audio', b.dataset.audio));
}
$('pair').addEventListener('click', () => send('pair'));
$('lastfm').addEventListener('submit', (e) => {
  e.preventDefault();
  const user = $('lfm-user').value.trim();
  const key = $('lfm-key').value.trim();
  if (!user) return;
  send('lastfm', key ? { user, key } : { user });
  $('lfm-key').value = '';
});
$('lfm-off').addEventListener('click', () => send('lastfm', { user: '' }));
$('jump').addEventListener('submit', (e) => {
  e.preventDefault();
  const n = Number($('demo-no').value);
  const extra = egg(n);
  if (extra) playOnPhone(extra.title, extra.link);
  else if (n >= 1) send('demo', n);
  $('demo-no').blur();
});

function status(text, cls = '') {
  const el = $('status');
  el.textContent = text;
  el.className = `status ${cls}`;
}

async function refresh() {
  try {
    const s = await (await fetch('/api/state', { cache: 'no-store' })).json();
    $('version').textContent = s.version;
    if (!s.tv_connected) status('The TV page is not running yet…', 'bad');
    else status(s.demo ? `Connected · demo ${s.demo}` : 'Connected', 'ok');
    for (const b of document.querySelectorAll('[data-audio]')) b.setAttribute('aria-checked', String(b.dataset.audio === s.audio));
    const bt = s.bluetooth;
    $('phone-hint').hidden = s.audio !== 'phone';
    $('bt-row').hidden = !bt?.available;
    $('bt-hint').hidden = !bt?.available;
    $('bt-none').hidden = !bt || bt.available;
    $('pair').textContent = bt?.pairing ? 'Pairing… pick UFOSCENE on your phone' : bt?.device ? `Bluetooth: ${bt.device} · Pair another` : 'Pair Bluetooth';
    $('pair').disabled = !!bt?.pairing;
    if ((s.spotify?.client_id || '') !== clientId) {
      clientId = s.spotify?.client_id || '';
      spShow();
      spRefresh();
    }
    const lf = s.lastfm || {};
    $('lfm-state').textContent = lf.user && lf.key ? `From Last.fm: ${lf.user} ✓` : lf.user ? 'Last.fm needs an API key.' : '';
    $('lfm-key-row').hidden = !!lf.built_in_key;
    $('lfm-off').hidden = !lf.user;
    if (document.activeElement !== $('lfm-user') && lf.user) $('lfm-user').value = lf.user;
    const np = s.nowplaying;
    const via = np?.source === 'airplay' ? 'AirPlay' : 'Bluetooth';
    $('nowplaying').textContent = np && (np.artist || np.title) ? `♪ ${np.artist} — ${np.title} · ${via}${np.playing ? '' : ' (paused)'}` : '';
    $('fps').textContent = s.fps == null ? '' : `Pi speed: ${Math.round(s.fps)} frames per second${s.fps < 25 ? ' — too slow for smooth demos' : ''}`;
  } catch {
    status('Looking for the Pi…', 'bad');
  }
}

// ufoscene's playlists (the bundle's ufo/playlists/index.json), as links into the Spotify app
const GROUPS = { decade: 'Decades', genre: 'Genres', artist: 'Artists', computer: 'Computer music' };
async function showPlaylists() {
  const box = $('playlists');
  let list = [];
  try {
    list = await (await fetch('/ufo/playlists/index.json')).json();
  } catch {}
  const spotify = (p) => typeof p.spotify === 'string' && p.spotify.startsWith('https://open.spotify.com/');
  for (const [group, heading] of Object.entries(GROUPS)) {
    const items = list.filter((p) => p.group === group && spotify(p));
    if (!items.length) continue;
    const h = document.createElement('h3');
    h.textContent = heading;
    const grid = document.createElement('div');
    grid.className = 'grid';
    for (const p of items) {
      const a = document.createElement('a');
      a.className = 'button';
      a.href = p.spotify;
      a.target = '_blank';
      a.rel = 'noopener';
      a.textContent = p.title;
      a.addEventListener('click', (e) => playOnPhone(p.title, p.spotify, e));
      grid.append(a);
    }
    box.append(h, grid);
  }
  if (!box.childElementCount) box.textContent = 'No playlists in this version.';
}

// Play a Spotify playlist or album with this phone as the player, and switch the Pi to the phone.
// Logged in to Spotify control: start it right here. Otherwise the link opens the Spotify app:
// a link the user tapped (e) opens by itself, anything else (a secret demo number) is opened here.
async function playOnPhone(title, link, e) {
  if (spotify.connected()) {
    e?.preventDefault();
    try {
      await spotify.playLink(link);
      spMsg(`Playing ${title}.`);
      setTimeout(spRefresh, 1500);
    } catch (err) {
      spMsg(err.message, true);
      if (err.reason === 'NO_ACTIVE_DEVICE') return; // open Spotify first; the hint says so
    }
  } else if (!e) {
    const w = window.open(link, '_blank'); // still inside the tap, so it is not blocked
    if (w) w.opener = null;
    else location.href = link;
  }
  fetch('/api/cmd', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ cmd: 'audio', arg: 'phone' }),
    keepalive: true,
  }).catch(() => {});
  $('phone-hint').hidden = false;
}

// ------------------------------------------------------------ Spotify control (optional)

let clientId = '';
function spMsg(text, bad = false) {
  $('sp-msg').textContent = text;
  $('sp-msg').className = `hint ${bad ? 'bad' : ''}`;
}
function spShow() {
  const on = spotify.connected();
  $('sp-player').hidden = !(on && clientId);
  $('sp-login').hidden = on || !clientId;
  $('sp-setup').hidden = !!clientId && !$('sp-setup').open;
  if (!clientId) $('sp-setup').hidden = false;
}
async function spRefresh() {
  if (!spotify.connected() || !clientId || document.visibilityState !== 'visible') return;
  try {
    const n = await spotify.nowPlaying();
    $('sp-now').textContent = n?.title ? `${n.playing ? '▶' : '⏸'} ${n.artist} — ${n.title}${n.device ? ` · on ${n.device}` : ''}` : 'Nothing playing on Spotify right now.';
    $('sp-toggle').textContent = n?.playing ? '⏸' : '▶';
    $('sp-toggle').dataset.playing = n?.playing ? '1' : '';
  } catch (e) {
    $('sp-now').textContent = e.message;
  }
}
const spDo = (fn) => async () => {
  if (navigator.vibrate) navigator.vibrate(15);
  try {
    await fn();
    spMsg('');
  } catch (e) {
    spMsg(e.message, true);
  }
  setTimeout(spRefresh, 700);
};
$('sp-redirect').textContent = spotify.REDIRECT_URI;
$('sp-prev').addEventListener('click', spDo(spotify.previous));
$('sp-next').addEventListener('click', spDo(spotify.next));
$('sp-toggle').addEventListener('click', spDo(() => ($('sp-toggle').dataset.playing ? spotify.pause() : spotify.play())));
$('sp-login-btn').addEventListener('click', () => spotify.login(clientId));
$('sp-logout').addEventListener('click', () => {
  spotify.logout();
  spMsg('Logged out of Spotify on this phone.');
  spShow();
});
$('sp-change').addEventListener('click', () => {
  $('sp-setup').hidden = false;
  $('sp-setup').open = true;
  $('sp-client').value = clientId;
});
$('sp-app').addEventListener('submit', (e) => {
  e.preventDefault();
  send('spotify-app', { client_id: $('sp-client').value.trim() });
  $('sp-setup').open = false;
});
$('sp-clear').addEventListener('click', () => {
  send('spotify-app', { client_id: '' });
  spotify.logout();
});
spotify.finishLogin().then((err) => {
  if (err === null) return;
  spMsg(err || 'Logged in to Spotify ✓', !!err);
  spShow();
  spRefresh();
});
setInterval(spRefresh, 5000);

showPlaylists();
refresh();
setInterval(refresh, 4000);
document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && refresh());
