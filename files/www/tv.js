// The TV page: runs ufoscene in a same-origin iframe and drives it with the same keys a
// keyboard would send, on commands from the phone remote (server-sent events from server.py).

const $ = (id) => document.getElementById(id);
const frame = $('ufo');
const overlay = $('connect');
const fpsBox = $('fps');
const toast = $('toast');

const CONNECT_SHOW_MS = 10_000; // the QR code at boot; the remote's QR code button brings it back
const MIN_FPS = 25; // the bar a Pi model has to clear to be supported

// First run: skip ufoscene's setup guide, use the radio, and steady 30 fps TV mode.
const seed = { 'ufo.guided': '1', 'ufo.tv': '1' };
for (const [k, v] of Object.entries(seed)) if (localStorage.getItem(k) === null) localStorage.setItem(k, v);
if (!localStorage.getItem('ufo.audio')) localStorage.setItem('ufo.audio', 'radio');

// Song names from Last.fm (set on the remote): ufoscene reads these when it starts. The key comes
// from the server on the Pi itself, never through the remote.
async function applyLastfm() {
  try {
    const lf = await (await fetch('/api/lastfm', { cache: 'no-store' })).json();
    if (lf.user && lf.key) {
      localStorage.setItem('ufo.songs', 'lastfm');
      localStorage.setItem('ufo.lastfm.user', lf.user);
      localStorage.setItem('ufo.lastfm.key', lf.key);
      localStorage.setItem('ufopi.lastfm', '1');
      return lf.user;
    }
    if (localStorage.getItem('ufopi.lastfm')) {
      localStorage.setItem('ufo.songs', 'none');
      localStorage.removeItem('ufopi.lastfm');
    }
  } catch {}
  return '';
}
const lastfmOn = () => localStorage.getItem('ufo.songs') === 'lastfm';
applyLastfm().then(() => (frame.src = '/ufo/'));

function say(text, ms = 3000) {
  toast.textContent = text;
  toast.style.opacity = 1;
  clearTimeout(say.t);
  say.t = setTimeout(() => (toast.style.opacity = 0), ms);
}

const win = () => frame.contentWindow;
function key(k) {
  win()?.dispatchEvent(new KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
}
const running = () => win()?.document.body.classList.contains('running');
const ufo = () => win()?.ufo;

// ufoscene starts from its panel with Enter; once the page is up, start it. Compiling its
// WebAssembly takes well over 20 s on a Pi 2, so wait for it for up to 10 minutes.
frame.addEventListener('load', () => {
  clearTimeout(tryStart.t);
  tryStart(600);
});
function tryStart(n) {
  if (ufo()?.ex) {
    if (!running()) key('Enter');
  } else if (n > 0) tryStart.t = setTimeout(() => tryStart(n - 1), 1000);
}

// ------------------------------------------------------------ connect overlay

async function showConnect(ms = CONNECT_SHOW_MS) {
  try {
    const s = await (await fetch('/api/state')).json();
    $('url-mdns').textContent = s.urls.mdns;
    $('url-ip').textContent = s.urls.ip ? `or ${s.urls.ip}` : '';
    $('qr').src = `/api/qr.svg?text=${encodeURIComponent(s.urls.ip || s.urls.mdns)}`;
  } catch {}
  overlay.hidden = false;
  clearTimeout(showConnect.t);
  if (ms) showConnect.t = setTimeout(() => (overlay.hidden = true), ms);
}

// ------------------------------------------------------------ audio sources

// 'phone' is a phone playing to the Pi over Bluetooth or AirPlay: its sound is captured from
// the audio output's monitor, which ufoscene sees as a microphone.
function setAudio(source) {
  const kind = source === 'radio' ? 'radio' : 'mic';
  localStorage.setItem('ufo.audio', kind);
  localStorage.setItem('ufopi.audio', source);
  // ufoscene picks up a new source on Start: open the panel and start again
  if (running()) key('p');
  setTimeout(() => key('Enter'), 300);
  say(source === 'radio' ? 'Internet radio' : source === 'phone' ? 'Music from your phone' : 'Microphone / line in');
  report({ audio: source });
}

// ------------------------------------------------------------ self-test

let selftest = false;
let frames = 0;
let lastFps = null;
// Counted on this page, not the iframe: a frame callback left with the iframe's document is
// dropped when ufoscene reloads, and the count would stay at 0 for good.
function countFrames() {
  frames++;
  requestAnimationFrame(countFrames);
}
requestAnimationFrame(countFrames);
setInterval(() => {
  lastFps = frames / 5;
  frames = 0;
  if (selftest) {
    fpsBox.textContent = `${lastFps.toFixed(0)} fps`;
    fpsBox.className = lastFps >= MIN_FPS ? '' : lastFps >= MIN_FPS * 0.8 ? 'slow' : 'bad';
  }
  report({ fps: lastFps, demo: currentDemo() });
}, 5000);

function currentDemo() {
  const u = ufo();
  try {
    return u?.ex ? u.ex.ufo_current_demo(u.demo) + 1 : null;
  } catch {
    return null;
  }
}

function report(state) {
  fetch('/api/state', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(state) }).catch(() => {});
}

// ------------------------------------------------------------ commands

const KEYS = {
  next: 'ArrowRight',
  prev: 'ArrowLeft',
  random: 'r',
  scene: 'n',
  'song-next': ' ',
  'song-prev': 'b',
  hud: 'h',
  tv: 't',
  panel: 'p',
  start: 'Enter',
};

