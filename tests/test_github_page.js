// GitHub in the real page (index.html + every js/ file in jsdom): pasted links, /gh, tool-loop hints, the Off setting.
// Run: node tests/test_github_page.js          (needs jsdom: npm i jsdom, or set JSDOM=/path/to/jsdom)
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
        if (url.startsWith('/api/github/status')) return json({ enabled: true, ok: true, why: '', token: false, limit: 60, remaining: 58, reset: 0 });
        if (url === '/api/github/lookup') return json(script.lookup ? script.lookup(body) : { results: [], read: 0 });
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


const REPO = { title: 'acme/widget', url: 'https://github.com/acme/widget', domain: 'github.com', snippet: 'A tiny widget library (★1,234)', text: 'Repository: acme/widget\nStars: 1,234' };
const plain = (extra = {}) => ({ select: () => ({ use: false, tools: [], seeds: [], intents: [], why: '' }), agent: () => [], lookup: () => ({ results: [REPO], read: 1, seconds: 0.1 }), ...extra });
const userText = c => c.body.messages[c.body.messages.length - 1].content;   // addWeb() appends the source block to the last user message

(async () => {
  await t('tools off: a pasted GitHub link is read through the GitHub API, not the page reader', async () => {
    const P = await boot(plain());
    P.$('#tormode').value = 'off';
    await P.ask('What does https://github.com/acme/widget do?');
    const lk = P.calls.find(c => c.url === '/api/github/lookup');
    assert.ok(lk, 'GitHub lookup was called');
    assert.deepStrictEqual(lk.body.targets, ['https://github.com/acme/widget']);
    assert.strictEqual(lk.headers['X-Cressy'], '1');
    assert.ok(!P.calls.some(c => c.url === '/api/web/read'), 'the page reader was not used for a GitHub link');
    const chat = P.calls.find(c => c.url === '/api/chat');
    assert.match(userText(chat), /GitHub REST API data/);
    assert.match(userText(chat), /Stars: 1,234/);
    assert.strictEqual(P.last().web.ghReq.targets[0], 'https://github.com/acme/widget');
    assert.match(P.$('.msg.assistant .web .wq').textContent, /GitHub/);
  });

  await t('tools off: "find a github repo for ..." searches repositories', async () => {
    const P = await boot(plain());
    P.$('#tormode').value = 'off';
    await P.ask('find the best github repos for pdf parsing');
    const lk = P.calls.find(c => c.url === '/api/github/lookup');
    assert.ok(lk && !lk.body.targets && /pdf parsing/.test(lk.body.query), JSON.stringify(lk && lk.body));
    assert.strictEqual(lk.body.n, 5);
    assert.strictEqual(lk.body.read, 1);
  });

  await t('/gh owner/repo forces a lookup for that repository', async () => {
    const P = await boot(plain());
    P.$('#tormode').value = 'off';
    await P.ask('/gh acme/widget');
    const lk = P.calls.find(c => c.url === '/api/github/lookup');
    assert.ok(lk, 'lookup called');
    assert.deepStrictEqual(lk.body.targets, ['acme/widget']);
  });

  await t('tools on: the link goes to the tool loop as a GitHub hint, not as a page to read', async () => {
    const S = plain({ select: () => ({ use: true, tools: ['github', 'web_search'], seeds: [{ name: 'github', arguments: { target: 'https://github.com/acme/widget' } }], intents: ['github'], why: 'intent: github' }),
      agent: () => [{ type: 'plan', tools: ['github'], why: 'g' }, { type: 'tool_start', id: 1, name: 'github', args: { target: 'https://github.com/acme/widget' }, via: 'intent' },
        { type: 'tool_result', id: 1, name: 'github', args: { target: 'https://github.com/acme/widget' }, status: 'ok', notes: [], ms: 300, via: 'intent', preview: 'acme/widget 1,234 stars',
          sources: [{ title: 'acme/widget', url: 'https://github.com/acme/widget', domain: 'github.com', snippet: '', read: true }] },
        { type: 'token', text: 'It is a widget library [1].' }, { type: 'done', completion_tokens: 8, tps: 10 }] });
    const P = await boot(S);
    await P.ask('What does https://github.com/acme/widget do?');
    const sel = P.calls.find(c => c.url === '/api/tools/select');
    assert.ok(sel, 'select called');
    assert.deepStrictEqual(sel.body.hints.gh.targets, ['https://github.com/acme/widget']);
    assert.deepStrictEqual(sel.body.hints.urls, [], 'GitHub links are not also offered to the page reader');
    assert.ok(P.calls.find(c => c.url === '/api/agent'));
    assert.ok(!P.calls.some(c => c.url === '/api/github/lookup'), 'the tool loop does its own lookup server-side');
    assert.match(P.$('.msg.assistant .toolcard .wq').textContent, /Used 1 tool: github/);
    assert.strictEqual(P.last().web.results[0].domain, 'github.com');
    assert.match(P.last().web.query, /acme\/widget/);
  });

  await t('Settings -> GitHub off: no lookup, no hint, and the tool is switched off for the loop', async () => {
    const P = await boot(plain());
    P.$('#ghmode').value = 'off';
    await P.ask('What does https://github.com/acme/widget do?');
    assert.ok(!P.calls.some(c => c.url === '/api/github/lookup'));
    const sel = P.calls.find(c => c.url === '/api/tools/select');
    if (sel) { assert.ok(!sel.body.hints.gh); assert.ok(sel.body.disabled.includes('github')); }
  });

  await t('a question that is not about GitHub never touches it', async () => {
    const P = await boot(plain());
    await P.ask('write a haiku about rain');
    assert.ok(!P.calls.some(c => c.url === '/api/github/lookup'));
    const sel = P.calls.find(c => c.url === '/api/tools/select');
    if (sel) assert.ok(!sel.body.hints.gh);
  });

  await t('the GitHub settings, /gh help entry and footer note exist', async () => {
    const P = await boot(plain());
    for (const id of ['#ghmode', '#ghn', '#ghread', '#ghtest', '#ghnote', '#ghst']) assert.ok(P.$(id), id + ' is missing');
    assert.ok(P.w.eval("typeof CMDS.gh==='object'&&typeof CMDS.github==='object'"));
    assert.match(P.$('.fine').textContent, /GitHub/);
    P.$('#ghtest').click(); await sleep(60);
    assert.match(P.$('#ghnote').textContent, /58 of 60/);
  });

  console.log(`\n${passed} passed`);
  process.exit(process.exitCode || 0);
})();
