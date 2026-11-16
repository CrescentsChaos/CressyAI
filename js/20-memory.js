/* ---------- memory: recall, project notes, rolling summaries, picking work back up ----------
   The server (memory.py) stores notes, tasks and per-chat hand-off notes and ranks them for each message, by meaning when an
   embedding model is available and by keywords otherwise. This file does what only the browser can do:
   - memoryFor(text)     before a send: ask the server what is relevant and format it for the system prompt
   - memoryTrim(hist)    swap old turns for the chat's rolling summary so the context window never has to drop them
   - memoryAfter()       after a reply: refresh the summary when the chat is getting long, learn notes and tasks
   - memoryLeave()       when you switch chats: learn from the chat you are leaving
   - memoryPaintEmpty()  "Pick up where you left off" card on the empty screen
   Everything here fails soft: if any step errors, the message is sent exactly as it would be without memory. */

/*==PURE==*/
const MEM_CUE=/\b(remember|from now on|always|never|prefer|todo|to-do|next (?:step|time)|we (?:decided|chose|agreed|will|should|need to)|let'?s|plan to|deadline|tomorrow|later|finish|continue|blocked|still need)\b/i;
const MEM_SECRET=/(?:\b(?:api[_-]?key|secret|passw(?:or)?d|passwd|token|bearer)\s*(?:is|=|:)\s*\S{6,})|sk-[A-Za-z0-9]{16,}|AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\./i;
const memEstTok=s=>Math.ceil(String(s||'').length/3.6);
function memClip(s,n){s=String(s||'');if(s.length<=n)return s;const h=Math.floor((n-12)/2);return s.slice(0,h)+'\n[...]\n'+s.slice(-h)}
const memOne=(s,n)=>String(s||'').replace(/\s+/g,' ').trim().slice(0,n);
/* fingerprint of the first `upto` messages: if one is edited or removed later, the stored summary no longer matches and is dropped */
function memSig(list,upto){let h=5381;const n=Math.max(0,Math.min(upto,list.length));
  for(let i=0;i<n;i++){const c=String(list[i].content||''),s=(list[i].role||'')+'|'+c.length+'|'+c.slice(0,160);for(let j=0;j<s.length;j++)h=((h<<5)+h+s.charCodeAt(j))|0}
  return n+':'+(h>>>0).toString(36)}
function memTurnText(m,cap){const who=m.role==='user'?'User':'Assistant',files=(m.files||[]).map(f=>f.name).filter(Boolean);
  return who+': '+(files.length?'[attached: '+files.join(', ')+'] ':'')+memClip(String(m.text||'').replace(/\n{3,}/g,'\n\n'),cap||1400)}
/* Where to cut the history. Messages before the cut get summarized, the rest stay word for word.
   The cut sits just before a user message (the model must see a user turn first) and keeps about keepTok tokens of recent turns.
   keepLast: the final message is the user's new message and must stay. Returns -1 when there is nothing new to summarize. */
function memPickCut(roles,toks,from,keepTok,keepLast){
  const L=roles.length;let best,tail=0,i;
  if(keepLast){best=L-1;tail=toks[L-1]||0;i=L-2}else{best=L;i=L-1}
  for(;i>=from;i--){tail+=toks[i]||0;if(tail>keepTok)break;if(roles[i]==='user')best=i}
  return best>from?best:-1;
}
function memParseLoose(text){
  let t=String(text||'').replace(/<think>[\s\S]*?<\/think>/gi,'');
  const f=t.match(/```(?:json)?\s*([\s\S]*?)```/i);if(f&&f[1].includes('{'))t=f[1];
  const a=t.indexOf('{'),b=t.lastIndexOf('}');if(a<0||b<=a)return null;
  const s=t.slice(a,b+1);
  for(const c of [s,s.replace(/,(\s*[}\]])/g,'$1')]){try{const o=JSON.parse(c);if(o&&typeof o==='object'&&!Array.isArray(o))return o}catch{}}
  return null;
}
function memParseExtraction(text){
  const o=memParseLoose(text);if(!o)return null;const arr=x=>Array.isArray(x)?x:[];
  const facts=arr(o.facts).map(f=>typeof f==='string'?{text:f,scope:'project',replaces:''}:{text:String(f&&f.text||''),scope:f&&f.scope==='global'?'global':'project',replaces:String(f&&f.replaces||'')})
    .map(f=>({...f,text:f.text.replace(/\s+/g,' ').trim()})).filter(f=>f.text.length>=8&&!MEM_SECRET.test(f.text)).slice(0,4);
  const tasks=arr(o.tasks).map(t=>typeof t==='string'?t:String(t&&t.text||'')).map(t=>t.replace(/\s+/g,' ').trim()).filter(t=>t.length>=8&&!MEM_SECRET.test(t)).slice(0,4);
  const done=arr(o.done).map(x=>String(x).trim()).filter(Boolean).slice(0,6);
  const progress=arr(o.progress).map(p=>({id:String(p&&p.id||'').trim(),note:memOne(p&&p.note,200)})).filter(p=>p.id&&p.note&&!MEM_SECRET.test(p.note)).slice(0,4);
  let handoff=null;const h=o.handoff;
  if(h&&typeof h==='object'){const s=memOne(h.summary,500);if(s&&!MEM_SECRET.test(s))handoff={summary:s,next:arr(h.next).map(x=>memOne(x,160)).filter(Boolean).slice(0,4)}}
  else if(typeof h==='string'&&memOne(h,500)&&!MEM_SECRET.test(h))handoff={summary:memOne(h,500),next:[]};
  return{facts,tasks,done,progress,handoff};
}
function memAge(ms,now){const s=Math.max(0,((now==null?Date.now():now)-ms)/1000);
  if(s<90)return'just now';if(s<3600)return Math.round(s/60)+' min ago';if(s<86400)return Math.round(s/3600)+' h ago';
  const d=Math.round(s/86400);if(d===1)return'yesterday';if(d<14)return d+' days ago';if(d<60)return Math.round(d/7)+' weeks ago';return Math.round(d/30)+' months ago'}