function handle({ cmd, arg }) {
  if (KEYS[cmd]) {
    if (cmd === 'song-next' && !running()) key('Enter'); // Space on the panel would only start
    else key(KEYS[cmd]);
    return;
  }
  switch (cmd) {
    case 'demo': {
      const u = ufo();
      if (u?.ex) {
        u.ex.ufo_set_demo(u.demo, arg - 1);
        if (!running()) key('Enter');
      }
      break;
    }
    case 'audio':
      setAudio(arg);
      break;
    case 'overlay':
      if (overlay.hidden) showConnect(0);
      else overlay.hidden = true;
      break;
    case 'selftest':
      selftest = !selftest;
      fpsBox.hidden = !selftest;
      say(selftest ? `Self-test: needs ${MIN_FPS} fps or more` : 'Self-test off');
      break;
    case 'nowplaying': {
      // a phone plays to the Pi (Bluetooth or AirPlay): switch to it, and tell the demo the song
      const song = `${arg?.artist || ''}\u0000${arg?.title || ''}`;
      if (arg?.playing && localStorage.getItem('ufopi.audio') !== 'phone') setAudio('phone');
      // with Last.fm on, ufoscene gets the songs from there: two sources would fight over the demo
      if ((arg?.artist || arg?.title) && song !== lastSong && !lastfmOn()) {
        lastSong = song;
        // after a switch ufoscene restarts its sound; give it a moment before the new song
        setTimeout(() => {
          try {
            ufo()?.sendTrack?.(arg.artist || '', arg.title || '');
          } catch {}
        }, 1500);
      }
      break;
    }
    case 'pair':
      pairing(true);
      break;
    case 'lastfm':
      // ufoscene picks Last.fm up when it starts: reload it (it starts itself again)
      applyLastfm().then((user) => {
        frame.contentWindow?.location.reload();
        say(user ? `Song names from Last.fm: ${user}` : 'Last.fm off', 4000);
      });
      break;
    case 'bluetooth':
      if (!arg?.pairing) pairing(false);
      if (arg?.device && arg.device !== lastDevice) say(`Connected: ${arg.device}`, 4000);
      lastDevice = arg?.device || null;
      break;
    case 'reload':
      location.reload();
      break;
    case 'wifi-code':
      wifiCode();
      break;
    case 'wifi':
      wifiState(arg);
      break;
  }
}

// ------------------------------------------------------------ Wi-Fi

// The code a Wi-Fi change on the remote needs. Fetched from the Pi itself, never broadcast.
async function wifiCode() {
  try {
    const c = await (await fetch('/api/wifi/code', { cache: 'no-store' })).json();
    if (!c.code) return;
    $('wifi-code-digits').textContent = c.code;
    $('wifi-code').hidden = false;
    clearTimeout(wifiCode.t);
    wifiCode.t = setTimeout(() => ($('wifi-code').hidden = true), c.seconds * 1000);
  } catch {}
}

// QR code text to join a Wi-Fi network; \ ; , : " are escaped
const wifiQr = (ssid, pw) => {
  const esc = (s) => s.replace(/([\\;,:"])/g, '\\$1');
  return `WIFI:T:WPA;S:${esc(ssid)};P:${esc(pw)};;`;
};
const qr = (text) => `/api/qr.svg?text=${encodeURIComponent(text)}`;

let lastWifi = null;
async function wifiState(w) {
  if (!w) return;
  if (w.state === 'connecting') $('wifi-code').hidden = true;
  const box = $('wifi-setup');
  if (w.state === 'setup' && w.setup) {
    try {
      const s = await (await fetch('/api/wifi/setup', { cache: 'no-store' })).json();
      if (s.password) {
        $('wifi-setup-ssid').textContent = s.ssid;
        $('wifi-setup-pw').textContent = s.password;
        $('wifi-setup-url').textContent = `http://${s.ip}/`;
        $('wifi-join-qr').src = qr(wifiQr(s.ssid, s.password));
        $('wifi-remote-qr').src = qr(`http://${s.ip}/`);
        box.hidden = false;
        overlay.hidden = true;
      }
    } catch {}
  } else box.hidden = true;
  const last = JSON.stringify(w.last);
  if (lastWifi !== null && last !== lastWifi && w.last) {
    say(w.last.result === 'joined' ? `Wi-Fi: joined ${w.last.ssid}` : `Wi-Fi: ${w.last.error}`, 8000);
  }
  lastWifi = last;
  if (w.state === 'connecting') say(`Wi-Fi: joining ${w.ssid}…`, 60_000);
}
// the state at start, and now and then in case an event was missed while reconnecting
async function pollWifi() {
  try {
    wifiState((await (await fetch('/api/state', { cache: 'no-store' })).json()).wifi);
  } catch {}
}
pollWifi();
setInterval(pollWifi, 15_000);

let lastSong = '';
let lastDevice = null;

// "Pair" on the remote: the Pi is visible over Bluetooth for two minutes
function pairing(on) {
  const box = $('pairing');
  clearTimeout(pairing.t);
  box.hidden = !on;
  if (on) pairing.t = setTimeout(() => (box.hidden = true), 120_000);
}

function listen() {
  const es = new EventSource('/api/events');
  es.onmessage = (e) => {
    try {
      handle(JSON.parse(e.data));
    } catch (err) {
      console.warn(err);
    }
  };
  es.onerror = () => {
    es.close();
    setTimeout(listen, 3000);
  };
}

listen();
showConnect();
