/* ================= tool orchestration =================
   The server (toolbox.py) lets the model choose and chain tools: web, Wikipedia, places & weather, nature, documents,
   memory, project files, calculator, clock. Every result is checked before the model sees it. This file decides when to
   use that path, shows each call (with its verdict) in a card above the answer, and feeds web/document sources into the
   existing [1] / [K1] citation chips. */
let toolsOnce=false;
const TOOL_DOT={ok:'ok',partial:'warn',empty:'mute',duplicate:'mute',declined:'mute',error:'bad',invalid:'bad',running:'run'};
const TOOL_WORD={ok:'ok',partial:'check',empty:'empty',duplicate:'repeat',declined:'declined',error:'failed',invalid:'rejected',running:'running'};
const argText=a=>Object.entries(a||{}).map(([k,v])=>k+'='+(typeof v==='string'?'“'+(v.length>40?v.slice(0,40)+'…':v)+'”':JSON.stringify(v))).join(', ');

/* Should this message go through the tool loop? Returns {body, sel} for /api/agent, or null for the normal path. */
async function toolsPlan(text,typed,u,webReq,kbReq){
  if(!CAPS.tools||$('#tormode').value==='off')return null;
  const force=toolsOnce;toolsOnce=false;
  if(!force&&((u.files||[]).length||planMode||imgMode))return null;
  if(window.CressyGeo?.pending?.(text))return null;                 // a /weather-style command keeps its own flow
  const hints={urls:webReq?.urls||[],web:!!webReq&&!webReq.urls&&!webReq.only,wiki:!!webReq?.wiki,gh:webReq?.gh||null,kb:!!kbReq,q:typeof makeQuery==='function'?makeQuery(text):text.slice(0,200)};
  const disabled=[...(webOn?[]:['web_search','read_url']),...($('#wikimode').value==='off'?['wikipedia']:[]),...($('#ghmode')?.value==='off'?['github']:[])];
  const body={text,hints,disabled,force,steps:+$('#torsteps').value||4,verify:$('#torverify').value!=='0',units:$('#geounits')?.value||'metric',lang:$('#wikilang')?.value||'en'};
  try{
    const r=await fetch('/api/tools/select',{method:'POST',headers:HDR,body:JSON.stringify(body),signal:ctl.signal});
    const sel=await r.json();
    return r.ok&&sel.use?{body,sel}:null;
  }catch(e){if(e.name==='AbortError')throw e;return null}            // selection is an optimisation: never block the answer on it
}

/* Writes, edits and commands from the model wait for the user. While one is running, ask the server what is waiting
   (a few times a second) and show it on the tool card with Approve / Deny; the server refuses on timeout or Stop. */
const NEEDS_OK=/^(filesystem\.(write|edit)|terminal\.run)$/;
function approvalsPoll(m){
  if(m._ap)return;
  const tick=async()=>{
    if(!m.tools||!m.tools.busy||!m.live){m._ap=0;if(m.approvals?.length){m.approvals=[];paintTools(m)}return}
    try{
      const r=await fetch('/api/tools/pending',{headers:HDR});
      if(r.ok){const j=await r.json(),p=j.pending||[];
        if(p.map(x=>x.id).join()!==(m.approvals||[]).map(x=>x.id).join()){m.approvals=p;paintTools(m)}}
    }catch(e){}
    m._ap=setTimeout(tick,500);
  };
  m._ap=setTimeout(tick,100);
}
function approvalBox(m,x){
  const b=document.createElement('div'),h=document.createElement('div'),d=document.createElement('pre'),row=document.createElement('div'),yes=document.createElement('button'),no=document.createElement('button');
  b.className='tap';h.className='tap-h';d.className='tap-d';row.className='tap-b';yes.className='tap-y';no.className='tap-n';yes.type=no.type='button';
  h.textContent=x.title||'The model wants to make a change';d.textContent=x.detail||'';d.hidden=!x.detail;yes.textContent='Approve';no.textContent='Deny';
  const send=ok=>{
    yes.disabled=no.disabled=true;
    fetch('/api/tools/approve',{method:'POST',headers:HDR,body:JSON.stringify({id:x.id,ok})}).catch(()=>{})
      .finally(()=>{m.approvals=(m.approvals||[]).filter(y=>y.id!==x.id);m._ts='';paintTools(m)});
  };
  yes.onclick=()=>send(true);no.onclick=()=>send(false);row.append(yes,no);b.append(h,d,row);return b;
}

