/* ================= agent mode: autonomous multi-step coding with verification =================
   The server runs the loop (see agent.py) and streams events; this file turns them into a live card.
   The pure part below (between PURE markers) is tested with: node tests/test_agent_js.js */
/*==PURE==*/
const AG_OUT=4000;
const agCap=(s,n)=>s.length>n?'…'+s.slice(s.length-n+1):s;
function agentNew(goal){
  return{goal,id:null,status:'running',step:0,max:0,attempt:1,attempts:3,tests:[],canRun:true,plan:[],steps:[],files:{},finalFiles:[],
    verify:null,verifying:false,baseline:null,baselineRunning:false,bout:'',note:'',error:'',approval:null,summary:'',markdown:'',
    vkind:'',vok:null,caveats:[],seconds:0,canRevert:false,reverted:false,open:{},resumed:false};
}
function agentReduce(st,ev){
  const cur=st.steps[st.steps.length-1],act=cur&&cur.actions[cur.actions.length-1];
  switch(ev.type){
    case'start':st.id=ev.id;st.max=ev.max_steps;st.attempts=ev.max_attempts;st.tests=ev.tests||[];st.canRun=ev.can_run!==false;st.root=ev.root||'';st.status='running';st.resumed=!!ev.resumed||st.resumed;st.models={planner:ev.planner||'',coder:ev.coder||''};if(ev.warn)st.note=ev.warn;break;
    case'verify':
      if(ev.baseline){if(ev.phase==='done'){st.baseline={ok:!!ev.ok,summary:ev.summary||'',failing:ev.failing||[]};st.baselineRunning=false}else st.baselineRunning=true;break}
      if(ev.phase==='start'){st.verifying=true;if(cur)cur.vout=''}
      else{st.verifying=false;st.verify={ok:!!ev.ok,summary:ev.summary||'',checks:ev.checks||[],final:!!ev.final};if(cur)cur.ver=st.verify}
      break;
    case'step':st.steps.push({n:ev.n,text:'',live:'',actions:[],notes:[],ver:null,vout:'',diag:null});st.step=ev.n;st.max=ev.max;st.attempt=ev.attempt;st.attempts=ev.attempts;break;
    case'token':if(cur)cur.live=agCap(cur.live+ev.text,6000);break;
    case'assistant':if(cur){cur.text=ev.text||'';cur.live=''}break;
    case'action':if(cur)cur.actions.push({i:ev.i,kind:ev.kind,path:ev.path||'',cmd:ev.cmd||'',ok:null,text:'',out:''});break;
    case'cmd_out':
      if(st.baselineRunning)st.bout=agCap(st.bout+ev.text,AG_OUT);
      else if(st.verifying&&cur)cur.vout=agCap(cur.vout+ev.text,AG_OUT);
      else if(act)act.out=agCap(act.out+ev.text,AG_OUT);
      break;
    case'result':{const a=cur&&cur.actions.find(x=>x.i===ev.i);if(a){a.ok=!!ev.ok;a.text=ev.text||''}break}
    case'file_change':st.files[ev.path]={path:ev.path,created:!!ev.created,external:!!ev.external};break;
    case'syntax':if(!ev.ok&&cur)cur.notes.push('Syntax error: '+(ev.message||ev.path).split('\n')[0]);break;
    case'diagnosis':if(cur)cur.diag={headline:ev.headline||'',locations:ev.locations||[]};break;
    case'plan':st.plan=(ev.items||[]).map(p=>({...p,done:!!p.done||st.plan.some(q=>q.done&&q.text===p.text)}));break;   // a tick is never lost
    case'planning':st.planning={phase:ev.phase,by:ev.by||'',ok:ev.ok!==false,text:ev.text||st.planning&&st.planning.text||'',replan:!!ev.replan};break;
    case'approval':st.approval={id:ev.id,cmd:ev.cmd,reason:ev.reason};break;
    case'approval_done':st.approval=null;break;
    case'rollback':if(cur)cur.notes.push('Rolled back '+((ev.files||[]).join(', ')||'nothing')+' — '+(ev.reason||'the same failure repeated'));break;
    case'retry':st.attempt=ev.attempt;if(cur)cur.notes.push('Starting attempt '+ev.attempt+' of '+ev.max);break;
    case'note':if(cur)cur.notes.push(ev.text);else st.note=ev.text;break;
    case'error':st.error=ev.message||'error';break;
    case'done':
      st.status=ev.status;st.summary=ev.summary||'';st.markdown=ev.markdown||'';st.seconds=ev.seconds||0;st.canRevert=!!ev.can_revert;
      st.finalFiles=ev.files||[];st.vkind=(ev.verification||{}).kind||'';st.vok=(ev.verification||{}).ok;st.caveats=(ev.verification||{}).caveats||[];
      st.verifying=false;st.baselineRunning=false;st.approval=null;if(!st.id)st.id=ev.id;
      if(ev.status==='success')st.plan.forEach(p=>{p.done=true});break;      // a finished run has every step ticked off
  }
  return st;
}
const agentRunning=st=>st&&st.status==='running';
function agentSlim(st){            // what is saved with the chat: no live text, short outputs
  const c=JSON.parse(JSON.stringify(st));c.approval=null;c.bout='';
  c.steps.forEach(s=>{s.live='';s.vout=agCap(s.vout||'',1200);s.actions.forEach(a=>a.out=agCap(a.out||'',1200))});
  return c;
}
function agentRevive(st){          // a card that was still "running" when the page closed
  if(st&&st.status==='running'){st.status='stopped';st.summary=st.summary||'Interrupted (the page was closed or reloaded).';st.approval=null;st.verifying=false}
  return st;
}
/* An agent card can be continued when its run did not finish cleanly (stopped, interrupted, out of steps, failed) or still has open plan steps. */
function agentResumable(st){
  if(!st||st.reverted)return false;
  return st.status!=='success'||st.plan.some(p=>!p.done);
}
/* What the server needs to carry on exactly where the run stopped: the plan with its ticks, the files already changed, the last things done. */
function agentResumeState(st){
  const files=[...new Set([...(st.finalFiles||[]).map(f=>f.path),...Object.keys(st.files||{})])];
  const steps=st.steps.slice(-12).map(s=>'step '+s.n+': '+agentStepLabel(s)+(s.actions.length?' ['+s.actions.map(a=>(AG_KIND[a.kind]||a.kind)+(a.path?' '+a.path:'')+(a.ok===false?' FAILED':'')).join(', ')+']':''));
  return{status:st.status==='running'?'interrupted':st.status,summary:(st.summary||st.error||'').slice(0,500),
    plan:st.plan.map(p=>({text:p.text,done:!!p.done})),plan_text:(st.planning&&st.planning.text)||'',files,steps};
}
const AG_CONTINUE=/^\s*\/?(?:continue|go on|keep going|carry on|resume|proceed)(?:\s+(?:please|pls|from where you (?:left off|stopped)))?\s*[.!]*\s*$/i;
const AG_KIND={read:'Read',ls:'List',grep:'Search',file:'Write',edit:'Edit',run:'Run',test:'Test',plan:'Plan',done:'Finish'};
function agentTitle(st){
  if(st.status==='running'&&st.planning&&st.planning.phase==='start')return(st.planning.replan?'Re-planning':'Planning')+(st.planning.by?' · '+st.planning.by:'');
  if(st.status==='running')return'Working…'+(st.attempt>1?' · attempt '+st.attempt+' of '+st.attempts:'');
  return({success:'Finished',failed:'Could not finish',stopped:'Stopped',budget:'Out of steps',error:'Error'})[st.status]||st.status;
}
function agentStepLabel(s){
  const t=(s.text||s.live||'').replace(/\s+/g,' ').trim();
  return t?t.slice(0,110):(s.actions.length?s.actions.map(a=>AG_KIND[a.kind]||a.kind).join(', '):'thinking…');
}
function agentStepState(s){        // ok | bad | run
  if(s.actions.some(a=>a.ok===false)||s.notes.some(n=>/^(Syntax|Rolled)/.test(n)))return'bad';
  if(s.actions.some(a=>a.ok===null))return'run';
  return'ok';
}
/* The activity feed: what the agent DID, flat and short, like a tool-call log. Model chatter is shown once per distinct sentence and
   identical neighbours (the same failing command, the same apology) collapse into one row with a count. */
