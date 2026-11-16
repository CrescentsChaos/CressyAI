/* iNaturalist explorer for CressyAI. Self-contained: builds its own button + panel, talks to /api/inat/*.
   Tabs: Species, Nearby, Sightings, Identify. "Add to chat" puts a compact data block into the composer (#input).
   Composer shortcut: type "/inat red fox" and press Enter to fill the composer with that species' data. */
(() => {
  const $ = (s, r = document) => r.querySelector(s);
  const standalone = document.body.dataset.page === 'nature';
  const h = (tag, a = {}, ...kids) => {
    const e = document.createElement(tag);
    for (const [k, v] of Object.entries(a || {})) {
      if (v == null || v === false) continue;
      if (k === 'class') e.className = v; else if (k === 'text') e.textContent = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v); else e.setAttribute(k, v === true ? '' : v);
    }
    for (const c of kids.flat(Infinity)) if (c != null && c !== false) e.append(c.nodeType ? c : document.createTextNode(c));
    return e;
  };
  const LS = 'cressy.inat.v1';
  const PENDING = 'cressy.inat.pending';
  const st = Object.assign({ place: null, lat: null, lng: null, radius: 10, iconic: '', days: 0, threatened: false, native: false, tab: 'species' },
    (() => { try { return JSON.parse(localStorage.getItem(LS) || '{}'); } catch { return {}; } })());
  const save = () => { try { localStorage.setItem(LS, JSON.stringify(st)); } catch {} };
  let caps = { enabled: true, identify: false, iconic: {} }, view = null, lastMd = '';

  async function api(path, body) {
    const r = await fetch('/api/inat/' + path, body ? { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-Cressy': '1' }, body: JSON.stringify(body) } : {});
    let j = {}; try { j = await r.json(); } catch {}
    if (!r.ok) throw new Error(j.detail || ('Request failed (' + r.status + ')'));
    return j;
  }
  const geo = () => st.place ? { place_id: st.place.id } : (st.lat != null ? { lat: st.lat, lng: st.lng, radius: st.radius } : {});
  const hasGeo = () => !!(st.place || st.lat != null);
  const filt = () => ({ ...geo(), iconic: st.iconic ? [st.iconic] : [], days: st.days || '', threatened: st.threatened, native: st.native });

  // ---------------------------------------------------------------- styles
  document.head.append(h('style', { text: `
#inat-fab{position:static;display:flex;align-items:center;gap:8px;padding:6px 11px;border:1px solid transparent;border-radius:9px;color:var(--mut);font:inherit;text-decoration:none}
#inat-fab:hover{background:var(--card2);border-color:transparent;color:var(--ink)}
#inat{position:fixed;inset:0;z-index:90;display:none;align-items:center;justify-content:center;background:#0008;backdrop-filter:blur(3px)}
#inat.on{display:flex}
#inat .box{width:min(880px,96vw);height:min(760px,92vh);background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:18px;display:flex;flex-direction:column;box-shadow:var(--shadow);overflow:hidden}
body[data-page=nature] #inat{position:static;inset:auto;z-index:auto;display:flex;flex:1;min-height:100vh;padding:0;background:none;backdrop-filter:none}
body[data-page=nature] #inat .box{width:100%;height:100vh;height:100dvh;min-height:100vh;min-height:100dvh;border:0;border-radius:0;box-shadow:none}
#inat header{display:flex;gap:10px;align-items:center;padding:12px 16px;border-bottom:1px solid var(--line)}
#inat header b{font-size:15px;flex:1}
#inat .tabs{display:flex;gap:4px;padding:8px 12px 0;border-bottom:1px solid var(--line)}
#inat .tab{border:0;background:none;color:var(--mut);padding:8px 12px;border-radius:10px 10px 0 0;cursor:pointer;font:600 13px inherit}
#inat .tab.on{color:var(--ink);box-shadow:inset 0 -2px var(--acc2)}
#inat .loc{display:flex;flex-wrap:wrap;gap:8px;align-items:center;padding:10px 16px;border-bottom:1px solid var(--line);font-size:12.5px;color:var(--mut)}
#inat input,#inat select{background:var(--card2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:7px 10px;font:13px inherit;min-width:0}
#inat input[type=text]{flex:1;min-width:140px}
#inat .b{background:var(--card2);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:7px 12px;font:600 12.5px inherit;cursor:pointer}
#inat .b:hover{border-color:var(--acc)}#inat button.p{background:var(--acc);border-color:var(--acc);color:#fff}
#inat .main{flex:1;overflow:auto;padding:14px 16px}
#inat .row{display:flex;gap:8px;margin-bottom:10px;flex-wrap:wrap;align-items:center}
#inat .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px}
#inat .card{background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden;cursor:pointer;display:flex;flex-direction:column}
#inat .card:hover{border-color:var(--acc2)}
#inat .card img,#inat .ph{width:100%;aspect-ratio:4/3;object-fit:cover;background:var(--card2);display:block}
#inat .card div{padding:8px 10px;font-size:12.5px;line-height:1.35}
#inat .card b{display:block;font-size:13px}#inat .card i,#inat .sci{color:var(--mut)}
#inat .pill{display:inline-block;border-radius:999px;padding:1px 8px;font-size:11px;font-weight:700;background:var(--card2);margin:2px 4px 0 0}
#inat .pill.bad{background:color-mix(in srgb,var(--danger) 22%,transparent);color:var(--danger)}
#inat .err{color:var(--danger);font-size:13px;padding:8px 0}#inat .mut{color:var(--mut);font-size:12.5px}
#inat .crumbs{font-size:12px;color:var(--mut);margin:6px 0}#inat .crumbs a{cursor:pointer;color:var(--acc2)}
#inat .photos{display:flex;gap:8px;overflow:auto;margin:10px 0}#inat .photos img{height:170px;border-radius:10px}
#inat .bars{display:flex;align-items:flex-end;gap:4px;height:90px;margin:8px 0 2px}
#inat .bars div{flex:1;background:var(--acc2);border-radius:4px 4px 0 0;min-height:2px;opacity:.85}
#inat .mon{display:flex;gap:4px;font-size:10px;color:var(--mut)}#inat .mon span{flex:1;text-align:center}
#inat h4{margin:16px 0 6px;font-size:13px}#inat p{line-height:1.6;font-size:13.5px;margin:6px 0}
#inat .drop{border:2px dashed var(--line);border-radius:14px;padding:28px;text-align:center;color:var(--mut);cursor:pointer}
` }));

  // ---------------------------------------------------------------- shell
  const body = h('div', { class: 'main' }), tabs = h('div', { class: 'tabs' }), locBar = h('div', { class: 'loc' });
  const panel = h('div', { id: 'inat', onclick: !standalone ? e => { if (e.target === panel) toggle(false); } : null },
    h('div', { class: 'box' },
      h('header', {}, h('b', { text: '🌿 iNaturalist — species & wildlife' }),
        standalone
          ? h('a', { class: 'b', href: '/', style: 'text-decoration:none', text: '← Back to chat' })
          : h('button', { class: 'b', text: 'Close', onclick: () => toggle(false) })),
      tabs, locBar, body));
  const fab = h('a', { id: 'inat-fab', href: '/nature', target: '_blank', rel: 'noopener', title: 'Open iNaturalist explorer in a new tab', text: '🌿 Nature' });

  function toggle(on) {
    if (!standalone) return;
    panel.classList.toggle('on', on ?? !panel.classList.contains('on'));
    if (panel.classList.contains('on')) render();
  }

  function addToChat(md, ask) {
    const ta = $('#input');
    if (!ta && standalone && md) {
      try {
        localStorage.setItem(PENDING, JSON.stringify({ md, ask }));
        window.location.href = '/';
      } catch {
        alert('Could not pass the iNaturalist result to chat. Check browser storage permissions.');
      }
      return;
    }
    if (!ta || !md) return;
    const block = '[iNaturalist data]\n' + md.trim() + '\n[/iNaturalist data]\n\n' + (ask || '');
    ta.value = (ta.value ? ta.value.trimEnd() + '\n\n' : '') + block;
    ta.dispatchEvent(new Event('input', { bubbles: true })); ta.focus(); toggle(false);
  }
  const note = (t, cls = 'mut') => h('div', { class: cls, text: t });
  const busy = el => { el.replaceChildren(note('Loading…')); };

  // ---------------------------------------------------------------- location bar
  function renderLoc() {
    const label = st.place ? st.place.name : st.lat != null ? `${(+st.lat).toFixed(3)}, ${(+st.lng).toFixed(3)} · ${st.radius} km` : 'No location set';
    const q = h('input', { type: 'text', placeholder: 'Search a place (city, park, country)…' });
    const res = h('div', { class: 'row', style: 'width:100%;margin:0' });
    const find = async () => {
      if (!q.value.trim()) return; res.replaceChildren(note('Searching…'));
      try {
        const d = await api('places', { query: q.value });
        res.replaceChildren(...(d.results.length ? d.results.map(p => h('button', { class: 'b', text: p.name, onclick: () => { st.place = { id: p.id, name: p.name }; st.lat = st.lng = null; save(); renderLoc(); render(); } })) : [note('No places found.')]));
      } catch (e) { res.replaceChildren(note(e.message, 'err')); }
    };
    q.addEventListener('keydown', e => { if (e.key === 'Enter') find(); });
    const rad = h('select', { onchange: e => { st.radius = +e.target.value; save(); renderLoc(); } }, ...[2, 5, 10, 25, 50, 100].map(n => h('option', { value: n, selected: n === st.radius, text: n + ' km' })));
    locBar.replaceChildren(h('span', { text: '📍 ' + label }), q, h('button', { class: 'b', text: 'Find', onclick: find }),
      h('button', { class: 'b', text: 'Use my location', onclick: () => navigator.geolocation ? navigator.geolocation.getCurrentPosition(p => { st.lat = p.coords.latitude; st.lng = p.coords.longitude; st.place = null; save(); renderLoc(); render(); }, () => res.replaceChildren(note('Location was blocked by the browser.', 'err'))) : 0 }),
      st.lat != null ? rad : null, (st.place || st.lat != null) ? h('button', { class: 'b', text: 'Clear', onclick: () => { st.place = null; st.lat = st.lng = null; save(); renderLoc(); render(); } }) : null, res);
  }

  // ---------------------------------------------------------------- shared pieces
  const pills = t => [t.status && t.status.name ? h('span', { class: 'pill' + (['CR', 'EN', 'VU'].includes(t.status.code) ? ' bad' : ''), text: t.status.name }) : null, t.extinct ? h('span', { class: 'pill bad', text: 'Extinct' }) : null];
  const taxCard = (t, extra) => h('div', { class: 'card', onclick: () => openTaxon(t.id) },
    t.photo ? h('img', { src: t.photo, loading: 'lazy', referrerpolicy: 'no-referrer', alt: t.common || t.name }) : h('div', { class: 'ph' }),
    h('div', {}, h('b', { text: t.common || t.name }), t.common ? h('i', { text: t.name }) : null, h('br'), h('span', { class: 'mut', text: [t.rank, extra].filter(Boolean).join(' · ') }), h('br'), ...pills(t)));
  const filters = (onchange) => {
    const ic = h('select', { onchange: e => { st.iconic = e.target.value; save(); onchange(); } }, h('option', { value: '', text: 'All life' }),
      ...Object.entries(caps.iconic || {}).map(([k, v]) => h('option', { value: k, selected: k === st.iconic, text: v })));
    const days = h('select', { onchange: e => { st.days = +e.target.value; save(); onchange(); } }, ...[[0, 'Any time'], [7, 'Past week'], [30, 'Past month'], [365, 'Past year']].map(([v, t]) => h('option', { value: v, selected: v === st.days, text: t })));
    const chk = (k, t) => h('label', { class: 'mut' }, h('input', { type: 'checkbox', checked: st[k], onchange: e => { st[k] = e.target.checked; save(); onchange(); } }), ' ' + t);
    return h('div', { class: 'row' }, ic, days, chk('threatened', 'Threatened only'), chk('native', 'Native only'));
  };
  const needGeo = () => note('Set a place or use your location above first.');

  // ---------------------------------------------------------------- tabs
  async function tabSpecies() {
    const q = h('input', { type: 'text', placeholder: 'Species name, e.g. red fox, monarch butterfly, Quercus robur…', value: view?.q || '' });
    const out = h('div');
    const go = async () => {
      if (!q.value.trim()) return; busy(out); view = { q: q.value };
      try { const d = await api('search', { query: q.value, limit: 12 }); lastMd = d.markdown; out.replaceChildren(d.results.length ? h('div', { class: 'grid' }, d.results.map(t => taxCard(t, t.observations.toLocaleString() + ' obs'))) : note('Nothing found.')); }
      catch (e) { out.replaceChildren(note(e.message, 'err')); }
    };
    q.addEventListener('keydown', e => { if (e.key === 'Enter') go(); });
    body.replaceChildren(h('div', { class: 'row' }, q, h('button', { class: 'b p', text: 'Search', onclick: go })), out);
    if (view?.q) go(); q.focus();
  }

  async function openTaxon(id) {
    toggle(true); st.tab = 'species'; save(); renderTabs(); busy(body);
    try {
      const t = await api('taxon', { id }); let seas = null;
      if (hasGeo()) { try { seas = await api('season', { taxon_id: id, ...geo(), radius: Math.max(st.radius, 50) }); } catch {} }
      else { try { seas = await api('season', { taxon_id: id, radius: 50 }); } catch {} }
      const md = [t.markdown, seas && seas.markdown ? (hasGeo() ? 'Locally: ' : 'Worldwide: ') + seas.markdown : ''].filter(Boolean).join('\n');
      body.replaceChildren(
        h('div', { class: 'row' }, h('button', { class: 'b', text: '← Back', onclick: () => { view = view || {}; tabSpecies(); } }),
          h('button', { class: 'b p', text: 'Add to chat', onclick: () => addToChat(md, 'Using the data above, tell me about this species.') }),
          h('a', { class: 'b', href: t.url, target: '_blank', rel: 'noopener', style: 'text-decoration:none', text: 'Open on iNaturalist ↗' })),
        h('h3', { text: t.common || t.name, style: 'margin:4px 0' }), h('div', { class: 'sci', text: t.common ? t.name + ' · ' + t.rank : t.rank }),
        h('div', {}, ...pills(t), h('span', { class: 'pill', text: t.observations.toLocaleString() + ' observations' })),
        h('div', { class: 'crumbs' }, t.ancestors.map((a, i) => [i ? ' › ' : '', h('a', { text: a.common || a.name, onclick: () => openTaxon(a.id) })])),
        t.photos.length ? h('div', { class: 'photos' }, t.photos.map(p => h('img', { src: p.url, loading: 'lazy', referrerpolicy: 'no-referrer', title: p.credit, alt: t.name }))) : null,
        t.summary ? h('p', { text: t.summary }) : null,
        seas && seas.total ? [h('h4', { text: (hasGeo() ? 'When it is seen here' : 'When it is seen worldwide') + ' — best: ' + (seas.best.join(', ') || '—') }),
          h('div', { class: 'bars' }, seas.counts.map((c, i) => h('div', { title: seas.months[i] + ': ' + c, style: `height:${Math.max(2, c / Math.max(...seas.counts) * 100)}%` }))),
          h('div', { class: 'mon' }, seas.months.map(m => h('span', { text: m[0] })))] : null,
        t.similar.length ? [h('h4', { text: 'Often confused with' }), h('div', { class: 'grid' }, t.similar.map(s => taxCard(s)))] : null,
        t.children.length ? [h('h4', { text: 'Includes' }), h('div', { class: 'grid' }, t.children.map(s => taxCard(s)))] : null);
    } catch (e) { body.replaceChildren(note(e.message, 'err')); }
  }

  async function tabNearby() {
    const out = h('div'); body.replaceChildren(filters(tabNearby), out);
    if (!hasGeo()) return out.replaceChildren(needGeo());
    busy(out);
    try {
      const d = await api('nearby', { ...filt(), limit: 30 }); lastMd = d.markdown;
      out.replaceChildren(h('div', { class: 'row' }, h('span', { class: 'mut', text: d.total_species.toLocaleString() + ' species recorded' }),
        h('button', { class: 'b p', text: 'Add to chat', onclick: () => addToChat(d.markdown, 'Which of these are most interesting or notable, and what should I look for?') })),
        h('div', { class: 'grid' }, d.results.map(t => taxCard(t, t.count + ' obs'))));
    } catch (e) { out.replaceChildren(note(e.message, 'err')); }
  }

  async function tabSightings() {
    const out = h('div'); body.replaceChildren(filters(tabSightings), out);
    if (!hasGeo()) return out.replaceChildren(needGeo());
    busy(out);
    try {
      const d = await api('observations', { ...filt(), limit: 18, photos: true });
      out.replaceChildren(h('div', { class: 'row' }, h('span', { class: 'mut', text: d.total.toLocaleString() + ' observations match' }),
        h('button', { class: 'b p', text: 'Add to chat', onclick: () => addToChat(d.markdown, 'Summarise what has been seen recently.') })),
        h('div', { class: 'grid' }, d.results.map(o => h('div', { class: 'card', onclick: () => o.taxon_id ? openTaxon(o.taxon_id) : window.open(o.url, '_blank', 'noopener') },
          o.photo ? h('img', { src: o.photo, loading: 'lazy', referrerpolicy: 'no-referrer', alt: o.taxon }) : h('div', { class: 'ph' }),
          h('div', {}, h('b', { text: o.taxon }), h('span', { class: 'mut', text: o.observed + (o.place ? ' · ' + o.place : '') }), h('br'),
            h('span', { class: 'pill', text: o.grade.replace('_', ' ') }))))));
    } catch (e) { out.replaceChildren(note(e.message, 'err')); }
  }

  function tabIdentify() {
    const out = h('div'), file = h('input', { type: 'file', accept: 'image/jpeg,image/png,image/webp', style: 'display:none' });
    const run = f => {
      if (!f) return; const rd = new FileReader(); busy(out);
      rd.onload = async () => {
        try {
          const d = await api('identify', { image: rd.result, ...(st.lat != null ? { lat: st.lat, lng: st.lng } : {}) });
          out.replaceChildren(h('div', { class: 'row' }, h('img', { src: rd.result, style: 'height:110px;border-radius:10px' }),
            h('button', { class: 'b p', text: 'Add to chat', onclick: () => addToChat(d.markdown, 'Which of these is most likely, and how can I tell them apart?') })),
            h('div', { class: 'grid' }, d.results.map(t => taxCard(t, 'score ' + t.score))));
        } catch (e) { out.replaceChildren(note(e.message, 'err')); }
      }; rd.readAsDataURL(f);
    };
    file.addEventListener('change', () => run(file.files[0]));
    const drop = h('div', { class: 'drop', text: caps.identify ? 'Click or drop a photo of a plant, animal or fungus' : 'Photo identification needs an iNaturalist token — start the server with --inat-token (see README).',
      onclick: () => caps.identify && file.click(), ondragover: e => e.preventDefault(), ondrop: e => { e.preventDefault(); caps.identify && run(e.dataTransfer.files[0]); } });
    body.replaceChildren(drop, file, out);
  }

  const TABS = [['species', '🔎 Species', tabSpecies], ['nearby', '📍 Nearby', tabNearby], ['sightings', '📷 Sightings', tabSightings], ['identify', '🧠 Identify', tabIdentify]];
  function renderTabs() { tabs.replaceChildren(...TABS.map(([k, t]) => h('button', { class: 'tab' + (st.tab === k ? ' on' : ''), text: t, onclick: () => { st.tab = k; save(); renderTabs(); render(); } }))); }
  function render() { (TABS.find(t => t[0] === st.tab) || TABS[0])[2](); }

  // ---------------------------------------------------------------- composer shortcut: /inat <name>
  document.addEventListener('keydown', async e => {
    const ta = e.target;
    if (e.key !== 'Enter' || e.shiftKey || !ta || ta.id !== 'input') return;
    const m = /^\/(?:inat|nature)\s+(.{2,})$/i.exec(ta.value.trim()); if (!m) return;
    e.preventDefault(); e.stopImmediatePropagation();
    const q = m[1]; ta.value = 'Looking up “' + q + '” on iNaturalist…';
    try {
      const s = await api('search', { query: q, limit: 1 }); if (!s.results.length) throw new Error('No species found for “' + q + '”.');
      const t = await api('taxon', { id: s.results[0].id });
      ta.value = '[iNaturalist data]\n' + t.markdown + '\n[/iNaturalist data]\n\nTell me about this species.';
    } catch (err) { ta.value = '/inat ' + q; alert(err.message); }
    ta.dispatchEvent(new Event('input', { bubbles: true })); ta.focus();
  }, true);

  // ---------------------------------------------------------------- boot
  (async () => {
    try { caps = await api('status'); } catch {}
    if (caps.enabled === false) return;
    if (standalone) {
      document.body.append(panel);
      renderTabs(); renderLoc();
    } else {
      $('.lfoot').prepend(fab);
      try {
        const pending = JSON.parse(localStorage.getItem(PENDING) || 'null');
        if (pending?.md) {
          localStorage.removeItem(PENDING);
          addToChat(pending.md, pending.ask || 'Using the data above, tell me about this species.');
        }
      } catch {
        localStorage.removeItem(PENDING);
      }
    }
  })();
})();