/* what goes into the system prompt. Sections are in priority order; when the budget runs out the rest is dropped. */
function memFormat(rec,budget,now){
  if(!rec)return'';const S=[];
  if(rec.profile)S.push(['',['Project: '+memOne(rec.profile,260)]]);
  const pins=(rec.pinned||[]).slice(0,8).map(f=>'- '+memOne(f.text,300));if(pins.length)S.push(['Pinned notes:',pins]);
  const ms=(rec.matches||[]).slice(0,5).map(f=>'- '+memOne(f.text,300));if(ms.length)S.push(['Notes that may matter for this message:',ms]);
  const ts=(rec.tasks||[]).slice(0,4).map(t=>'- '+memOne(t.text,200)+(t.progress?' (so far: '+memOne(t.progress,140)+')':'')+(t.stale?' [old]':''));
  if(ts.length)S.push(['Open tasks in this project (bring them up only if the user is picking work back up):',ts]);
  const ss=(rec.sessions||[]).slice(0,2).map(s=>'- '+memAge(s.updated,now)+', "'+memOne(s.title,60)+'": '+memOne(s.handoff||s.summary,240));
  if(ss.length)S.push(['Earlier chats that may be related:',ss]);
  const l=rec.last_session;
  if(l){const nx=(l.next||[]).slice(0,3).map(x=>memOne(x,120)).join('; ');
    S.push(['Last chat in this project ('+memAge(l.updated,now)+', "'+memOne(l.title,60)+'"):',[memOne(l.handoff||l.summary,360)+(nx?' Next steps: '+nx+'.':'')]])}
  if(!S.length)return'';
  const head='Memory from earlier work (background only: use it when it helps, never recite it, and the user\'s latest message always wins).';
  let text=head;
  for(const[title,lines]of S){
    const block=(title?'\n'+title:'')+'\n'+lines.join('\n');
    if((text+block).length<=budget){text+=block;continue}
    let b=title?'\n'+title:'',n=0;for(const ln of lines){if((text+b+'\n'+ln).length>budget)break;b+='\n'+ln;n++}
    if(n)text+=b;break;
  }
  return text.length>head.length?text:'';
}
function memSummaryPrompt(prev,chunk){
  return[{role:'system',content:'You keep a running summary of a conversation between a user and a coding assistant, so the conversation can continue from the summary alone. '
    +'Update the summary with the new messages. Keep goals, decisions and the reason for them, names, file paths, commands, the code details that matter, errors and their fixes, constraints and open questions. '
    +'Drop greetings and anything that was superseded. Write compact bullets under four headings: Goal, Decisions, State (files and code), Open items. At most 350 words. Output only the summary.'},
   {role:'user',content:'Previous summary:\n'+(prev&&prev.trim()?prev.trim():'(none yet)')+'\n\nNew messages:\n'+chunk+'\n\nWrite the updated summary.'}];
}
function memExtractionPrompt(known,tasks,excerpt){
  const kn=(known||[]).map(f=>'- ['+f.id+'] '+memOne(f.text,200)).join('\n')||'(none)';
  const tk=(tasks||[]).map(t=>'- ['+t.id+'] '+memOne(t.text,160)+(t.progress?' (so far: '+memOne(t.progress,100)+')':'')).join('\n')||'(none)';
  return[{role:'system',content:'You maintain long-term memory for a coding assistant. Read the conversation excerpt and answer with ONLY a JSON object, no other text:\n'
    +'{"facts":[{"text":"","scope":"project","replaces":""}],"tasks":[""],"done":[""],"progress":[{"id":"","note":""}],"handoff":{"summary":"","next":[""]}}\n'
    +'Rules:\n'
    +'- facts: durable things worth knowing in later chats: stack, conventions, decisions and why, where things live, the user\'s preferences. At most 3. scope is "global" only for facts about the user themselves, otherwise "project". If a fact corrects a known note, put that note\'s id in "replaces". Skip what is already known, one-off details, and secrets (keys, passwords, tokens).\n'
    +'- tasks: work that still has to be done, as one short imperative sentence each. At most 3. Skip tasks already listed.\n'
    +'- done: ids of listed open tasks that the excerpt shows are finished.\n'
    +'- progress: for listed open tasks that moved forward but are not finished, a short note on where they stand.\n'
    +'- handoff.summary: one to three sentences on what this chat worked on and where it stands. handoff.next: up to 3 concrete next steps.\n'
    +'Use empty arrays when nothing applies.'},
   {role:'user',content:'Known notes:\n'+kn+'\n\nOpen tasks:\n'+tk+'\n\nConversation excerpt:\n'+excerpt+'\n\nReturn the JSON.'}];
}
function memApplySummary(hist,text,upto){
  if(!text||!(upto>0)||upto>hist.length-1||!hist[upto]||hist[upto].role!=='user')return hist;
  return[{role:'user',content:'Summary of the earlier part of our conversation (background):\n\n'+text},
         {role:'assistant',content:'Understood. I will use that as background.'},...hist.slice(upto)];
}
function memFallbackHandoff(list){
  const u=[...list].reverse().find(m=>m.role==='user'),a=[...list].reverse().find(m=>m.role==='assistant');
  const first=s=>{const t=memOne(s,400);const m=t.match(/^.{20,}?[.!?](\s|$)/);return(m?m[0].trim():t).slice(0,180)};
  return[u&&'Last request: '+memOne(u.text,150),a&&'Last reply: '+first(a.text)].filter(Boolean).join(' | ').slice(0,480);
}
function memResumeDraft(snap,now){
  const last=(snap.sessions||[])[0],open=(snap.tasks||[]).filter(t=>t.status==='open').slice(0,4);
  if(!last&&!open.length)return'';
  const L=["Let's pick up where we left off."];
  if(last){L.push('');L.push('Last time ('+memAge(last.updated,now)+') in "'+(last.title||'an earlier chat')+'": '+(last.handoff||last.summary||'no notes saved'));
    if((last.next||[]).length)L.push('Next steps I noted: '+last.next.join('; '))}
  if(open.length){L.push('');L.push('Open tasks:');open.forEach(t=>L.push('- '+t.text+(t.progress?' (so far: '+t.progress+')':'')))}
  L.push('');L.push('Start with: '+(((last&&last.next)||[])[0]||(open[0]&&open[0].text)||'whatever matters most')+'.');
  return L.join('\n');
}
/*==END PURE==*/