const AG_VERB={read:'Read',ls:'Listed',grep:'Searched',file:'Wrote',edit:'Edited',run:'Ran',test:'Ran tests'};
const AG_ICON={read:'▤',ls:'☰',grep:'⌕',file:'✎',edit:'✎',run:'▶',test:'✓'};
function agentProse(t){const s=String(t||'').replace(/\s+/g,' ').trim(),m=s.match(/^(.{1,240}?[.!?])(\s|$)/);return(m?m[1]:s).slice(0,240)}
function agentRowLabel(a){return a.kind==='run'||a.kind==='test'?(a.cmd||'project tests'):(a.path||'')}
function agentRowMeta(a){
  if(a.ok===null)return'running…';
  if(a.kind==='run'||a.kind==='test')return a.ok?'ok':'failed';
  const t=(a.text||'').split('\n')[0].replace(/\.$/,'');
  return a.ok?t.replace(/^(Created|Replaced|Edited)\s+\S+\s*/,'').replace(/^[(:]\s*|\)$/g,'').slice(0,60):'failed: '+t.slice(0,80);
}
function agentFeed(st){
  const groups=[];
  st.steps.forEach(s=>{
    const items=[],prose=agentProse(s.text);
    if(prose)items.push({kind:'prose',key:'p:'+prose,text:prose});
    s.actions.forEach(a=>{if(a.kind==='plan'||a.kind==='done')return;items.push({kind:'act',key:'a:'+a.kind+':'+agentRowLabel(a)+':'+a.ok,id:'a'+s.n+'.'+a.i,a})});
    if(s.diag&&s.diag.headline)items.push({kind:'note',key:'d:'+s.diag.headline,text:'Diagnosis: '+s.diag.headline,bad:false});
    (s.notes||[]).forEach(n=>items.push({kind:'note',key:'n:'+n,text:n,bad:/^(Syntax|Rolled)/.test(n)}));
    if(s.ver)items.push({kind:'ver',key:'v:'+s.ver.ok+s.ver.summary,ver:s.ver});
    if(!items.length)return;
    const sig=items.map(x=>x.key).join('|'),last=groups[groups.length-1];
    if(last&&last.sig===sig){last.count++;last.items.forEach((x,i)=>{if(x.a)x.a=items[i].a})}      // the same step again: one row with a count
    else groups.push({sig,count:1,items});
  });
  const out=[];
  groups.forEach(g=>{
    const hasAct=g.items.some(x=>x.kind==='act'||x.kind==='ver');
    g.items.forEach(x=>{x.count=(x.kind==='act'||x.kind==='ver'||!hasAct)?g.count:1;out.push(x)});
  });
  return out;
}
/*==END PURE==*/

