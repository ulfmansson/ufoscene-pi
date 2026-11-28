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

// ------------------------------------------------------------ Wi-Fi

function wifiMsg(text, bad = false) {
  $('wifi-msg').textContent = text;
  $('wifi-msg').className = `hint ${bad ? 'bad' : ''}`;
}
async function post(path, body) {
  const r = await fetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || `The Pi said ${r.status}`);
  return j;
}
$('wifi-code-btn').addEventListener('click', async () => {
  try {
    await post('/api/wifi/code');
    wifiMsg('The code is on the TV now.');
    $('wifi-code').focus();
  } catch (e) {
    wifiMsg(e.message, true);
  }
});
$('wifi-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const ssid = $('wifi-ssid').value;
  try {
    await post('/api/wifi', { ssid, psk: $('wifi-psk').value, code: $('wifi-code').value });
    $('wifi-psk').value = '';
    $('wifi-code').value = '';
    wifiMsg(`The Pi is joining ${ssid}. This takes up to a minute; the TV shows how it went.`);
  } catch (err) {
    wifiMsg(err.message, true);
  }
  setTimeout(refresh, 1500);
});
let lastWifi = '';
function showWifi(w, urls) {
  $('wifi').hidden = !w?.available;
  if (!w?.available) return;
  // on the setup network the Wi-Fi is the reason to be here: put it first
  if (w.state === 'setup' && document.querySelector('main').firstElementChild !== $('wifi')) document.querySelector('main').prepend($('wifi'));
  $('wifi-now').textContent = {
    connected: `On ${w.ssid}.`,
    setup: 'The Pi could not reach its Wi-Fi and opened its setup network. Give it your Wi-Fi below.',
    connecting: `Joining ${w.ssid}…`,
    offline: 'Looking for a known Wi-Fi…',
    cable: 'On a network cable.',
  }[w.state] || '';
  const nets = $('wifi-nets');
  const names = (w.networks || []).map((n) => n.ssid);
  if (names.join('\n') !== [...nets.options].map((o) => o.value).join('\n')) {
    nets.replaceChildren(...names.map((n) => Object.assign(document.createElement('option'), { value: n })));
  }
  const last = JSON.stringify(w.last);
  if (w.last && last !== lastWifi && lastWifi) {
    wifiMsg(w.last.result === 'joined' ? `Joined ${w.last.ssid} ✓` : w.last.error, w.last.result !== 'joined');
  }
  lastWifi = last;
  if (urls?.mdns) $('wifi-mdns').textContent = urls.mdns;
}

function status(text, cls = '') {
  const el = $('status');
  el.textContent = text;
  el.className = `status ${cls}`;
}

async function refresh() {
  try {
    const s = await (await fetch('/api/state', { cache: 'no-store' })).json();
    $('version').textContent = s.version;
    const up = s.update;
    const when = up?.checked ? new Date(up.checked * 1000).toLocaleDateString() : '';
    $('update').textContent = !up ? '' : {
      updated: `Updated by itself from ${up.previous} on ${when}`,
      failed: `${up.latest} did not start here, so it went back to ${up.running} (${when})`,
      skipped: `Staying on ${up.running}: ${up.latest} did not start here`,
      off: 'Automatic updates are off',
    }[up.result] || (up.result === 'current' || up.result === 'error' ? `Updates are checked every night · last ${when}` : '');
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
    showWifi(s.wifi, s.urls);
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

// null until the Pi's first state: then even "no Client ID" ('') counts as news, so spShow runs
// and the setup box appears. Starting at '' left the whole section hidden on a Pi without one.
let clientId = null;
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

// ------------------------------------------------------------ the shows

// 🎸 Concert and 🇺🇦 Ukraine start a show on the TV's pixel stage; the same button again ends it.
// Logged in to Spotify control, this phone also starts the show's setlist (ufoscene's
// playlists/*.txt) in order, as ufoscene.net does with C and U, and the Pi listens to the phone.
// Every new song brings the next act. Ending a show leaves the music alone.
const SHOWS = {
  concert: { setlist: '/ufo/playlists/concert.txt', name: 'The concert' },
  ukraine: { setlist: '/ufo/playlists/ukraine.txt', name: 'Ukraine at Eurovision' },
};
function showMsg(text, bad = false) {
  $('show-msg').hidden = !text;
  $('show-msg').textContent = text;
  $('show-msg').className = `hint ${bad ? 'bad' : ''}`;
}
const wait = (ms) => new Promise((r) => setTimeout(r, ms));

async function startShow(show) {
  let s = {};
  try {
    s = await (await fetch('/api/state', { cache: 'no-store' })).json();
  } catch {}
  const withMusic = s.show !== show && !!clientId && spotify.connected();
  if (!withMusic) showMsg('');
  if (withMusic && s.audio !== 'phone') {
    await send('audio', 'phone');
    await wait(1500); // the TV starts ufoscene again for the new source; the show comes after
  }
  send(show);
  if (withMusic) playSetlist(show);
}

async function playSetlist(show) {
  const { setlist, name } = SHOWS[show];
  showMsg(`${name}: finding the songs on Spotify…`);
  try {
    const r = await fetch(setlist);
    if (!r.ok) throw new Error('this ufoscene version has no setlist for it.');
    const songs = spotify.setlistSongs(await r.text());
    const uris = (await Promise.all(songs.map((l) => spotify.findTrack(l).catch(() => '')))).filter(Boolean);
    if (!uris.length) throw new Error('Spotify found none of its songs.');
    await spotify.playInOrder(uris);
    showMsg(`${name}: ${uris.length} songs on Spotify, in order. Send the sound to UFOSCENE and enjoy the show!`);
    setTimeout(spRefresh, 1500);
  } catch (e) {
    showMsg(`${name}: ${e.message}`, true);
  }
}
for (const b of document.querySelectorAll('[data-show]')) b.addEventListener('click', () => startShow(b.dataset.show));

showPlaylists();
refresh();
setInterval(refresh, 4000);
document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && refresh());
