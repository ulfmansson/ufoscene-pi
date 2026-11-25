// The phone remote: buttons become commands for the TV page on the Pi.

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
  if (n >= 1) send('demo', n);
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
      // the music will come from this phone: switch the Pi to it now (the page may go to Spotify)
      a.addEventListener('click', () => {
        fetch('/api/cmd', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ cmd: 'audio', arg: 'phone' }),
          keepalive: true,
        }).catch(() => {});
        $('phone-hint').hidden = false;
      });
      grid.append(a);
    }
    box.append(h, grid);
  }
  if (!box.childElementCount) box.textContent = 'No playlists in this version.';
}

showPlaylists();
refresh();
setInterval(refresh, 4000);
document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && refresh());