let agentMode=false,agentOnce=false,agentLive=null;
const agentSetOnce=v=>{agentOnce=v};
function agEl(tag,cls,text){const e=document.createElement(tag);if(cls)e.className=cls;if(text!=null)e.textContent=text;return e}

/* ---------- the card ---------- */
/* The card is PATCHED in place instead of rebuilt. Rebuilding it ~10x a second restarted the spinner, closed open <details>,
   reset the scroll position inside every output box and made the page jump: that was the shaking and flickering. */
function agSync(dst,src){
  const a=[...dst.childNodes],b=[...src.childNodes];
  for(let i=0;i<b.length;i++){
    const x=a[i],y=b[i];
    if(!x){dst.append(y);continue}
    if(x.nodeType!==y.nodeType||(x.nodeType===1&&x.tagName!==y.tagName)){x.replaceWith(y);continue}
    if(x.nodeType!==1){if(x.data!==y.data)x.data=y.data;continue}
    for(const at of [...x.attributes])if(!y.hasAttribute(at.name)&&!(x.tagName==='DETAILS'&&at.name==='open'))x.removeAttribute(at.name);
    for(const at of [...y.attributes])if(x.getAttribute(at.name)!==at.value)x.setAttribute(at.name,at.value);
    x.onclick=y.onclick;
    if(x.tagName==='PRE'&&x.classList.contains('agout')){
      const t=y.textContent;
      if(x.textContent!==t){const near=x.scrollHeight-x.scrollTop-x.clientHeight<40;x.textContent=t;if(near)x.scrollTop=x.scrollHeight}
      continue;
    }
    agSync(x,y);
  }
  for(let i=a.length-1;i>=b.length;i--)a[i].remove();
}
function paintAgent(m){
  const box=m.el&&m.el.querySelector('.agent');if(!box)return;
  const st=m.agent;if(!st){box.hidden=true;return}
  if(agentRunning(st)&&m!==agentLive)agentRevive(st);
  box.hidden=false;box.dataset.status=st.status;
  const kids=[];
  const head=agEl('div','agh');
  const dot=agEl('span','agdot'+(st.status==='running'?' spin':''));
  head.append(dot,agEl('b','',agentTitle(st)));
  if(st.seconds)head.append(agEl('span','agm',st.seconds+' s'));
  kids.push(head,agEl('div','aggoal',st.goal));
  if(st.resumed)kids.push(agEl('div','agm','Continuing an earlier run: finished steps stay ticked.'));
  if(st.root)kids.push(agEl('div','agm','Working in: '+st.root));
  if(st.models&&(st.models.planner||st.models.coder))kids.push(agEl('div','agm',(st.models.planner?'Plans with '+st.models.planner+' · ':'')+'Codes with '+(st.models.coder||'the coder')));
  if(st.planning&&st.planning.phase==='done'&&st.planning.text){
    const d=agEl('details','agplanx');d.append(agEl('summary','agm','Plan from '+st.planning.by+' (details)'),agEl('pre','agout',st.planning.text));kids.push(d);
  }
  if(st.note)kids.push(agEl('div','agwarn',st.note));
  if(st.baselineRunning||st.baseline){
    const b=agEl('div','agbase');
    b.textContent=st.baselineRunning?'Running the tests once before changing anything…':
      (st.baseline.ok?'Tests passed before the task started.':'Tests failed before the task started ('+st.baseline.summary+'); the agent knows which ones.');
    kids.push(b);
  }
  if(st.plan.length){
    const done=st.plan.filter(p=>p.done).length,pl=agEl('div','agplan'),h=agEl('div','agplh','Plan · '+done+' / '+st.plan.length);
    const ul=agEl('ul');st.plan.forEach(p=>{const li=agEl('li',p.done?'done':'');li.append(agEl('span','agck',p.done?'✓':'○'),agEl('span','',p.text));ul.append(li)});
    pl.append(h,ul);kids.push(pl);
  }
  const feed=agentFeed(st),running=agentRunning(st);
  const nAct=feed.reduce((n,x)=>n+(x.kind==='act'?(x.count||1):0),0);
  const collapsed=!running&&st.status==='success'&&!st.open.feed&&feed.length>0;
  if(!running&&feed.length){
    const tg=agEl('button','agtog',(collapsed?'▸ Show ':'▾ Hide ')+(nAct?nAct+' action'+(nAct===1?'':'s'):'activity'));tg.type='button';
    tg.onclick=()=>{st.open.feed=collapsed;paintAgent(m)};kids.push(tg);
  }
  if(!collapsed){
    const list=agEl('div','agfeed');
    feed.forEach(it=>{
      if(it.kind==='prose'){const p=agEl('div','agprose',it.text);if(it.count>1)p.append(agEl('span','agx','×'+it.count));list.append(p);return}
      if(it.kind==='note'){list.append(agEl('div','agnote',it.text));return}
      if(it.kind==='ver'){const v=agEl('div','agrow '+(it.ver.ok?'ok':'bad')+' nod'),h=agEl('div','agrh');
        h.append(agEl('span','agic',it.ver.ok?'✓':'✕'),agEl('span','agv',it.ver.final?'Final check':'Check'),agEl('span','agt',it.ver.summary||''),agEl('span','agr '+(it.ver.ok?'ok':'bad'),it.ver.ok?'passed':'failed'));v.append(h);list.append(v);return}
      const a=it.a,open=!!st.open[it.id],state=a.ok===null?'run':a.ok?'ok':'bad';
      const hasDet=!!(a.out||(a.ok===false&&a.text&&a.kind!=='run'&&a.kind!=='test'));
      const row=agEl('div','agrow '+state+(hasDet?'':' nod')+(open&&hasDet?' open':'')),hd=agEl('button','agrh');hd.type='button';
      hd.append(agEl('span','agic'+(state==='run'?' spin':''),state==='run'?'':state==='bad'?'✕':(AG_ICON[a.kind]||'•')),agEl('span','agv',AG_VERB[a.kind]||a.kind),agEl('code','agt',agentRowLabel(a).slice(0,120)));
      if(it.count>1)hd.append(agEl('span','agx','×'+it.count));
      hd.append(agEl('span','agr '+state,agentRowMeta(a)));
      if(hasDet)hd.onclick=()=>{st.open[it.id]=!open;paintAgent(m)};
      row.append(hd);
      if(open&&hasDet){const dt=agEl('div','agdet');if(a.out)dt.append(agEl('pre','agout',a.out));else dt.append(agEl('div','agdiag',a.text));row.append(dt)}
      list.append(row);
    });
    const cur=st.steps[st.steps.length-1];
    if(running&&st.verifying)list.append(agEl('div','aglive','Checking the result…'));
    else if(running&&!st.approval&&(!cur||!cur.actions.length||cur.actions.every(x=>x.ok!==null)))list.append(agEl('div','aglive','Working…'));
    kids.push(list);
  }
  if(st.approval){
    const a=agEl('div','agappr');a.append(agEl('b','','Allow this command?'),agEl('span','agm',st.approval.reason||''));
    const pre=agEl('pre','agout',st.approval.cmd);
    const row=agEl('div','agbtns'),yes=agEl('button','primary','Allow'),all=agEl('button','','Allow this kind for the rest of the run'),no=agEl('button','','Deny');
    yes.type=all.type=no.type='button';yes.onclick=()=>agentAnswer(m,true);all.onclick=()=>agentAnswer(m,true,true);no.onclick=()=>agentAnswer(m,false);row.append(yes,all,no);a.append(pre,row);kids.push(a);a.dataset.fresh=st.approval.id;
  }
  if(st.error&&!st.summary)kids.push(agEl('div','agwarn',st.error));
  const files=st.finalFiles&&st.finalFiles.length?st.finalFiles:Object.values(st.files);
  if(files.length){
    const f=agEl('div','agfiles');f.append(agEl('span','agm','Changed files'));
    files.forEach(x=>{const c=agEl('button','agchip',x.path+(x.added!=null?'  +'+x.added+' −'+x.removed:x.created?'  new':''));c.type='button';c.title='Open the Changes tab';c.onclick=()=>showTab('changes');f.append(c)});
    kids.push(f);
  }
  if(!agentRunning(st)){
    const row=agEl('div','agbtns');
    if(files.length){const b=agEl('button','','Review changes');b.type='button';b.onclick=()=>showTab('changes');row.append(b)}
    if(st.canRevert&&!st.reverted){const b=agEl('button','','Revert all changes');b.type='button';b.onclick=()=>agentRevert(m,b);row.append(b)}
    if(st.reverted)row.append(agEl('span','agm','Reverted.'));
    if(agentResumable(st)&&typeof lastReply==='function'&&m===lastReply()&&!busy){const b=agEl('button','primary','Continue');b.type='button';b.title='Pick up exactly where the agent stopped';b.onclick=()=>{$('#input').value='continue';send()};row.append(b)}
    if(row.children.length)kids.push(row);
  }
  const tmp=document.createElement('div');tmp.append(...kids);agSync(box,tmp);
  box.querySelectorAll('.agout').forEach(p=>{if(!p._s){p._s=1;p.scrollTop=p.scrollHeight}});     // a new output box starts at its end; existing ones keep their place
  const ap=box.querySelector('.agappr');           // the permission box sits at the bottom of the card; bring it into view once
  if(ap&&box.dataset.seenAppr!==ap.dataset.fresh){box.dataset.seenAppr=ap.dataset.fresh;stick=true;requestAnimationFrame(()=>{chat.scrollTop=chat.scrollHeight;ap.scrollIntoView({block:'end',behavior:'smooth'})})}
}
let agPaintQ=new Set(),agRaf=0;
let agSaveAt=0;
function agentSchedule(m){agPaintQ.add(m);if(!agRaf)agRaf=setTimeout(()=>{agRaf=0;const q=[...agPaintQ];agPaintQ.clear();q.forEach(paintAgent);
  if(Date.now()-agSaveAt>2500&&q.some(x=>agentRunning(x.agent)&&msgs.includes(x))){agSaveAt=Date.now();save()}},220)}   // progress is written to the chat while the agent works, so a refresh loses nothing

