/* Places & weather: Open-Meteo (weather, air quality) + OpenStreetMap (places, nearby).
   Self-contained: it only uses elements declared in index.html and the shape of the /api/chat request, so it does not
   depend on the other scripts. What it does:
   - /weather /aqi /climate /where /near /distance /trip are turned into a plain question before the message is sent;
   - the Places button (or one of those commands) fetches live data from the local server and appends it to the
     outgoing /api/chat request, as <live-data> the model must answer from;
   - the Places tab searches, shows weather, nearby places and a map, and puts a command in the message box.
   Exposes window.CressyGeo for other scripts. */
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
  if (!$('#tab-geo') || !$('#input')) return;
  const nativeFetch = window.fetch.bind(window);
  const KEY = 'cressy.geo.v1';
  const DEF = { place: null, units: 'metric', days: 7, radius: 1000, climate: true, map: 'radar', auto: false, recent: [] };
  let S = { ...DEF };
  try { S = { ...DEF, ...JSON.parse(localStorage.getItem(KEY) || '{}') }; } catch (e) { /* private mode: keep defaults */ }
  const save = () => { try { localStorage.setItem(KEY, JSON.stringify(S)); } catch (e) { /* ignore */ } };
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const safeUrl = u => /^https?:\/\//i.test(u || '') ? String(u) : '';
  const num = (x, d = 0) => x == null || isNaN(x) ? '–' : (+x).toFixed(d).replace(/\.0+$/, '').replace(/(\.\d*?)0+$/, '$1');
  let caps = { geo: true, weather: true, osm: true };
  let view = null;          // the place shown in the Places tab
  let lastCard = null;
  let kinds = [];
  let pending = null;       // a command rewritten into a question, waiting for its /api/chat request
  const ctxByText = new Map();   // question text -> live data, so Retry on the same question keeps its data

  async function post(path, body) {
    const r = await nativeFetch(path, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Cressy': '1' }, body: JSON.stringify(body) });
    let j = null;
    try { j = await r.json(); } catch (e) { /* not JSON */ }
    if (!r.ok) throw new Error((j && j.detail) || ('The lookup failed (' + r.status + ').'));
    return j;
  }
  const opts = () => ({ units: S.units, days: S.days, radius: S.radius, climate: S.climate, default: S.place && { name: S.place.name, lat: S.place.lat, lon: S.place.lon } });
  const coordArg = p => `${p.name || 'Place'} @${(+p.lat).toFixed(4)},${(+p.lon).toFixed(4)}`;
  const shortName = p => (p.name || p.display_name || 'this place');

  /* ---------------------------------------------------------------- settings + composer button */
  function bindSettings() {
    const map = { geounits: 'units', geodays: 'days', georadius: 'radius', geoclimate: 'climate', geomap: 'map' };
    for (const [id, key] of Object.entries(map)) {
      const el = $('#' + id); if (!el) continue;
      el.value = key === 'climate' ? (S.climate ? '1' : '0') : String(S[key]);
      el.addEventListener('change', () => {
        S[key] = key === 'days' || key === 'radius' ? +el.value : key === 'climate' ? el.value === '1' : el.value;
        save(); if (view && key !== 'map') loadView(); else if (key === 'map') renderNear(lastNear);
      });
    }
    $('#geoforget').addEventListener('click', () => { S.place = null; save(); paintSave(); $('#geonote').textContent = 'Saved place removed.'; });
    $('#geotest').addEventListener('click', async () => {
      const n = $('#geonote'); n.textContent = 'Testing…';
      try { n.textContent = statusText(await (await nativeFetch('/api/geo/status?force=1')).json()); } catch (e) { n.textContent = 'Could not reach the CressyAI server.'; }
    });
    const chip = $('#geo');
    const paint = () => { chip.setAttribute('aria-pressed', S.auto ? 'true' : 'false'); chip.classList.toggle('on', S.auto); $('#geofine').hidden = !S.auto; };
    chip.addEventListener('click', () => { S.auto = !S.auto; save(); paint(); if (S.auto && !S.place) setStrip('warn', 'Places is on. Save your place in the Places tab so “weather today” works without a name.'); else if (!S.auto) setStrip(); });
    paint();
  }
  function statusText(st) {
    if (!st.enabled) return st.why || 'Places and weather are turned off.';
    const one = (name, x) => !x.enabled ? name + ': off' : x.ok ? name + ': reachable' : name + ': ' + (x.why || 'not reachable');
    return [one('Open-Meteo', st.weather), one('OpenStreetMap', st.osm)].join('  ·  ') + (st.osm.enabled && !st.contact ? '  ·  Tip: start the server with --osm-contact you@example.com' : '');
  }
  async function refreshStatus() {
    try {
      const st = await (await nativeFetch('/api/geo/status')).json();
      kinds = st.kinds || [];
      const ok = st.enabled && (!st.weather.enabled || st.weather.ok) && (!st.osm.enabled || st.osm.ok);
      for (const id of ['geost', 'geost2']) { const e = $('#' + id); if (e) { e.textContent = !st.enabled ? 'off' : ok ? 'ready' : 'offline'; e.className = 'wst ' + (ok ? 'ok' : 'bad'); } }
      renderKinds();
    } catch (e) { /* server unreachable: leave blank */ }
  }

  /* ---------------------------------------------------------------- the status strip above the message box */
  function setStrip(kind, html, onclick) {
    const s = $('#geostrip');
    if (!kind) { s.hidden = true; s.innerHTML = ''; return; }
    s.hidden = false; s.className = 'geostrip ' + kind;
    s.innerHTML = `<span class="gs-t">${html}</span><button type="button" aria-label="Dismiss">✕</button>`;
    s.onclick = e => { if (e.target.closest('button')) { setStrip(); return; } if (onclick) onclick(); };
    $('#geofine').hidden = false;
  }
  function summarize(r) {
    const c = r.card, p = c.place || {};
    if (c.type === 'nearby') return `📍 ${c.items.length} ${esc(c.label.toLowerCase())} within ${S.units === 'imperial' ? num(c.radius / 1609.344, 1) + ' mi' : c.radius >= 1000 ? num(c.radius / 1000, 1) + ' km' : c.radius + ' m'} of ${esc(shortName(p))}`;
    if (c.type === 'distance') return `📍 ${esc(shortName(p))} → ${esc(shortName(c.place2))}: ${c.distance.km} km ${esc(c.distance.compass)} (straight line)`;
    const w = c.weather;
    if (c.type === 'where') return `📍 ${esc(shortName(p))} (${num(p.lat, 2)}, ${num(p.lon, 2)})` + (w ? ` · ${w.now.emoji} ${num(w.now.temp)}${esc(w.units.temp)}` : '');
    const a = c.air && c.air.us_aqi != null ? ` · AQI ${c.air.us_aqi}` : '';
    return `📍 ${esc(shortName(p))} · ${w.now.emoji} ${num(w.now.temp)}${esc(w.units.temp)}${a}` + (c.type === 'trip' ? ` · trip brief: ${c.sights.length} sights` : '') + ' · ' + esc(r.sources.join(' + '));
  }

  /* ---------------------------------------------------------------- command rewriting (runs before the app's own handlers) */
  const CMD = /^\/(weather|forecast|aqi|air|climate|where|near|nearby|distance|trip)\b[ \t]*([\s\S]*)$/i;
  const stripArgs = a => a.replace(/@\s*-?\d+(?:\.\d+)?\s*,\s*-?\d+(?:\.\d+)?/g, ' ').replace(/\bkind:\w+/g, ' ').replace(/\b(?:for |next )?\d{1,2}[- ]?(?:days?|d)\b/gi, ' ').replace(/\s+/g, ' ').trim();
  function question(cmd, args) {
    const n = (args.match(/\b(\d{1,2})[- ]?(?:days?|d)\b/i) || [])[1];
    const name = stripArgs(args).replace(/^(?:in|for|at|near)\s+/i, '') || (S.place && S.place.name) || 'my saved place';
    const where = /@/.test(args) || stripArgs(args) || S.place ? name : 'my saved place';
    switch (cmd) {
      case 'weather': case 'forecast': return `What's the weather in ${where}${n ? ' for the next ' + n + ' days' : ''}? Give me the current conditions, what the coming days look like, and anything I should plan around.`;
      case 'aqi': case 'air': return `How is the air quality in ${where} right now, and is it fine to be outside?`;
      case 'climate': return `Is the weather in ${where} unusual for this time of year compared with normal?`;
      case 'where': return `Where is ${where}? Tell me about its location, local time and the weather there now.`;
      case 'near': case 'nearby': {
        const k = (args.match(/\bkind:(\w+)/) || [])[1];
        const label = k && kinds.find(x => x.id === k) ? kinds.find(x => x.id === k).label.toLowerCase() : stripArgs(args).split(/\s+(?:near|in|around|at|by)\s+/i)[0] || 'places';
        const rest = k ? stripArgs(args) : (stripArgs(args).split(/\s+(?:near|in|around|at|by)\s+/i)[1] || '');
        return `What ${label} are near ${rest || (S.place && S.place.name) || 'my saved place'}? List the nearest first.`;
      }
      case 'distance': { const [a, b] = args.split(/\s+(?:to|and|vs\.?)\s+|\s*(?:->|→)\s*/i); return `How far is ${a} from ${b}, and in which direction?`; }
      case 'trip': return `Help me plan ${n || 3} days in ${where}. Use the forecast to decide what to do on which day, and tell me what to pack.`;
    }
    return '';
  }
  function invalid(cmd, args) {
    if (/^(near|nearby)$/.test(cmd) && !/\bkind:\w+/.test(args) && !args.trim()) return 'Say what to look for, for example /near pharmacy or /near cafe in Lisbon.';
    if (cmd === 'distance' && !/\s(?:to|and|vs\.?)\s|->|→/i.test(args)) return 'Give two places: /distance Paris to Rome';
    if (/^(where|find)$/.test(cmd) && !args.trim()) return 'Say which place: /where Eiffel Tower';
    return '';
  }
  // Returns true when the text was a command (rewritten, or refused with a message)
  function rewrite(ev) {
    if (!caps.geo) return false;
    const ta = $('#input'), m = CMD.exec((ta.value || '').trim());
    if (!m) return false;
    const cmd = m[1].toLowerCase(), args = m[2].trim();
    const bad = invalid(cmd, args);
    if (bad) { if (ev) { ev.preventDefault(); ev.stopImmediatePropagation(); } setStrip('err', esc(bad)); return true; }
    const text = question(cmd, args);
    pending = { cmd, args, text };
    ta.value = text;
    ta.dispatchEvent(new Event('input', { bubbles: true }));
    return true;
  }
  document.addEventListener('keydown', e => { if (e.key === 'Enter' && !e.shiftKey && !e.isComposing && e.target && e.target.id === 'input') rewrite(e); }, true);
  document.addEventListener('submit', e => { if (e.target && e.target.id === 'f') rewrite(e); }, true);
  document.addEventListener('click', e => { if (e.target.closest && e.target.closest('#go')) rewrite(e); }, true);

  /* ---------------------------------------------------------------- attaching live data to the outgoing chat request */
  const isPending = text => pending && text.startsWith(pending.text);      // attachments may be appended after the question
  async function lookup(text) {
    if (isPending(text)) {
      const p = pending; pending = null;
      return post('/api/geo/context', { cmd: p.cmd, args: p.args, ...opts() });
    }
    if (ctxByText.has(text)) return { used: true, text: ctxByText.get(text), cached: true };
    return post('/api/geo/context', { message: text, ...opts() });
  }
  async function augment(bodyStr) {
    let b; try { b = JSON.parse(bodyStr); } catch (e) { return bodyStr; }
    const msgs = b && b.messages; if (!Array.isArray(msgs)) return bodyStr;
    let i = msgs.length - 1; while (i >= 0 && msgs[i].role !== 'user') i--;
    if (i < 0 || typeof msgs[i].content !== 'string') return bodyStr;
    const text = msgs[i].content;
    const forced = isPending(text), cached = ctxByText.has(text);
    if (!forced && !cached && !(S.auto && caps.geo && text.length <= 1200)) return bodyStr;   // long messages (files attached) are never auto-detected
    const slow = setTimeout(() => setStrip('busy', 'Looking up live data…'), forced ? 0 : 400);
    try {
      const r = await lookup(text);
      clearTimeout(slow);
      if (r.need_place) { setStrip('warn', esc(r.why)); return bodyStr; }
      if (!r.used) { if ($('#geostrip').classList.contains('busy')) setStrip(); return bodyStr; }
      msgs[i].content = text + '\n\n' + r.text;
      ctxByText.set(text, r.text); if (ctxByText.size > 20) ctxByText.delete(ctxByText.keys().next().value);
      if (r.card) { showCard(r); setStrip('ok', summarize(r), () => openTab()); } else setStrip('ok', 'Live data attached (from the earlier lookup).');
    } catch (e) {
      clearTimeout(slow);
      msgs[i].content = text + '\n\n<live-data status="unavailable">The live weather/place lookup failed: ' + String(e.message || e).slice(0, 200) +
        '. Tell the user you could not fetch live data and do not guess numbers or places.</live-data>';
      setStrip('err', esc(String(e.message || e)));
    }
    return JSON.stringify(b);
  }
  window.fetch = function (input, init) {
    const url = typeof input === 'string' ? input : (input && input.url) || '';
    if (!/\/api\/chat(?:\?|$)/.test(url) || !init || typeof init.body !== 'string') return nativeFetch(input, init);
    return augment(init.body).then(body => nativeFetch(input, { ...init, body }), () => nativeFetch(input, init));
  };

  /* ---------------------------------------------------------------- the Places tab */
  function openTab() {
    const b = $('.tabs button[data-tab="geo"]'); if (b) b.click();
  }
  const tabBtn = $('.tabs button[data-tab="geo"]');
  if (tabBtn) tabBtn.addEventListener('click', () => {      // works even if the app's own tab handler does not know this tab
    $$('.tabs button').forEach(x => x.classList.toggle('on', x === tabBtn));
    $$('section.tab').forEach(x => x.classList.toggle('on', x.id === 'tab-geo'));
    if (!view && S.place) showPlace(S.place);
  });
  const msg = (t, bad) => { const m = $('#geomsg'); m.textContent = t || ''; m.className = 'mc-n gnote' + (bad ? ' bad' : ''); };

  $('#geoform').addEventListener('submit', async e => {
    e.preventDefault();
    const q = $('#geoq').value.trim(); if (!q) return;
    msg('Searching…'); $('#geores').innerHTML = '';
    try {
      const r = await post('/api/geo/search', { query: q, n: 5 });
      if (!r.results.length) { msg('Nothing found for “' + q + '”. Try adding the country.', true); return; }
      msg(r.source === 'open-meteo' ? 'OpenStreetMap did not answer, so Open-Meteo’s place search was used.' : '');
      $('#geores').innerHTML = r.results.map((p, i) => `<li data-i="${i}"><b>${esc(p.name)}</b><small>${esc(p.display_name)}${p.kind ? ' · ' + esc(p.kind) : ''}</small></li>`).join('');
      $('#geores').onclick = ev => { const li = ev.target.closest('li'); if (li) { showPlace(r.results[+li.dataset.i]); $('#geores').innerHTML = ''; } };
      if (r.results.length === 1) showPlace(r.results[0]);
    } catch (err) { msg(err.message, true); }
  });
  $('#geolocate').addEventListener('click', () => {
    if (!navigator.geolocation) { msg('Your browser cannot share a location.', true); return; }
    msg('Asking your browser for your location…');
    navigator.geolocation.getCurrentPosition(async pos => {
      try { const r = await post('/api/geo/reverse', { lat: pos.coords.latitude, lon: pos.coords.longitude }); msg(''); showPlace(r.place); }
      catch (err) { msg(err.message, true); }
    }, err => msg(err.code === 1 ? 'Location permission was denied. You can search for your place instead.' : 'Could not get your location.', true), { timeout: 10000, maximumAge: 600000 });
  });

  function paintSave() {
    const b = $('#gsave'); if (!b) return;
    const on = view && S.place && Math.abs(S.place.lat - view.lat) < 1e-4 && Math.abs(S.place.lon - view.lon) < 1e-4;
    b.textContent = on ? '★ My place' : '☆ My place'; b.title = on ? 'This is your saved place (click to remove)' : 'Use this place when a question does not name one';
  }
  $('#gsave').addEventListener('click', () => {
    if (!view) return;
    const on = S.place && Math.abs(S.place.lat - view.lat) < 1e-4 && Math.abs(S.place.lon - view.lon) < 1e-4;
    S.place = on ? null : { name: shortName(view), lat: +view.lat, lon: +view.lon };
    save(); paintSave();
  });
  $('#gosm').addEventListener('click', () => { const u = $('#gosm').dataset.url; if (u) window.open(u, '_blank', 'noopener,noreferrer'); });
  $('#gcopy').addEventListener('click', async () => {
    if (!view) return; const t = (+view.lat).toFixed(5) + ', ' + (+view.lon).toFixed(5);
    try { await navigator.clipboard.writeText(t); msg('Copied ' + t); } catch (e) { msg(t); }
  });
  $$('#tab-geo [data-ask]').forEach(b => b.addEventListener('click', () => {
    if (!view) return; const ta = $('#input');
    ta.value = `/${b.dataset.ask} ${coordArg(view)}`; ta.dispatchEvent(new Event('input', { bubbles: true })); ta.focus();
  }));

  function showPlace(p) {
    view = { name: p.name, display_name: p.display_name || p.name, lat: +p.lat, lon: +p.lon, map_url: p.map_url, kind: p.kind, country: p.country };
    S.recent = [view, ...S.recent.filter(r => Math.abs(r.lat - view.lat) > 1e-4 || Math.abs(r.lon - view.lon) > 1e-4)].slice(0, 6).map(r => ({ name: r.name, display_name: r.display_name, lat: r.lat, lon: r.lon })); save();
    $('#geoview').hidden = false;
    $('#gname').textContent = shortName(view);
    $('#gsub').textContent = `${(+view.lat).toFixed(4)}, ${(+view.lon).toFixed(4)}` + (view.display_name && view.display_name !== view.name ? ' · ' + view.display_name : '');
    $('#gosm').dataset.url = safeUrl(view.map_url) || `https://www.openstreetmap.org/?mlat=${view.lat}&mlon=${view.lon}#map=16/${view.lat}/${view.lon}`;
    paintSave(); $('#gnear').innerHTML = ''; lastNear = null; $$('#gkinds button').forEach(b => b.classList.remove('primary'));
    loadView();
  }
  async function loadView() {
    if (!view) return;
    const box = $('#gwx'); box.innerHTML = caps.weather ? '<p class="gnote">Loading weather…</p>' : '';
    if (!caps.weather) return;
    try {
      const r = await post('/api/geo/context', { cmd: 'weather', args: coordArg(view), ...opts() });
      lastCard = r; renderWeather(r.card, r);
    } catch (err) { box.innerHTML = `<p class="gnote bad">${esc(err.message)}</p>`; }
  }
  function showCard(r) {    // a lookup made from a chat message: show it in the tab without switching to it
    const c = r.card, p = c.place; if (!p) return;
    if (c.type === 'distance') return;
    view = { name: p.name, display_name: p.display_name || p.name, lat: +p.lat, lon: +p.lon, map_url: p.map_url };
    $('#geoview').hidden = false; $('#gname').textContent = shortName(view);
    $('#gsub').textContent = `${(+view.lat).toFixed(4)}, ${(+view.lon).toFixed(4)}`; paintSave();
    if (c.weather) renderWeather(c, r);
    if (c.type === 'nearby') { lastNear = c; renderNear(c); } else if (c.type === 'trip') { lastNear = { ...c, items: c.sights, label: 'Sights', kind: 'attraction' }; renderNear(lastNear); }
  }

  /* ---------------------------------------------------------------- weather card */
  const aqClass = v => v == null ? '' : v <= 50 ? 'g1' : v <= 100 ? 'g2' : v <= 150 ? 'g3' : v <= 200 ? 'g4' : 'g5';
  function chart(h, u) {
    if (!h || h.length < 2) return '';
    const W = 300, H = 78, pad = 14, t = h.map(x => x.temp), lo = Math.min(...t), hi = Math.max(...t), span = Math.max(1, hi - lo);
    const x = i => pad + i * (W - 2 * pad) / (h.length - 1), y = v => 8 + (H - 30) * (1 - (v - lo) / span);
    const bars = h.map((p, i) => `<rect class="p" x="${x(i) - 3}" y="${H - 16 - (p.pop || 0) * 0.3}" width="6" height="${(p.pop || 0) * 0.3}"></rect>`).join('');
    const labels = [0, 6, 12, 18, 23].filter(i => i < h.length).map(i => `<text x="${x(i)}" y="${H - 3}" text-anchor="middle">${esc(h[i].time.slice(11, 13))}h</text>`).join('');
    return `<svg class="gsvg" viewBox="0 0 ${W} ${H}" role="img" aria-label="Temperature and rain chance, next 24 hours">${bars}<polyline class="t" points="${h.map((p, i) => x(i) + ',' + y(p.temp).toFixed(1)).join(' ')}"></polyline>` +
      `<text x="${W - pad}" y="${y(hi) - 2}" text-anchor="end">${num(hi)}${esc(u.temp)}</text><text x="${W - pad}" y="${y(lo) + 10}" text-anchor="end">${num(lo)}${esc(u.temp)}</text>${labels}</svg>`;
  }
  function renderWeather(c, r) {
    const w = c.weather, n = w.now, u = w.units, box = $('#gwx');
    const a = c.air, cl = c.climate;
    const chips = [`💧 ${num(n.humidity)}%`, `💨 ${num(n.wind)} ${esc(u.wind)} ${esc(n.wind_dir_txt || '')}`, `🧭 ${num(n.pressure)} ${esc(u.pressure)}`, `☁️ ${num(n.cloud)}%`]
      .map(t => `<span>${t}</span>`);
    if (w.daily[0]) chips.push(`<span>🌅 ${esc(w.daily[0].sunrise)} · 🌇 ${esc(w.daily[0].sunset)}</span>`, `<span>🕶️ UV ${num(w.daily[0].uv)}</span>`);
    if (a && a.us_aqi != null) chips.unshift(`<span class="aq ${aqClass(a.us_aqi)}" title="${esc(a.advice || '')}">AQI ${a.us_aqi} · ${esc(a.category)}</span>`);
    const days = w.daily.map(d => `<div><b>${esc(d.weekday)}</b><span>${d.emoji}</span><span>${esc(d.text)}</span><small>${d.pop != null ? num(d.pop) + '%' : ''}</small><span>${num(d.tmin)}–${num(d.tmax)}${esc(u.temp)}</span></div>`).join('');
    box.innerHTML = `<div class="gwx"><div class="gnow"><span class="em">${n.emoji}</span><div><div class="big">${num(n.temp, 1)}${esc(u.temp)}</div><small>${esc(n.text)} · feels ${num(n.feels, 1)}${esc(u.temp)} · local ${esc((n.time || '').slice(11, 16))}</small></div></div>` +
      `<div class="gchips">${chips.join('')}</div>${chart(w.hourly, u)}<div class="gdays">${days}</div>` +
      (cl ? `<p class="gnote">Compared with the ${esc(cl.years)} average for these dates: ${esc(cl.label)} (highs ${cl.tmax_diff >= 0 ? '+' : ''}${num(cl.tmax_diff, 1)}${esc(cl.temp)}).</p>` : '') +
      ((r.notes || []).map(t => `<p class="gnote bad">${esc(t)}</p>`).join('')) + '</div>';
    $('#gattr').textContent = (r.attribution || []).filter(Boolean).join(' · ');
  }

  /* ---------------------------------------------------------------- nearby + map */
  let lastNear = null, selected = -1;
  function renderKinds() {
    const box = $('#gkinds'); if (!box) return;
    box.innerHTML = kinds.map(k => `<button type="button" data-k="${esc(k.id)}" title="${esc(k.label)}">${k.emoji} ${esc(k.label)}</button>`).join('');
  }
  $('#gkinds').addEventListener('click', async e => {
    const b = e.target.closest('button'); if (!b || !view) return;
    $$('#gkinds button').forEach(x => x.classList.toggle('primary', x === b));
    $('#gnear').innerHTML = '<p class="gnote">Searching OpenStreetMap…</p>';
    try {
      const r = await post('/api/geo/context', { cmd: 'near', args: `kind:${b.dataset.k} ${coordArg(view)}`, ...opts() });
      lastNear = r.card; selected = -1; renderNear(r.card); $('#gattr').textContent = (r.attribution || []).filter(Boolean).join(' · ');
    } catch (err) { $('#gnear').innerHTML = `<p class="gnote bad">${esc(err.message)}</p>`; }
  });
  const dist = m => S.units === 'imperial' ? (m < 160 ? Math.round(m * 3.28084 / 10) * 10 + ' ft' : num(m / 1609.344, 1) + ' mi') : (m < 1000 ? Math.round(m / 10) * 10 + ' m' : num(m / 1000, 1) + ' km');
  function renderNear(c) {
    const box = $('#gnear'); if (!box) return;
    if (!c) { box.innerHTML = ''; return; }
    const items = c.items || [];
    if (!items.length) { box.innerHTML = '<p class="gnote">Nothing found in OpenStreetMap within that distance. The area may simply not be well mapped. Try a larger radius in Settings.</p>'; return; }
    const li = items.map((p, i) => {
      const bits = [dist(p.distance_m) + ' ' + p.compass, p.address, p.cuisine, p.opening_hours && 'hours ' + p.opening_hours, p.phone].filter(Boolean).map(esc).join(' · ');
      const web = safeUrl(p.website), link = safeUrl(p.osm_url);
      return `<li data-i="${i}"><b>${i + 1}. ${esc(p.name)}</b><small>${bits}</small>${link ? `<small><a href="${esc(link)}" target="_blank" rel="noopener noreferrer">OpenStreetMap</a>${web ? ` · <a href="${esc(web)}" target="_blank" rel="noopener noreferrer">website</a>` : ''}</small>` : ''}</li>`;
    }).join('');
    box.innerHTML = `<div class="gmap" id="gmapbox"></div><ul class="gres" id="gresl">${li}</ul><div class="btns"><button type="button" id="ggpx">Download GPX</button></div>` +
      '<div class="mc-n">OpenStreetMap is made by volunteers, so places can be missing or out of date, and opening hours are what contributors entered.</div>';
    drawMap(c, items);
    $('#gresl').onclick = e => { if (e.target.closest('a')) return; const l = e.target.closest('li'); if (l) select(+l.dataset.i); };
    $('#ggpx').onclick = async () => {
      try {
        const r = await nativeFetch('/api/geo/gpx', { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Cressy': '1' }, body: JSON.stringify({ name: c.label || 'Places', points: items.map(p => ({ name: p.name, lat: p.lat, lon: p.lon, address: p.address })) }) });
        if (!r.ok) throw new Error('Export failed.');
        const a = document.createElement('a'); a.href = URL.createObjectURL(await r.blob()); a.download = 'places.gpx'; a.click(); setTimeout(() => URL.revokeObjectURL(a.href), 2000);
      } catch (err) { msg(err.message, true); }
    };
  }
  function select(i) {
    selected = i;
    $$('#gresl li').forEach((l, k) => l.classList.toggle('on', k === i));
    $$('#gmapbox .pt').forEach(g => g.classList.toggle('on', +g.dataset.i === i));
    if (leafletMap && leafletMarkers[i]) { leafletMarkers[i].openPopup(); }
  }
  let leafletMap = null, leafletMarkers = [], leafletPromise = null;
  function loadLeaflet() {
    if (window.L) return Promise.resolve();
    if (leafletPromise) return leafletPromise;
    leafletPromise = new Promise((ok, fail) => {
      const css = document.createElement('link'); css.rel = 'stylesheet'; css.href = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css'; document.head.appendChild(css);
      const js = document.createElement('script'); js.src = 'https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js'; js.onload = ok; js.onerror = () => { leafletPromise = null; fail(new Error('map library')); }; document.head.appendChild(js);
    });
    return leafletPromise;
  }
  function radar(c, items) {
    const R = 125, cx = 150, cy = 150, max = Math.max(c.radius || 1000, ...items.map(p => p.distance_m)) * 1.05;
    const pts = items.map((p, i) => {
      const r = Math.min(R + 6, p.distance_m / max * R), b = p.bearing * Math.PI / 180;
      const x = cx + Math.sin(b) * r, y = cy - Math.cos(b) * r;
      return `<g class="pt" data-i="${i}"><circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="7"></circle><text x="${x.toFixed(1)}" y="${(y + 3).toFixed(1)}">${i + 1}</text></g>`;
    }).join('');
    return `<svg viewBox="0 0 300 300" role="img" aria-label="Offline map: the places around the centre, by distance and direction"><circle class="ring" cx="${cx}" cy="${cy}" r="${R}"></circle><circle class="ring" cx="${cx}" cy="${cy}" r="${R / 2}"></circle>` +
      `<text x="${cx}" y="${cy - R - 4}" text-anchor="middle">N</text><text x="${cx + R / 2 + 3}" y="${cy - 3}">${esc(dist(max / 2))}</text><text x="${cx + R - 28}" y="${cy + R - 4}">${esc(dist(max))}</text><circle class="ctr" cx="${cx}" cy="${cy}" r="5"></circle>${pts}</svg>` +
      '<div class="gmapn">Offline view: distance and direction only, nothing is loaded from the internet. Choose “OpenStreetMap tiles” in Settings for a real map.</div>';
  }
  async function drawMap(c, items) {
    const box = $('#gmapbox'); if (!box) return;
    leafletMap = null; leafletMarkers = [];
    if (S.map !== 'tiles') {
      box.innerHTML = radar(c, items);
      box.onclick = e => { const g = e.target.closest('.pt'); if (g) select(+g.dataset.i); };
      return;
    }
    box.innerHTML = '<div class="gmapn">Loading map…</div>';
    try {
      await loadLeaflet();
      box.innerHTML = '<div class="gmapl" id="gmapl"></div><div class="gmapn">Map tiles are loaded from tile.openstreetmap.org. © OpenStreetMap contributors.</div>';
      const L = window.L, p = c.place;
      leafletMap = L.map('gmapl', { scrollWheelZoom: false }).setView([p.lat, p.lon], 15);
      L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors' }).addTo(leafletMap);
      L.circleMarker([p.lat, p.lon], { radius: 6 }).addTo(leafletMap).bindPopup(esc(shortName(p)));
      items.forEach((it, i) => { const m = L.marker([it.lat, it.lon]).addTo(leafletMap).bindPopup(`<b>${i + 1}. ${esc(it.name)}</b><br>${esc(dist(it.distance_m))} ${esc(it.compass)}`); m.on('click', () => select(i)); leafletMarkers.push(m); });
      if (items.length) leafletMap.fitBounds(L.latLngBounds([[p.lat, p.lon], ...items.map(it => [it.lat, it.lon])]).pad(0.15));
    } catch (err) { box.innerHTML = radar(c, items); S.map = 'radar'; const s = $('#geomap'); if (s) s.value = 'radar'; msg('The map library could not be loaded, so the offline view is shown.', true); }
  }

  /* ---------------------------------------------------------------- start */
  window.CressyGeo = { pending: text => isPending(text), state: () => S, augment, rewrite: () => rewrite(null), openTab, show: showPlace, parseQuestion: question };
  bindSettings();
  nativeFetch('/api/caps').then(r => r.json()).then(c => {
    caps = { geo: !!c.geo, weather: !!c.weather, osm: !!c.osm };
    if (!caps.geo) { $('#geo').hidden = true; if (tabBtn) tabBtn.hidden = true; const t = $('#geost'); if (t) t.textContent = 'off'; $('#geonote').textContent = 'Places and weather are turned off (--no-geo, --no-weather, --no-osm or --no-web).'; }
    else refreshStatus();
  }).catch(() => { /* older server without /api/caps: leave everything enabled */ });
})();