/* ---------- runtime ---------- */
let MEMS=null,memSum={text:'',upto:0,sig:''},memUpto=0,memBg=null,memFailAt=0,memQuickT=0;
const memAutoOn=()=>$('#memauto')?.value!=='0',memLearnOn=()=>$('#memlearn')?.value!=='0';
const memReady=()=>!!info&&info.loaded!==false;
/* thresholds use at most 12k tokens: the reasoner's window is usually smaller than the coder's, and its server-side trim drops the oldest turns first */
const memCtx=()=>Math.min(info?.n_ctx||8192,12288);
const memBudget=()=>Math.max(900,Math.min(2400,Math.round(memCtx()*.05*3.6)));
const memLastRatio=()=>typeof lastCtxRatio==='number'?lastCtxRatio:0;
async function memApi(method,url,body){
  const r=await fetch(url,{method,headers:HDR,body:body===undefined?undefined:JSON.stringify(body)});let d={};try{d=await r.json()}catch{}
  if(!r.ok)throw new Error(d.detail||('server answered '+r.status));return d;
}
async function memComplete(messages,max,signal){
  const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},signal,
    body:JSON.stringify({messages,max_tokens:max,temperature:.15,top_p:.9,top_k:40,repeat_penalty:1.05})});
  if(!r.ok)throw new Error('server answered '+r.status);
  const rd=r.body.getReader(),dec=new TextDecoder();let out='',buf='';
  for(;;){const{done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});let i;
    while((i=buf.indexOf('\n\n'))>=0){const l=buf.slice(0,i);buf=buf.slice(i+2);if(!l.startsWith('data: '))continue;
      let e;try{e=JSON.parse(l.slice(6))}catch{continue}
      if(e.type==='token')out+=e.text;else if(e.type==='error')throw new Error(e.message)}}
  return(typeof split==='function'?split(out).ans:'')||out;
}
/* one background job at a time; sending a message cancels it so the model is free for you */
function memJob(fn){
  if(memBg||busy||!memReady()||Date.now()-memFailAt<60000)return null;
  const c=new AbortController(),job={ctl:c};memBg=job;
  job.promise=(async()=>{try{await fn(c.signal)}catch(e){if(e.name!=='AbortError')memFailAt=Date.now()}finally{if(memBg===job)memBg=null}})();
  return job.promise;
}
async function memCancelBg(){const j=memBg;if(!j)return;j.ctl.abort();await Promise.race([j.promise,new Promise(r=>setTimeout(r,1500))])}