/* ---------- consent, approval, revert ---------- */
function agentConsent(){
  if(LS('gc.agentok'))return Promise.resolve(true);
  return new Promise(res=>{
    let ok=false;const M=modal('Turn on agent mode?');M.sub.textContent=CAPS.root||'';
    const p=agEl('p','','In agent mode the model works on its own: it reads and edits files in your project folder and runs commands (tests, scripts) on your computer without asking before every step.');
    const ul=agEl('ul');['Dangerous commands (deleting outside the project, sudo, git push, piping downloads into a shell) never run.',
      'Installs, deletes, git changes and network commands ask you first.','Every changed file is listed in the Changes tab, and Revert undoes the whole run.',
      'It checks its own work: syntax after every write, the project tests before it may finish.','Keep the project under git as well.'].forEach(t=>ul.append(agEl('li','',t)));
    M.body.append(p,ul);
    M.btn('Turn on',()=>{ok=true;LS('gc.agentok',1);res(true);M.close()},'primary');M.btn('Cancel',M.close);M.onclose=()=>{if(!ok)res(false)};
  });
}
async function agentAnswer(m,ok,always){
  const st=m.agent;if(!st||!st.approval)return;const id=st.approval.id;st.approval=null;paintAgent(m);
  try{await jpost('/api/agentrun/approve',always?{id,ok,always:true}:{id,ok})}catch(e){toast('Could not send your answer: '+e.message,'bad')}
}
async function agentRevert(m,btn){
  const st=m.agent;if(!st||!st.id||st.reverted)return;
  if(!confirm('Undo every change this agent run made to the project folder?'))return;
  btn&&(btn.disabled=true);
  try{
    const r=await jpost('/api/agentrun/revert',{id:st.id}),d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.detail||'HTTP '+r.status);
    st.reverted=true;(d.files||[]).forEach(p=>WS.delete(p));paintChanges();FILES=null;ls(cwd,'none');paintAgent(m);save();
    toast('Reverted '+(d.files||[]).length+' file(s)','good');
  }catch(e){btn&&(btn.disabled=false);toast('Revert failed: '+e.message,'bad')}
}

