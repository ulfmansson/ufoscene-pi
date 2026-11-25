// The troubleshooting page (/diag): what the Pi sees about itself, and step-by-step guides.
//
// CHECKS turns the facts from /api/diag into lines with a level (ok, warn, bad, info, unknown).
// The info tab shows them in groups; each guide step shows the ones that matter for that step,
// live, so "is it the Pi or the phone?" is answered on the page itself.

const $ = (id) => document.getElementById(id);
let d = null; // the last /api/diag

// ------------------------------------------------------------------ helpers

const svc = (name) => d?.services?.[name];
function svcCheck(name, what) {
  const s = svc(name);
  if (!d?.services || !Object.keys(d.services).length) return ['unknown', 'Unknown (no systemd here)'];
  if (!s || !s.found) return ['info', 'Not installed'];
  const again = s.restarts > 3 ? ` · restarted ${s.restarts} times` : '';
  if (s.active === 'active') return [s.restarts > 3 ? 'warn' : 'ok', `Running${again}`];
  if (s.active === 'activating') return ['warn', `Starting…${again}`];
  return ['bad', `${s.active === 'failed' ? 'Failed' : 'Stopped'}${what ? ` (${what})` : ''}${again}`];
}
function duration(s) {
  if (s == null) return 'unknown';
  if (s < 90) return `${s} seconds`;
  if (s < 5400) return `${Math.round(s / 60)} minutes`;
  if (s < 172800) return `${Math.round(s / 3600)} hours`;
  return `${Math.round(s / 86400)} days`;
}
const SOURCES = { radio: 'Radio', phone: 'Phone (AirPlay or Bluetooth)', mic: 'Mic / line in' };

// ------------------------------------------------------------------ checks