/* ---------- history, summaries ---------- */
const memHistLen=()=>msgs.length-(msgs[msgs.length-1]?.live?1:0);
const memPromptText=m=>m.role==='user'?withFiles(m.content,m.files||[]):asst(m);
function memHistTokens(){let t=memSum.text?memEstTok(memSum.text):0;for(let i=memSum.upto;i<memHistLen();i++)t+=memEstTok(memPromptText(msgs[i]));return t}
const memNeedSummary=frac=>memAutoOn()&&memHistTokens()>memCtx()*frac||(memAutoOn()&&memLastRatio()>.75&&memHistLen()>3);
async function memSummarize(signal){
  const mine=msgs,mycid=cid,live=!!msgs[msgs.length-1]?.live,hl=memHistLen();
  const roles=[],toks=[];for(let i=0;i<hl;i++){roles.push(msgs[i].role);toks.push(memEstTok(memPromptText(msgs[i])))}
  const from=memSum.upto,cut=memPickCut(roles,toks,from,Math.round(memCtx()*.22),live);
  if(cut<0)return false;
  const sig0=memSig(msgs,cut),limit=Math.round(memCtx()*.4*3.6),pieces=[];let cur='';
  for(let i=from;i<cut;i++){const t=memTurnText({role:msgs[i].role,text:msgs[i].role==='user'?msgs[i].content:asst(msgs[i]),files:msgs[i].files},1400);
    if(cur&&cur.length+t.length>limit){pieces.push(cur);cur=''}cur+=(cur?'\n\n':'')+t}
  if(cur)pieces.push(cur);
  let text=memSum.text;
  for(const p of pieces){const out=(await memComplete(memSummaryPrompt(text,p),700,signal)).trim();if(out.length<20)throw new Error('empty summary');text=out.slice(0,2400)}
  if(msgs!==mine||cid!==mycid||memSig(msgs,cut)!==sig0)return false;   // the chat changed while we were summarizing
  memSum={text,upto:cut,sig:sig0};save();memSessionQuick(true);memPaintStat();return true;
}
async function memEnsureFits(){
  if(!memAutoOn()||!memReady()||!memNeedSummary(.5))return;
  note('Summarizing earlier messages to make room…',true);
  try{await memSummarize(ctl?.signal)}finally{note('')}
}
function memoryTrim(hist){
  try{
    if(!memSum.text)return hist;
    if(memSig(msgs,memSum.upto)!==memSum.sig){memSum={text:'',upto:0,sig:''};memPaintStat();return hist}   // an earlier message was edited
    return memApplySummary(hist,memSum.text,memSum.upto);
  }catch{return hist}
}