/* ---------- running ---------- */
async function agentRoute(text,raw,opts){
  if(opts&&opts.noAgent)return false;
  if(AG_CONTINUE.test(text)&&!att.length){                         // "continue": carry on from the last agent card that did not finish
    const last=lastReply();
    if(last&&last.agent&&agentResumable(agentRevive(last.agent))){
      if(!last.agent.goal){note('That agent run has no saved goal to continue.');return true}
      await sendAgent(last.agent.goal,[],{state:agentResumeState(last.agent),label:text.trim()});
      return true;
    }
  }
  const mt=text.match(/^\/agent\b\s*([\s\S]*)$/i),forced=agentOnce;
  if(!mt&&!agentMode&&!forced)return false;
  if(!mt&&!forced&&text.startsWith('/'))return false;          // other slash commands keep working in agent mode
  agentOnce=false;
  const goal=mt?mt[1].trim():text;
  if(!goal&&!att.length){note('Describe the task after /agent');return true}
  if(att.some(a=>a.thumb))note('Images are not used in agent mode; attach text files or describe the task.');
  await sendAgent(goal||'Work on the attached file(s).',att.filter(a=>!a.thumb));
  return true;
}
async function sendAgent(goal,files,resume){
  if(!CAPS.write){toast('Agent mode needs to write files, which is off on this server (--read-only, or a non-loopback host).','bad');return}
  if(!await agentConsent())return;
  const shown=(resume&&resume.label)||goal;SENT.push(shown);si=0;
  const full=files.length?withFiles(goal,files):goal;
  $('#input').value='';grow();pop.hidden=true;
  const u={role:'user',content:shown,full:shown,files:att},m={role:'assistant',content:'',full:'',shown:0,stats:null,live:false,agent:agentNew(goal)};
  if(resume){const ps=resume.state;m.agent.plan=ps.plan.map(p=>({...p}));m.agent.resumed=true;ps.files.forEach(p=>{m.agent.files[p]={path:p,created:false,external:false}})}   // the card starts with the old ticks and files
  agentLive=m;                                                       // before addMsg paints the card, or it is shown as "Stopped" at once
  msgs.push(u,m);addMsg(u);addMsg(m);setTitle();save();att=[];renderTray();setBusy(true);ctl=new AbortController();toBottom();   // saved now, so a refresh keeps the chat
  const st=m.agent;
  ctl.signal.addEventListener('abort',()=>{if(st.id)jpost('/api/agentrun/cancel',{id:st.id}).catch(()=>{})});   // the stream then ends with a "stopped" result
  let gotDone=false;
  try{
    const mem=$('#mem').value.trim(),mc=typeof memoryFor==='function'?await memoryFor(goal):'';
    const system=[$('#sys').value.trim(),todayLine(),mem&&'Standing notes about the user (always respect them):\n'+mem,mc].filter(Boolean).join('\n\n');
    const body={goal:full,system,...params(),max_steps:+$('#agsteps').value||25,max_attempts:+$('#agtries').value||3,
      auto_tests:$('#agtests').value!=='0',baseline:$('#agbase').value!=='0',approval:$('#agapprove').value||'ask',planner:($('#agplanner')&&$('#agplanner').value)||'auto',mode:($('#agmode')&&$('#agmode').value)||'fast',
      test_cmd:$('#agcmd').value.trim(),timeout:+$('#runtimeout').value||60,...(resume?{resume:resume.state}:{})};
    const r=await jpost('/api/agentrun',body);                      // no abort signal on purpose: Stop asks the server to stop and the stream reports it
    if(!r.ok){let d={};try{d=await r.json()}catch{}throw new Error(d.detail||'HTTP '+r.status)}
    const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){
      const{done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});let i;
      while((i=buf.indexOf('\n\n'))>=0){
        const line=buf.slice(0,i);buf=buf.slice(i+2);if(!line.startsWith('data: '))continue;
        let ev;try{ev=JSON.parse(line.slice(6))}catch{continue}
        agentReduce(st,ev);
        if(ev.type==='file_change'&&typeof ev.after==='string')recordWS(ev.path,ev.before,ev.after,true,!!ev.crlf);
        if(ev.type==='approval')toast('The agent needs your approval to run a command','good');
        if(ev.type==='done'){gotDone=true;if(st.markdown){m.content=m.full=st.markdown}}
        agentSchedule(m);
      }
    }
  }catch(e){st.error=e.message;if(!gotDone){st.status='error';st.summary='The agent could not run: '+e.message}}
  if(!gotDone&&st.status==='running'){st.status='error';st.summary='The connection to the server was lost before the agent finished.'}
  if(!m.content)m.content=m.full='**'+({error:'Error.',stopped:'Stopped.'}[st.status]||'Stopped.')+'** '+(st.summary||st.error||'');
  agentLive=null;m.stats=null;setBusy(false);paint(m,false);paintAgent(m);save();
  paintChanges();FILES=null;ls(cwd,'none');refreshModels();
  if(typeof memoryAfter==='function')memoryAfter();
  toBottom();
}