const CHECKS = {
  // the Pi
  model: { label: 'Model', f: () => (d.pi?.model ? ['info', d.pi.model] : ['unknown', 'Unknown']) },
  os: { label: 'System', f: () => ['info', [d.pi?.os, d.pi?.arch].filter(Boolean).join(' · ') || 'Unknown'] },
  version: { label: 'ufoscene-pi', f: () => ['info', `${d.version}${d.bundle && d.bundle !== d.version ? ` (bundle ${d.bundle})` : ''}`] },
  uptime: { label: 'Running for', f: () => ['info', duration(d.pi?.uptime_s)] },
  temp: {
    label: 'Temperature',
    f: () => {
      const t = d.pi?.temp_c;
      if (t == null) return ['unknown', 'Unknown'];
      if (t >= 80) return ['bad', `${t} °C: too hot, the Pi slows down`];
      if (t >= 75) return ['warn', `${t} °C: warm`];
      return ['ok', `${t} °C`];
    },
  },
  power: {
    label: 'Power supply',
    f: () => {
      const th = d.pi?.throttled;
      if (!th) return ['unknown', 'Unknown'];
      if (th.now.includes('under_voltage')) return ['bad', 'Too little power right now: use the official Pi power supply'];
      if (th.since_boot.includes('under_voltage')) return ['warn', 'The power dipped since the Pi started'];
      return ['ok', 'Enough power'];
    },
  },
  throttle: {
    label: 'Full speed',
    f: () => {
      const th = d.pi?.throttled;
      if (!th) return ['unknown', 'Unknown'];
      const slow = th.now.filter((x) => x !== 'under_voltage');
      if (slow.length) return ['warn', 'Slowed down right now (heat or power)'];
      if (th.since_boot.some((x) => x !== 'under_voltage')) return ['info', 'Yes, but it was slowed down earlier'];
      return ['ok', 'Yes'];
    },
  },
  load: {
    label: 'CPU load',
    f: () => {
      const l = d.pi?.load;
      if (!l) return ['unknown', 'Unknown'];
      const per = l[0] / (d.pi.cpus || 1);
      return [per > 1.5 ? 'warn' : 'info', `${l[0]} (on ${d.pi.cpus} cores)${per > 1.5 ? ': very busy' : ''}`];
    },
  },
  mem: {
    label: 'Free memory',
    f: () => {
      const m = d.pi?.mem;
      if (!m) return ['unknown', 'Unknown'];
      return [m.available_mb < 100 ? 'warn' : 'ok', `${m.available_mb} of ${m.total_mb} MB`];
    },
  },
  disk: {
    label: 'Free space on the card',
    f: () => {
      const k = d.pi?.disk;
      if (!k) return ['unknown', 'Unknown'];
      return [k.free_gb < 0.5 ? 'bad' : k.free_gb < 2 ? 'warn' : 'ok', `${k.free_gb} of ${k.total_gb} GB`];
    },
  },

  // network
  name: { label: 'Name on the network', f: () => ['info', d.network?.urls?.mdns || d.network?.hostname || 'Unknown'] },
  ip: {
    label: 'Address',
    f: () => {
      const a = d.network?.addresses;
      if (!a) return ['unknown', 'Unknown'];
      if (!a.length) return ['bad', 'None: the Pi is on no network'];
      return ['info', a.map((x) => `${x.ip} (${x.ifname.startsWith('wl') ? 'Wi-Fi' : x.ifname.startsWith('e') ? 'cable' : x.ifname})`).join(', ')];
    },
  },
  conn: {
    label: 'Connection',
    f: () => {
      const m = d.network?.manager;
      const link = d.network?.wifi?.link;
      if (m && m.available !== false) {
        const r = {
          connected: ['ok', `Wi-Fi ${m.ssid}`],
          cable: ['ok', 'Network cable'],
          setup: ['bad', `Setup network ${m.setup?.ssid || 'ufoscene-setup'} is open: the Pi could not join its Wi-Fi`],
          connecting: ['warn', `Joining ${m.ssid}…`],
          offline: ['bad', 'No network: looking for a known Wi-Fi'],
        }[m.state];
        if (r) return r;
      }
      if (link?.ssid) return ['ok', `Wi-Fi ${link.ssid}`];
      if (d.network?.addresses?.length) return ['ok', 'Connected'];
      return d.network?.addresses ? ['bad', 'Not connected'] : ['unknown', 'Unknown'];
    },
  },
  signal: {
    label: 'Wi-Fi signal',
    f: () => {
      const w = d.network?.wifi;
      const dbm = w?.link?.signal_dbm;
      if (dbm == null) return w ? ['info', 'Not on Wi-Fi'] : ['unknown', 'No Wi-Fi on this Pi'];
      if (dbm >= -60) return ['ok', `Strong (${dbm} dBm)`];
      if (dbm >= -70) return ['ok', `Good (${dbm} dBm)`];
      if (dbm >= -80) return ['warn', `Weak (${dbm} dBm): music may stutter`];
      return ['bad', `Very weak (${dbm} dBm)`];
    },
  },
  band: {
    label: 'Wi-Fi band and speed',
    f: () => {
      const l = d.network?.wifi?.link;
      if (!l?.freq_mhz) return ['info', '–'];
      return ['info', `${l.freq_mhz >= 5000 ? '5' : '2.4'} GHz${l.tx_bitrate ? ` · ${l.tx_bitrate}` : ''}`];
    },
  },
  powersave: {
    label: 'Wi-Fi power saving',
    f: () => {
      const p = d.network?.wifi?.power_save;
      if (p == null) return ['info', '–'];
      return p ? ['warn', 'On: can make AirPlay stutter and the Pi slow to answer'] : ['ok', 'Off'];
    },
  },
  gateway: { label: 'Router', f: () => ['info', d.network?.gateway || 'None found'] },
  internet: {
    label: 'Internet',
    f: () => {
      const n = d.network;
      if (n?.internet == null) return ['unknown', 'Unknown'];
      return n.internet ? ['ok', 'Reachable'] : ['bad', 'Not reachable: no radio, song names or pictures'];
    },
  },
  dns: {
    label: 'Name lookup (DNS)',
    f: () => {
      const n = d.network;
      if (n?.dns_ok == null) return ['unknown', 'Unknown'];
      return n.dns_ok ? ['ok', 'Works'] : ['bad', `Fails${n.dns?.length ? ` (asks ${n.dns.join(', ')})` : ''}`];
    },
  },
  you: { label: 'This phone', f: () => ['info', `${d.network?.you?.ip || '?'} · opened ${d.network?.you?.host || '?'}`] },

  // TV
  hdmi: {
    label: 'TV on HDMI',
    f: () => {
      const ports = d.screen?.hdmi;
      if (!ports?.length) return ['unknown', 'Unknown'];
      const on = ports.filter((p) => p.connected);
      if (!on.length) return ['bad', 'No TV found: check the cable and that the TV is on'];
      return ['ok', on.map((p) => `${p.port}${p.mode ? ` · ${p.mode}` : ''}`).join(', ')];
    },
  },
  tvpage: {
    label: 'Demos on the TV',
    f: () => {
      const t = d.tv;
      if (!t?.connected) return ['bad', 'Not running'];
      return ['ok', `Running${t.seen_s_ago != null ? ` · last heard ${duration(t.seen_s_ago)} ago` : ''}`];
    },
  },
  fps: {
    label: 'Speed',
    f: () => {
      const f = d.tv?.fps;
      if (f == null) return ['unknown', 'Not measured yet'];
      const n = Math.round(f);
      return [n >= 25 ? 'ok' : n >= 20 ? 'warn' : 'bad', `${n} frames per second${n < 25 ? ' (25 needed for smooth demos)' : ''}`];
    },
  },
  demo: { label: 'Demo', f: () => ['info', d.tv?.demo ? `#${d.tv.demo}` : '–'] },
  kiosk: { label: 'TV browser', f: () => svcCheck('ufoscene-kiosk', 'nothing on the TV') },

  // sound
  source: { label: 'Music from', f: () => ['info', SOURCES[d.tv?.audio] || d.tv?.audio || 'Unknown'] },
  sound: {
    label: 'Sound system',
    f: () => (d.audio?.server == null ? ['unknown', 'Unknown'] : d.audio.server ? ['ok', 'Running (PipeWire)'] : ['bad', 'Not running']),
  },
  output: {
    label: 'Sound goes to',
    f: () => {
      const a = d.audio;
      if (!a?.server) return ['unknown', 'Unknown'];
      if (!a.default_sink) return ['bad', 'Nowhere: no output found'];
      if (a.hdmi) return ['ok', 'The TV (HDMI)'];
      return ['warn', `Not the TV: ${a.default_sink}`];
    },
  },
  volume: {
    label: 'Pi volume',
    f: () => {
      const a = d.audio;
      if (a?.volume_pct == null) return ['unknown', 'Unknown'];
      if (a.muted) return ['bad', 'Muted'];
      return [a.volume_pct < 50 ? 'warn' : 'ok', `${a.volume_pct}%${a.volume_pct < 50 ? ': low, the TV remote is meant to be the volume' : ''}`];
    },
  },
  playing: {
    label: 'Playing into it',
    f: () => {
      const p = d.audio?.playing;
      if (!p) return ['unknown', 'Unknown'];
      return ['info', p.length ? p.join(', ') : 'Nothing right now'];
    },
  },
  input: {
    label: 'Listening to',
    f: () => {
      const s = d.audio?.default_source;
      if (!s) return ['unknown', 'Unknown'];
      return ['info', s.endsWith('.monitor') ? 'What the Pi plays (phone mode)' : s];
    },
  },
  mics: {
    label: 'Microphones / line in',
    f: () => {
      const s = d.audio?.sources;
      if (!s) return ['unknown', 'Unknown'];
      return [s.length ? 'ok' : 'info', s.length ? s.map((x) => x.name).join(', ') : 'None plugged in'];
    },
  },

  // AirPlay
  airplay: { label: 'AirPlay speaker', f: () => svcCheck('ufoscene-airplay', 'UFOSCENE is not offered') },
  apListen: {
    label: 'AirPlay ready',
    f: () => {
      const a = d.airplay;
      if (a?.listening == null) return ['unknown', 'Unknown'];
      return a.listening ? ['ok', `Yes, as ${a.name} (port ${a.ports.join(', ')})`] : ['bad', 'No: phones cannot connect'];
    },
  },
  avahi: { label: 'Announced on the network', f: () => svcCheck('avahi-daemon', 'phones cannot see UFOSCENE or ufoscene.local') },
  apMeta: {
    label: 'Song names from AirPlay',
    f: () => {
      if (svc('ufoscene-airplay')?.active !== 'active') return ['unknown', 'Not while AirPlay is off'];
      return d.airplay?.metadata_pipe ? ['ok', 'Yes'] : ['warn', 'No: the music plays, but without names'];
    },
  },
  apVersion: { label: 'shairport-sync', f: () => ['info', d.airplay?.version || 'Unknown'] },
  nowplaying: {
    label: 'Now playing',
    f: () => {
      const n = d.nowplaying;
      if (!n) return ['info', 'Nothing from a phone yet'];
      const via = n.source === 'airplay' ? 'AirPlay' : 'Bluetooth';
      const what = [n.artist, n.title].filter(Boolean).join(' — ') || 'a song without a name';
      return ['info', `${what} · ${via}${n.playing ? '' : ' (paused)'}`];
    },
  },

  // Bluetooth
  bt: {
    label: 'Bluetooth',
    f: () => {
      const b = d.bluetooth;
      if (!b) return ['unknown', 'Not reported yet'];
      if (!b.available) return ['warn', 'No Bluetooth on this Pi (a Pi 2 needs a USB adapter)'];
      if (b.pairing) return ['ok', 'Pairing is open: pick UFOSCENE on the phone'];
      return ['ok', b.device ? `Ready · last phone: ${b.device}` : 'Ready'];
    },
  },
  btService: { label: 'Bluetooth service', f: () => svcCheck('bluetooth') },
  phoneService: { label: 'Phone speaker helper', f: () => svcCheck('ufoscene-phone', 'no pairing, no song names') },

  // song names
  lastfm: {
    label: 'Last.fm',
    f: () => {
      const l = d.lastfm;
      if (!l?.user) return ['info', 'Off'];
      return l.key ? ['ok', `On, for ${l.user}`] : ['warn', `${l.user}, but no API key`];
    },
  },

  // the rest of the services
  server: { label: 'Remote and TV server', f: () => svcCheck('ufoscene-server') },
  wifiService: { label: 'Wi-Fi helper', f: () => svcCheck('ufoscene-wifi', 'no Wi-Fi changes or setup network') },
  nm: { label: 'NetworkManager', f: () => svcCheck('NetworkManager') },
};

