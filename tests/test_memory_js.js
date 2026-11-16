// Tests for the pure part of js/20-memory.js (between /*==PURE==*/ and /*==END PURE==*/). Run: node tests/test_memory_js.js
const fs = require('fs'), vm = require('vm'), path = require('path'), assert = require('assert');
const src = fs.readFileSync(process.env.MEM_JS || path.join(__dirname, '..', 'js', '20-memory.js'), 'utf8');
const block = src.slice(src.indexOf('/*==PURE==*/'), src.indexOf('/*==END PURE==*/'));
const names = [...block.matchAll(/^(?:const|function)\s+(\w+)/gm)].map(m => m[1]);
const ctx = vm.createContext({});
vm.runInContext(block + '\nglobalThis.__x={' + names.join(',') + '}', ctx);
const X = ctx.__x;
const J = x => JSON.parse(JSON.stringify(x));   // values built inside the vm sandbox come from another realm
let n = 0; const t = (name, fn) => { try { fn(); n++; console.log('ok   ', name) } catch (e) { console.log('FAIL ', name, '\n', e.stack); process.exitCode = 1 } };
const roles = (k) => Array.from({ length: k }, (_, i) => i % 2 ? 'assistant' : 'user');

t('signature changes when an earlier message is edited, removed or reordered', () => {
  const m = [{ role: 'user', content: 'a' }, { role: 'assistant', content: 'b' }, { role: 'user', content: 'c' }];
  const s = X.memSig(m, 2);
  assert.strictEqual(s, X.memSig(JSON.parse(JSON.stringify(m)), 2));
  assert.notStrictEqual(s, X.memSig([{ role: 'user', content: 'a!' }, m[1], m[2]], 2));
  assert.notStrictEqual(s, X.memSig([m[1], m[2]], 2));
  assert.strictEqual(X.memSig(m, 2), X.memSig([m[0], m[1], { role: 'user', content: 'changed later' }], 2), 'messages after the cut do not matter');
  assert.ok(X.memSig(m, 99).startsWith('3:'));
});

t('cut: keeps the new user message and lands on a user turn', () => {
  const r = roles(8), tk = Array(8).fill(100);                       // u a u a u a u a ; the last one is an assistant placeholder-free history
  const live = X.memPickCut(roles(7), Array(7).fill(100), 0, 250, true);   // 7 msgs, last is the new user message
  assert.strictEqual(live, 6, 'budget fits only the new message');
  assert.strictEqual(X.memPickCut(roles(7), Array(7).fill(100), 0, 450, true), 4, 'two more turns fit -> cut before a user turn');
  assert.strictEqual(roles(7)[X.memPickCut(roles(7), Array(7).fill(100), 0, 450, true)], 'user');
  const bg = X.memPickCut(r, tk, 0, 250, false);                     // after a reply: everything can be summarized, recent turns stay
  assert.ok(bg === 6 || bg === 8, 'bg cut ' + bg);
  assert.strictEqual(X.memPickCut(r, tk, 0, 10, false), 8, 'nothing fits the budget -> summarize it all');
});

t('cut: -1 when there is nothing new to summarize', () => {
  assert.strictEqual(X.memPickCut(roles(3), [10, 10, 10], 0, 10000, true), -1);
  assert.strictEqual(X.memPickCut(roles(7), Array(7).fill(100), 6, 50, true), -1, 'already summarized up to the last user message');
});

t('a huge new message is still kept whole', () => {
  assert.strictEqual(X.memPickCut(roles(5), [100, 100, 100, 100, 99999], 0, 500, true), 4);
});

t('parseLoose copes with fences, think tags, prose and trailing commas', () => {
  assert.deepStrictEqual(J(X.memParseLoose('```json\n{"a":1}\n```')), { a: 1 });
  assert.deepStrictEqual(J(X.memParseLoose('<think>hmm {x}</think>Sure! {"a":[1,2,],"b":"c",} hope that helps')), { a: [1, 2], b: 'c' });
  assert.strictEqual(X.memParseLoose('no json here'), null);
  assert.strictEqual(X.memParseLoose('{broken'), null);
  assert.strictEqual(X.memParseLoose('[1,2,3]'), null);
});