/* ---------- the toggle, the slash command, settings ---------- */
function setAgent(v){
  agentMode=v;const b=$('#agent');b.classList.toggle('on',v);b.setAttribute('aria-pressed',v);
  $('#input').placeholder=v?'Agent mode: describe the task. I will look at the project, edit files, run the tests and fix errors myself…':DEFPH;
  if(v&&planMode)setPlan(false);
}
const _setPlan=setPlan;setPlan=function(v){if(v&&agentMode)setAgent(false);_setPlan(v)};
$('#agent').onclick=async()=>{
  if(!agentMode){if(!CAPS.write){toast('Agent mode needs file writing, which is off on this server (--read-only, or a non-loopback host).','bad');return}if(!await agentConsent())return}
  setAgent(!agentMode);$('#input').focus();
};
fetch('/api/caps').then(r=>r.json()).then(c=>{const b=$('#agent');b.disabled=!c.agent;if(!c.agent)b.title='Agent mode needs file writing, which is off on this server (--read-only, or a non-loopback host)'}).catch(()=>{});
$('#agdetect').onclick=async()=>{
  const n=$('#agnote');n.textContent='Looking…';
  try{const r=await fetch('/api/agentrun/detect',{headers:{'X-Cressy':'1'}}),d=await r.json();
    n.textContent=d.tests&&d.tests.length?'Detected: '+d.tests.join('  |  '):'No test command found in '+(d.root||'the project')+'. Add one above, or put {"test": "your command"} in cressy-agent.json.'}
  catch(e){n.textContent='Could not check: '+e.message}
};
SUGG.push(['Run the agent','/agent','Explore, edit, run tests and fix errors on its own']);
HELP+='\n\n**Agent mode:** press **Agent** (or type `/agent <task>`) and the model works on its own: it looks at the project, edits files, runs the tests, reads the errors and fixes them, and only finishes after the project\'s tests pass. Every change appears in the Changes tab; *Revert all changes* undoes a run. Settings → Agent has the step limit, retries, test command and approval rules.';
msgs.forEach(m=>{if(m.agent)paintAgent(m)});         // chats restored before this script loaded
