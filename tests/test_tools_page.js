// Loads the real index.html + every js/ file in jsdom, with a scripted server behind fetch, and drives send().
// Run: node tests/test_tools_page.js          (needs jsdom: npm i jsdom, or set JSDOM=/path/to/jsdom)
const fs = require('fs'), path = require('path'), assert = require('assert');
const { JSDOM } = require(process.env.JSDOM || 'jsdom');
const root = path.join(__dirname, '..');
let passed = 0;
const sleep = ms => new Promise(r => setTimeout(r, ms));

const STUBS = `window.marked={parse:s=>'<p>'+String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;')+'</p>',use(){},setOptions(){},Renderer:function(){}};
window.DOMPurify={sanitize:s=>s,addHook(){}};window.hljs={highlightElement(){},highlightAuto:()=>({value:''}),getLanguage:()=>null};
window.matchMedia=window.matchMedia||(()=>({matches:false,addEventListener(){},addListener(){}}));
window.ResizeObserver=window.ResizeObserver||class{observe(){}disconnect(){}};window.IntersectionObserver=window.IntersectionObserver||class{observe(){}disconnect(){}};
Element.prototype.scrollTo=Element.prototype.scrollTo||function(){};Element.prototype.scrollIntoView=function(){};window.scrollTo=()=>{};`;

async function boot(script) {
  let html = fs.readFileSync(path.join(root, 'index.html'), 'utf8');
  html = html.replace(/<script src="https:[^>]*><\/script>/g, '').replace(/<link[^>]*(?:googleapis|cdnjs)[^>]*>/g, '');
  html = html.replace(/<script src="(js\/[^"]+)"><\/script>/g, (_, f) => '<script>' + fs.readFileSync(path.join(root, f), 'utf8').replace(/<\/script/gi, '<\\/script') + '</script>');
  const calls = [];
  const dom = new JSDOM(html.replace('<head>', '<head><script>' + STUBS + '</script>'), {
    url: 'http://127.0.0.1:8000/', runScripts: 'dangerously', pretendToBeVisual: true,
    beforeParse(w) {
      const enc = new TextEncoder();
      const stream = evs => { let i = 0; const ch = evs.map(e => enc.encode('data: ' + JSON.stringify(e) + '\n\n')); return { getReader: () => ({ read: async () => i < ch.length ? { done: false, value: ch[i++] } : { done: true } }) }; };
      const json = (d, ok = true) => ({ ok, status: ok ? 200 : 400, json: async () => d, text: async () => JSON.stringify(d) });
      w.fetch = async (url, o = {}) => {
        const body = o.body && typeof o.body === 'string' ? JSON.parse(o.body) : null;
        calls.push({ url, body, headers: o.headers || {} });
        if (url === '/api/caps') return json({ tools: true, web: true, geo: true, weather: true, osm: true, kb: false, vision: false, image: false, terminal: false, write: false, root: '', os: '', shell: '' });
        if (url === '/api/info') return json({ loaded: true, n_ctx: 8192, file: 'm.gguf', model_state: 'coder' });
        if (url.startsWith('/api/fs')) return json({ path: '', root: '/proj', items: [] });
        if (url === '/api/kb') return json({ enabled: false, docs: [], chunks: 0, chars: 0, pdf: false, max_mb: 0 });
        if (/^\/api\/(web|wiki|inat|geo)\/status/.test(url)) return json({ enabled: true, ok: true, why: '', weather: { enabled: true, ok: true }, osm: { enabled: true, ok: true }, kinds: [] });
        if (url === '/api/chats') return json([]);
        if (url.startsWith('/api/memory/recall')) return json({ profile: '', pinned: [], matches: [], tasks: [], sessions: [], last_session: null });
        if (url.startsWith('/api/memory')) return json({ facts: [], tasks: [], sessions: [], semantic: { on: false }, project_name: 'p', profile: '' });
        if (url === '/api/route') return json({ enabled: false, switched: false, reason: false });
        if (url === '/api/tools/select') return json(script.select(body));
        if (url === '/api/agent') return { ok: true, status: 200, body: stream(script.agent(body)) };
        if (url === '/api/chat') return { ok: true, status: 200, body: stream([{ type: 'token', text: 'plain answer' }, { type: 'done' }]) };
        return json({});
      };
    },
  });
  const w = dom.window;
  await sleep(150);
  for (const id of ['#memlearn', '#memauto']) w.document.querySelector(id).value = '0';   // background memory learning also uses /api/chat
  const ask = async text => { w.document.querySelector('#input').value = text; const p = w.send(); await p; for (let i = 0; i < 100 && w.eval('busy'); i++) await sleep(20); await sleep(30); };
  return { w, calls, ask, $: s => w.document.querySelector(s), $$: s => [...w.document.querySelectorAll(s)], last: () => w.eval('msgs[msgs.length-1]') };
}