function check(id) {
  try {
    return CHECKS[id].f();
  } catch {
    return ['unknown', 'Unknown'];
  }
}

const GROUPS = [
  ['The Pi', ['model', 'os', 'version', 'uptime', 'temp', 'power', 'throttle', 'load', 'mem', 'disk']],
  ['Network', ['name', 'ip', 'conn', 'signal', 'band', 'powersave', 'gateway', 'internet', 'dns', 'you']],
  ['TV', ['hdmi', 'kiosk', 'tvpage', 'fps', 'demo']],
  ['Sound', ['source', 'sound', 'output', 'volume', 'playing', 'input', 'mics']],
  ['AirPlay', ['airplay', 'apListen', 'avahi', 'apMeta', 'nowplaying', 'apVersion']],
  ['Bluetooth', ['bt', 'btService', 'phoneService']],
  ['Song names', ['lastfm']],
  ['Services', ['server', 'wifiService', 'nm']],
];

// a check that is not OK points at the guide for it
const FIX_FOR = {
  temp: 'slow', power: 'slow', throttle: 'slow', fps: 'slow', load: 'slow', disk: 'slow',
  ip: 'wifi', conn: 'wifi', signal: 'stutter', powersave: 'stutter', internet: 'radio', dns: 'radio',
  hdmi: 'picture', tvpage: 'picture', kiosk: 'picture',
  sound: 'nosound', output: 'nosound', volume: 'nosound',
  airplay: 'airplay', apListen: 'airplay', avahi: 'airplay', apMeta: 'names',
  bt: 'bluetooth', btService: 'bluetooth', phoneService: 'bluetooth',
  lastfm: 'names', server: 'picture', wifiService: 'wifi', nm: 'wifi',
};

