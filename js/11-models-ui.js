/* ================= model management: auto-switch, quantization, benchmarks ================= */
let lastIntent='';
async function routeStep(text,typed){
  const tm=setTimeout(()=>{$('#pill').className='pill swap';$('#pilltx').textContent='Choosing the best model…'},700);
  try{
    const r=await fetch('/api/route',{method:'POST',headers:HDR,body:JSON.stringify({text:typed||text,prev:lastIntent})});
    if(!r.ok)return null;const d=await r.json();lastIntent=d.intent;
    if(d.switched){note('Switched to '+d.model+' for this '+({code:'code',chat:'chat',reason:'reasoning'}[d.intent]||'')+' question.',true);refreshModels(true)}
    else if(d.error)note(d.error);
    else if(d.reason)note('Hard question: the Reasoner thinks first.',true);
    return d;
  }catch{return null}finally{clearTimeout(tm);if(refreshInfo)refreshInfo().catch(()=>{});paintPill(true)}
}
const IN={code:'Code',chat:'Chat',reason:'Reasoning'};
let asig='',rtext='';
function paintAuto(){
  const el=$('#autocard');if(!el||!MS||!MS.routing)return;
  const r=MS.routing,ms=MS.available||[],g=JSON.stringify([r,ms.map(m=>m.path)]);
  if(g===asig||el.contains(document.activeElement))return;asig=g;
  const opts=cur=>'<option value="">Pick automatically</option>'+ms.filter(m=>!/mmproj|vision|llava|embed|rerank/i.test(m.name)).map(m=>`<option value="${esc(m.path)}"${sameP(m.path,cur)?' selected':''}>${esc(m.name)}</option>`).join('');
  const l=r.last;
  el.innerHTML=`<div class="mc"><div class="mc-h">Auto-switch<small>by what you ask</small><span class="bdg ${r.enabled?'on':''}">${r.enabled?'On':'Off'}</span></div>
   <div class="mc-n">Off by default: your selected coder stays selected for every message. Turn this on only if you want messages to switch coder models automatically. Each model keeps its own context and tweak settings.</div>
   <label class="f">Switch models automatically<select data-r="enabled"><option value="1"${r.enabled?' selected':''}>On</option><option value="0"${r.enabled?'':' selected'}>Off</option></select></label>
   <label class="f">Code questions use<select data-r="code">${opts(r.map.code)}</select></label>
   <label class="f">Everything else uses<select data-r="chat">${opts(r.map.chat)}</select></label>
   ${l?`<div class="mc-n">Last message: ${esc(IN[l.intent]||l.intent)} (${esc(l.why)}) → ${esc(l.switched?'switched to '+l.model:(l.target||l.model||'no model loaded'))}</div>`:''}
   <div class="row"><input type="text" id="rtest" placeholder="Type a message to see where it would go" value="${esc(rtext)}"><button type="button" id="rtbtn">Test</button></div><div class="mc-n" id="rtout"></div></div>`;
}
$('#autocard').addEventListener('change',async e=>{
  const sel=e.target.closest('[data-r]');if(!sel)return;const k=sel.dataset.r,body=k==='enabled'?{enabled:sel.value==='1'}:{map:{[k]:sel.value}};
  try{MS=await post_('/api/route/config',body);asig='';msig='';renderSlots()}catch(x){note(x.message)}
});
$('#autocard').addEventListener('input',e=>{if(e.target.id==='rtest')rtext=e.target.value});
$('#autocard').addEventListener('click',async e=>{
  if(e.target.id!=='rtbtn')return;const out=$('#rtout');if(!rtext.trim()){out.textContent='Type something first.';return}
  try{const d=await post_('/api/route',{text:rtext,prev:'',dry:true});
    out.textContent='Looks like '+(IN[d.intent]||d.intent).toLowerCase()+' ('+d.why+'). '+(d.reason?'The Reasoner would think first.':d.target?'It would switch to '+d.target+'.':'It would stay on '+(d.model||'the current model')+'.')}catch(x){out.textContent=x.message}
});

