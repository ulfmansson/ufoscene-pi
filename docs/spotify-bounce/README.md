# Spotify login bounce page (for pi.ufoscene.net)

The phone remote's optional Spotify login (`files/www/spotify.js`) uses
`https://pi.ufoscene.net/spotify` as its redirect URI, because Spotify redirects only to https or
loopback addresses, never to `http://ufoscene.local`.

These two files are that page, for the ufoscene-site repository to serve:

| File | Serve at |
|---|---|
| `spotify.html` | `GET /spotify` (any query string), `text/html` |
| `spotify-bounce.js` | `GET /static/spotify-bounce.js` |
| `spotify-bounce.css` | `GET /static/spotify-bounce.css` (no inline style, for `style-src 'self'`) |

What the page does: it reads the state (`<nonce>.<base64url(origin)>`) and forwards `code`,
`state` and `error` in the **fragment** to that origin. The origin may only be `http://` on a
`.local` name or a private IPv4 address (10/8, 172.16/12, 192.168/16), with an optional port.
Anything else gets an error message, so the page is not an open redirect.

Requirements for the site:

- No server-side handling: the code is never logged, stored or exchanged by the site. Access
  logs see `?code=` in the query string. Leave the query out of the logs for this route if you
  can. The code expires in minutes, and it is useless without the verifier on the phone.
- CSP for this route: `script-src 'self'`; the page navigates to `http://*.local` or a private
  IP, which `navigate-to` (unsupported) or `default-src` doesn't block. No `form-action` is
  needed.
- `Referrer-Policy: no-referrer` (also set in the page).

Served by ufoscene-site since commit 8c28346 (branch m1-marketplace): app logs record the path
only. The qloudpulse ALB/ingress access logs still see `?code=`; that is qloudpulse's config.