/* ---------- learning: notes, tasks, hand-off ---------- */
function memShouldLearn(){
  const part=msgs.slice(memUpto).filter(m=>!m.live),users=part.filter(m=>m.role==='user');
  if(!users.length)return false;
  const last=users[users.length-1].content||'';
  if(/^\/\w+/.test(last)&&!/^\/(think|plan)\b/.test(last))return false;
  return users.length>=3||MEM_CUE.test(last);
}
async function memLearn(signal,S){
  S=S||{msgs,cid,upto:memUpto,title:titleOf()};
  const part=S.msgs.slice(S.upto).filter(m=>!m.live);if(!part.length)return;
  const excerpt=part.slice(-10).map(m=>memTurnText({role:m.role,text:m.role==='user'?m.content:asst(m),files:m.files},1100)).join('\n\n');
  const lastUser=[...part].reverse().find(m=>m.role==='user')?.content||'';
  const rec=await memApi('POST','/api/memory/recall',{query:lastUser,chat:S.cid||'',k:5});
  const known=[...rec.pinned,...rec.matches].slice(0,8),open=(MEMS?.tasks||[]).filter(t=>t.status==='open').slice(0,8);
  const ex=memParseExtraction(await memComplete(memExtractionPrompt(known,open,excerpt),520,signal));
  if(S.msgs===msgs&&S.cid===cid)memUpto=S.msgs.length;      // never look at the same turns twice, even if nothing was worth keeping
  const old=CH.find(c=>c.id===S.cid);if(old&&old.mem_upto!==S.msgs.length){old.mem_upto=S.msgs.length;putChat(old)}
  if(!ex)return;
  const res=await memApi('POST','/api/memory/absorb',{...ex,chat:S.cid||'',title:S.title});
  MEMS=res;memPaint();memRepaintEmpty();
  const n=res.added_facts+res.added_tasks+res.replaced+res.closed;
  if(n)note('Memory updated'+[res.added_facts&&res.added_facts+' note'+(res.added_facts>1?'s':''),res.added_tasks&&res.added_tasks+' task'+(res.added_tasks>1?'s':''),res.closed&&res.closed+' finished'].filter(Boolean).map((x,i)=>(i?', ':': ')+x).join(''),true);
}
/* a hand-off note that needs no model, so closing the tab never loses where you were */
function memSessionQuick(now){
  if(!cid)return;clearTimeout(memQuickT);
  const go=()=>{try{
    const done=msgs.filter(m=>!m.live),users=done.filter(m=>m.role==='user').length;
    if(users<2&&!memSum.text&&done.reduce((s,m)=>s+m.content.length,0)<600)return;
    const list=done.map(m=>({role:m.role,text:m.role==='user'?m.content:asst(m)}));
    memApi('POST','/api/memory/session',{chat:cid,title:titleOf(),summary:memSum.text||'',handoff:memFallbackHandoff(list),turns:users,quality:'auto'}).then(()=>memRefresh()).catch(()=>{});
  }catch{}};
  if(now)go();else memQuickT=setTimeout(go,2500);
}

/* ---------- hooks used by the rest of the app ---------- */
async function memoryFor(text){
  try{
    await memCancelBg();
    await memEnsureFits();
    const rec=await memApi('POST','/api/memory/recall',{query:text,chat:cid||'',fresh:msgs.length<=2,k:6});
    return memFormat(rec,memBudget(),Date.now());
  }catch{return''}
}
function memoryAfter(){
  try{
    memSessionQuick();
    if(busy||!memReady())return;
    memJob(async signal=>{
      if(memNeedSummary(.42))await memSummarize(signal);
      if(memLearnOn()&&memShouldLearn())await memLearn(signal);
    });
  }catch{}
}
function memoryLeave(){
  try{
    if(!cid||msgs.length<2||busy)return;
    memSessionQuick(true);
    if(!memLearnOn()||!memReady()||!msgs.slice(memUpto).some(m=>m.role==='user'&&!m.live))return;
    const S={msgs:msgs.slice(),cid,upto:memUpto,title:titleOf()};
    memJob(signal=>memLearn(signal,S));
  }catch{}
}
const memChatFields=()=>memSum.text?{summary:memSum.text,summary_upto:memSum.upto,summary_sig:memSum.sig,mem_upto:memUpto||undefined}:(memUpto?{mem_upto:memUpto}:{});
function memChatLoad(c){
  memSum={text:'',upto:0,sig:''};memUpto=0;memPaintStat();if(!c)return;
  memUpto=Math.min(+c.mem_upto||0,msgs.length);
  const up=+c.summary_upto||0;
  if(c.summary&&up>0&&up<=msgs.length){const sig=memSig(msgs,up);if(!c.summary_sig||sig===c.summary_sig)memSum={text:c.summary,upto:up,sig}}
  memPaintStat();
}
const memReset=()=>{memSum={text:'',upto:0,sig:''};memUpto=0;memPaintStat()};
function memoryChatDeleted(id){memApi('POST','/api/memory/session/delete',{chat:id}).then(d=>{MEMS=d;memPaint()}).catch(()=>{})}