t('extraction is normalized, capped and scrubbed of secrets', () => {
  const ex = X.memParseExtraction(JSON.stringify({
    facts: ['short', 'The project stores data in PostgreSQL.', { text: 'My editor of choice is Neovim.', scope: 'global' },
            { text: 'Use port 8081 now.', replaces: 'abc123abc123' }, 'The API key is sk-abcdefghijklmnopqrstu123', 'Fifth valid fact for the limit.'],
    tasks: ['Fix it', 'Add the resume banner for open tasks', { text: 'Write the migration guide' }, 'password: hunter2hunter2 must be rotated'],
    done: ['t1', '', 't2'], progress: [{ id: 'a', note: 'half done' }, { id: '', note: 'x' }, { id: 'b' }],
    handoff: { summary: 'Worked on the login flow.', next: ['Test it', 'Ship it', '', 'x', 'y'] }
  }));
  assert.deepStrictEqual(J(ex.facts.map(f => f.text)), ['The project stores data in PostgreSQL.', 'My editor of choice is Neovim.', 'Use port 8081 now.', 'Fifth valid fact for the limit.']);
  assert.strictEqual(ex.facts[1].scope, 'global'); assert.strictEqual(ex.facts[2].replaces, 'abc123abc123');
  assert.deepStrictEqual(J(ex.tasks), ['Add the resume banner for open tasks', 'Write the migration guide']);
  assert.deepStrictEqual(J(ex.done), ['t1', 't2']); assert.deepStrictEqual(J(ex.progress), [{ id: 'a', note: 'half done' }]);
  assert.strictEqual(ex.handoff.next.length, 4);
  assert.strictEqual(X.memParseExtraction('nothing'), null);
  const bare = X.memParseExtraction('{"facts":"oops","tasks":null,"handoff":"Short plain hand-off text."}');
  assert.deepStrictEqual(J([bare.facts, bare.tasks, bare.handoff.summary]), [[], [], 'Short plain hand-off text.']);
});

const rec = {
  profile: 'cressy (Python, FastAPI): A private chat UI',
  pinned: [{ text: 'My name is Rowan.' }, { text: 'I prefer short answers.' }],
  matches: [{ text: 'We store all application data in PostgreSQL.' }],
  tasks: [{ text: 'Finish the login error copy', progress: 'Copy written, tests missing' }, { text: 'Old thing to do someday', stale: true }],
  sessions: [{ title: 'Database migration', handoff: 'Chose PostgreSQL over SQLite.', updated: 1000 }],
  last_session: { title: 'Login work', handoff: 'Rewrote the error text.', next: ['Add an empty-password test'], updated: 1000 }
};
t('context text: all sections, in priority order, with ages', () => {
  const s = X.memFormat(rec, 5000, 1000 + 3 * 864e5);
  for (const bit of ['Project: cressy', 'My name is Rowan', 'PostgreSQL', 'so far: Copy written', '[old]', '3 days ago', 'Next steps: Add an empty-password test'])
    assert.ok(s.includes(bit), 'missing ' + bit + '\n' + s);
  assert.ok(s.indexOf('Pinned') < s.indexOf('may matter') && s.indexOf('may matter') < s.indexOf('Open tasks') && s.indexOf('Open tasks') < s.indexOf('Last chat'));
});
t('context text: respects its budget by dropping the lowest priorities first', () => {
  for (const b of [200, 400, 700, 1200]) {
    const s = X.memFormat(rec, b, 5000);
    assert.ok(s.length <= b || s === '', `budget ${b} got ${s.length}`);
  }
  const tight = X.memFormat(rec, 330, 5000);
  assert.ok(tight.includes('Rowan') && !tight.includes('Last chat'), tight);
  assert.strictEqual(X.memFormat(null, 900), ''); assert.strictEqual(X.memFormat({ pinned: [], matches: [], tasks: [] }, 900), '');
});

t('summary is swapped in only at a user turn that exists', () => {
  const h = [{ role: 'user', content: 'u0' }, { role: 'assistant', content: 'a0' }, { role: 'user', content: 'u1' }, { role: 'assistant', content: 'a1' }, { role: 'user', content: 'u2' }];
  const out = X.memApplySummary(h, 'SUM', 2);
  assert.deepStrictEqual(J(out.map(m => m.role)), ['user', 'assistant', 'user', 'assistant', 'user']);
  assert.ok(out[0].content.includes('SUM') && out[2].content === 'u1');
  assert.strictEqual(X.memApplySummary(h, 'SUM', 1), h, 'not a user turn');
  assert.strictEqual(X.memApplySummary(h, 'SUM', 5), h, 'past the end');
  assert.strictEqual(X.memApplySummary(h, '', 2), h);
  assert.strictEqual(X.memApplySummary(h, 'SUM', 0), h);
});