// ------------------------------------------------------------------ guides
//
// Each step: a title, a short text (fixed HTML, written here), the checks to show live, and
// buttons that do something on the Pi (a remote command) or open a page.

const cmd = (label, c, arg) => ({ label, cmd: c, arg });
const link = (label, href) => ({ label, href });

const PROBLEMS = [
  {
    id: 'picture', area: 'TV', title: 'Nothing on the TV',
    steps: [
      {
        title: 'Pick the right input on the TV',
        body: `<p>Use the TV's remote (<b>Input</b> or <b>Source</b>) and pick the HDMI port the Pi is plugged into.</p>
          <p>On a Pi 4 or 5, use the micro-HDMI port <b>next to the power plug</b> (HDMI 0).</p>`,
        checks: ['hdmi'],
      },
      {
        title: 'Check the power',
        body: `<p>The red light on the Pi should be on all the time, the green one blinks while it starts.</p>
          <p>Use the official Raspberry Pi power supply: a phone charger is often too weak, and then the Pi restarts or shows nothing.</p>`,
        checks: ['power', 'uptime'],
      },
      {
        title: 'Give it a few minutes',
        body: `<p>The very first start takes a few minutes, and a Pi 2 or 3 needs a minute or two every time before the demos start.</p>`,
        checks: ['kiosk', 'tvpage'],
      },
      {
        title: 'Restart the demos',
        body: `<p>This restarts the page on the TV. It takes up to half a minute.</p>`,
        checks: ['tvpage', 'server'],
        actions: [cmd('Reload the TV page', 'reload')],
      },
      {
        title: 'Restart the Pi with the TV on',
        body: `<p>Turn the TV on and switch it to the Pi's input first. Then pull the Pi's power for ten seconds and put it back.</p>
          <p>A Pi that started with the TV off sometimes picks a resolution the TV can't show.</p>`,
        checks: ['hdmi'],
      },
    ],
  },
  {
    id: 'nosound', area: 'Sound', title: 'Demos on the TV, but no sound',
    steps: [
      {
        title: 'Turn up the TV',
        body: `<p>The Pi always plays at full level: <b>the TV's remote is the volume</b>. Check that the TV is not muted,
          and that its sound goes where you expect (its own speakers, or a soundbar that is on).</p>`,
        checks: ['volume'],
      },
      {
        title: 'Does the sound go to the TV?',
        body: `<p>It should say <b>The TV (HDMI)</b> below. If the Pi started before the TV was on, it may have picked the headphone jack instead.</p>
          <p>Fix: turn the TV on, then restart the Pi (pull the power for ten seconds).</p>`,
        checks: ['sound', 'output', 'hdmi'],
      },
      {
        title: 'Is there music to play?',
        body: `<p>Check where the music comes from. <b>Radio</b> needs internet; with <b>Phone</b>, the phone must be playing to UFOSCENE.
          Try the radio to rule out the phone.</p>`,
        checks: ['source', 'playing', 'internet'],
        actions: [cmd('Use the radio', 'audio', 'radio'), cmd('Next radio station', 'song-next')],
      },
      {
        title: 'Restart the demos',
        body: `<p>The sound of the radio plays in the TV page. Reloading it often brings the sound back.</p>`,
        checks: ['tvpage'],
        actions: [cmd('Reload the TV page', 'reload')],
      },
    ],
  },
  {
    id: 'airplay', area: 'iPhone, iPad, Mac (AirPlay)', title: 'UFOSCENE is not in the AirPlay list',
    steps: [
      {
        title: 'Same Wi-Fi as the Pi',
        body: `<p>The phone must be on <b>the same Wi-Fi network</b> as the Pi. A guest network won't work: it keeps devices apart.</p>
          <p>The Pi is on the network shown below. On the phone: Settings → Wi-Fi.</p>`,
        checks: ['conn', 'you'],
      },
      {
        title: 'Is the AirPlay speaker running?',
        body: `<p>All three lines should be green. If one is red, restart the Pi (pull the power for ten seconds).</p>`,
        checks: ['airplay', 'apListen', 'avahi'],
      },
      {
        title: 'Look in the right place',
        body: `<ul><li><b>iPhone / iPad</b>: in Spotify or Music, tap the AirPlay icon ▭▲, or Control Center → the AirPlay icon on the music tile.</li>
          <li><b>Mac</b>: Control Center → <b>Sound</b> → UFOSCENE.</li></ul>
          <p>Screen Mirroring is something else: use the <b>sound</b> AirPlay, not the screen.</p>`,
      },
      {
        title: 'Wake up the phone\'s Wi-Fi',
        body: `<p>Turn Wi-Fi off and on again on the phone (Settings → Wi-Fi, not Control Center), wait ten seconds, and look again.</p>
          <p>A VPN on the phone can hide speakers at home: turn it off and try.</p>`,
      },
      {
        title: 'Check the router',
        body: `<p>Some routers keep devices from finding each other:</p>
          <ul><li>Turn off <b>client isolation</b> / <b>AP isolation</b> for your network.</li>
          <li>Mesh or two-band routers with separate 2.4 and 5 GHz names: put the phone on the same name as the Pi.</li>
          <li>Restart the router, then the Pi.</li></ul>`,
        checks: ['band'],
      },
    ],
  },
  {
    id: 'stutter', area: 'iPhone, iPad, Mac (AirPlay)', title: 'The music stutters or drops out',
    steps: [
      {
        title: 'Check the Wi-Fi signal',
        body: `<p>A weak signal is the most common cause. Move the Pi or the router closer, or keep the Pi out of a metal TV cabinet.
          A network cable fixes it for good.</p>`,
        checks: ['signal', 'band', 'powersave'],
      },
      {
        title: 'Give the Pi room to breathe',
        body: `<p>A hot or underpowered Pi slows down, and the music suffers first. TV mode makes the demos lighter.</p>`,
        checks: ['temp', 'power', 'throttle', 'load'],
        actions: [cmd('Turn TV mode on/off', 'tv')],
      },
      {
        title: 'One phone at a time',
        body: `<p>If someone else picks UFOSCENE, they take over and your music stops. Check what plays now.</p>`,
        checks: ['nowplaying', 'playing'],
      },
      {
        title: 'Keep the phone close',
        body: `<p>The phone sends the music the whole time: keep it on the same Wi-Fi, in range, and not in low-power mode.</p>`,
      },
    ],
  },
  {
    id: 'bluetooth', area: 'Android and others (Bluetooth)', title: 'The phone can\'t find or connect over Bluetooth',
    steps: [
      {
        title: 'Does this Pi have Bluetooth?',
        body: `<p>A Pi 3, 4 and 5 have it built in. A Pi 2 needs a USB Bluetooth adapter.</p>`,
        checks: ['bt', 'btService', 'phoneService'],
      },
      {
        title: 'Open pairing',
        body: `<p>Press <b>Pair</b> below. For two minutes the Pi can be found: on the phone, open Settings → Bluetooth → pair a new device, and pick <b>UFOSCENE</b>. No PIN.</p>`,
        checks: ['bt'],
        actions: [cmd('Pair Bluetooth', 'pair')],
      },
      {
        title: 'Forget it and pair again',
        body: `<p>If the phone knew UFOSCENE before, it may hold an old key. On the phone, forget / unpair <b>UFOSCENE</b>, then press Pair again.</p>`,
        actions: [cmd('Pair Bluetooth', 'pair')],
      },
      {
        title: 'Come closer',
        body: `<p>Bluetooth reaches a few metres, less through a TV. Stand near the TV while pairing, and turn Bluetooth off and on on the phone.</p>`,
      },
      {
        title: 'Or use AirPlay',
        body: `<p>On an iPhone, iPad or Mac, AirPlay works better: it needs no pairing, and sends the song names.</p>`,
      },
    ],
  },
  {
    id: 'react', area: 'Sound', title: 'The demos don\'t move with the music',
    steps: [
      {
        title: 'Pick where the music comes from',
        body: `<p>The demos listen to the source chosen on the remote. Music from a phone needs <b>Phone</b>; a USB microphone needs <b>Mic / line in</b>.</p>`,
        checks: ['source', 'input', 'playing'],
        actions: [cmd('Phone', 'audio', 'phone'), cmd('Radio', 'audio', 'radio'), cmd('Mic / line in', 'audio', 'mic')],
      },
      {
        title: 'Microphone or line in',
        body: `<p>Plug the USB microphone or sound card in <b>before</b> you press Mic / line in. It should be listed below.</p>`,
        checks: ['mics'],
        actions: [cmd('Mic / line in', 'audio', 'mic')],
      },
      {
        title: 'Restart the demos',
        body: `<p>Reloading the TV page makes it listen again from the start.</p>`,
        actions: [cmd('Reload the TV page', 'reload')],
      },
    ],
  },
  {
    id: 'names', area: 'Sound', title: 'No song names or pictures',
    steps: [
      {
        title: 'Not every source sends names',
        body: `<ul><li><b>AirPlay</b> from an iPhone or iPad sends them.</li>
          <li><b>Bluetooth</b>: many phones do, some don't.</li>
          <li><b>A Mac or PC</b> sends only the sound.</li>
          <li><b>Radio</b>: only some stations send the song playing (Heart, Smooth, Gold, Capital, Radio X…).</li></ul>`,
        checks: ['source', 'nowplaying', 'apMeta'],
        actions: [cmd('Next radio station', 'song-next')],
      },
      {
        title: 'Pictures need internet',
        body: `<p>Artist photos and covers come from the internet.</p>`,
        checks: ['internet', 'dns'],
      },
      {
        title: 'Names from anywhere with Last.fm',
        body: `<p>Link Spotify to Last.fm and type your Last.fm username under <b>Song names</b> on the remote. Then the Pi shows what you play anywhere, within about ten seconds.</p>`,
        checks: ['lastfm'],
        actions: [link('Open Song names on the remote', '/#lastfm')],
      },
    ],
  },
  {
    id: 'slow', area: 'TV', title: 'The demos are slow or jerky',
    steps: [
      {
        title: 'Turn on TV mode',
        body: `<p>TV mode runs a steady 30 frames per second without the scanlines: much lighter for the Pi.</p>`,
        checks: ['fps'],
        actions: [cmd('Turn TV mode on/off', 'tv'), cmd('Show speed on the TV (self-test)', 'selftest')],
      },
      {
        title: 'Heat and power',
        body: `<p>A Pi that is too hot or gets too little power slows itself down. Give it air (not in a closed cabinet), a case with a fan or heatsink, and the official power supply.</p>`,
        checks: ['temp', 'power', 'throttle'],
      },
      {
        title: 'Which Pi is it?',
        body: `<p>A Pi 4, 400, 5 or 500 is smooth. A Pi 3 or Zero 2 W is borderline, and a Pi 2 is slow.</p>`,
        checks: ['model', 'mem', 'load'],
      },
      {
        title: 'Restart the demos',
        body: `<p>After many hours, a reload can help.</p>`,
        actions: [cmd('Reload the TV page', 'reload')],
      },
    ],
  },
  {
    id: 'radio', area: 'Sound', title: 'The radio doesn\'t play',
    steps: [
      {
        title: 'Is the Pi online?',
        body: `<p>The radio streams from the internet. Both lines should be green.</p>`,
        checks: ['internet', 'dns', 'conn'],
      },
      {
        title: 'Try another station',
        body: `<p>Stations come and go. Skip to the next one a few times.</p>`,
        actions: [cmd('Use the radio', 'audio', 'radio'), cmd('Next radio station', 'song-next')],
      },
      {
        title: 'Restart the demos',
        body: `<p>Reloading the TV page restarts the radio.</p>`,
        actions: [cmd('Reload the TV page', 'reload')],
      },
    ],
  },
  {
    id: 'remote', area: 'Network', title: 'Another phone can\'t open the remote',
    steps: [
      {
        title: 'Scan the QR code on the TV',
        body: `<p>The QR code takes the phone straight here. Press <b>QR code</b> to show it on the TV.</p>`,
        actions: [cmd('Show the QR code on the TV', 'overlay')],
      },
      {
        title: 'Use the address instead of the name',
        body: `<p>Some Android phones can't open <b>.local</b> names. Type the number address instead; it is shown below.</p>`,
        checks: ['name', 'ip'],
      },
      {
        title: 'Same Wi-Fi, no VPN',
        body: `<p>The phone must be on the same Wi-Fi as the Pi, not a guest network or mobile data, and with any VPN turned off.
          On an iPhone, turn off <b>Limit IP Address Tracking</b> and <b>Private Relay</b> for the network if it still can't.</p>`,
        checks: ['conn'],
      },
    ],
  },
  {
    id: 'wifi', area: 'Network', title: 'The Pi lost its Wi-Fi, or moved to a new place',
    steps: [
      {
        title: 'Wait for the setup network',
        body: `<p>After two minutes without a known Wi-Fi, the Pi opens its own network, <b>ufoscene-setup</b>, and the TV shows its password and two QR codes.</p>
          <p>Scan the first to join it, the second to open the remote, and give your Wi-Fi under <b>Wi-Fi</b>.</p>`,
        checks: ['conn'],
      },
      {
        title: 'Change the Wi-Fi from the remote',
        body: `<p>On the remote, under <b>Wi-Fi</b>: the new network's name and password, then <b>Show code on TV</b> and type the six digits.
          The Pi keeps its old Wi-Fi until the new one works.</p>`,
        actions: [link('Open Wi-Fi on the remote', '/#wifi')],
      },
      {
        title: 'Or use a network cable',
        body: `<p>Plug a cable from the router into the Pi: it works straight away, and the setup network stays closed.</p>`,
        checks: ['ip'],
      },
    ],
  },
];

