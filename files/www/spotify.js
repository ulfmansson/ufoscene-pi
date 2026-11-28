// Optional Spotify control on the phone remote, with the owner's own Client ID (off by default).
//
// Login is PKCE in this phone's browser. Spotify only redirects to https (or loopback), not to
// http://ufoscene.local, so it redirects to a bounce page on pi.ufoscene.net, which sends the
// code back here in the URL fragment. The bounce page only forwards to a .local name or a private
// address, and the code is useless without the verifier, which never leaves this phone. Tokens
// stay in this phone's localStorage: the Pi and the site never see them.
//
// What it does: show what plays, play/pause/skip, and start a playlist on the device that is
// playing (the phone, whose sound goes to UFOSCENE over AirPlay or Bluetooth). Control needs
// Spotify Premium.

export const REDIRECT_URI = 'https://pi.ufoscene.net/spotify';
const AUTH = 'https://accounts.spotify.com/authorize';
const TOKEN = 'https://accounts.spotify.com/api/token';
const API = 'https://api.spotify.com/v1';
const SCOPES = 'user-read-playback-state user-modify-playback-state user-read-currently-playing';

const store = {
  get: (k) => localStorage.getItem(`ufopi.spotify.${k}`),
  set: (k, v) => localStorage.setItem(`ufopi.spotify.${k}`, v),
  del: (k) => localStorage.removeItem(`ufopi.spotify.${k}`),
};

// ---------------------------------------------------------------- PKCE without crypto.subtle
// http://ufoscene.local is not a secure context, so browsers leave out crypto.subtle there.
// getRandomValues still works; SHA-256 is done here (FIPS 180-4).

const K = new Uint32Array([
  0x428a2f98, 0x71374491, 0xb5c0fbcf, 0xe9b5dba5, 0x3956c25b, 0x59f111f1, 0x923f82a4, 0xab1c5ed5,
  0xd807aa98, 0x12835b01, 0x243185be, 0x550c7dc3, 0x72be5d74, 0x80deb1fe, 0x9bdc06a7, 0xc19bf174,
  0xe49b69c1, 0xefbe4786, 0x0fc19dc6, 0x240ca1cc, 0x2de92c6f, 0x4a7484aa, 0x5cb0a9dc, 0x76f988da,
  0x983e5152, 0xa831c66d, 0xb00327c8, 0xbf597fc7, 0xc6e00bf3, 0xd5a79147, 0x06ca6351, 0x14292967,
  0x27b70a85, 0x2e1b2138, 0x4d2c6dfc, 0x53380d13, 0x650a7354, 0x766a0abb, 0x81c2c92e, 0x92722c85,
  0xa2bfe8a1, 0xa81a664b, 0xc24b8b70, 0xc76c51a3, 0xd192e819, 0xd6990624, 0xf40e3585, 0x106aa070,
  0x19a4c116, 0x1e376c08, 0x2748774c, 0x34b0bcb5, 0x391c0cb3, 0x4ed8aa4a, 0x5b9cca4f, 0x682e6ff3,
  0x748f82ee, 0x78a5636f, 0x84c87814, 0x8cc70208, 0x90befffa, 0xa4506ceb, 0xbef9a3f7, 0xc67178f2,
]);

/** SHA-256 of bytes (Uint8Array), as a Uint8Array of 32. */
export function sha256(bytes) {
  const len = bytes.length;
  const padded = new Uint8Array(((len + 9 + 63) >> 6) << 6);
  padded.set(bytes);
  padded[len] = 0x80;
  const bits = len * 8;
  const view = new DataView(padded.buffer);
  view.setUint32(padded.length - 8, Math.floor(bits / 2 ** 32));
  view.setUint32(padded.length - 4, bits >>> 0);
  const h = new Uint32Array([0x6a09e667, 0xbb67ae85, 0x3c6ef372, 0xa54ff53a, 0x510e527f, 0x9b05688c, 0x1f83d9ab, 0x5be0cd19]);
  const w = new Uint32Array(64);
  const rotr = (x, n) => (x >>> n) | (x << (32 - n));
  for (let off = 0; off < padded.length; off += 64) {
    for (let i = 0; i < 16; i++) w[i] = view.getUint32(off + i * 4);
    for (let i = 16; i < 64; i++) {
      const s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >>> 3);
      const s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >>> 10);
      w[i] = (w[i - 16] + s0 + w[i - 7] + s1) | 0;
    }
    let [a, b, c, d, e, f, g, hh] = h;
    for (let i = 0; i < 64; i++) {
      const t1 = (hh + (rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25)) + ((e & f) ^ (~e & g)) + K[i] + w[i]) | 0;
      const t2 = ((rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22)) + ((a & b) ^ (a & c) ^ (b & c))) | 0;
      hh = g; g = f; f = e; e = (d + t1) | 0; d = c; c = b; b = a; a = (t1 + t2) | 0;
    }
    h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
  }
  const out = new Uint8Array(32);
  const ov = new DataView(out.buffer);
  for (let i = 0; i < 8; i++) ov.setUint32(i * 4, h[i]);
  return out;
}

