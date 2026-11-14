/* ---------- terminal ---------- */
const term={running:false,ctl:null,id:null,last:null,hist:LS('gc.thist')||[],hi:-1,trust:false};
function logAdd(text,cls){const l=$('#termlog'),s=document.createElement('span');if(cls)s.className=cls;s.textContent=text;l.append(s);while(l.childNodes.length>400)l.firstChild.remove();l.scrollTop=l.scrollHeight;return s}
function paintTerm(){const g=$('#termgo');g.textContent=term.running?'Stop':'Run';g.classList.toggle('stop',term.running);g.disabled=!CAPS.terminal;$('#termin').disabled=!CAPS.terminal||term.running;$('#termsend').disabled=!term.last}
function paintCaps(){
  $('#termnote').textContent=CAPS.terminal?'Every command starts fresh in '+CAPS.root+' ('+(CAPS.os||'shell')+'). It runs with your user permissions.':'The terminal is turned off for this server (started with --no-terminal, or on a non-loopback host).';
  paintTerm();paintChanges();
}
fetch('/api/caps').then(r=>r.json()).then(c=>{CAPS=c;paintCaps();paintImgBtn()}).catch(()=>paintCaps());
async function runCommand(cmd,sink){
  if(!CAPS.terminal){toast('The terminal is disabled on this server','bad');return null}
  if(term.running){toast('A command is already running. Stop it first.','bad');return null}
  term.running=true;term.ctl=new AbortController();term.id=null;paintTerm();
  logAdd('$ '+cmd+'\n','cmd');const live=logAdd('');
  let out='',fin=null,err=null,pend=false;const secs=+$('#runtimeout').value||60;
  const upd=()=>{pend=false;const t=cleanOut(out);live.textContent=t;sink&&sink.update&&sink.update(t);const l=$('#termlog');l.scrollTop=l.scrollHeight};
  sink&&sink.start&&sink.start();
  try{
    const r=await jpost('/api/run',{cmd,cwd:'',timeout:secs},{signal:term.ctl.signal});
    if(!r.ok){let d={};try{d=await r.json()}catch{}throw new Error(d.detail||'HTTP '+r.status)}
    const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){const{done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});let i;
      while((i=buf.indexOf('\n\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+2);if(!line.startsWith('data: '))continue;let ev;try{ev=JSON.parse(line.slice(6))}catch{continue}
        if(ev.type==='start')term.id=ev.id;else if(ev.type==='out'){out+=ev.text;if(!pend){pend=true;requestAnimationFrame(upd)}}else if(ev.type==='done')fin=ev;else if(ev.type==='error')err=ev.message}}
  }catch(e){if(e.name==='AbortError')fin=fin||{code:null,killed:true};else err=e.message}
  if(term.ctl.signal.aborted&&term.id)jpost('/api/kill',{id:term.id}).catch(()=>{});
  upd();term.running=false;
  const clean=cleanOut(out),res={cmd,out:clean,code:fin?fin.code:null,timeout:!!fin?.timeout,killed:!!fin?.killed,truncated:!!fin?.truncated,error:err};
  const msg=err?'error: '+err:res.killed?'stopped':res.timeout?'timed out after '+secs+' s':'exit '+fin?.code+(fin?.seconds!=null?' · '+fin.seconds+' s':'');
  logAdd((clean&&!clean.endsWith('\n')?'\n':'')+'['+msg+']'+(res.truncated?' · output truncated':'')+'\n\n',err||res.killed||res.timeout||res.code?'ex bad':'ex ok');
  term.last=res;paintTerm();sink&&sink.done&&sink.done(res,msg);return res;
}
function sendOutput(r){
  if(!r)return;if(busy){toast('Wait for the current reply to finish','bad');return}
  const tail=r.out.length>6000?'(earlier output omitted)\n'+r.out.slice(-6000):r.out;
  $('#input').value='I ran `'+r.cmd.split('\n')[0].slice(0,120)+'` in the project folder ('+(r.timeout?'timed out':r.killed?'stopped by me':'exit code '+r.code)+'). Output:\n\n'+fenced(tail||'(no output)')+'\n\nWhat does this tell us, and what should I do next?';
  grow();send(true,{noPlan:true});
}
$('#termf').onsubmit=e=>{e.preventDefault();
  if(term.running){term.ctl.abort();return}
  const i=$('#termin'),c=i.value.trim();if(!c)return;
  term.hist=[...term.hist.filter(x=>x!==c),c].slice(-50);LS('gc.thist',term.hist);term.hi=-1;i.value='';runCommand(c)};