t('prompts carry what the model needs', () => {
  const p = X.memSummaryPrompt('- Goal: x', 'User: hi');
  assert.ok(p[1].content.includes('- Goal: x') && p[1].content.includes('User: hi') && /Open items/.test(p[0].content));
  assert.ok(X.memSummaryPrompt('', 'c')[1].content.includes('(none yet)'));
  const e = X.memExtractionPrompt([{ id: 'f1', text: 'Fact one is here' }], [{ id: 't1', text: 'Task one', progress: 'half' }], 'User: do it');
  assert.ok(e[1].content.includes('[f1]') && e[1].content.includes('[t1]') && e[1].content.includes('(so far: half)') && /ONLY a JSON/.test(e[0].content));
  assert.ok(X.memExtractionPrompt([], [], 'x')[1].content.includes('(none)'));
});

t('resume draft, fallback hand-off, ages', () => {
  const snap = { sessions: [{ title: 'Login work', handoff: 'Rewrote the error text.', next: ['Add a test'], updated: 0 }], tasks: [{ status: 'open', text: 'Ship it', progress: 'staged' }, { status: 'done', text: 'Old one' }] };
  const d = X.memResumeDraft(snap, 2 * 864e5);
  assert.ok(d.includes('2 days ago') && d.includes('Rewrote the error text.') && d.includes('- Ship it (so far: staged)') && d.endsWith('Start with: Add a test.') && !d.includes('Old one'));
  assert.strictEqual(X.memResumeDraft({ sessions: [], tasks: [] }, 0), '');
  const h = X.memFallbackHandoff([{ role: 'user', text: 'Fix the login bug please' }, { role: 'assistant', text: 'Sure. I changed the handler. More text follows here.' }]);
  assert.ok(h.startsWith('Last request: Fix the login bug please') && h.includes('Last reply: Sure. I changed the handler.'), h);
  assert.deepStrictEqual(J([X.memAge(0, 30e3), X.memAge(0, 600e3), X.memAge(0, 5 * 3600e3), X.memAge(0, 864e5), X.memAge(0, 5 * 864e5), X.memAge(0, 21 * 864e5)]),
    ['just now', '10 min ago', '5 h ago', 'yesterday', '5 days ago', '3 weeks ago']);
});

t('a long conversation stays inside the window, with valid turns, across many summaries', () => {
  const NCTX = 8000, keep = Math.round(NCTX * .22), msgs = [];
  let sum = { text: '', upto: 0, sig: '' }, rounds = 0, maxTail = 0;
  const body = i => 'word '.repeat(300 + (i % 5) * 120);                       // roughly 400-700 tokens per message
  for (let turn = 0; turn < 60; turn++) {
    msgs.push({ role: 'user', content: 'question ' + turn + ' ' + body(turn) });
    const L = msgs.length, toks = msgs.map(m => X.memEstTok(m.content));
    const over = () => (sum.text ? X.memEstTok(sum.text) : 0) + toks.slice(sum.upto).reduce((a, b) => a + b, 0);
    if (over() > NCTX * .5) {
      const cut = X.memPickCut(msgs.map(m => m.role), toks, sum.upto, keep, true);
      assert.ok(cut > sum.upto && cut < L, 'cut ' + cut);
      assert.strictEqual(msgs[cut].role, 'user');
      sum = { text: 'summary of ' + cut + ' messages. ' + 'x'.repeat(1500), upto: cut, sig: X.memSig(msgs, cut) }; rounds++;
      assert.ok(over() <= NCTX * .5 + 1, 'still over after summarizing: ' + over());
    }
    const hist = X.memApplySummary(msgs.map(m => ({ role: m.role, content: m.content })), sum.text, sum.upto);
    assert.strictEqual(hist[hist.length - 1].role, 'user'); assert.strictEqual(hist[hist.length - 1].content, msgs[L - 1].content, 'the new message is never summarized away');
    for (let i = 1; i < hist.length; i++) assert.notStrictEqual(hist[i].role, hist[i - 1].role, 'roles alternate at ' + i);
    maxTail = Math.max(maxTail, hist.reduce((a, m) => a + X.memEstTok(m.content), 0));
    if (sum.text) assert.strictEqual(X.memSig(msgs, sum.upto), sum.sig, 'stored summary still matches its messages');
    msgs.push({ role: 'assistant', content: 'answer ' + turn + ' ' + body(turn + 2) });
  }
  assert.ok(rounds >= 8, 'summaries happened: ' + rounds);
  assert.ok(maxTail < NCTX * .75, 'prompt stayed inside the window: ' + maxTail);
  msgs[2].content += ' edited';                                           // editing an old message invalidates the stored summary
  assert.notStrictEqual(X.memSig(msgs, sum.upto), sum.sig);
});

console.log(`\n${n} passed`);