// ------------------------------------------------------------------ rendering

function dot(level) {
  const s = document.createElement('span');
  s.className = `dot ${level}`;
  s.setAttribute('aria-hidden', 'true');
  return s;
}

function renderInfo() {
  const groups = $('groups');
  groups.replaceChildren();
  for (const [title, ids] of GROUPS) {
    const box = document.createElement('section');
    box.className = 'group';
    const h = document.createElement('h2');
    h.textContent = title;
    const table = document.createElement('table');
    for (const id of ids) {
      const [level, text] = check(id);
      const tr = table.insertRow();
      const th = document.createElement('th');
      th.textContent = CHECKS[id].label;
      const td = tr.insertCell();
      td.className = level;
      td.append(dot(level), text);
      tr.prepend(th);
    }
    box.append(h, table);
    groups.append(box);
  }

  // the summary: what is wrong, each with its guide
  const wrong = Object.keys(CHECKS).map((id) => [id, ...check(id)]).filter(([, l]) => l === 'bad' || l === 'warn');
  const sum = $('summary');
  sum.replaceChildren();
  const worst = wrong.some(([, l]) => l === 'bad') ? 'bad' : wrong.length ? 'warn' : 'ok';
  sum.className = `summary ${worst}`;
  const p = document.createElement('p');
  p.textContent = worst === 'ok' ? '✓ Everything the Pi can check looks fine.' : `${wrong.length} thing${wrong.length > 1 ? 's' : ''} to look at:`;
  sum.append(p);
  if (wrong.length) {
    const ul = document.createElement('ul');
    for (const [id, level, text] of wrong) {
      const li = document.createElement('li');
      const what = document.createElement('span');
      what.textContent = `${CHECKS[id].label}: ${text} `;
      if (FIX_FOR[id]) {
        const a = document.createElement('a');
        a.href = `#fix/${FIX_FOR[id]}/1`;
        a.textContent = 'Fix it →';
        what.append(a);
      }
      li.append(dot(level), what);
      ul.append(li);
    }
    sum.append(ul);
  }
}