let qsrc='',qtgt='Q4_K_M',qre=false;
function paintQuant(){
  const el=$('#quantcard');if(!el||!MS)return;if(el.contains(document.activeElement))return;
  const q=MS.quantize||{state:'idle'},ms=(MS.available||[]).filter(m=>m.quant),run=q.state==='running';
  if(!qsrc||!ms.some(m=>m.path===qsrc))qsrc=(ms.slice().sort((a,b)=>b.bpw-a.bpw)[0]||{}).path||'';
  const st=run?`Creating ${esc(q.dst)}… this can take several minutes and uses the CPU.`:q.state==='done'?`Done: ${esc(q.dst)} is now in your model list.`:q.state==='error'?`Failed: ${esc(q.error)}`:q.state==='cancelled'?'Cancelled.':'';
  el.innerHTML=`<div class="mc"><div class="mc-h">Quantization<small>make another size of a model you have</small></div>
   <div class="mc-n">Each Coder / Reasoner card above lists every quantization found for a model; pick one there and press Load. Only have one file? Create a smaller (or, from an F16 file, any) quantization here. Q4_K_M is the usual sweet spot, Q8_0 is almost lossless, below Q4 quality drops noticeably. Run the benchmark below on each to compare.</div>
   ${ms.length?`<label class="f">From<select id="q_src">${ms.map(m=>`<option value="${esc(m.path)}"${m.path===qsrc?' selected':''}>${esc(m.name)} · ${fSize(m.size)}</option>`).join('')}</select></label>
   <label class="f">To<select id="q_tgt">${(MS.quant_targets||[]).map(t=>`<option${t===qtgt?' selected':''}>${t}</option>`).join('')}</select></label>
   <label class="chips"><span><input type="checkbox" id="q_re"${qre?' checked':''}> Allow making it from an already-quantized file (loses a little extra quality)</span></label>
   <div class="btns"><button type="button" class="primary" id="q_go"${run?' disabled':''}>Create</button><button type="button" id="q_stop"${run?'':' disabled'}>Cancel</button></div>`:'<div class="mc-n">No quantized .gguf files found yet.</div>'}
   <div class="mc-n" id="q_msg">${st}</div></div>`;
}
$('#quantcard').addEventListener('change',e=>{if(e.target.id==='q_src')qsrc=e.target.value;if(e.target.id==='q_tgt')qtgt=e.target.value;if(e.target.id==='q_re')qre=e.target.checked});
$('#quantcard').addEventListener('click',async e=>{
  const go=e.target.id==='q_go',stop=e.target.id==='q_stop';if(!go&&!stop)return;
  try{MS=await post_(go?'/api/model/quantize':'/api/model/quantize/cancel',go?{path:qsrc,target:qtgt,allow_requantize:qre}:{});msig='';renderSlots()}catch(x){const m=$('#q_msg');if(m)m.textContent=x.message}
});

