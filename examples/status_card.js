/* Example status card for wardogs_status_monitor. Plain JavaScript, no libraries.
   Copy status_card.html's markup into your page and load this file with defer. */
/* ===== COMMUNITY STATUS card =====
   Reads the status.json published by the collector (URL from data-status-url).
   Safe rendering only (textContent). Anything missing, invalid or old shows as Unknown. */
(function () {
  var card = document.getElementById('community-status');
  if (!card || !window.fetch) return;
  var URL_ = card.getAttribute('data-status-url') || '/wardogs/data/status.json';
  var POLL_MS = 60000, TICK_MS = 5000, TIMEOUT_MS = 8000;
  var data = null, skew = 0, lastFetch = 0, lastOk = 0, failed = false, inflight = false;

  var LABEL = {
    community_server: { responding: ['Responding', 'ok'], unreachable: ['Unreachable', 'bad'],
                        offline: ['Confirmed offline', 'bad'], unknown: ['Unknown', 'unknown'] },
    votebot: { running: ['Running', 'ok'], stopped: ['Stopped', 'bad'], unknown: ['Unknown', 'unknown'] },
    discord: { connected: ['Connected', 'ok'], reconnecting: ['Reconnecting', 'warn'],
               disconnected: ['Disconnected', 'bad'], unknown: ['Unknown', 'unknown'] },
    rcon: { healthy: ['Healthy', 'ok'], degraded: ['Degraded', 'warn'], failing: ['Failing', 'bad'],
            unknown: ['Stale / unknown', 'unknown'] }
  };
  var CAT = { timeout: 'timeout', unreachable: 'no connection', auth: 'authentication',
              rate_limited: 'rate limited', api_error: 'API failure', invalid_response: 'invalid response',
              internal_error: 'bot error' };

  function t(iso) { if (typeof iso !== 'string') return null; var v = Date.parse(iso); return isNaN(v) ? null : v; }
  function now() { return Date.now() + skew; }
  function age(ms) {
    if (ms == null) return null;
    var s = Math.max(0, Math.round((now() - ms) / 1000));
    if (s < 60) return s + 's ago';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 172800) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }
  function valid(d) {
    if (!d || typeof d !== 'object' || d.schema_version !== 1 || t(d.generated_at) == null) return false;
    for (var k in LABEL) {
      if (!d[k] || typeof d[k] !== 'object' || !LABEL[k].hasOwnProperty(d[k].state)) return false;
    }
    return true;
  }
  // Per-server rows live inside <div data-server="main|server_2|…">; without one, the whole card is one server.
  var scopes = [].slice.call(card.querySelectorAll('[data-server]'));
  if (!scopes.length) scopes = [card];
  function row(name, label, tone, meta, scope) {
    var r = (scope || card).querySelector('[data-component="' + name + '"]');
    if (!r) return;
    r.setAttribute('data-tone', tone);
    r.querySelector('.st-state').textContent = label;
    r.querySelector('.st-age').textContent = meta;
  }
  function players(p, meta, scope) {
    var r = (scope || card).querySelector('[data-component="players"]');
    if (!r) return;
    var ok = p && typeof p.current === 'number';
    r.setAttribute('data-tone', ok ? 'ok' : 'unknown');
    r.querySelector('.st-count').textContent = ok ? p.current + (typeof p.max === 'number' ? '/' + p.max : '') : '—';
    r.querySelector('.st-age').textContent = meta;
  }
  function serverUnknown(meta, scope) {
    players(null, meta, scope);
    row('community_server', 'Unknown', 'unknown', meta, scope);
    row('rcon', 'Stale / unknown', 'unknown', meta, scope);
  }
  function allUnknown(meta) {
    scopes.forEach(function (sc) { serverUnknown(meta, sc); });
    row('votebot', 'Unknown', 'unknown', meta);
    row('discord', 'Unknown', 'unknown', meta);
  }
  // This server's {community_server, rcon}: from the "servers" list, or the top level for the first server.
  function serverData(d, scope) {
    var id = scope === card ? 'main' : scope.getAttribute('data-server');
    if (Array.isArray(d.servers) && d.servers.length) {
      for (var i = 0; i < d.servers.length; i++) {
        var x = d.servers[i];
        if (x && x.id === id && x.community_server && x.rcon && LABEL.community_server.hasOwnProperty(x.community_server.state)
            && LABEL.rcon.hasOwnProperty(x.rcon.state)) return x;
      }
      return null;
    }
    return id === 'main' ? d : null;
  }
  function foot(text, demo) {
    var f = card.querySelector('.st-foot');
    f.textContent = text;
    var h = card.querySelector('h3'), b = h.querySelector('.st-demo');
    if (demo && !b) { b = document.createElement('span'); b.className = 'st-demo'; b.textContent = 'DEMO DATA'; h.appendChild(b); }
    if (!demo && b) b.parentNode.removeChild(b);
  }

  function render() {
    if (!data) {
      allUnknown(failed ? 'no data' : 'checking…');
      foot(failed ? 'Monitoring unavailable.' : 'Checking monitoring…', false);
      return;
    }
    var d = data, ex = d.expiry || {};
    var pubMax = (ex.public_json_seconds || 120) * 1000, obsMax = (ex.observation_seconds || 180) * 1000,
        hbMax = (ex.heartbeat_seconds || 180) * 1000;
    var gen = t(d.generated_at), genAge = now() - gen, demo = d.demo === true;
    var demoNote = demo ? 'Demo data, not live. ' : '';
    // An old or future-dated report: drop every green indicator.
    if (genAge > pubMax || genAge < -60000) {
      allUnknown('last report ' + (age(gen) || 'unknown'));
      foot(demoNote + 'Monitoring stale: last update ' + age(gen) + '.', demo);
      return;
    }
    function fresh(iso, max) { var v = t(iso); return v != null && now() - v <= max && now() - v > -60000; }

    scopes.forEach(function (sc) {
      var x = serverData(d, sc);
      if (!x) { serverUnknown('no data yet', sc); return; }
      // Community server (shares the RCON check)
      var s = x.community_server, L = LABEL.community_server[s.state];
      if (s.state === 'responding' && !fresh(s.observed_at, obsMax)) L = ['Unknown', 'unknown'];
      var sMeta = s.observed_at ? 'checked ' + age(t(s.observed_at)) :
        (s.last_success_at ? 'last response ' + age(t(s.last_success_at)) : 'no current check');
      row('community_server', L[0], L[1], sMeta, sc);
      // Player count (numbers only), only while the server check is current
      var pl = s.players && s.state === 'responding' && L[1] === 'ok' && fresh(s.players.observed_at, obsMax) ? s.players : null;
      players(pl, pl ? 'checked ' + age(t(pl.observed_at)) : 'no current count', sc);
      // Game / RCON
      var r = x.rcon, rl = LABEL.rcon[r.state].slice();
      if (r.state !== 'unknown' && !fresh(r.last_attempt_at, obsMax)) rl = ['Stale / unknown', 'unknown'];
      else if ((r.state === 'degraded' || r.state === 'failing') && CAT[r.category]) rl[0] += ' · ' + CAT[r.category];
      var rm = r.state === 'healthy' && rl[1] === 'ok' ? 'checked ' + age(t(r.last_attempt_at)) :
        (r.last_success_at ? 'last success ' + age(t(r.last_success_at)) : 'no successful check yet');
      row('rcon', rl[0], rl[1], rm, sc);
    });

    // Votebot process
    var b = d.votebot, bl = LABEL.votebot[b.state].slice(), bm = 'checked ' + age(t(b.checked_at));
    if (b.state === 'stopped' && b.detail === 'failed') bl[0] = 'Stopped (failed)';
    if (b.state === 'stopped' && b.detail === 'restarting') bl[0] = 'Stopped (restarting)';
    if (b.state === 'unknown' && b.detail === 'starting') bl = ['Starting', 'warn'];
    if (b.state === 'running' && b.application_health !== 'confirmed') { bl[1] = 'warn'; bm += ' · application health unconfirmed'; }
    row('votebot', bl[0], bl[1], bm);

    // Discord
    var dc = d.discord, dl = LABEL.discord[dc.state];
    if (dc.state !== 'unknown' && !fresh(dc.observed_at, hbMax)) dl = ['Unknown', 'unknown'];
    row('discord', dl[0], dl[1], dc.observed_at ? 'observed ' + age(t(dc.observed_at)) :
      (dc.last_connected_at ? 'last connected ' + age(t(dc.last_connected_at)) : 'no current observation'));

    foot(demoNote + (failed ? "Couldn't refresh. Last update " : 'Updated ') + age(gen) + '.', demo);
  }

  function load() {
    if (inflight) return;
    inflight = true; lastFetch = Date.now();
    var ctl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctl) ctl.abort(); }, TIMEOUT_MS);
    fetch(URL_ + '?t=' + Date.now(), { cache: 'no-store', credentials: 'omit', signal: ctl ? ctl.signal : undefined })
      .then(function (res) {
        if (!res.ok) throw new Error('http');
        var hd = Date.parse(res.headers.get('Date') || '');
        if (!isNaN(hd)) skew = hd - Date.now();   // use the web server's clock, not the visitor's
        return res.json();
      })
      .then(function (j) {
        if (!valid(j)) throw new Error('invalid');
        data = j; failed = false; lastOk = Date.now();
      })
      .catch(function () { failed = true; })
      .then(function () { clearTimeout(timer); inflight = false; render(); });
  }

  load();
  setInterval(function () { if (!document.hidden) load(); }, POLL_MS);
  setInterval(render, TICK_MS);
  document.addEventListener('visibilitychange', function () {
    if (!document.hidden && Date.now() - lastFetch > 15000) load();
  });
})();