async function t(name, fn) { try { await fn(); passed++; console.log('ok   ' + name); } catch (e) { console.log('FAIL ' + name + '\n' + (e.stack || e)); process.exitCode = 1; } }

const SEL = { use: true, tools: ['weather', 'geocode'], seeds: [], intents: ['weather'], why: 'intent: weather → weather, geocode' };
const toks = (...xs) => xs.map(text => ({ type: 'token', text }));

(async () => {
  await t('an answer that needs a tool goes to /api/agent and shows the tool card', async () => {
    const S = { select: () => SEL, agent: () => [{ type: 'plan', tools: ['weather'], why: 'w' }, { type: 'tool_start', id: 1, name: 'weather', args: { place: 'Dhaka' }, via: 'model' },
      { type: 'tool_result', id: 1, name: 'weather', args: { place: 'Dhaka' }, status: 'ok', notes: [], ms: 420, via: 'model', preview: 'Dhaka 28 C' }, ...toks('It is ', '28 C in Dhaka.'), { type: 'done', completion_tokens: 8, tps: 5 }] };
    const P = await boot(S);
    await P.ask('weather in Dhaka tomorrow?');
    const ag = P.calls.find(c => c.url === '/api/agent'), sel = P.calls.find(c => c.url === '/api/tools/select');
    assert.ok(ag && sel, 'agent and select were called');
    assert.strictEqual(ag.headers['X-Cressy'], '1');
    assert.ok(ag.body.messages.some(m => m.role === 'user' && m.content.includes('weather in Dhaka')) && ag.body.text.includes('Dhaka') && ag.body.steps === 4 && ag.body.verify === true);
    assert.ok(!P.calls.some(c => c.url === '/api/chat'));
    assert.strictEqual(P.last().content, 'It is 28 C in Dhaka.');
    const card = P.$('.msg.assistant .toolcard');
    assert.ok(!card.hidden);
    assert.match(card.querySelector('.wq').textContent, /Used 1 tool: weather/);
    assert.strictEqual(card.querySelectorAll('.tool-l li').length, 1);
    assert.match(card.querySelector('.tn').textContent, /weather\(place=“Dhaka”\)/);
    assert.ok(card.querySelector('.td.ok'));
    assert.ok(P.last().tools.calls[0].preview.includes('28'));
    assert.strictEqual(P.last().stats.completion_tokens, 8);
  });

  await t('no matching tool: the normal /api/chat path is used', async () => {
    const P = await boot({ select: () => ({ use: false }), agent: () => [] });
    await P.ask('tell me a joke about compilers');
    assert.ok(P.calls.some(c => c.url === '/api/chat') && !P.calls.some(c => c.url === '/api/agent'));
    assert.strictEqual(P.last().content, 'plain answer');
    assert.ok(P.$('.msg.assistant .toolcard').hidden);
  });

  await t('web sources from tools feed the existing citation chips', async () => {
    const src = [{ title: 'A', url: 'https://a.example/x', domain: 'a.example', snippet: 's', read: true }, { title: 'B', url: 'https://b.example/y', domain: 'b.example', snippet: '', read: false }];
    const P = await boot({ select: () => ({ ...SEL, tools: ['web_search'] }), agent: () => [{ type: 'plan', tools: ['web_search'] }, { type: 'tool_start', id: 1, name: 'web_search', args: { query: 'x' } },
      { type: 'tool_result', id: 1, name: 'web_search', args: { query: 'x' }, status: 'ok', notes: [], ms: 900, sources: src, preview: 'r' }, ...toks('Per A [1] and B [2].'), { type: 'done' }] });
    await P.ask('latest news about x');
    assert.strictEqual(P.$$('.msg.assistant .web-l li').length, 2);
    assert.ok(!P.$('.msg.assistant .web').hidden);
    assert.ok(P.$$('.msg.assistant .body a.cite').length >= 1, 'citation chip rendered');
  });

  await t('failures open the card and say why; unsupported figures are flagged', async () => {
    const P = await boot({ select: () => SEL, agent: () => [{ type: 'plan', tools: ['weather'] }, { type: 'tool_start', id: 1, name: 'weather', args: { place: 'Atlantis' } },
      { type: 'tool_result', id: 1, name: 'weather', args: { place: 'Atlantis' }, status: 'error', notes: ['I could not find a place called Atlantis.'], ms: 80, via: 'model', preview: '' },
      ...toks('Sorry, 98765 unknown.'), { type: 'grounding', unsupported: ['98765'] }, { type: 'done' }] });
    await P.ask('weather in Atlantis');
    const card = P.$('.msg.assistant .toolcard');
    assert.ok(card.classList.contains('open'));
    assert.ok(card.querySelector('.td.bad') && /Atlantis/.test(card.querySelector('.tnote').textContent));
    assert.ok(!card.querySelector('.tool-note').hidden && /98765/.test(card.querySelector('.tool-note').textContent));
    assert.match(card.querySelector('.wq').textContent, /1 failed or empty.*unverified figures/);
  });

  await t('/tools forces the whole toolbox for one message, then reverts', async () => {
    const P = await boot({ select: b => ({ use: !!b.force, tools: ['calculator'], seeds: [], intents: [], why: 'forced' }), agent: () => [{ type: 'plan', tools: ['calculator'] }, ...toks('done'), { type: 'done' }] });
    await P.ask('/tools what is a monad');
    assert.strictEqual(P.calls.find(c => c.url === '/api/tools/select').body.force, true);
    assert.strictEqual(P.calls.find(c => c.url === '/api/agent').body.text, 'what is a monad');
    P.calls.length = 0;
    await P.ask('another question');
    assert.strictEqual(P.calls.find(c => c.url === '/api/tools/select').body.force, false);
  });

  await t('Settings: off means no selection call at all; web off disables the web tools', async () => {
    const P = await boot({ select: () => SEL, agent: () => [] });
    P.$('#tormode').value = 'off';
    await P.ask('weather in Dhaka');
    assert.ok(!P.calls.some(c => c.url === '/api/tools/select' || c.url === '/api/agent'));
    P.$('#tormode').value = 'auto';
    P.w.eval('webOn=false');
    P.calls.length = 0;
    await P.ask('weather in Paris');
    assert.deepStrictEqual(P.calls.find(c => c.url === '/api/tools/select').body.disabled, ['web_search', 'read_url']);
  });

  await t('an interrupted tool call is shown as stopped, not as running forever', async () => {
    const P = await boot({ select: () => SEL, agent: () => [{ type: 'plan', tools: ['weather'] }, { type: 'tool_start', id: 1, name: 'weather', args: { place: 'X' } }, { type: 'done' }] });
    await P.ask('weather in X');
    const c = P.$('.msg.assistant .toolcard');
    assert.ok(c.querySelector('.td.bad') && /stopped/.test(c.querySelector('.tnote').textContent));
  });

  console.log(`\n${passed} passed`);
})();
