// Tests for js/24-agent.js: the pure reducer, and the whole script against a fake page and a scripted server stream.
// Run: node tests/test_agent_js.js
const fs = require('fs'), vm = require('vm'), path = require('path'), assert = require('assert');
const src = fs.readFileSync(path.join(__dirname, '..', 'js', '24-agent.js'), 'utf8').replace(/\r\n/g, '\n');
let passed = 0;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const J = x => JSON.parse(JSON.stringify(x));
const t = async (name, fn) => { try { await fn(); passed++; console.log('ok   ', name) } catch (e) { console.log('FAIL ', name, '\n', e.stack); process.exitCode = 1 } };

/* ---------- pure part ---------- */
const block = src.slice(src.indexOf('/*==PURE==*/'), src.indexOf('/*==END PURE==*/'));
const names = [...block.matchAll(/^(?:const|function)\s+(\w+)/gm)].map(m => m[1]);
const pctx = vm.createContext({ JSON });
vm.runInContext(block + '\nglobalThis.__x={' + names.join(',') + '}', pctx);
const X = pctx.__x;
const EV = {
  start: { type: 'start', id: 'r1', goal: 'g', max_steps: 10, max_attempts: 3, tests: ['pytest'], can_run: true },
  step: n => ({ type: 'step', n, max: 10, attempt: 1, attempts: 3 }),
};