$('#termin').onkeydown=e=>{
  if(e.key==='ArrowUp'||e.key==='ArrowDown'){e.preventDefault();const h=term.hist;if(!h.length)return;
    term.hi=e.key==='ArrowUp'?Math.min(h.length-1,term.hi+1):Math.max(-1,term.hi-1);e.target.value=term.hi<0?'':h[h.length-1-term.hi]}};
$('#tab-term').addEventListener('keydown',e=>{if(e.key==='Escape'&&term.running){e.stopPropagation();term.ctl.abort()}});
$('#termclear').onclick=()=>{$('#termlog').textContent=''};
$('#termsend').onclick=()=>sendOutput(term.last);
$('#termgo').addEventListener('click',e=>{if(term.running){e.preventDefault();term.ctl.abort()}});
const cmdFrom=t=>t.split('\n').map(l=>l.replace(/^\s*(?:\$|PS [^>]*>|>)\s+/,'')).join('\n').trim();
function confirmRun(cmd){
  if(term.trust||$('#confirmrun').value==='no')return Promise.resolve(true);
  return new Promise(res=>{let decided=false;const M=modal('Run this command?');M.sub.textContent='in '+(CAPS.root||'the project folder');
    const p=document.createElement('p');p.textContent='The model suggested this. It will run on your computer with your user permissions, so read it first.';
    const pre=document.createElement('pre');pre.className='cmdprev';pre.textContent=cmd;
    const lab=document.createElement('label');lab.className='chk';const cb=document.createElement('input');cb.type='checkbox';lab.append(cb,' Don\'t ask again until I reload this page');
    M.body.append(p,pre,lab);M.btn('Run',()=>{decided=true;if(cb.checked)term.trust=true;res(true);M.close()},'primary');M.btn('Cancel',M.close);
    M.onclose=()=>{if(!decided)res(false)}});
}
async function runFromBlock(pre){
  if(streamingGuard(pre))return;
  const code=pre.querySelector('code'),cmd=cmdFrom(code.dataset.raw??code.textContent);if(!cmd)return;
  if(!CAPS.terminal)return toast('The terminal is disabled on this server','bad');
  if(!await confirmRun(cmd))return;
  let o=pre.querySelector('.runout');
  if(!o){o=document.createElement('div');o.className='runout';o.innerHTML='<div class="runh"><span class="rs"></span><span class="sp"></span><button type="button" class="rsend">Send output to chat</button></div><div class="runb"></div>';pre.append(o)}
  const rs=o.querySelector('.rs'),rb=o.querySelector('.runb'),rsend=o.querySelector('.rsend');rsend.disabled=true;
  runCommand(cmd,{start:()=>{rs.className='rs';rs.textContent='Running…';rb.textContent=''},update:t=>{rb.textContent=t;rb.scrollTop=rb.scrollHeight},
    done:(r,msg)=>{rs.textContent=msg;rs.className='rs '+(r.error||r.code||r.timeout||r.killed?'bad':'ok');rsend.disabled=false;rsend.onclick=()=>sendOutput(r);if(!r.out)rb.textContent='(no output)'}});
}
/* ---------- plan mode ---------- */
let planMode=false,planOnce=false;const DEFPH=$('#input').placeholder;
function setPlan(v){planMode=v;$('#plan').classList.toggle('on',v);$('#plan').setAttribute('aria-pressed',v);$('#input').placeholder=v?'Plan mode: describe what to build. I will plan first, you approve, then we build it…':DEFPH}
$('#plan').onclick=()=>{setPlan(!planMode);$('#input').focus()};
const PLAN_PROMPT='PLAN MODE. Do not write code, files or commands yet. Reply with a plan in exactly this structure:\n## Goal\nOne sentence.\n## Approach\n2-4 bullets with the key decisions and assumptions.\n## Files\n- `path` (new or modify): why\n## Steps\n- [ ] Small, verifiable step 1\n- [ ] Step 2\n(each step should be doable in one reply)\n## Risks and questions\n- anything unclear or risky\nEnd with: "Approve the plan or tell me what to change."';
function paintPlan(m){
  const box=m.el.querySelector('.plan');if(!box)return;
  if(!m.plan||m.live){box.hidden=true;return}
  const steps=parsePlan(split(m.content).ans);m.planDone=m.planDone||[];
  const nd=steps.filter((_,i)=>m.planDone[i]).length;box.hidden=false;
  box.innerHTML=`<div class="planh">${ic('list',15)}<b>Plan</b><span class="sp"></span><span class="pc">${steps.length?nd+' / '+steps.length+' done':'review it, then approve'}</span></div>`+
   (steps.length?'<ul class="steps">'+steps.map((s,i)=>`<li class="${m.planDone[i]?'done':''}"><input type="checkbox" data-i="${i}" ${m.planDone[i]?'checked':''} aria-label="Mark done"><span></span><button type="button" class="go" data-go="${i}" title="Build this step">${ic('right',13)}Build</button></li>`).join('')+'</ul>':'')+
   `<div class="planb"><button type="button" class="primary" data-a="${steps.length?'next':'all'}">${steps.length?'Approve &amp; build next step':'Approve &amp; build'}</button>${steps.length>1?'<button type="button" data-a="all">Build everything</button>':''}<button type="button" data-a="rev">Revise plan</button></div>`;
  box.querySelectorAll('.steps li span').forEach((sp,i)=>sp.textContent=steps[i]);
  box.onclick=e=>{
    const cb=e.target.closest('input[data-i]');if(cb){m.planDone[+cb.dataset.i]=cb.checked;save();paintPlan(m);return}
    const go=e.target.closest('[data-go]');if(go)return buildStep(m,steps,+go.dataset.go);
    const a=e.target.closest('[data-a]')?.dataset.a;if(!a)return;
    if(a==='rev'){$('#input').value='Change the plan: ';grow();$('#input').focus();return}
    if(a==='next'){const i=steps.findIndex((_,j)=>!m.planDone[j]);if(i<0)return toast('All steps are done','good');return buildStep(m,steps,i)}
    if(busy)return toast('Wait for the current reply to finish','bad');
    setPlan(false);steps.forEach((_,i)=>m.planDone[i]=true);save();paintPlan(m);
    $('#input').value='The plan is approved. Implement all of it now. Use file: blocks for new files, edit: blocks for changes to existing files, and a bash block for commands. Keep explanations short.';grow();send(true,{noPlan:true});
  };
}
function buildStep(m,steps,i){
  if(busy)return toast('Wait for the current reply to finish','bad');
  setPlan(false);m.planDone[i]=true;save();paintPlan(m);
  $('#input').value=`The plan is approved. Implement step ${i+1} of ${steps.length} only: "${steps[i]}"\nUse file: blocks for new files, edit: blocks for changes to existing files, and a bash block for commands. Keep explanations short.`;grow();send(true,{noPlan:true});
}
/* ---------- system prompt for the workspace formats ---------- */
function toolsPrompt(){
  if($('#tools').value==='0')return'';
  return['You work inside the user\'s project. When the answer needs files, charts or commands, use these exact formats.',
  '1. New file or full rewrite: a fenced block whose info string is file:PATH (relative path, no spaces) with the complete file inside. Example info string: '+T3+'file:src/app.py',
  '2. Change an existing file: a fenced block with info string edit:PATH holding one or more blocks like:\n<<<<<<< SEARCH\n(old lines, copied exactly from the current file)\n=======\n(new lines)\n>>>>>>> REPLACE\nNever use "..." in SEARCH. You can only edit files you have been shown; otherwise ask the user to attach them with @path.',
  '3. Chart: a '+T3+'chart block with JSON like {"type":"bar","title":"Sales","labels":["Jan","Feb"],"datasets":[{"label":"2025","data":[10,12]}]}. Types: bar, line, area, pie, doughnut, radar, scatter. Use only numbers the user gave you; never invent data.',
  '4. Diagram: a '+T3+'mermaid block using a Mermaid 12 diagram type. For UML use-case diagrams, use the native usecase-beta syntax: actor Customer("Customer"), systemBoundary "Online Store", use cases such as Browse("Browse items") inside the boundary, then end and connect them with -->. Use ..> : include TARGET, ..> : extend TARGET, and --|> for generalization. Use a single physical line for each statement.',
  '5. Terminal command: a '+T3+'bash block, one command per line, nothing destructive. The user decides whether to run it and can show you the output.',
  'If a file itself contains triple backticks, use a four-backtick fence. Write one short sentence before each block and do not repeat file contents elsewhere.'].join('\n');
}