/* benchmark */
const B={slot:'coder',sel:null,speed:true,running:false,ctl:null,done:0,total:0,log:'',cur:null,suites:null,sdir:'',serr:[],hist:null};
const famOf=(n,q)=>n.replace(/\.gguf$/i,'').replace(q?new RegExp('[-_.]?'+q+'(?![A-Za-z0-9])','i'):/$^/,'').replace(/[-_.]+$/,'');
async function benchInit(){
  if(B.suites&&B.hist)return;
  try{const [a,b]=await Promise.all([fetch('/api/bench/suites').then(r=>r.json()),fetch('/api/bench/history').then(r=>r.json())]);
    B.suites=a.suites;B.sdir=a.dir;B.serr=a.errors||[];B.hist=b.runs;if(!B.sel)B.sel=new Set(a.suites.filter(x=>!x.custom).map(x=>x.name));drawBench()}catch{}
}
function paintBench(){benchInit();if(!B.running&&!$('#benchcard').contains(document.activeElement))drawBench()}
function drawBench(){
  const el=$('#benchcard');if(!el||!MS||!B.suites)return;
  const sl=MS.slots,cs=[['coder',sl.coder],['reasoner',sl.reasoner]].filter(([k,s])=>s.path);
  if(!cs.some(([k])=>k===B.slot))B.slot=(cs[0]||['coder'])[0];
  const sp=B.cur?.speed,ss=B.cur?.suites||{};
  const hist=(B.hist||[]),best=Math.max(-1,...hist.map(r=>r.overall??-1));
  const groups={};hist.forEach(r=>(groups[famOf(r.model,r.quant)+' · '+r.slot]=groups[famOf(r.model,r.quant)+' · '+r.slot]||[]).push(r));
  const date=t=>new Date(t*1000).toLocaleString([],{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'});
  const rows=Object.entries(groups).sort().map(([g,rs])=>`<tr class="grp"><td colspan="7">${esc(g)}</td></tr>`+rs.sort((a,b)=>a.size-b.size).map(r=>`<tr class="${r.overall===best&&best>=0&&hist.length>1?'best':''}"><td>${esc(r.quant||'file')}</td><td>${r.speed?r.speed.pp_tps:'–'}</td><td>${r.speed?r.speed.tg_tps:'–'}</td><td>${r.overall==null?'–':r.overall+'%'}</td><td>${Object.entries(r.suites||{}).map(([k,v])=>k.slice(0,4)+' '+v.correct+'/'+v.total).join(' · ')||'–'}</td><td>${date(r.ts)}</td><td><button type="button" data-bdel="${esc(r.id)}" title="Delete this run">✕</button></td></tr>`).join('')).join('');
  el.innerHTML=`<div class="mc"><div class="mc-h">Benchmark<small>on a loaded model</small>${B.running?'<span class="bdg wait">Running</span>':''}</div>
   <div class="mc-n">Measures speed (prompt reading and writing, in tokens per second) and scores a short question set per area. The built-in sets are small samples in the style of MMLU, ARC, GSM8K, IFEval and code reading, so use them to compare your own models and quantizations against each other, not against published leaderboards. Want bigger tests? Put .jsonl files in <code>${esc(B.sdir)}</code> (one JSON object per line: <code>question</code>, <code>choices</code> and <code>answer</code>, or just <code>question</code> and <code>answer</code>).${B.serr.length?'<br>'+esc(B.serr.join(' · ')):''}</div>
   ${cs.length?`<label class="f">Model<select id="b_slot"${B.running?' disabled':''}>${cs.map(([k,s])=>`<option value="${k}"${k===B.slot?' selected':''}>${SLOT_INFO[k][0]}: ${esc(s.name||s.path.split(/[\\/]/).pop())}${s.loaded?'':' (loads first)'}</option>`).join('')}</select></label>
   <div class="chips"><label><input type="checkbox" id="b_speed"${B.speed?' checked':''}${B.running?' disabled':''}> Speed</label>${B.suites.map(x=>`<label><input type="checkbox" data-bs="${esc(x.name)}"${B.sel.has(x.name)?' checked':''}${B.running?' disabled':''}> ${esc(x.title)} (${x.n})</label>`).join('')}</div>
   <div class="btns"><button type="button" class="primary" id="b_run"${B.running?' disabled':''}>Run benchmark</button><button type="button" id="b_stop"${B.running?'':' disabled'}>Stop</button></div>`:'<div class="mc-n">Choose a Coder or Reasoner model above first.</div>'}
   ${B.running||B.cur?`<div class="bbar"><i style="width:${B.total?Math.round(B.done/B.total*100):(B.running?5:100)}%"></i></div><div class="mc-n">${esc(B.log)}</div>`:''}
   ${sp?`<div class="mc-n"><b>Speed:</b> prompt ${sp.pp_tps} tok/s · writing ${sp.tg_tps} tok/s · first token ${sp.ttft}s${sp.rss?' · memory '+fSize(sp.rss):''}</div>`:''}
   ${Object.entries(ss).map(([k,v])=>`<div class="mc-n"><b>${esc(v.title||k)}:</b> ${v.correct}/${v.total} (${Math.round(v.correct/Math.max(1,v.total)*100)}%)${v.cut?` · ${v.cut} cut off`:''} · ${v.seconds}s</div>`).join('')}
   ${B.cur?.overall!=null?`<div class="mc-n"><b>Overall: ${B.cur.overall}%</b> (each test counts equally)</div>`:''}
   ${rows?`<div style="overflow-x:auto"><table class="btab"><tr><th>Quant</th><th>Prompt t/s</th><th>Write t/s</th><th>Score</th><th>Tests</th><th>When</th><th><button type="button" data-bdel="all" title="Clear history">clear</button></th></tr>${rows}</table></div><div class="mc-n">Same model, different quantization: compare Score against Write t/s to see what the smaller file costs you.</div>`:''}</div>`;
}
$('#benchcard').addEventListener('change',e=>{
  const t=e.target;if(t.id==='b_slot')B.slot=t.value;else if(t.id==='b_speed')B.speed=t.checked;else if(t.dataset.bs){t.checked?B.sel.add(t.dataset.bs):B.sel.delete(t.dataset.bs)}
});
$('#benchcard').addEventListener('click',async e=>{
  const t=e.target;
  if(t.dataset.bdel){try{B.hist=(await post_('/api/bench/delete',t.dataset.bdel==='all'?{all:true}:{id:t.dataset.bdel})).runs;drawBench()}catch(x){note(x.message)}return}
  if(t.id==='b_stop'){B.ctl?.abort();return}
  if(t.id!=='b_run')return;
  if(busy||MOP){note('Wait for the current job to finish (or press Stop) first.');return}
  Object.assign(B,{running:true,done:0,total:0,log:'Starting…',cur:{suites:{}},ctl:new AbortController()});drawBench();
  const body={slot:B.slot,suites:[...B.sel],speed:B.speed};
  try{
    const r=await fetch('/api/bench',{method:'POST',headers:HDR,body:JSON.stringify(body),signal:B.ctl.signal});
    if(!r.ok){let d={};try{d=await r.json()}catch{}throw new Error(d.detail||('server answered '+r.status))}
    const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){const{value,done}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});
      let i;while((i=buf.indexOf('\n\n'))>=0){const ch=buf.slice(0,i);buf=buf.slice(i+2);
        for(const ln of ch.split('\n'))if(ln.startsWith('data:'))try{benchEvent(JSON.parse(ln.slice(5)))}catch{}}}
  }catch(x){B.log=x.name==='AbortError'?'Stopped.':x.message}
  B.running=false;B.ctl=null;
  try{B.hist=(await fetch('/api/bench/history').then(r=>r.json())).runs}catch{}
  drawBench();refreshModels(true);
});
function benchEvent(e){
  if(e.type==='start'){B.total=e.total;B.log='Testing '+e.model}
  else if(e.type==='status')B.log=e.message;
  else if(e.type==='speed'){B.cur.speed=e;B.log='Speed measured. Running tests…'}
  else if(e.type==='item'){B.done=e.done;B.log='Testing '+e.suite+' ('+e.done+'/'+e.total+')'}
  else if(e.type==='suite')B.cur.suites[e.suite]=e;
  else if(e.type==='done'){B.cur=e.record;B.log='Finished in '+e.record.seconds+'s.'}
  else if(e.type==='error')B.log=e.message;
  drawBench();
}
/* theme */
let th=LS('gc.theme')||'auto';
function applyTheme(){document.documentElement.dataset.theme=th;try{applyLook()}catch{}setTimeout(()=>window.remountViz&&remountViz(),0);$$('#seg button').forEach(b=>b.classList.toggle('on',b.dataset.th===th));$('#theme').innerHTML=ic(th==='dark'?'moon':th==='light'?'sun':'half',16)+'Theme: '+th}
$('#theme').onclick=()=>{th={auto:'light',light:'dark',dark:'auto'}[th];LS('gc.theme',th);applyTheme()};
$('#seg').onclick=e=>{const b=e.target.closest('[data-th]');if(b){th=b.dataset.th;LS('gc.theme',th);applyTheme()}};
applyTheme();
/* tabs + panels */
function tab(n){$$('.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.tab===n));$$('.tab').forEach(s=>s.classList.toggle('on',s.id==='tab-'+n));LS('gc.tab',n)}
$('.tabs').onclick=e=>{const b=e.target.closest('[data-tab]');if(b)tab(b.dataset.tab)};tab(LS('gc.tab')||'files');
const mob=()=>innerWidth<=900;
function togR(){mob()?(app.classList.remove('lopen'),app.classList.toggle('ropen')):app.classList.toggle('noright')}
function togL(){mob()?(app.classList.remove('ropen'),app.classList.toggle('lopen')):app.classList.toggle('noleft')}
if(innerWidth<1180)app.classList.add('noright');
$('#tr').onclick=togR;$('#tl').onclick=togL;$('#tlw').onclick=togL;$('#scrim').onclick=()=>app.classList.remove('lopen','ropen');
$('#tlw').style.display=mob()?'none':'';addEventListener('resize',()=>$('#tlw').style.display=mob()?'none':'');
/* search + git */
$('#sq').onkeydown=async e=>{
  if(e.key!=='Enter')return;const q=e.target.value.trim();if(!q)return;
  const r=await fetch('/api/search?q='+encodeURIComponent(q)),d=await r.json(),b=$('#sres');b.innerHTML='';
  if(!r.ok)return note(d.detail);if(!d.hits.length)b.innerHTML='<div class="nohist">No matches</div>';
  d.hits.forEach(h=>{const row=document.createElement('div');row.className='hit';const s=document.createElement('span');s.textContent=h.file+':'+h.line+'  '+h.text.trim().slice(0,70);s.title=s.textContent;
    const a=document.createElement('button');a.type='button';a.textContent='Add';a.style.cssText='padding:1px 9px;font-size:12px';a.onclick=()=>attachPath(h.file,false);row.append(s,a);b.append(row)});
};
$$('[data-git]').forEach(b=>b.onclick=async()=>{note();const r=await fetch('/api/git?mode='+b.dataset.git),d=await r.json();if(!r.ok)return note(d.detail);addAtt(d);note('Attached '+d.name,true)});
/* ---------- autocomplete for /commands and @files ---------- */
const pop=$('#pop');let pi=0;
function showPop(its,apply){
  pop.innerHTML='';pop.hidden=!its.length;pi=0;
  its.forEach(([a,b],i)=>{const r=document.createElement('div');r.className='prow'+(i?'':' sel');
    const n=document.createElement('button');n.type='button';n.className='nm';n.textContent=a;n.onclick=()=>{apply(a);pop.hidden=true;$('#input').focus()};r.append(n);
    if(b){const s=document.createElement('span');s.textContent=b;r.append(s)}
    r.onmouseenter=()=>selPop(i);pop.append(r)})
}
function selPop(i){const rs=[...pop.children];if(!rs.length)return;pi=(i+rs.length)%rs.length;rs.forEach((r,j)=>r.classList.toggle('sel',j===pi));rs[pi].scrollIntoView({block:'nearest'})}
function suggest(){
  const t=$('#input'),v=t.value.slice(0,t.selectionStart),after=t.value.slice(t.selectionStart);let m;
  if(m=v.match(/^\/(\w*)$/))showPop(Object.keys(CMDS).concat('help').filter(k=>k.startsWith(m[1])).map(k=>['/'+k,(CMDS[k]?.[1]||'Show help').slice(0,60)]),a=>{t.value=a+' '+after;grow()});
  else if(m=v.match(/(?:^|\s)@([\w.\/-]*)$/)){
    if(!FILES){FILES=[];fetch('/api/files').then(r=>r.json()).then(d=>{FILES=d.files;suggest()})}
    const q=m[1].toLowerCase();showPop(FILES.filter(f=>f.toLowerCase().includes(q)).slice(0,8).map(f=>['@'+f]),a=>{t.value=v.slice(0,v.length-m[1].length-1)+a+' '+after;grow()});
  }else pop.hidden=true;
}
function grow(){const t=$('#input');t.style.height='auto';t.style.height=Math.min(t.scrollHeight,200)+'px'}
$('#input').addEventListener('input',()=>{grow();suggest()});
$('#input').addEventListener('keydown',e=>{
  const t=e.target;
  if(!pop.hidden){
    if(e.key==='ArrowDown'||e.key==='ArrowUp'){e.preventDefault();selPop(pi+(e.key==='ArrowDown'?1:-1));return}
    const sel=pop.children[pi]?.querySelector('button');
    if(e.key==='Tab'||(e.key==='Enter'&&!e.shiftKey&&sel&&sel.textContent.trim()!==t.value.trim())){e.preventDefault();sel?.click();return}
  }
  if(e.key==='Enter'&&!e.shiftKey&&!e.isComposing){e.preventDefault();if(!busy)$('#f').requestSubmit();return}
  if(e.key==='ArrowUp'&&(!t.value||t.value===SENT[SENT.length-si])&&si<SENT.length){e.preventDefault();si++;t.value=SENT[SENT.length-si];grow()}
  else if(e.key==='ArrowDown'&&si>0&&t.value===SENT[SENT.length-si]){e.preventDefault();si--;t.value=si?SENT[SENT.length-si]:'';grow()}
});
/* ---------- global shortcuts ---------- */
addEventListener('keydown',e=>{
  if(e.key==='Escape'){if(!pop.hidden)pop.hidden=true;else if(busy)ctl.abort();else app.classList.remove('lopen','ropen');return}
  if(!e.altKey)return;
  const A={KeyN:newChat,KeyL:()=>$('#input').focus(),KeyR:()=>{const l=lastReply();l&&regen(l)},KeyC:()=>{const l=lastReply();l&&navigator.clipboard.writeText(l.content)},KeyE:exportMd,KeyS:togR,KeyB:togL,KeyT:()=>showTab('term'),Slash:()=>localMsg(HELP),KeyI:()=>setImgMode(!imgMode),KeyG:()=>imgRerollLast(),KeyV:()=>imgEditLast()}[e.code];
  if(A){e.preventDefault();A()}
});
$('#clear').onclick=newChat;$('#exp').onclick=exportMd;$('#help').onclick=()=>{localMsg(HELP);if(mob())app.classList.remove('lopen')};
$('#f').onsubmit=e=>{e.preventDefault();busy?ctl.abort():send()};
$('#go').addEventListener('click',e=>{if(busy){e.preventDefault();ctl.abort()}});
$('#mf').oninput=()=>info&&showInfo(info);
refreshInfo=()=>fetch('/api/info').then(r=>r.json()).then(showInfo);
refreshInfo().catch(()=>{setDl($('#info'),[['Status','server unreachable']]);$('#pill').className='pill bad';$('#pilltx').textContent='server unreachable'});
const poll=()=>fetch('/api/system').then(r=>r.json()).then(s=>{if(s.rss){$('#rss').textContent=fmtB(s.rss);$('#cpu').textContent=s.cpu.toFixed(0)+'%'}}).catch(()=>{});
poll();setInterval(poll,2000);

