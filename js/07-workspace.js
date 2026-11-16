/* ================= workspace: files, terminal, plan, charts ================= */
const T3='```';
const HDR={'Content-Type':'application/json','X-Cressy':'1'};
const jpost=(u,b,o={})=>fetch(u,{method:'POST',headers:HDR,body:JSON.stringify(b),...o});
let CAPS={terminal:false,write:false,root:'',os:'',shell:'',vision:false,vision_why:''};
const base_=p=>p.split('/').pop();
const fenced=t=>{const f='`'.repeat((t.match(/`+/g)||[]).reduce((m,s)=>Math.max(m,s.length+1),3));return f+'text\n'+t+'\n'+f};
function toast(msg,kind){const t=document.createElement('div');t.className='toast '+(kind||'');t.setAttribute('role','status');t.textContent=msg;document.body.append(t);setTimeout(()=>t.remove(),kind==='bad'?6000:3500)}
/* lazy-loaded libraries (cdnjs, pinned) */
const LIB={chart:'https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.1/chart.umd.min.js',zip:'https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js',mermaid:'https://cdnjs.cloudflare.com/ajax/libs/mermaid/12.0.0/mermaid.min.js',katex:'https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.js'},libs={};
const lib=k=>libs[k]||(libs[k]=new Promise((res,rej)=>{const s=document.createElement('script');s.src=LIB[k];s.onload=res;s.onerror=()=>{delete libs[k];s.remove();rej(new Error('Could not load the '+k+' library from cdnjs.cloudflare.com. Check your internet connection.'))};document.head.append(s)}));
/* small modal */
function modal(title){
  const d=document.createElement('div');d.className='pv';
  d.innerHTML='<div class="pvb mdl" role="dialog" aria-modal="true"><div class="pvh"><b></b><span></span><button type="button" aria-label="Close"></button></div><div class="mbody"></div><div class="mfoot"></div></div>';
  const M={el:d,title:d.querySelector('b'),sub:d.querySelector('.pvh span'),body:d.querySelector('.mbody'),foot:d.querySelector('.mfoot'),onclose:null};
  M.title.textContent=title;d.querySelector('.pvh button').innerHTML=ic('x',16);
  const k=e=>{if(e.key==='Escape'){e.stopPropagation();M.close()}};
  M.close=()=>{if(!d.isConnected)return;d.remove();removeEventListener('keydown',k,true);M.onclose&&M.onclose()};
  addEventListener('keydown',k,true);d.querySelector('.pvh button').onclick=M.close;d.onmousedown=e=>{if(e.target===d)M.close()};
  M.btn=(label,fn,cls,title)=>{const b=document.createElement('button');b.type='button';b.textContent=label;if(cls)b.className=cls;if(title)b.title=title;b.onclick=()=>fn(b);M.foot.append(b);return b};
  document.body.append(d);return M;
}
const showTab=n=>{tab(n);if(mob()){app.classList.remove('lopen');app.classList.add('ropen')}else app.classList.remove('noright')};

/* ---------- workspace state: files the model produced this chat ---------- */
let WS=new Map();   // path -> {orig:string|null, cur, saved, crlf}
const resetWS=()=>{WS=new Map();paintChanges();editFrom=null;if(window.paintEditChip)paintEditChip()};
function recordWS(path,before,after,saved,crlf){
  const e=WS.get(path);WS.set(path,{orig:e?e.orig:before,cur:after,saved:!!saved,crlf:e?e.crlf:!!crlf});paintChanges();
}
async function getBase(path){
  const e=WS.get(path);if(e)return{text:e.cur,crlf:e.crlf};
  try{
    const r=await fetch('/api/file?path='+encodeURIComponent(path));
    if(r.ok){const d=await r.json();
      if(d.truncated)return{err:path+' is too large to edit safely. Ask for a full file: block instead.'};
      if(d.lossy)return{err:path+' is not valid UTF-8, so it cannot be edited safely.'};
      return{text:d.text.replace(/\r\n/g,'\n'),crlf:d.text.includes('\r\n')}}
    if(r.status!==404){const d=await r.json().catch(()=>({}));return{err:path+': '+(d.detail||'cannot be read')}}
  }catch{}
  const f=[...att,...msgs.flatMap(m=>m.files||[])].find(f=>f.text&&!f.truncated&&(f.name===path||f.name===base_(path)));
  if(f)return{text:f.text.replace(/\r\n/g,'\n'),crlf:f.text.includes('\r\n')};
  return{text:null,crlf:false};
}
async function blockChange(pre){
  const k=pre.dataset.kind,path=pre.dataset.path,code=pre.querySelector('code'),raw=code.dataset.raw??code.textContent;
  const b=await getBase(path);if(b.err)return{err:b.err};
  if(k==='file')return{path,before:b.text,after:raw.replace(/\s+$/,'')+'\n',crlf:b.crlf};
  const pe=parseEdits(raw);
  if(!pe.seen)return{err:'No <<<<<<< SEARCH / ======= / >>>>>>> REPLACE markers found in this edit block.'};
  if(!pe.complete)return{err:'This edit block is incomplete (the reply may have been cut off).'};
  if(b.text===null)return{err:path+' does not exist in the project or your attachments. Attach it with @'+path+' or ask for a file: block.'};
  const r=applyEdits(b.text,pe.edits);
  if(!r.applied&&!r.already)return{err:r.errors.join(' ')||'Nothing to apply.'};
  return{path,before:b.text,after:r.text,crlf:b.crlf,warnings:r.errors,already:r.already};
}
async function saveFile(path,text){
  const r=await jpost('/api/write',{path,text}),d=await r.json().catch(()=>({}));
  if(!r.ok)throw new Error(d.detail||'HTTP '+r.status);return d;
}
const asDisk=(t,crlf)=>crlf?t.replace(/\n/g,'\r\n'):t;
function setBadge(pre,text){const bar=pre.querySelector('.codebar');if(!bar)return;let s=bar.querySelector('.stat');if(!s){s=document.createElement('span');s.className='stat';bar.insertBefore(s,bar.lastChild)}s.textContent='✓ '+text}
function reviewChange(ch,o={}){
  const M=modal(ch.path),isNew=ch.before===null,ops=diffOps(ch.before??'',ch.after),st=diffStat(ops);
  M.sub.textContent=isNew?'New file · '+ch.after.split('\n').length+' lines':st.add||st.del?`+${st.add}  −${st.del}`:'no changes';
  if(ch.warnings?.length){const w=document.createElement('div');w.className='warn';w.textContent='Some edits could not be applied: '+ch.warnings.join(' ');M.body.append(w)}
  if(ch.already){const w=document.createElement('div');w.className='warn good';w.textContent=ch.already+' edit(s) look already applied.';M.body.append(w)}
  const rows=isNew?ops.filter(x=>x.t==='+'):collapseOps(ops,3);
  if(!isNew&&!st.add&&!st.del){const p=document.createElement('p');p.textContent='The file already matches this change.';M.body.append(p)}
  else{const box=document.createElement('div');box.className='diff';
    rows.slice(0,700).forEach(x=>{const l=document.createElement('div');l.className='dl '+({'+':'a','-':'d','~':'g',' ':''}[x.t]);l.textContent=(x.t==='~'?'… '+x.s:(x.t===' '?'  ':x.t+' ')+x.s);box.append(l)});
    if(rows.length>700){const l=document.createElement('div');l.className='dl g';l.textContent='… '+(rows.length-700)+' more lines';box.append(l)}
    M.body.append(box)}
  const err=document.createElement('div');err.className='warn bad';err.hidden=true;M.body.append(err);
  const done=how=>{recordWS(ch.path,ch.before,ch.after,how==='save',ch.crlf);o.pre&&setBadge(o.pre,how==='save'?'Saved to project':how==='download'?'Downloaded':'Added to Changes');M.close()};
  const sv=M.btn('Save to project',async()=>{try{const r=await saveFile(ch.path,asDisk(ch.after,ch.crlf));toast((r.created?'Created ':'Saved ')+r.path,'good');FILES=null;ls(cwd,'none');done('save')}catch(e){err.hidden=false;err.textContent=e.message}},'primary');
  if(!CAPS.write){sv.disabled=true;sv.title='Writing is off (--read-only or non-loopback host). Use Download.'}
  M.btn('Download',()=>{dl_(base_(ch.path),asDisk(ch.after,ch.crlf));done('download')});
  if(!o.view)M.btn('Add to Changes',()=>done('stage'));
  M.btn('Close',M.close);
}
/* ---------- fenced-block actions ---------- */
const streamingGuard=pre=>{if(pre.closest('.msg.streaming')){toast('Wait for the reply to finish first');return true}return false};
async function actFile(pre,mode){
  if(streamingGuard(pre))return;
  const code=pre.querySelector('code'),path=pre.dataset.path;
  if(mode==='download'){const after=(code.dataset.raw??code.textContent).replace(/\s+$/,'')+'\n',b=await getBase(path);
    dl_(base_(path),asDisk(after,b.crlf));recordWS(path,b.err?null:b.text,after,false,b.crlf);setBadge(pre,'Downloaded');return}
  const ch=await blockChange(pre);if(ch.err)return toast(ch.err,'bad');reviewChange(ch,{pre});
}
/* ---------- Changes tab ---------- */
function paintChanges(){
  const box=$('#chg');if(!box)return;box.innerHTML='';
  const n=WS.size,bd=$('#bd-changes');if(bd)bd.textContent=n||'';
  ['zipall','saveall','clrchg'].forEach(i=>$('#'+i).disabled=!n);
  if(!n){box.innerHTML='<div class="nohist">Files the model creates or edits appear here after you review or download them, so you can grab them all at once.</div>';return}
  WS.forEach((e,p)=>{
    const st=diffStat(diffOps(e.orig??'',e.cur)),r=document.createElement('div');r.className='chrow';
    r.innerHTML=`<span class="p"></span><span class="tag${e.saved?' ok':''}">${e.orig===null?'new':'edited'}${e.saved?' · saved':''}</span><em class="st">+${st.add} −${st.del}</em><span class="a"></span>`;
    r.querySelector('.p').textContent=p;r.querySelector('.p').title=p;
    const mk=(t,f)=>{const b=document.createElement('button');b.type='button';b.textContent=t;b.onclick=f;r.querySelector('.a').append(b)};
    mk('Diff',()=>reviewChange({path:p,before:e.orig,after:e.cur,crlf:e.crlf},{view:true}));
    mk('Download',()=>dl_(base_(p),asDisk(e.cur,e.crlf)));
    mk('Save',async()=>{try{await saveFile(p,asDisk(e.cur,e.crlf));e.saved=true;toast('Saved '+p,'good');FILES=null;ls(cwd,'none');paintChanges()}catch(x){toast(x.message,'bad')}});
    if(!CAPS.write)r.querySelector('.a').lastChild.disabled=true;
    mk('×',()=>{WS.delete(p);paintChanges()});r.querySelector('.a').lastChild.title='Remove from list';
    box.append(r)});
}
$('#clrchg').onclick=resetWS;
$('#zipall').onclick=async()=>{
  try{await lib('zip');const z=new JSZip();WS.forEach((e,p)=>z.file(p,asDisk(e.cur,e.crlf),{createFolders:false}));dl_('cressyai-files.zip',await z.generateAsync({type:'blob'}))}
  catch(e){toast(e.message+' Downloading the files one by one instead.','bad');WS.forEach((e2,p)=>dl_(base_(p),asDisk(e2.cur,e2.crlf)))}
};
$('#saveall').onclick=async()=>{
  const todo=[...WS].filter(([,e])=>!e.saved);if(!todo.length)return toast('Everything is already saved');
  if(!confirm('Write '+todo.length+' file(s) into the project folder?\n\n'+todo.map(([p,e])=>(e.orig===null?'+ ':'~ ')+p).join('\n')))return;
  let ok=0;for(const [p,e] of todo){try{await saveFile(p,asDisk(e.cur,e.crlf));e.saved=true;ok++}catch(x){toast(p+': '+x.message,'bad')}}
  paintChanges();FILES=null;ls(cwd,'none');if(ok)toast('Saved '+ok+' file(s)','good');
};