/* One streamed event from /api/agent. Returns true when it was a tool event (the caller then skips it). */
function toolsHandle(m,ev){
  const t=m.tools;
  const ph=()=>m.el?.querySelector('.foot .ph');
  const say=l=>{if(m.stop1){m.stop1();m.stop1=null}const e=ph();if(e)e.textContent=l};
  const rest=()=>{const e=ph();if(e&&!m.stop1)m.stop1=ticker(e,PHRASES,2600)};
  if(ev.type==='plan'){m.tools={busy:true,why:ev.why||'',tools:ev.tools||[],calls:[]};say('Choosing tools…');paintTools(m);return true}
  if(ev.type==='tool_start'&&t){t.calls.push({id:ev.id,name:ev.name,args:ev.args||{},status:'running',via:ev.via,notes:[]});say('Running '+ev.name+'…');if(NEEDS_OK.test(ev.name||''))approvalsPoll(m);paintTools(m);return true}
  if(ev.type==='tool_result'&&t){
    const i=t.calls.findIndex(c=>c.id===ev.id),c={id:ev.id,name:ev.name,args:ev.args||{},status:ev.status,notes:ev.notes||[],ms:ev.ms,via:ev.via,preview:ev.preview||''};
    if(i<0)t.calls.push(c);else t.calls[i]=c;
    if(ev.sources){
      const qs=t.calls.filter(x=>/^(web_search|wikipedia|github)$/.test(x.name)&&(x.args.query||x.args.target)).map(x=>x.args.query||x.args.target);
      m.web={query:[...new Set(qs)].slice(0,2).join(' + ')||'sources',results:ev.sources.map(s=>({title:s.title,url:s.url,domain:s.domain||hostOf(s.url),snippet:s.snippet||'',published:s.published||'',read:!!s.read,err:s.err||''})),read:ev.sources.filter(s=>s.read).length};
      paintWeb(m);
    }
    if(ev.kb){m.kb={query:(t.calls.find(x=>x.name==='knowledge_search')?.args.query)||'',hits:ev.kb};paintKbCard(m)}
    if(!t.calls.some(x=>x.status==='running'))rest();
    paintTools(m);return true;
  }
  if(ev.type==='grounding'&&t){t.ground=ev.unsupported||[];paintTools(m);return true}
  if(ev.type==='done'&&t){t.busy=false;paintTools(m);return false}
  if(ev.type==='error'&&t){t.busy=false;paintTools(m);return false}
  return false;
}

function toolsLabel(t,live){
  const n=t.calls.length,run=t.calls.find(c=>c.status==='running');
  if(live&&t.busy)return run?'Running '+run.name+'…':n?'Thinking about the results…':'Choosing tools…';
  const bad=t.calls.filter(c=>/^(error|invalid|empty)$/.test(c.status)).length,chk=t.calls.filter(c=>c.status==='partial').length;
  if(!n)return'No tool was needed';
  return'Used '+n+' tool'+(n===1?'':'s')+': '+[...new Set(t.calls.map(c=>c.name))].join(', ')+(bad?' · '+bad+' failed or empty':'')+(chk?' · '+chk+' to double-check':'')+(t.ground?.length?' · unverified figures':'');
}

function paintTools(m){
  const c=m.el?.querySelector('.toolcard');if(!c)return;const t=m.tools;
  if(!t){c.hidden=true;m._ts='';return}
  const live=!!m.live;if(!live)t.calls.forEach(x=>{if(x.status==='running'){x.status='error';x.notes=['stopped before it finished']}});
  const aps=live?(m.approvals||[]):[];const sig=JSON.stringify([t.busy&&live,t.calls.map(x=>[x.id,x.status,x.ms]),t.ground,aps.map(x=>x.id)]);if(sig===m._ts)return;m._ts=sig;
  c.hidden=false;c.classList.toggle('live',!!(t.busy&&live));
  const q=c.querySelector('.wq');q.textContent=aps.length?'Waiting for your approval…':toolsLabel(t,live);q.title=t.why||q.textContent;q.classList.toggle('shimmer',!!(t.busy&&live));
  const l=c.querySelector('.tool-l');l.innerHTML='';
  t.calls.forEach(x=>{
    const li=document.createElement('li'),h=document.createElement('div'),d=document.createElement('span'),nm=document.createElement('span'),st=document.createElement('span');
    h.className='tc';d.className='td '+(TOOL_DOT[x.status]||'mute');nm.className='tn';nm.textContent=x.name+'('+argText(x.args)+')';nm.title=nm.textContent;
    st.className='ts';st.textContent=(TOOL_WORD[x.status]||x.status)+(x.ms!=null&&x.status!=='running'?' · '+(x.ms<1000?x.ms+' ms':(x.ms/1000).toFixed(1)+' s'):'')+(x.via&&x.via!=='model'?' · '+(x.via==='intent'?'picked from your question':'automatic fallback'):'');
    h.append(d,nm,st);li.append(h);
    (x.notes||[]).forEach(n=>{const e=document.createElement('div');e.className='tnote '+(/^(error|invalid)$/.test(x.status)?'bad':'');e.textContent=n;li.append(e)});
    if(x.preview&&/^(ok|partial)$/.test(x.status)){const p=document.createElement('div');p.className='tprev';p.textContent=x.preview;li.append(p)}
    l.append(li);
  });
  let ap=c.querySelector('.tool-ap');if(!ap){ap=document.createElement('div');ap.className='tool-ap';l.after(ap)}
  ap.innerHTML='';aps.forEach(x=>ap.append(approvalBox(m,x)));
  const g=c.querySelector('.tool-note');g.hidden=!t.ground?.length;g.textContent=t.ground?.length?'These figures were not found in the tool results or the conversation, so check them before relying on them: '+t.ground.join(', '):'';
  if((aps.length||t.ground?.length||t.calls.some(x=>/^(error|invalid)$/.test(x.status)))&&!m._tt)c.classList.add('open');
}

Object.assign(CMDS,{tools:['d','Let the model pick and chain tools for this question']});
SUGG.push(['Use tools','/tools','Let the model look things up, calculate and check the results']);
HELP+='\n\n**Tools** — with *Tool orchestration* on (Settings), the model can look things up, run exact calculations and chain several tools for one answer: web, Wikipedia, places & weather, nature, your documents, memory, project files, calculator and clock. With the filesystem tools it can also read, search, create and edit files in your project folder, and with `terminal.run` run commands (for example Docker). Every write, edit and command waits for your Approve click on the tool card. The tool card above the answer shows each call and whether its result passed the checks; numbers in the answer that no tool returned are flagged. `/tools your question` hands the model every available tool for one message.';