/* ---------- slash commands: /remember /forget /resume /summary ---------- */
async function memoryCommand(t){
  const m=t.match(/^\/(remember|forget|resume|summary)\b\s*([\s\S]*)$/i);if(!m)return undefined;
  const cmd=m[1].toLowerCase(),arg=m[2].trim(),clear=()=>{$('#input').value='';grow()};
  if(cmd==='remember'){
    if(!arg){note('Write a fact after /remember');return null}
    let scope=$('#memscope')?.value==='global'?'global':'project',text=arg;
    const sm=arg.match(/^(global|everywhere|project)\s*:\s*([\s\S]+)$/i);if(sm){scope=/^project$/i.test(sm[1])?'project':'global';text=sm[2].trim()}
    clear();
    try{const d=await memApi('POST','/api/memory/fact',{text,scope});MEMS=d;memPaint();localMsg('Saved to **Memory** ('+(scope==='global'?'every project':'this project')+'): '+d.fact.text)}
    catch(e){localMsg('Could not save that: '+e.message)}
    return null;
  }
  if(cmd==='forget'){
    if(!arg){note('Write some words from the note after /forget');return null}
    clear();
    try{
      const r=await memApi('POST','/api/memory/search',{query:arg,k:3}),f=r.facts[0],tk=r.tasks[0];
      if(!f&&!tk){localMsg('Nothing in Memory matches **'+arg.replace(/[*_`]/g,'')+'**.');return null}
      if(f&&confirm('Forget this note?\n\n'+f.text)){MEMS=await memApi('POST','/api/memory/fact/delete',{id:f.id});memPaint();localMsg('Forgot: '+f.text)}
      else if(!f&&tk&&confirm('Delete this task?\n\n'+tk.text)){MEMS=await memApi('POST','/api/memory/task/delete',{id:tk.id});memPaint();localMsg('Deleted task: '+tk.text)}
    }catch(e){localMsg('Could not search Memory: '+e.message)}
    return null;
  }
  if(cmd==='resume'){
    clear();await memRefresh().catch(()=>{});
    const d=MEMS?memResumeDraft(MEMS,Date.now()):'';
    if(!d){localMsg('Nothing to resume yet. Hand-off notes and open tasks appear here after a few messages.');return null}
    $('#input').value=d;grow();$('#input').focus();note('Edit it if you like, then press Enter to continue.',true);return null;
  }
  clear();
  localMsg(memSum.text?'**Running summary of this chat** ('+memSum.upto+' earlier messages are sent to the model as this summary):\n\n'+memSum.text
    :'No summary yet. Long chats are summarized automatically once they fill about half the context window.');
  return null;
}
Object.assign(CMDS,{forget:['d','Remove a saved note: /forget <words from the note>'],resume:['d','Pick up the last chat and your open tasks'],summary:['d','Show the running summary of this chat']});
try{HELP+='\n\n**Memory:** `/remember [global:|project:] text` saves a note · `/forget words` removes one · `/resume` pre-fills a message that picks up the last chat and your open tasks · `/summary` shows this chat\'s running summary. Long chats are summarized automatically, and notes, tasks and a hand-off are learned from your chats (Settings → Memory).'}catch{}

/* ---------- panel + empty screen ---------- */
async function memRefresh(){MEMS=await memApi('GET','/api/memory');memPaint();memRepaintEmpty()}
const memRepaintEmpty=()=>{if($('#empty'))memoryPaintEmpty()};
function memPaintStat(){
  const el=$('#memstat');if(!el)return;const sem=MEMS?.semantic,bits=[];
  if(sem)bits.push(sem.on?`Semantic search: on (${esc(sem.model)}, ${sem.indexed}/${sem.total} notes indexed)`:`Semantic search: off. Memory matches on keywords. ${esc(sem.why||'')}`);
  bits.push(memSum.text?`This chat: ${memSum.upto} earlier messages are summarized to save context.`:'This chat: nothing summarized yet.');
  el.innerHTML=bits.join('<br>');
}
function memPaint(){
  if(!MEMS)return;
  const proj=$('#memproj');
  if(proj)proj.innerHTML=`<b>${esc(MEMS.project_name)}</b>${MEMS.profile?` · ${esc(MEMS.profile)}`:''}<br>Notes marked <i>this project</i> belong to this folder only; <i>every project</i> notes follow you everywhere. Pinned notes and the box below go out with every message; other notes are picked by what you ask. Open tasks and a hand-off from your last chat carry into new chats here.`;
  memPaintStat();
  const rows=(a,html)=>a.length?a.map(html).join(''):'<div class="mc-n">Nothing yet.</div>';
  $('#memfacts').innerHTML=rows(MEMS.facts,f=>`<div class="mem-row" data-id="${f.id}"><span class="tx">${esc(f.text)}</span><span class="tag">${f.scope==='global'?'every project':'this project'}</span>${f.pin?'<span class="tag">pinned</span>':''}${f.source==='chat'?'<span class="tag">learned</span>':''}<button type="button" data-a="pin">${f.pin?'Unpin':'Pin'}</button><button type="button" data-a="scope">${f.scope==='global'?'Only here':'Everywhere'}</button><button type="button" data-a="edit">Edit</button><button type="button" data-a="del" aria-label="Delete">✕</button></div>`);
  $('#memtasks').innerHTML=rows(MEMS.tasks,t=>`<div class="mem-row${t.status==='done'?' done':''}" data-id="${t.id}"><span class="tx">${esc(t.text)}${t.progress?`<span class="sub">${esc(t.progress)}</span>`:''}</span>${t.stale?'<span class="tag">old</span>':''}${t.status==='open'?'<button type="button" data-a="go">Continue</button>':''}<button type="button" data-a="toggle">${t.status==='open'?'Done':'Reopen'}</button><button type="button" data-a="del" aria-label="Delete">✕</button></div>`);
  const ss=$('#memsessions');
  if(ss)ss.innerHTML=rows(MEMS.sessions,s=>`<div class="mem-row" data-chat="${esc(s.chat)}"><span class="tx"><b>${esc(s.title||'Chat')}</b> · ${esc(memAge(s.updated))}<span class="sub">${esc(s.handoff||s.summary||'')}</span>${(s.next||[]).length?`<span class="sub">Next: ${esc(s.next.join('; '))}</span>`:''}</span>${CH.some(c=>c.id===s.chat)?'<button type="button" data-a="open">Open</button>':''}<button type="button" data-a="del" aria-label="Forget">✕</button></div>`);
}
async function memPaintProjects(){
  const el=$('#memprojects');if(!el)return;
  try{const d=await memApi('GET','/api/memory/projects');
    el.innerHTML=d.projects.map(p=>`<div class="mem-row" data-path="${esc(p.path)}"><span class="tx"><b>${esc(p.name)}</b>${p.current?' <span class="tag">this one</span>':''}${p.exists?'':' <span class="tag">folder missing</span>'}<span class="sub" title="${esc(p.path)}">${p.facts} note${p.facts===1?'':'s'} · ${p.tasks} open task${p.tasks===1?'':'s'} · ${p.sessions} chat note${p.sessions===1?'':'s'}</span></span>${p.current?'':'<button type="button" data-a="adopt">Move here</button>'}</div>`).join('')}
  catch(e){el.textContent='Could not load projects: '+e.message}
}
function memoryPaintEmpty(){
  const hero=$('#empty');if(!hero||!MEMS)return;hero.querySelector('.cont')?.remove();
  const last=(MEMS.sessions||[])[0],recent=last&&Date.now()-last.updated<45*864e5,open=(MEMS.tasks||[]).filter(t=>t.status==='open').slice(0,3);
  if(!recent&&!open.length)return;
  const d=document.createElement('div');d.className='cont';
  d.innerHTML='<h2>Pick up where you left off</h2>'
    +(recent?`<p>${esc(memAge(last.updated))} · <b>${esc(last.title||'Earlier chat')}</b><br>${esc(memOne(last.handoff||last.summary||'',220))}</p>`:'')
    +open.map((t,i)=>`<button type="button" class="task" data-t="${i}"><b>${esc(t.text)}</b>${t.progress?'<br><small>'+esc(t.progress)+'</small>':''}</button>`).join('')
    +(recent||open.length?'<div class="btns"><button type="button" data-resume>Draft a message that resumes this</button></div>':'');
  d.addEventListener('click',e=>{
    const b=e.target.closest('button');if(!b)return;const inp=$('#input');
    if(b.dataset.t!==undefined){const t=open[+b.dataset.t];inp.value="Let's continue this task: "+t.text+(t.progress?' (so far: '+t.progress+')':'');}
    else if('resume' in b.dataset)inp.value=memResumeDraft(MEMS,Date.now());
    else return;
    grow();inp.focus();
  });
  hero.append(d);
}
async function memAct(fn,okMsg){try{MEMS=await fn();memPaint();if(okMsg)note(okMsg,true)}catch(e){note(e.message)}}
const memAddFact=()=>{const i=$('#memadd'),t=i.value.trim();if(!t)return;memAct(async()=>{const d=await memApi('POST','/api/memory/fact',{text:t,scope:$('#memscope').value});i.value='';return d},'Saved')};
const memAddTask=()=>{const i=$('#taskadd'),t=i.value.trim();if(!t)return;memAct(async()=>{const d=await memApi('POST','/api/memory/task',{text:t,chat:cid||''});i.value='';return d},'Task added')};
(function wire(){
  const on=(sel,ev,fn)=>{const e=$(sel);if(e)e.addEventListener(ev,fn)},enter=fn=>e=>{if(e.key==='Enter'){e.preventDefault();fn()}};
  on('#memsave','click',memAddFact);on('#memadd','keydown',enter(memAddFact));
  on('#tasksave','click',memAddTask);on('#taskadd','keydown',enter(memAddTask));
  on('#taskclear','click',()=>memAct(()=>memApi('POST','/api/memory/task/clear',{})));
  on('#memfacts','click',e=>{
    const b=e.target.closest('button[data-a]'),row=e.target.closest('.mem-row');if(!b||!row)return;const id=row.dataset.id,f=MEMS.facts.find(x=>x.id===id);if(!f)return;
    if(b.dataset.a==='pin')memAct(()=>memApi('POST','/api/memory/fact/update',{id,pin:!f.pin}));
    else if(b.dataset.a==='scope')memAct(()=>memApi('POST','/api/memory/fact/update',{id,scope:f.scope==='global'?'project':'global'}));
    else if(b.dataset.a==='edit'){const t=prompt('Edit this note',f.text);if(t&&t.trim()&&t.trim()!==f.text)memAct(()=>memApi('POST','/api/memory/fact/update',{id,text:t.trim()}))}
    else if(b.dataset.a==='del'&&confirm('Delete this note?'))memAct(()=>memApi('POST','/api/memory/fact/delete',{id}));
  });
  on('#memtasks','click',e=>{
    const b=e.target.closest('button[data-a]'),row=e.target.closest('.mem-row');if(!b||!row)return;const id=row.dataset.id,t=MEMS.tasks.find(x=>x.id===id);if(!t)return;
    if(b.dataset.a==='toggle')memAct(()=>memApi('POST','/api/memory/task/update',{id,status:t.status==='open'?'done':'open',chat:cid||''}));
    else if(b.dataset.a==='del')memAct(()=>memApi('POST','/api/memory/task/delete',{id}));
    else if(b.dataset.a==='go'){const inp=$('#input');inp.value="Let's continue this task: "+t.text+(t.progress?' (so far: '+t.progress+')':'');grow();inp.focus()}
  });
  on('#memsessions','click',e=>{
    const b=e.target.closest('button[data-a]'),row=e.target.closest('.mem-row');if(!b||!row)return;const chat=row.dataset.chat;
    if(b.dataset.a==='open'){if(!busy)load(chat)}
    else if(b.dataset.a==='del'&&confirm('Forget the hand-off note for this chat? The chat itself stays.'))memAct(()=>memApi('POST','/api/memory/session/delete',{chat,only_here:true}));
  });
  on('#memprojects','click',e=>{
    const b=e.target.closest('button[data-a="adopt"]'),row=e.target.closest('.mem-row');if(!b||!row)return;
    if(confirm('Move the notes, open tasks and chat notes of "'+row.querySelector('b').textContent+'" into this project?\nUse this when a project folder was renamed or moved.'))
      memAct(()=>memApi('POST','/api/memory/adopt',{from:row.dataset.path}),'Moved').then(memPaintProjects);
  });
  on('#memexport','click',async()=>{try{const d=await memApi('GET','/api/memory/export');dl_('cressy-memory-'+(MEMS?.project_name||'project')+'.json',JSON.stringify(d,null,1))}catch(e){note(e.message)}});
  on('#memimport','click',()=>$('#memfile').click());
  on('#memfile','change',async e=>{const f=e.target.files[0];e.target.value='';if(!f)return;
    try{const d=JSON.parse(await f.text()),r=await memApi('POST','/api/memory/import',d);MEMS=r;memPaint();note(`Imported ${r.added_facts} notes, ${r.added_tasks} tasks, ${r.added_sessions} chat notes (${r.skipped} skipped)`,true)}
    catch(err){note('Import failed: '+(err.message||'not a CressyAI memory file'))}});
  on('#memreindex','click',()=>memAct(()=>memApi('POST','/api/memory/reindex',{}),'Search index rebuilt'));
  on('#memprojtoggle','toggle',e=>{if(e.target.open)memPaintProjects()});
  addEventListener('focus',()=>{if(!busy)memRefresh().catch(()=>{})});
  memRefresh().catch(()=>{});
})();