(async () => {
  await t('reducer builds steps, actions, output, results and the final report', () => {
    const st = X.agentNew('fix add');
    [EV.start, EV.step(1), { type: 'token', text: 'Let me ' }, { type: 'token', text: 'look' }].forEach(e => X.agentReduce(st, e));
    assert.strictEqual(st.steps[0].live, 'Let me look');
    X.agentReduce(st, { type: 'assistant', n: 1, text: 'Looking at calc.py' });
    assert.strictEqual(st.steps[0].live, '');
    [{ type: 'action', n: 1, i: 0, kind: 'run', path: null, cmd: 'pytest -q' }, { type: 'cmd_out', text: 'F.\n' }, { type: 'cmd_out', text: '1 failed' },
      { type: 'result', n: 1, i: 0, ok: false, text: 'exit code 1' },
      { type: 'diagnosis', kind: 'python', headline: 'AssertionError: 1 != 2', locations: [{ path: 'a.py', line: 3 }], failing: [] },
      { type: 'syntax', path: 'a.py', ok: false, message: 'a.py: line 3: SyntaxError: bad\n  x(' },
      { type: 'file_change', path: 'a.py', created: false }, { type: 'plan', items: [{ text: 'a', done: true }, { text: 'b', done: false }] }
    ].forEach(e => X.agentReduce(st, e));
    const s = st.steps[0], a = s.actions[0];
    assert.strictEqual(a.out, 'F.\n1 failed');
    assert.strictEqual(a.ok, false);
    assert.strictEqual(s.diag.headline, 'AssertionError: 1 != 2');
    assert.ok(/^Syntax error: a\.py: line 3/.test(s.notes[0]));
    assert.strictEqual(X.agentStepState(s), 'bad');
    assert.deepStrictEqual(J(Object.keys(st.files)), ['a.py']);
    X.agentReduce(st, { type: 'done', id: 'r1', status: 'success', summary: 'ok', markdown: '**Done.** ok', seconds: 3.2, can_revert: true,
      files: [{ path: 'a.py', created: false, added: 1, removed: 1 }], verification: { kind: 'tests', ok: true, caveats: [] } });
    assert.strictEqual(st.status, 'success');
    assert.strictEqual(X.agentTitle(st), 'Finished');
    assert.ok(st.canRevert && st.finalFiles.length === 1 && st.vkind === 'tests');
  });

  await t('a plan tick is never lost, and a successful finish ticks every step', () => {
    const st = X.agentNew('g');
    X.agentReduce(st, { type: 'plan', items: [{ text: 'a', done: true }, { text: 'b', done: false }] });
    X.agentReduce(st, { type: 'plan', items: [{ text: 'a', done: false }, { text: 'b', done: false }] });   // the model forgot the [x]
    assert.deepStrictEqual(J(st.plan.map(p => p.done)), [true, false]);
    X.agentReduce(st, { type: 'done', id: 'r', status: 'success', summary: 'ok', files: [], verification: {} });
    assert.deepStrictEqual(J(st.plan.map(p => p.done)), [true, true]);
  });

  await t('resume: only unfinished runs can be continued, and the saved state carries plan, files and last steps', () => {
    const st = X.agentNew('build a page');
    [EV.start, EV.step(1), { type: 'assistant', n: 1, text: 'Writing the page' }, { type: 'action', n: 1, i: 0, kind: 'file', path: 'index.html' },
      { type: 'result', n: 1, i: 0, ok: true, text: 'wrote' }, { type: 'file_change', path: 'index.html', created: true },
      { type: 'plan', items: [{ text: 'write index.html', done: true }, { text: 'write style.css', done: false }] }].forEach(e => X.agentReduce(st, e));
    assert.ok(X.agentResumable(st), 'a run that is still going when the page closed can be continued');
    X.agentRevive(st);
    assert.strictEqual(st.status, 'stopped');
    const r = X.agentResumeState(st);
    assert.strictEqual(r.status, 'stopped');
    assert.deepStrictEqual(J(r.plan), [{ text: 'write index.html', done: true }, { text: 'write style.css', done: false }]);
    assert.deepStrictEqual(J(r.files), ['index.html']);
    assert.ok(/^step 1: Writing the page \[Write index\.html\]$/.test(r.steps[0]), r.steps[0]);
    X.agentReduce(st, { type: 'plan', items: [{ text: 'write index.html', done: true }, { text: 'write style.css', done: true }] });
    X.agentReduce(st, { type: 'done', id: 'r', status: 'success', summary: 'ok', files: [], verification: {} });
    assert.ok(!X.agentResumable(st), 'a finished run with every step ticked is not resumable');
    const open = X.agentNew('g'); open.status = 'budget'; assert.ok(X.agentResumable(open));
    open.reverted = true; assert.ok(!X.agentResumable(open));
  });

  await t('"continue" wording that resumes a run', () => {
    ['continue', 'Continue.', '/continue', 'go on', 'keep going', 'carry on', 'resume', 'continue please', 'continue from where you left off'].forEach(s => assert.ok(X.AG_CONTINUE.test(s), s));
    ['continue the login page with a dark theme', 'do not continue', 'continued', ''].forEach(s => assert.ok(!X.AG_CONTINUE.test(s), s));
  });

  await t('reducer: baseline output, verification output, rollback, retry, approval', () => {
    const st = X.agentNew('g');
    X.agentReduce(st, { type: 'verify', phase: 'start', baseline: true, commands: ['t'] });
    X.agentReduce(st, { type: 'cmd_out', text: 'baseline out' });
    X.agentReduce(st, { type: 'verify', phase: 'done', baseline: true, ok: false, summary: '1 failed', failing: ['test_x'] });
    assert.strictEqual(st.bout, 'baseline out');
    assert.deepStrictEqual(J(st.baseline), { ok: false, summary: '1 failed', failing: ['test_x'] });
    X.agentReduce(st, EV.step(1));
    X.agentReduce(st, { type: 'verify', phase: 'start', commands: ['t'] });
    X.agentReduce(st, { type: 'cmd_out', text: 'suite out' });
    assert.strictEqual(st.steps[0].vout, 'suite out');
    X.agentReduce(st, { type: 'verify', phase: 'done', ok: false, summary: 'unittest: 1 failed', checks: [] });
    assert.strictEqual(st.steps[0].ver.ok, false);
    X.agentReduce(st, { type: 'rollback', attempt: 1, files: ['calc.py'], reason: 'same failure' });
    X.agentReduce(st, { type: 'retry', attempt: 2, max: 3 });
    assert.strictEqual(st.attempt, 2);
    assert.ok(/attempt 2 of 3/.test(X.agentTitle(st)));
    assert.ok(st.steps[0].notes.some(n => /Rolled back calc.py/.test(n)));
    X.agentReduce(st, { type: 'approval', id: 'ab', cmd: 'pip install x', reason: 'installs packages' });
    assert.strictEqual(st.approval.id, 'ab');
    X.agentReduce(st, { type: 'approval_done', id: 'ab', ok: true });
    assert.strictEqual(st.approval, null);
  });

  await t('output is capped and the saved copy is slim; an interrupted run is revived as stopped', () => {
    const st = X.agentNew('g');
    X.agentReduce(st, EV.step(1));
    X.agentReduce(st, { type: 'action', n: 1, i: 0, kind: 'run', cmd: 'x' });
    for (let i = 0; i < 100; i++) X.agentReduce(st, { type: 'cmd_out', text: 'y'.repeat(200) });
    assert.ok(st.steps[0].actions[0].out.length <= 4000);
    st.steps[0].live = 'writing';
    const slim = X.agentSlim(st);
    assert.ok(slim.steps[0].actions[0].out.length <= 1200 && slim.steps[0].live === '');
    assert.strictEqual(X.agentRevive(slim).status, 'stopped');
  });

  /* ---------- the whole script against a fake page ---------- */
  /* a small but faithful DOM: text nodes, parents, attributes and replaceWith, which the card patcher (agSync) relies on */
  const swap = (old, n) => { const p = old.parent; if (!p) return; const i = p.children.indexOf(old); if (n) { n.parent = p; p.children.splice(i, 1, n) } else p.children.splice(i, 1); old.parent = null };
  class TextNode {
    constructor(d) { this.nodeType = 3; this.data = String(d); this.children = []; this.tag = '#text'; this.className = ''; this.parent = null }
    get textContent() { return this.data }
    replaceWith(n) { swap(this, n) }
    remove() { swap(this, null) }
  }
  class El {
    constructor(tag) { this.tag = tag; this.nodeType = 1; this.children = []; this.parent = null; this.attrs = {}; this.dataset = {}; this.hidden = false; this.value = ''; this.placeholder = '';
      this.onclick = null; this.disabled = false; this.classes = new Set(); this.scrollTop = 0; this.scrollHeight = 0; this.clientHeight = 0;
      this.classList = { toggle: (c, v) => (v ? this.classes.add(c) : this.classes.delete(c)), add: c => this.classes.add(c), remove: c => this.classes.delete(c),
        contains: c => this.className.split(/\s+/).includes(c) || this.classes.has(c) }; }
    get tagName() { return this.tag.toUpperCase() }
    get className() { return this.attrs.class || '' }
    set className(v) { this.attrs.class = String(v) }
    get childNodes() { return this.children }
    get attributes() { return Object.entries(this.attrs).map(([name, value]) => ({ name, value: String(value) })) }
    hasAttribute(k) { return k in this.attrs }
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null }
    removeAttribute(k) { delete this.attrs[k] }
    setAttribute(k, v) { this.attrs[k] = String(v) }
    get textContent() { return this.children.map(c => c.textContent).join('') }
    set textContent(v) { this.children.forEach(c => { c.parent = null }); this.children = []; if (v !== '' && v != null) this.append(new TextNode(v)) }
    append(...k) { for (const c of k) { const n = typeof c === 'string' ? new TextNode(c) : c; n.parent = this; this.children.push(n) } }
    replaceChildren(...k) { this.children = []; this.append(...k) }
    replaceWith(n) { swap(this, n) }
    remove() { swap(this, null) }
    querySelector() { return null }
    querySelectorAll() { return [] }
    focus() {}
  }
  const text = e => (e.textContent || '') + e.children.map(text).join(' ');
  const find = (e, pred, out = []) => { if (pred(e)) out.push(e); e.children.forEach(c => find(c, pred, out)); return out };

  function makeEnv(stream) {
    const reg = {}, calls = [], toasts = [], WSset = new Map();
    const defaults = { '#agsteps': '25', '#agtries': '3', '#agtests': '1', '#agbase': '1', '#agapprove': 'ask', '#agcmd': '', '#runtimeout': '60', '#sys': 'be brief', '#mem': '' };
    const $ = sel => { if (!reg[sel]) { reg[sel] = new El('x'); if (defaults[sel] !== undefined) reg[sel].value = defaults[sel] } return reg[sel] };
    const enc = new TextEncoder();
    const sse = events => { let i = 0; return { getReader: () => ({ read: async () => { if (i >= events.length) return { done: true }; await sleep(2); return { done: false, value: enc.encode('data: ' + JSON.stringify(events[i++]) + '\n\n') } } }) } };
    const state = { stream, stopAfter: null };
    const fetch = async (url, o = {}) => {
      const body = o.body ? JSON.parse(o.body) : null; calls.push({ url, body, headers: o.headers });
      if (url === '/api/caps') return { ok: true, json: async () => ({ agent: true, write: true }) };
      if (url === '/api/agentrun') {
        const evs = typeof state.stream === 'function' ? state.stream(body) : state.stream;
        return { ok: true, status: 200, body: sse(evs) };
      }
      if (url === '/api/agentrun/revert') return { ok: true, status: 200, json: async () => ({ ok: true, files: ['calc.py'] }) };
      return { ok: true, status: 200, json: async () => ({ ok: true }) };
    };
    const ctx = vm.createContext({
      console, setTimeout, clearTimeout, Date, JSON, Promise, TextEncoder, TextDecoder, AbortController, Math, Array, Object, String, Number, Error, RegExp, Set, Map,
      $, fetch, HDR: { 'Content-Type': 'application/json', 'X-Cressy': '1' }, CAPS: { write: true, root: '/proj' }, msgs: [], att: [], SENT: [], si: 0, cwd: '', FILES: 1, WS: WSset,
      stick: false, pop: new El('pop'), DEFPH: 'Ask', planMode: false, SUGG: [], HELP: 'help', busy: false, ctl: null,
      LS: () => true, toast: (m, k) => toasts.push([m, k]), modal: () => { throw new Error('consent should be skipped') }, showTab: n => { ctx.tab = n },
      confirm: () => true, note: m => { ctx.lastNote = m }, grow() {}, save() { ctx.saves = (ctx.saves || 0) + 1 }, setTitle() {}, renderTray() {}, toBottom() {}, refreshModels() {},
      setBusy(b) { ctx.busy = b; ctx.busyLog.push(b) }, busyLog: [], ls() {}, paintChanges() {}, paint(m) { m.painted = true }, todayLine: () => 'Today is a day',
      params: () => ({ temperature: 0.7, top_p: 0.95, top_k: 40, repeat_penalty: 1.1, max_tokens: 4096 }), withFiles: (c, f) => c + '\n[files:' + f.map(x => x.name) + ']',
      addMsg(m) { m.el = new El('msg'); const box = new El('div'); box.className = 'agent'; m.el.agentBox = box; m.el.querySelector = s => (s === '.agent' ? box : null); ctx.msgs.push(m) },
      recordWS: (p, b, a, saved, crlf) => { WSset.set(p, { b, a }); ctx.ws = WSset },
      memoryFor: async () => '', memoryAfter() { ctx.memAfter = (ctx.memAfter || 0) + 1 }, document: { createElement: tag => new El(tag) },
      location: {},
    });
    ctx.msgs = []; ctx.addMsg = m => { m.el = new El('msg'); const box = new El('div'); box.className = 'agent'; box.hidden = true; m.el.querySelector = s => (s === '.agent' ? box : null); m.box = box; ctx.msgs.push(m); if (m.role === 'assistant' && ctx.__a) ctx.__a.paintAgent(m) };   // like the real addMsg: it paints the card at once
    // things 08-terminal-plan.js defines that 24-agent.js hooks into
    vm.runInContext('var setPlan=function(v){planMode=v};var jpost=(u,b,o={})=>fetch(u,{method:"POST",headers:HDR,body:JSON.stringify(b),...o});', ctx);
    ctx.planMode = false;
    vm.runInContext(src + '\n;globalThis.__a={agentRoute,sendAgent,paintAgent,agentRevert,agentAnswer,get mode(){return agentMode},setAgent};', ctx);
    return { ctx, A: ctx.__a, calls, reg, toasts, state };
  }

  const done = (extra = {}) => ({ type: 'done', id: 'r1', status: 'success', summary: 'fixed', markdown: '**Done.** fixed\n\nChanged files:\n- `calc.py`', seconds: 1.5, can_revert: true,
    files: [{ path: 'calc.py', created: false, deleted: false, added: 1, removed: 1 }], verification: { kind: 'tests', ok: true, summary: 'unittest: 2 passed', caveats: [] }, ...extra });
  const happy = [
    { type: 'start', id: 'r1', goal: 'g', max_steps: 25, max_attempts: 3, tests: ['python -m unittest'], can_run: true },
    { type: 'verify', phase: 'start', baseline: true, commands: ['t'] }, { type: 'cmd_out', text: 'boom' },
    { type: 'verify', phase: 'done', baseline: true, ok: false, summary: '1 failed', failing: ['test_add'] },
    { type: 'step', n: 1, max: 25, attempt: 1, attempts: 3 }, { type: 'token', text: 'I will read' }, { type: 'assistant', n: 1, text: 'Reading calc.py' },
    { type: 'action', n: 1, i: 0, kind: 'read', path: 'calc.py' }, { type: 'result', n: 1, i: 0, ok: true, text: 'calc.py: 2 lines' },
    { type: 'step', n: 2, max: 25, attempt: 1, attempts: 3 }, { type: 'assistant', n: 2, text: 'Fix the operator' },
    { type: 'action', n: 2, i: 0, kind: 'edit', path: 'calc.py' }, { type: 'file_change', path: 'calc.py', before: 'a - b', after: 'a + b', created: false, crlf: false },
    { type: 'result', n: 2, i: 0, ok: true, text: 'Edited calc.py' },
    { type: 'verify', phase: 'start', final: true, commands: ['t'] }, { type: 'verify', phase: 'done', final: true, ok: true, summary: 'unittest: 2 passed', checks: [] },
    done(),
  ];

  await t('sending a task runs the whole flow: request body, live card, Changes tab, final report, save', async () => {
    const { ctx, A, calls } = makeEnv(happy);
    await A.sendAgent('fix add', []);
    await sleep(200);
    const call = calls.find(c => c.url === '/api/agentrun');
    assert.strictEqual(call.body.goal, 'fix add');
    assert.strictEqual(call.body.max_steps, 25);
    assert.strictEqual(call.body.approval, 'ask');
    assert.strictEqual(call.body.auto_tests, true);
    assert.ok(call.body.system.includes('be brief') && call.body.system.includes('Today is'));
    assert.strictEqual(call.headers['X-Cressy'], '1');
    const [u, m] = ctx.msgs;
    assert.strictEqual(u.role, 'user');
    assert.strictEqual(m.agent.status, 'success');
    assert.ok(m.content.startsWith('**Done.**'));
    assert.deepStrictEqual(J([...ctx.ws.keys()]), ['calc.py']);
    assert.deepStrictEqual(J(ctx.busyLog), [true, false]);
    assert.ok(ctx.saves >= 1 && ctx.memAfter === 1);
    const shown = text(m.box);
    assert.ok(shown.includes('Finished') && shown.includes('calc.py') && shown.includes('Review changes') && shown.includes('Revert all changes'), shown);
    assert.ok(shown.includes('Tests failed before the task started'), shown);
  });

  await t('/agent and agent mode route messages; other slash commands and plain chat do not', async () => {
    const { ctx, A, calls } = makeEnv(happy);
    assert.strictEqual(await A.agentRoute('hello there', false, {}), false);
    assert.strictEqual(await A.agentRoute('/help', false, {}), false);
    assert.strictEqual(await A.agentRoute('/agent', false, {}), true);
    assert.ok(/Describe the task/.test(ctx.lastNote));
    assert.strictEqual(calls.filter(c => c.url === '/api/agentrun').length, 0);
    assert.strictEqual(await A.agentRoute('/agent fix the bug in calc.py', false, {}), true);
    assert.strictEqual(calls.find(c => c.url === '/api/agentrun').body.goal, 'fix the bug in calc.py');
    A.setAgent(true);
    assert.strictEqual(A.mode, true);
    assert.strictEqual(await A.agentRoute('/compact', false, {}), false, 'slash commands still work in agent mode');
    assert.strictEqual(await A.agentRoute('add a test', false, { noAgent: true }), false);
    assert.strictEqual(await A.agentRoute('add a test', false, {}), true);
  });

  await t('approval requests are shown inline and the answer is posted', async () => {
    const evs = [{ type: 'start', id: 'r2', max_steps: 5, max_attempts: 1, tests: [], can_run: true }, { type: 'step', n: 1, max: 5, attempt: 1, attempts: 1 },
      { type: 'action', n: 1, i: 0, kind: 'run', cmd: 'pip install x' }, { type: 'approval', id: 'ap1', cmd: 'pip install x', reason: 'installs packages' }];
    const { ctx, A, calls, toasts } = makeEnv(evs);
    await A.sendAgent('install x', []);
    await sleep(200);
    const m = ctx.msgs[1];
    // the stream ended without "done": the card must not stay "running" forever
    assert.strictEqual(m.agent.status, 'error');
    assert.ok(toasts.some(x => /approval/.test(x[0])));
    m.agent.approval = { id: 'ap1', cmd: 'pip install x', reason: 'installs packages' };
    m.agent.status = 'running'; ctx.__a.paintAgent;   // repaint a live card with a pending approval
    await A.agentAnswer(m, true);
    const post = calls.find(c => c.url === '/api/agentrun/approve');
    assert.deepStrictEqual(J(post.body), { id: 'ap1', ok: true });
    assert.strictEqual(m.agent.approval, null);
  });

  await t('the live card says Working (never Stopped) while the run is going, and shows who plans and who codes', async () => {
    const evs = [{ type: 'start', id: 'r9', goal: 'g', root: '/proj', max_steps: 5, max_attempts: 3, tests: [], can_run: true, planner: 'Qwen3-8B.gguf', coder: 'coder.gguf' },
      { type: 'planning', phase: 'start', by: 'Qwen3-8B.gguf' },
      { type: 'planning', phase: 'done', ok: true, by: 'Qwen3-8B.gguf', text: 'Approach: x\nSteps:\n- [ ] a' },
      { type: 'plan', items: [{ text: 'a', done: false }] },
      { type: 'step', n: 1, max: 5, attempt: 1, attempts: 3 }, ...Array.from({ length: 40 }, () => ({ type: 'token', text: '.' })),
      { type: 'done', id: 'r9', status: 'success', summary: 'ok', markdown: '**Done.** ok', seconds: 1, can_revert: false, files: [], verification: { kind: 'syntax', caveats: [] } }];
    const { ctx, A } = makeEnv(evs);
    const p = A.sendAgent('make a calculator', []);
    await sleep(60);
    const m = ctx.msgs[1];
    assert.strictEqual(m.agent.status, 'running');
    A.paintAgent(m);
    assert.strictEqual(m.agent.status, 'running', 'repainting must not revive a live card as stopped');
    const shown = text(m.box);
    assert.ok(/Working/.test(shown) && /Plans with Qwen3-8B\.gguf/.test(shown) && /Codes with coder\.gguf/.test(shown), shown);
    await p; await sleep(150);
    assert.strictEqual(m.agent.status, 'success');
  });

  await t('repainting patches the card in place: the same nodes stay (spinner, open step), only their text changes', async () => {
    const evs = [{ type: 'start', id: 'p1', goal: 'g', root: '/proj', max_steps: 5, max_attempts: 3, tests: [], can_run: true },
      { type: 'step', n: 1, max: 5, attempt: 1, attempts: 3 }, { type: 'action', n: 1, i: 0, kind: 'run', cmd: 'echo hi' }];
    const { ctx, A } = makeEnv(evs);
    const p = A.sendAgent('make a calculator', []);
    await sleep(80);
    const m = ctx.msgs[1], box = m.box;
    m.agent.open['a1.0'] = true;   // show the command's output
    A.paintAgent(m);
    const head1 = box.children[0], dot1 = head1.children[0], title1 = head1.children[1];
    const list1 = box.children.find(c => c.className === 'agfeed');
    m.agent.steps[0].actions[0].out = 'line 1';
    A.paintAgent(m);
    const head2 = box.children[0];
    assert.strictEqual(head2, head1, 'the header node must be reused, not rebuilt');
    assert.strictEqual(head2.children[0], dot1, 'the spinner node must be reused so its animation does not restart');
    assert.strictEqual(box.children.find(c => c.className === 'agfeed'), list1, 'the feed node must be reused');
    const before = m.agent.step;
    m.agent.steps[0].actions[0].out = 'line 1\nline 2';
    A.paintAgent(m);
    assert.ok(text(box).includes('line 2'), 'new text still shows up');
    assert.strictEqual(m.agent.step, before);
    await p;
  });

  await t('the feed is a flat activity log: no step numbers, repeats collapse into one row with a count', () => {
    const st = X.agentNew('word counter');
    X.agentReduce(st, EV.start);
    for (let n = 1; n <= 6; n++) {
      [EV.step(n), { type: 'assistant', n, text: 'I apologize for the repetition. Let us try again with something else entirely.' },
        { type: 'action', n, i: 0, kind: 'run', cmd: 'python wc.py' }, { type: 'result', n, i: 0, ok: false, text: '$ python wc.py' }].forEach(e => X.agentReduce(st, e));
    }
    X.agentReduce(st, EV.step(7));
    X.agentReduce(st, { type: 'action', n: 7, i: 0, kind: 'file', path: 'wc.py' });
    X.agentReduce(st, { type: 'result', n: 7, i: 0, ok: true, text: 'Created wc.py (3 lines).' });
    const f = X.agentFeed(st);
    assert.deepStrictEqual(J(f.map(x => x.kind)), ['prose', 'act', 'act']);
    assert.strictEqual(f[0].count, 1);
    assert.strictEqual(f[1].count, 6);
    assert.strictEqual(f[0].text, 'I apologize for the repetition.');
    assert.strictEqual(X.agentRowMeta(f[2].a), '3 lines');
    assert.strictEqual(X.agentRowMeta(f[1].a), 'failed');
    assert.ok(!/step \d|of 25/.test(X.agentTitle(st)), X.agentTitle(st));
  });

  await t('a finished successful run folds its activity away; a failed one stays open', async () => {
    const mk = status => { const evs = [{ type: 'start', id: 'f1', goal: 'g', max_steps: 5, max_attempts: 1, tests: [], can_run: true }, { type: 'step', n: 1, max: 5, attempt: 1, attempts: 1 },
      { type: 'action', n: 1, i: 0, kind: 'file', path: 'wc.py' }, { type: 'result', n: 1, i: 0, ok: true, text: 'Created wc.py (3 lines).' },
      { type: 'done', id: 'f1', status, summary: 's', markdown: '**x** s', seconds: 1, can_revert: false, files: [], verification: { kind: 'syntax', caveats: [] } }]; return makeEnv(evs) };
    let e = mk('success'); await e.A.sendAgent('g', []); await sleep(120);
    let shown = text(e.ctx.msgs[1].box);
    assert.ok(/Show 1 action/.test(shown) && !/Wrote/.test(shown), shown);
    e = mk('failed'); await e.A.sendAgent('g', []); await sleep(120);
    shown = text(e.ctx.msgs[1].box);
    assert.ok(/Hide 1 action/.test(shown) && /Wrote/.test(shown), shown);
  });

  await t('reducer: planning shows in the title while it runs and keeps the plan text', () => {
    const st = X.agentNew('g');
    [EV.start, { type: 'planning', phase: 'start', by: 'Qwen3-8B.gguf' }].forEach(e => X.agentReduce(st, e));
    assert.strictEqual(X.agentTitle(st), 'Planning · Qwen3-8B.gguf');
    X.agentReduce(st, { type: 'planning', phase: 'done', ok: true, by: 'Qwen3-8B.gguf', text: 'Approach: x' });
    assert.strictEqual(st.planning.text, 'Approach: x');
    X.agentReduce(st, EV.step(1));
    assert.ok(/^Working/.test(X.agentTitle(st)));
  });

  await t('Stop asks the server to cancel and the stream still ends with a stopped result', async () => {
    const evs = [{ type: 'start', id: 'r3', max_steps: 5, max_attempts: 1, tests: [], can_run: true }, { type: 'step', n: 1, max: 5, attempt: 1, attempts: 1 },
      { type: 'done', id: 'r3', status: 'stopped', summary: 'Stopped by you.', markdown: '**Stopped.** Stopped by you.', seconds: 1, can_revert: false, files: [], verification: { kind: 'none', caveats: [] } }];
    const { ctx, A, calls } = makeEnv(evs);
    const p = A.sendAgent('long task', []);
    await sleep(10); ctx.ctl.abort();
    await p; await sleep(150);
    assert.ok(calls.some(c => c.url === '/api/agentrun/cancel' && c.body.id === 'r3'));
    assert.strictEqual(ctx.msgs[1].agent.status, 'stopped');
  });

  await t('server refusal (409 busy) shows an error card and frees the composer', async () => {
    const { ctx, A } = makeEnv([]);
    ctx.fetch = async (u, o) => (u === '/api/agentrun' ? { ok: false, status: 409, json: async () => ({ detail: 'An agent run is already in progress.' }) } : { ok: true, json: async () => ({}) });
    await A.sendAgent('x', []);
    const m = ctx.msgs[1];
    assert.strictEqual(m.agent.status, 'error');
    assert.ok(m.content.includes('already in progress'));
    assert.strictEqual(ctx.busy, false);
  });

  await t('Revert all calls the server, clears the Changes list and marks the card', async () => {
    const { ctx, A, calls } = makeEnv(happy);
    await A.sendAgent('fix add', []);
    await sleep(200);
    const m = ctx.msgs[1];
    assert.ok(ctx.ws.has('calc.py'));
    await A.agentRevert(m, null);
    assert.deepStrictEqual(J(calls.find(c => c.url === '/api/agentrun/revert').body), { id: 'r1' });
    assert.strictEqual(m.agent.reverted, true);
    assert.ok(!ctx.ws.has('calc.py'));
    assert.ok(!text(m.box).includes('Revert all changes'));
  });

  console.log('\n' + passed + ' passed');
})();