function renderProblems() {
  const box = $('problems');
  box.replaceChildren();
  const flagged = new Set(Object.keys(CHECKS).filter((id) => ['bad', 'warn'].includes(check(id)[0])).map((id) => FIX_FOR[id]));
  for (const area of [...new Set(PROBLEMS.map((p) => p.area))]) {
    const h = document.createElement('h3');
    h.textContent = area;
    const list = document.createElement('div');
    list.className = 'list';
    for (const p of PROBLEMS.filter((x) => x.area === area)) {
      const a = document.createElement('a');
      a.className = 'button';
      a.href = `#fix/${p.id}/1`;
      a.textContent = p.title;
      if (d && flagged.has(p.id)) {
        const f = document.createElement('span');
        f.className = 'flag';
        f.textContent = '● the Pi sees something';
        a.append(f);
      }
      list.append(a);
    }
    box.append(h, list);
  }
}

function renderStep(problem, n) {
  const step = problem.steps[n - 1];
  $('step-problem').textContent = problem.title;
  $('step-count').textContent = `STEP ${n} OF ${problem.steps.length}`;
  $('step-title').textContent = step.title;
  $('step-body').innerHTML = step.body; // fixed text from PROBLEMS above, never from the network
  renderStepChecks(step);
  const actions = $('step-actions');
  actions.replaceChildren();
  for (const a of step.actions || []) {
    if (a.href) {
      const el = document.createElement('a');
      el.className = 'button';
      el.href = a.href;
      el.textContent = a.label;
      actions.append(el);
    } else {
      const b = document.createElement('button');
      b.textContent = a.label;
      b.addEventListener('click', () => send(b, a.cmd, a.arg));
      actions.append(b);
    }
  }
  $('step-fixed').onclick = () => go('#fix/done');
  $('step-next').onclick = () => go(n < problem.steps.length ? `#fix/${problem.id}/${n + 1}` : `#fix/stuck`);
  $('step-next').textContent = n < problem.steps.length ? 'Still not working →' : 'Still not working';
  $('step-back').href = n > 1 ? `#fix/${problem.id}/${n - 1}` : '#fix';
}

