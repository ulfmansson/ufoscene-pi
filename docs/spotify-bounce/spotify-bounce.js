// pi.ufoscene.net/spotify: Spotify's redirect for a ufoscene Pi's remote (optional Spotify login).
//
// Spotify redirects only to https (or loopback), so the remote on http://<pi>.local can't be the
// redirect URI. Spotify sends the login answer here; this page passes it on, in the URL fragment,
// to the remote that started it. It runs entirely in the browser: nothing reaches the server, and
// the code is useless without the PKCE verifier, which stays on the phone.
//
// Where it goes back to comes from the state the remote made: "<nonce>.<base64url(origin)>". To
// keep this from being an open redirect, only a Pi on the home network is allowed: http on a
// .local name or a private IPv4 address, with an optional port, and nothing else.
(function () {
  var msg = document.getElementById('msg');
  var q = new URLSearchParams(location.search);
  var state = q.get('state') || '';
  var origin = '';
  try {
    var b64 = state.split('.')[1] || '';
    origin = atob(b64.replace(/-/g, '+').replace(/_/g, '/'));
  } catch (e) {}
  var LAN = /^http:\/\/([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.local|10(\.\d{1,3}){3}|192\.168(\.\d{1,3}){2}|172\.(1[6-9]|2\d|3[01])(\.\d{1,3}){2})(:\d{1,5})?$/i;
  if (!LAN.test(origin)) {
    msg.textContent = 'This link does not lead back to a ufoscene Pi. Start the Spotify login again from the remote.';
    return;
  }
  var out = new URLSearchParams();
  ['code', 'state', 'error'].forEach(function (k) { if (q.has(k)) out.set(k, q.get(k)); });
  // drop the code from this page's own history entry before leaving
  history.replaceState(null, '', location.pathname);
  location.replace(origin + '/#' + out.toString());
})();