export const b64url = (bytes) =>
  btoa(String.fromCharCode(...bytes)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');

const random = (n) => b64url(crypto.getRandomValues(new Uint8Array(n)));

// ---------------------------------------------------------------- login

export const connected = () => !!store.get('refresh');

/** Go to Spotify's login. The bounce page brings the answer back to this page's origin. */
export function login(clientId) {
  const verifier = random(48);
  // the state carries where to come back to (this origin) for the bounce page, plus a nonce
  const state = `${random(16)}.${b64url(new TextEncoder().encode(location.origin))}`;
  store.set('verifier', verifier);
  store.set('state', state);
  store.set('client', clientId);
  const q = new URLSearchParams({
    client_id: clientId,
    response_type: 'code',
    redirect_uri: REDIRECT_URI,
    scope: SCOPES,
    state,
    code_challenge_method: 'S256',
    code_challenge: b64url(sha256(new TextEncoder().encode(verifier))),
  });
  location.href = `${AUTH}?${q}`;
}

export function logout() {
  for (const k of ['access', 'refresh', 'expires', 'verifier', 'state']) store.del(k);
}

async function tokenRequest(params) {
  const r = await fetch(TOKEN, {
    method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
    body: new URLSearchParams({ client_id: store.get('client') || '', ...params }),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error_description || j.error || `Spotify said ${r.status}`);
  store.set('access', j.access_token);
  store.set('expires', String(Date.now() + (j.expires_in - 60) * 1000));
  if (j.refresh_token) store.set('refresh', j.refresh_token);
}

/**
 * Back from the bounce page: http://ufoscene.local/#code=…&state=… (or #error=…).
 * Returns null when this load is not a login, else '' on success or an error message.
 */
export async function finishLogin() {
  const h = new URLSearchParams(location.hash.slice(1));
  if (!h.has('state') || !(h.has('code') || h.has('error'))) return null;
  history.replaceState(null, '', location.pathname + location.search);
  const expected = store.get('state');
  store.del('state');
  if (!expected || h.get('state') !== expected) return 'That Spotify login was not started here. Try again.';
  if (h.has('error')) return h.get('error') === 'access_denied' ? 'Spotify login cancelled.' : `Spotify: ${h.get('error')}`;
  try {
    await tokenRequest({
      grant_type: 'authorization_code',
      code: h.get('code'),
      redirect_uri: REDIRECT_URI,
      code_verifier: store.get('verifier') || '',
    });
    return '';
  } catch (e) {
    return `Spotify login failed: ${e.message}`;
  } finally {
    store.del('verifier');
  }
}

async function token() {
  if (!store.get('access') || Date.now() > Number(store.get('expires') || 0)) {
    await tokenRequest({ grant_type: 'refresh_token', refresh_token: store.get('refresh') || '' });
  }
  return store.get('access');
}

// ---------------------------------------------------------------- the player

export class SpotifyError extends Error {
  constructor(message, reason) {
    super(message);
    this.reason = reason;
  }
}

async function api(method, path, body) {
  const call = async () =>
    fetch(API + path, {
      method,
      headers: { Authorization: `Bearer ${await token()}`, ...(body ? { 'Content-Type': 'application/json' } : {}) },
      body: body ? JSON.stringify(body) : undefined,
    });
  let r = await call();
  if (r.status === 401) {
    store.del('access');
    r = await call();
  }
  if (r.status === 204 || r.status === 202) return null;
  const j = await r.json().catch(() => null);
  if (r.ok) return j;
  const reason = j?.error?.reason || '';
  const why = {
    PREMIUM_REQUIRED: 'Controlling Spotify needs Spotify Premium.',
    NO_ACTIVE_DEVICE: 'Open Spotify on your phone and play something first.',
  }[reason];
  throw new SpotifyError(why || j?.error?.message || `Spotify said ${r.status}`, reason || String(r.status));
}

/** { playing, artist, title, device } or null when nothing plays. */
export async function nowPlaying() {
  const p = await api('GET', '/me/player');
  if (!p?.item) return p ? { playing: false, artist: '', title: '', device: p.device?.name || '' } : null;
  return {
    playing: !!p.is_playing,
    artist: (p.item.artists || []).map((a) => a.name).join(', '),
    title: p.item.name || '',
    device: p.device?.name || '',
  };
}

export const play = () => api('PUT', '/me/player/play');
export const pause = () => api('PUT', '/me/player/pause');
export const next = () => api('POST', '/me/player/next');
export const previous = () => api('POST', '/me/player/previous');

/** spotify:playlist:… or spotify:album:… for an open.spotify.com link; '' for anything else. */
export function contextUri(link) {
  const m = /^https:\/\/open\.spotify\.com\/(?:intl-[a-z-]+\/)?(playlist|album)\/([A-Za-z0-9]+)(?:[/?#]|$)/.exec(String(link));
  return m ? `spotify:${m[1]}:${m[2]}` : '';
}

/** Start a playlist or an album (an open.spotify.com link) on the device that is playing. */
export function playLink(link) {
  const uri = contextUri(link);
  if (!uri) return Promise.reject(new SpotifyError('That is not a Spotify playlist or album.', 'BAD_LINK'));
  return api('PUT', '/me/player/play', { context_uri: uri });
}
export const playPlaylist = playLink;

// ---------------------------------------------------------------- the shows' setlists

/** The song lines of a setlist (ufoscene's playlists/*.txt): blank lines and # comments left out. */
export function setlistSongs(text) {
  return String(text)
    .split(/\r?\n/)
    .map((l) => l.trim())
    .filter((l) => l && !l.startsWith('#'));
}

/** spotify:track:… for a line that holds a track link or URI, else ''. */
export function trackUri(line) {
  const id = /track[/:]([A-Za-z0-9]{22})/.exec(String(line))?.[1];
  return id ? `spotify:track:${id}` : '';
}

/** Spotify searches for an "Artist - Title" line, best first (as ufoscene.net looks them up). */
export function searchQueries(line) {
  const parts = String(line).split(/\s+[-–—]\s+/);
  const [artist, ...title] = parts;
  return parts.length >= 2 ? [`track:${title.join(' - ')} artist:${artist}`, line] : [line];
}

/** spotify:track:… for one setlist line, or '' when Spotify has no such song. */
export async function findTrack(line) {
  const uri = trackUri(line);
  if (uri) return uri;
  for (const q of searchQueries(line)) {
    const j = await api('GET', `/search?${new URLSearchParams({ q, type: 'track', limit: '1' })}`);
    const hit = j?.tracks?.items?.[0]?.uri;
    if (hit) return hit;
  }
  return '';
}

/**
 * Play these tracks in this order, once (shuffle and repeat off). It plays on the device that
 * plays now; with none active, on this phone's Spotify app if it is open, else on any other.
 */
export async function playInOrder(uris) {
  let on = '';
  try {
    await api('PUT', '/me/player/play', { uris });
  } catch (e) {
    if (e.reason !== 'NO_ACTIVE_DEVICE') throw e;
    const devices = ((await api('GET', '/me/player/devices'))?.devices || []).filter((d) => !d.is_restricted);
    const d = devices.find((x) => x.type === 'Smartphone') || devices[0];
    if (!d) throw e;
    on = `device_id=${encodeURIComponent(d.id)}`;
    await api('PUT', `/me/player/play?${on}`, { uris });
  }
  const and = on ? `&${on}` : '';
  // not fatal if a device refuses these
  await api('PUT', `/me/player/shuffle?state=false${and}`).catch(() => {});
  await api('PUT', `/me/player/repeat?state=off${and}`).catch(() => {});
}
