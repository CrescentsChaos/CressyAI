// Runs the whole js/20-memory.js (not just the pure part) against stubbed DOM / fetch / model. Run: node tests/test_memory_ui.js
const fs = require('fs'), vm = require('vm'), path = require('path'), assert = require('assert');
const src = fs.readFileSync(process.env.MEM_JS || path.join(__dirname, '..', 'js', '20-memory.js'), 'utf8');
let passed = 0;
const sleep = ms => new Promise(r => setTimeout(r, ms));

function makeEnv() {
  const calls = [], reg = {}, notes = [], locals = [];
  const el = () => ({ value: '', innerHTML: '', textContent: '', hidden: false, dataset: {}, handlers: {}, open: false, children: [],
    addEventListener(e, f) { this.handlers[e] = f }, querySelector() { return null }, closest() { return null }, append(x) { this.children.push(x) },
    remove() {}, click() {}, focus() {}, classList: { toggle() {}, add() {}, remove() {} } });
  const $ = sel => (reg[sel] = reg[sel] || el());
  reg['#memauto'] = Object.assign(el(), { value: '1' }); reg['#memlearn'] = Object.assign(el(), { value: '1' }); reg['#memscope'] = Object.assign(el(), { value: 'project' });
  const state = { chatReply: null, hang: false, aborted: 0, absorbed: [], recall: { profile: 'proj (Python)', pinned: [{ id: 'f1', text: 'My name is Rowan.' }], matches: [], tasks: [], sessions: [], last_session: null } };
  const snap = () => ({ project_name: 'proj', profile: 'proj (Python)', facts: [], tasks: [{ id: 't1', text: 'Finish the login copy', status: 'open', progress: 'tests missing' }],
    sessions: [{ chat: 'c0', title: 'Login work', handoff: 'Rewrote the error text.', next: ['Add a test'], updated: Date.now() - 3600e3 }], semantic: { on: true, model: 'fake', indexed: 1, total: 1, why: '' } });
  const sse = chunks => { const enc = new TextEncoder(); let i = 0; return { read: async () => i < chunks.length ? { done: false, value: enc.encode(chunks[i++]) } : { done: true } } };
  const fetch = async (url, o = {}) => {
    const body = o.body ? JSON.parse(o.body) : null; calls.push({ url, method: o.method || 'GET', body });
    const json = (d, ok = true) => ({ ok, status: ok ? 200 : 400, json: async () => d });
    if (url === '/api/chat') {
      if (state.hang) await new Promise((_, rej) => { o.signal.addEventListener('abort', () => { state.aborted++; const e = new Error('aborted'); e.name = 'AbortError'; rej(e) }) });
      const sys = body.messages[0].content, reply = typeof state.chatReply === 'function' ? state.chatReply(sys, body) : state.chatReply;
      return { ok: true, status: 200, body: { getReader: () => sse(['data: ' + JSON.stringify({ type: 'token', text: reply }) + '\n\n', 'data: {"type":"done"}\n\n']) } };
    }
    if (url === '/api/memory') return json(snap());
    if (url === '/api/memory/recall') return json(state.recall);
    if (url === '/api/memory/session') return json({ ok: true });
    if (url === '/api/memory/absorb') { state.absorbed.push(body); return json({ ...snap(), added_facts: (body.facts || []).length, added_tasks: (body.tasks || []).length, replaced: 0, closed: (body.done || []).length }) }
    if (url === '/api/memory/fact') return json({ ...snap(), fact: { text: body.text } });
    return json({});
  };
  const ctx = vm.createContext({ console, setTimeout, clearTimeout, Date, JSON, Promise, TextEncoder, TextDecoder, AbortController, Math, Array, Object, String, Number, Error, RegExp,
    $, esc: s => String(s), HDR: {}, fetch, info: { n_ctx: 4000, loaded: true }, msgs: [], cid: 'c1', busy: false, ctl: new AbortController(), CH: [], CMDS: {}, HELP: 'help',
    grow() {}, note: (t, g) => notes.push(t), localMsg: m => locals.push(m), save() { ctx.saves = (ctx.saves || 0) + 1 }, titleOf: () => 'My chat', putChat() {}, dl_() {}, load() {},
    withFiles: (c, f) => c, asst: m => m.content, split: s => ({ ans: s }), confirm: () => true, prompt: () => null, lastCtxRatio: 0,
    document: { createElement: () => el() }, addEventListener() {} });
  vm.runInContext(src + `\n;globalThis.__m={get sum(){return memSum},get upto(){return memUpto},get bg(){return memBg},get mems(){return MEMS},memoryFor,memoryTrim,memoryAfter,memoryLeave,memoryCommand,memChatFields,memChatLoad,memoryPaintEmpty,memRefresh,memSig,memReset};`, ctx);
  return { ctx, m: ctx.__m, calls, reg, notes, locals, state, $ };
}
const msg = (role, n, tag) => ({ role, content: (tag || role) + ' ' + 'word '.repeat(n) });
const longChat = (pairs, words = 350) => { const a = []; for (let i = 0; i < pairs; i++) a.push(msg('user', words, 'q' + i), msg('assistant', words, 'a' + i)); return a };
const t = async (name, fn) => { try { await fn(); passed++; console.log('ok   ', name) } catch (e) { console.log('FAIL ', name, '\n', e.stack.split('\n').slice(0, 6).join('\n')); process.exitCode = 1 } };
const roles = h => JSON.parse(JSON.stringify(h.map(x => x.role)));