function renderStepChecks(step) {
  const box = $('step-checks');
  box.replaceChildren();
  if (!step?.checks || !d) return;
  for (const id of step.checks) {
    const [level, text] = check(id);
    const line = document.createElement('div');
    line.className = 'line';
    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = `${CHECKS[id].label}:`;
    line.append(dot(level), label, text);
    box.append(line);
  }
}

async function send(button, c, arg) {
  if (navigator.vibrate) navigator.vibrate(15);
  const was = button.textContent;
  try {
    const r = await fetch('/api/cmd', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(arg === undefined ? { cmd: c } : { cmd: c, arg }),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.status);
    button.textContent = `${was} ✓`;
  } catch (e) {
    button.textContent = `${was}: failed (${e.message})`;
  }
  setTimeout(() => (button.textContent = was), 2500);
  setTimeout(refresh, 1500);
}

// ------------------------------------------------------------------ the report

function reportText() {
  if (!d) return 'No answer from the Pi.';
  const lines = [`ufoscene-pi troubleshooting report · ${new Date(d.time * 1000).toISOString()}`];
  const mark = { ok: 'OK', warn: 'WARN', bad: 'BAD', info: '', unknown: '?' };
  for (const [title, ids] of GROUPS) {
    lines.push('', `## ${title}`);
    for (const id of ids) {
      const [level, text] = check(id);
      lines.push(`${CHECKS[id].label}: ${text}${mark[level] ? `  [${mark[level]}]` : ''}`);
    }
  }
  const t = d.pi?.throttled;
  if (t) lines.push('', `throttled=${t.raw}`);
  const s = d.services || {};
  lines.push('', 'Services: ' + Object.entries(s).map(([k, v]) => `${k}=${v.active}/${v.sub}${v.restarts ? ` (${v.restarts} restarts)` : ''}`).join(', '));
  if (d.audio?.sinks?.length) lines.push('Outputs: ' + d.audio.sinks.map((x) => `${x.name} ${x.state || ''}`.trim()).join(', '));
  return lines.join('\n');
}