(async () => {
  await t('loads, fetches memory and paints the panel without errors', async () => {
    const e = makeEnv(); await sleep(20);
    assert.ok(e.m.mems && e.m.mems.project_name === 'proj');
    assert.ok(e.reg['#memtasks'].innerHTML.includes('Finish the login copy') && e.reg['#memtasks'].innerHTML.includes('tests missing'));
    assert.ok(e.reg['#memsessions'].innerHTML.includes('Login work') && e.reg['#memstat'].innerHTML.includes('Semantic search: on'));
    assert.ok(e.ctx.CMDS.resume && e.ctx.CMDS.forget && e.ctx.CMDS.summary && /\/resume/.test(e.ctx.HELP));
  });

  await t('memoryFor returns the formatted context, and an outage just means no context', async () => {
    const e = makeEnv(); await sleep(10); e.ctx.msgs = [msg('user', 5), msg('assistant', 0)];
    const s = await e.m.memoryFor('hello');
    assert.ok(s.includes('Memory from earlier work') && s.includes('My name is Rowan') && s.includes('Project: proj'), s);
    const rc = e.calls.find(c => c.url === '/api/memory/recall').body; assert.strictEqual(rc.fresh, true); assert.strictEqual(rc.chat, 'c1');
    e.ctx.fetch = async () => { throw new Error('server down') };
    assert.strictEqual(await e.m.memoryFor('hello'), '');
  });

  await t('a long chat is summarized before the send, and the history sent is short and well-formed', async () => {
    const e = makeEnv(); await sleep(10);
    e.state.chatReply = sys => /running summary/.test(sys) ? '- Goal: build it\n- Decisions: use PostgreSQL\n- State: app.py exists\n- Open items: tests' : '{}';
    const chat = longChat(8); e.ctx.msgs = [...chat, msg('user', 40, 'newest'), { role: 'assistant', content: '', live: true }];
    const before = e.ctx.msgs.length;
    await e.m.memoryFor('newest question');
    assert.ok(e.m.sum.text.includes('PostgreSQL') && e.m.sum.upto > 0 && e.m.sum.upto < before - 1, JSON.stringify(e.m.sum).slice(0, 120));
    assert.strictEqual(e.ctx.msgs[e.m.sum.upto].role, 'user');
    assert.ok(e.ctx.saves >= 1, 'chat saved with its summary');
    const hist = e.ctx.msgs.slice(0, -1).map(x => ({ role: x.role, content: x.content }));
    const out = e.m.memoryTrim(hist);
    assert.ok(out[0].content.includes('Summary of the earlier part') && out[out.length - 1].content.startsWith('user newest') || out[out.length - 1].content.startsWith('newest'));
    for (let i = 1; i < out.length; i++) assert.notStrictEqual(out[i].role, out[i - 1].role);
    const tok = out.reduce((a, m) => a + Math.ceil(m.content.length / 3.6), 0);
    assert.ok(tok < 4000 * .6, 'history is ' + tok + ' tokens');
    assert.deepStrictEqual(roles(out)[0], 'user');
    const f = e.m.memChatFields(); assert.ok(f.summary && f.summary_upto === e.m.sum.upto && f.summary_sig);
  });

  await t('a short chat is left alone', async () => {
    const e = makeEnv(); await sleep(10); e.ctx.msgs = [msg('user', 20), msg('assistant', 20), msg('user', 20), { role: 'assistant', content: '', live: true }];
    await e.m.memoryFor('hi'); assert.strictEqual(e.m.sum.text, '');
    const h = e.ctx.msgs.slice(0, -1); assert.strictEqual(e.m.memoryTrim(h), h);
    assert.ok(!e.calls.some(c => c.url === '/api/chat'), 'no model call for a short chat');
  });

  await t('a summary is dropped when an earlier message is edited, and restored from a saved chat when it still matches', async () => {
    const e = makeEnv(); await sleep(10);
    e.state.chatReply = () => '- Goal: x\n- Decisions: y\n- State: z\n- Open items: w';
    e.ctx.msgs = [...longChat(8), msg('user', 40, 'newest'), { role: 'assistant', content: '', live: true }];
    await e.m.memoryFor('q'); const saved = e.m.memChatFields(); assert.ok(saved.summary);
    const plain = e.ctx.msgs.slice(0, e.ctx.msgs.length - 1);
    e.ctx.msgs[0].content += ' EDITED';
    const h = plain.map(x => ({ role: x.role, content: x.content }));
    assert.strictEqual(e.m.memoryTrim(h), h, 'stale summary must not be used'); assert.strictEqual(e.m.sum.text, '');
    e.ctx.msgs[0].content = e.ctx.msgs[0].content.replace(' EDITED', '');
    e.m.memChatLoad(saved); assert.ok(e.m.sum.text, 'restored when the messages still match');
    e.ctx.msgs[1].content += '!'; e.m.memChatLoad(saved); assert.strictEqual(e.m.sum.text, '', 'not restored when they do not');
  });

  await t('after a reply it learns facts, tasks and a hand-off in the background, once', async () => {
    const e = makeEnv(); await sleep(10);
    e.state.chatReply = sys => JSON.stringify({ facts: [{ text: 'The project stores data in PostgreSQL.', scope: 'project' }], tasks: ['Write the migration guide'], done: ['t1'], progress: [], handoff: { summary: 'Chose PostgreSQL.', next: ['Write the guide'] } });
    e.ctx.msgs = [msg('user', 10, "let's decide: we will use PostgreSQL"), msg('assistant', 10)]; e.ctx.busy = false;
    e.m.memoryAfter(); await e.m.bg?.promise; await sleep(10);
    assert.strictEqual(e.state.absorbed.length, 1, 'absorbed once');
    const a = e.state.absorbed[0]; assert.deepStrictEqual(JSON.parse(JSON.stringify(a.done)), ['t1']); assert.strictEqual(a.handoff.summary, 'Chose PostgreSQL.'); assert.strictEqual(a.chat, 'c1');
    assert.strictEqual(e.m.upto, 2, 'turns are marked as learned');
    assert.ok(e.notes.some(n => /Memory updated: 1 note, 1 task, 1 finished/.test(n)), e.notes.join('|'));
    e.m.memoryAfter(); await e.m.bg?.promise; assert.strictEqual(e.state.absorbed.length, 1, 'same turns are not mined twice');
  });

  await t('unparseable model output is skipped without retrying forever', async () => {
    const e = makeEnv(); await sleep(10); e.state.chatReply = 'Sorry, I cannot do that.';
    e.ctx.msgs = [msg('user', 10, "remember: we always use tabs"), msg('assistant', 10)];
    e.m.memoryAfter(); await e.m.bg?.promise; assert.strictEqual(e.state.absorbed.length, 0); assert.strictEqual(e.m.upto, 2);
  });

  await t('sending a message cancels a running background job straight away', async () => {
    const e = makeEnv(); await sleep(10); e.state.hang = true;
    e.ctx.msgs = [msg('user', 10, "let's finish this tomorrow"), msg('assistant', 10)];
    e.m.memoryAfter(); await sleep(20); assert.ok(e.m.bg, 'job is running');
    e.ctx.msgs.push(msg('user', 5), { role: 'assistant', content: '', live: true });
    const t0 = Date.now(); await e.m.memoryFor('next question'); assert.ok(Date.now() - t0 < 1000, 'took ' + (Date.now() - t0));
    assert.strictEqual(e.state.aborted, 1); assert.strictEqual(e.m.bg, null);
  });

  await t('background work never starts while a reply is streaming or the model is unloaded', async () => {
    const e = makeEnv(); await sleep(10); e.ctx.msgs = [msg('user', 10, "remember we use tabs"), msg('assistant', 10)];
    e.ctx.busy = true; e.m.memoryAfter(); assert.strictEqual(e.m.bg, null);
    e.ctx.busy = false; e.ctx.info = { n_ctx: 4000, loaded: false }; e.m.memoryAfter(); assert.strictEqual(e.m.bg, null);
    assert.ok(!e.calls.some(c => c.url === '/api/chat'));
  });

  await t('/remember, /resume, /summary and unrelated text', async () => {
    const e = makeEnv(); await sleep(10);
    assert.strictEqual(await e.m.memoryCommand('just a message'), undefined); assert.strictEqual(await e.m.memoryCommand('/fix this'), undefined);
    assert.strictEqual(await e.m.memoryCommand('/remember global: I like tabs'), null);
    const post = e.calls.find(c => c.url === '/api/memory/fact'); assert.deepStrictEqual(JSON.parse(JSON.stringify(post.body)), { text: 'I like tabs', scope: 'global' });
    assert.ok(e.locals.at(-1).includes('every project'));
    await e.m.memoryCommand('/remember we run tests with pytest'); assert.strictEqual(e.calls.filter(c => c.url === '/api/memory/fact').at(-1).body.scope, 'project');
    assert.strictEqual(await e.m.memoryCommand('/resume'), null);
    const v = e.$('#input').value; assert.ok(v.includes("pick up where we left off") && v.includes('Rewrote the error text.') && v.includes('Finish the login copy'), v);
    await e.m.memoryCommand('/summary'); assert.ok(/No summary yet/.test(e.locals.at(-1)));
    assert.strictEqual(await e.m.memoryCommand('/remember'), null); assert.ok(e.notes.some(n => /after \/remember/.test(n)));
  });

  await t('the empty screen offers to pick up open tasks, and a click pre-fills the box', async () => {
    const e = makeEnv(); await sleep(10); const hero = e.$('#empty'); e.m.memoryPaintEmpty();
    const card = hero.children[0]; assert.ok(card && card.className === 'cont' && card.innerHTML.includes('Pick up where you left off') && card.innerHTML.includes('Finish the login copy'));
    card.handlers.click({ target: { closest: () => ({ dataset: { t: '0' } }) } });
    assert.ok(e.$('#input').value.startsWith("Let's continue this task: Finish the login copy"), e.$('#input').value);
    card.handlers.click({ target: { closest: () => ({ dataset: { resume: '' } }) } });
    assert.ok(e.$('#input').value.includes('Start with: Add a test.'));
  });

  await t('leaving a chat saves a hand-off note at once and learns from it in the background', async () => {
    const e = makeEnv(); await sleep(10);
    e.state.chatReply = JSON.stringify({ facts: [], tasks: ['Review the empty password case'], done: [], progress: [], handoff: { summary: 'Reviewed login.', next: [] } });
    e.ctx.msgs = [msg('user', 30, 'u1'), msg('assistant', 30), msg('user', 30, 'u2'), msg('assistant', 30)];
    e.m.memoryLeave(); await e.m.bg?.promise; await sleep(10);
    const sess = e.calls.filter(c => c.url === '/api/memory/session'); assert.ok(sess.length >= 1 && sess[0].body.quality === 'auto' && sess[0].body.handoff.includes('Last request'), JSON.stringify(sess[0]));
    assert.strictEqual(e.state.absorbed.length, 1);
  });

  console.log(`\n${passed} passed`);
})();