async function copyReport(msgEl) {
  const text = reportText();
  const ta = $('report');
  ta.value = text;
  let ok = false;
  try {
    // only on https or localhost; ufoscene.local is plain http, so usually the fallback below
    await navigator.clipboard.writeText(text);
    ok = true;
  } catch {
    ta.hidden = false;
    ta.select();
    try {
      ok = document.execCommand('copy');
    } catch {}
  }
  if (msgEl) msgEl.textContent = ok ? 'Copied ✓ Paste it in a message or an issue.' : 'Select the text below and copy it.';
  if (!ok) {
    ta.hidden = false;
    go('#info');
    ta.scrollIntoView({ block: 'center' });
  }
}

$('copy').addEventListener('click', () => copyReport($('copied')));
$('stuck-copy').addEventListener('click', () => copyReport($('stuck-copied')));
$('show-report').addEventListener('click', () => {
  const ta = $('report');
  ta.value = reportText();
  ta.hidden = !ta.hidden;
});
$('ssh-logs').textContent = 'journalctl -b -u ufoscene-kiosk -u ufoscene-server -u ufoscene-phone -u ufoscene-airplay -u ufoscene-wifi';
$('ssh-sound').textContent = 'sudo -u ufo XDG_RUNTIME_DIR=/run/user/$(id -u ufo) pactl list short sinks';

// ------------------------------------------------------------------ pages

function go(hash) {
  if (location.hash === hash) route();
  else location.hash = hash;
}

function route() {
  const [page, a, b] = location.hash.replace(/^#/, '').split('/');
  const fix = page === 'fix';
  $('page-info').hidden = fix;
  $('page-fix').hidden = !fix;
  $('tab-info').setAttribute('aria-selected', String(!fix));
  $('tab-fix').setAttribute('aria-selected', String(fix));
  const problem = PROBLEMS.find((p) => p.id === a);
  const n = Math.min(Math.max(Number(b) || 1, 1), problem?.steps.length || 1);
  $('fix-list').hidden = !(fix && !problem && a !== 'done' && a !== 'stuck');
  $('fix-step').hidden = !(fix && problem);
  $('fix-done').hidden = !(fix && a === 'done');
  $('fix-stuck').hidden = !(fix && a === 'stuck');
  if (fix && problem) renderStep(problem, n);
  if (fix && !problem) renderProblems();
  window.scrollTo(0, 0);
}
window.addEventListener('hashchange', route);

// ------------------------------------------------------------------ data

function status(text, cls = '') {
  $('status').textContent = text;
  $('status').className = `status ${cls}`;
}

async function refresh() {
  try {
    const r = await fetch('/api/diag', { cache: 'no-store' });
    if (!r.ok) throw new Error(r.status);
    d = await r.json();
    $('version').textContent = d.version;
    status(`Pi info · ${new Date().toLocaleTimeString()}`, 'ok');
  } catch {
    status('No answer from the Pi: is this phone on the same Wi-Fi?', 'bad');
    return;
  }
  renderInfo();
  // keep the open guide step's checks live, without redrawing the rest of it
  const [page, a, b] = location.hash.replace(/^#/, '').split('/');
  const problem = page === 'fix' && PROBLEMS.find((p) => p.id === a);
  if (problem) renderStepChecks(problem.steps[Math.min(Math.max(Number(b) || 1, 1), problem.steps.length) - 1]);
  else if (page === 'fix' && !a) renderProblems();
}

route();
refresh();
setInterval(() => document.visibilityState === 'visible' && refresh(), 5000);
document.addEventListener('visibilitychange', () => document.visibilityState === 'visible' && refresh());
