/* ---------- models: manual load / unload / tweak, and the reasoning step ---------- */
let MS=null,MOP=false,msig='';const MERR={},MOPEN={};
const SLOT_INFO={coder:['Coder','Answers your messages and writes the code.'],reasoner:['Reasoner','Thinks the task through first. The coder is unloaded while it runs.'],vision:['Vision','Reads attached images. It loads only while an image is being read.'],
  image:['Image','Makes and edits pictures (Stable Diffusion). It loads only while an image is being made; the coder is unloaded meanwhile.']};
async function post_(url,body){
  const r=await fetch(url,{method:'POST',headers:HDR,body:JSON.stringify(body)});let d={};try{d=await r.json()}catch{}
  if(!r.ok)throw new Error(d.detail||('server answered '+r.status));return d;
}
function paintPill(force){
  if((busy||MOP)&&!force)return;
  const p=$('#pill'),tx=$('#pilltx'),st=MS?.state||info?.model_state||'none',sl=MS?.slots;
  if(st==='reasoner'){tx.textContent='Reasoner · '+(sl?.reasoner.name||'');p.className='pill ok';return}
  if(st==='vision'){tx.textContent='Vision · '+(sl?.vision.name||'');p.className='pill swap';return}
  if(st==='image'){tx.textContent='Image · '+(sl?.image.name||'');p.className='pill swap';return}
  if(st==='coder'){const md=info?.metadata||{},q=FT[md['general.file_type']]||md['general.file_type'];
    tx.textContent=(md['general.name']||info?.file||sl?.coder.name||'coder')+(q?' · '+q:'');p.className='pill ok';return}
  if(!MS&&!info)return;
  tx.textContent=sl?.coder.path?'No model loaded':'No model selected';p.className='pill';
}
async function refreshModels(force){
  let d;try{d=await(await fetch('/api/models')).json()}catch{return}
  if(!d||!d.slots)return;
  MS=d;
  if(d.state==='coder'&&info&&(!info.loaded||info.file!==d.slots.coder.name)&&refreshInfo)refreshInfo().catch(()=>{});
const sig=JSON.stringify([
  d.state,
  d.busy,
  d.available.length,
  Object.values(d.slots).map(s=>[s.loaded,s.path,s.dirty,s.cfg,s.available]),
  (d.image_models||[]).length,
  d.routing,
  d.quantize,
  Math.round((d.ram?.free||0)/2e9)
]);  if((force||sig!==msig)&&(force||!$('#slots').contains(document.activeElement))){msig=sig;renderSlots()}
  paintPill();
}
const fSize=n=>n>=1e9?(n/1e9).toFixed(1)+' GB':(n/1e6).toFixed(0)+' MB';
const sameP=(a,b)=>String(a||'').toLowerCase()===String(b||'').toLowerCase();
/* every model family with its quantization variants; the loaded file is added if it is outside the scanned folders */
function famList(s){
  const fams=(MS.families||[]).map(f=>({...f,variants:[...f.variants]}));
  if(s.path&&!fams.some(f=>f.variants.some(v=>sameP(v.path,s.path)))){const nm=s.name||s.path.split(/[\\/]/).pop();fams.push({family:nm.replace(/\.gguf$/i,''),key:nm.toLowerCase(),variants:[{name:nm,path:s.path,size:s.size||0,quant:'',bpw:0,note:''}]})}
  return fams;
}
function fitTag(v){const f=MS.ram?.free;if(!f||!v.size)return'';return v.size*1.15>f?' · may not fit in RAM':''}
function quantOpts(f,path){return f.variants.map(v=>`<option value="${esc(v.path)}"${sameP(v.path,path)?' selected':''}>${esc(v.quant||'file')} · ${fSize(v.size)}${v.bpw?' · ≈'+v.bpw+' bits/weight':''}${fitTag(v)}</option>`).join('')}
function quantNote(v){return v?esc([v.note,v.quant&&v.bpw?'Bigger numbers = closer to the original model but more memory and slower.':''].filter(Boolean).join(' ')):''}
function quantPick(k,s,wanted){
  const fams=famList(s);if(!fams.length)return'<div class="mc-n">No .gguf files found yet.</div>';
  fams.sort((a,b)=>k==='reasoner'?((fStar(b,wanted)-fStar(a,wanted))||a.family.localeCompare(b.family)):a.family.localeCompare(b.family));
  const cur=fams.find(f=>f.variants.some(v=>sameP(v.path,s.path)))||fams[0],v0=cur.variants.find(v=>sameP(v.path,s.path))||cur.variants[0];
  return `<select data-fam aria-label="${SLOT_INFO[k][0]} model">${s.path?'':'<option value="">Choose a model…</option>'}${fams.map(f=>`<option value="${esc(f.key)}"${f===cur&&s.path?' selected':''}>${k==='reasoner'&&fStar(f,wanted)?'★ ':''}${esc(f.family)}${f.variants.length>1?` · ${f.variants.length} quantizations`:''}</option>`).join('')}</select>
   <select data-pick aria-label="${SLOT_INFO[k][0]} quantization">${quantOpts(cur,s.path)}</select><div class="mc-n" data-qn>${quantNote(v0)}</div>`;
}
const fStar=(f,wanted)=>f.variants.some(v=>wanted.has(v.name.toLowerCase()))?1:0;
$('#slots').addEventListener('change',e=>{
  const fs=e.target.closest('[data-fam]');if(fs){
    const card=fs.closest('.mc'),k=card.dataset.slot,s=MS.slots[k],f=famList(s).find(x=>x.key===fs.value);if(!f)return;
    const cq=(famList(s).find(x=>x.variants.some(v=>sameP(v.path,s.path)))?.variants.find(v=>sameP(v.path,s.path))||{}).quant;
    const want=f.variants.find(v=>v.quant===cq)||f.variants.slice().sort((a,b)=>Math.abs((a.bpw||4.9)-4.9)-Math.abs((b.bpw||4.9)-4.9))[0];
    const pk=card.querySelector('[data-pick]');pk.innerHTML=quantOpts(f,want.path);pk.value=want.path;card.querySelector('[data-qn]').innerHTML=quantNote(want);return}
  const pk=e.target.closest('[data-pick]');if(pk){const card=pk.closest('.mc'),s=MS.slots[card.dataset.slot],v=famList(s).flatMap(f=>f.variants).find(v=>sameP(v.path,pk.value));card.querySelector('[data-qn]').innerHTML=quantNote(v)}
});
function slotCard(k){
  if(k==='image')return imageCard();
  const s=MS.slots[k],[title,sub]=SLOT_INFO[k],canManage=CAPS.can_manage!==false,wanted=new Set((MS.wanted||[]).map(x=>x.toLowerCase()));
  let badge,cls='';
  if(k==='vision'){badge=s.loaded?'In use':s.available?'On demand':'Not found';cls=s.loaded?'on':''}
  else{badge=s.loaded?'Loaded':s.path?'Unloaded':'None chosen';cls=s.loaded?'on':''}
  let pick='';
if(k!=='vision'){
  pick=quantPick(k,s,wanted);
}

const note=k==='vision'
  ?(s.available?`${esc(s.name)} + ${esc(s.mmproj)}`:esc(s.why||''))
  :    k==='reasoner'&&!s.path?esc(s.why||'No reasoning model chosen yet.'):'';
  const c=s.cfg,T=(f,label,extra='')=>`<label class="f">${label}<input type="number" data-f="${f}" value="${c[f]??''}" ${extra}></label>`;
  const btns=k==='vision'?'':`<div class="btns"><button type="button" data-act="load" class="primary">${s.loaded?'Switch / load':'Load'}</button>`+
    `<button type="button" data-act="unload"${s.loaded?'':' disabled'}>Unload</button><button type="button" data-act="reload"${s.loaded?'':' disabled'}>Reload</button></div>`;
  return `<div class="mc" data-slot="${k}"><div class="mc-h">${title}<small>${esc(s.loaded&&s.n_ctx_loaded?s.n_ctx_loaded.toLocaleString()+' ctx':'')}</small><span class="bdg ${cls}">${badge}</span></div>
   <div class="mc-n">${sub}${note?'<br>'+note:''}</div>${canManage?pick+btns:''}
   ${s.dirty?'<div class="warn">Saved settings differ from the loaded model. Reload to apply them.</div>':''}
   ${MERR[k]?`<div class="err">${esc(MERR[k])}</div>`:''}
   ${canManage?`<details${MOPEN[k]?' open':''}><summary>Tweak settings</summary>
    ${T('n_ctx','Context size (tokens)','min="512" step="512"')}${T('n_gpu_layers','GPU layers (-1 = all)','min="-1"')}${T('n_threads','CPU threads (0 = auto)','min="0"')}${T('n_batch','Batch size (0 = default)','min="0" step="64"')}
    <label class="f">Flash attention<select data-f="flash_attn"><option value="0"${c.flash_attn?'':' selected'}>Off</option><option value="1"${c.flash_attn?' selected':''}>On</option></select></label>
    ${k==='vision'?'':`<label class="f t">Chat format<input type="text" data-f="chat_format" value="${esc(c.chat_format||'')}" placeholder="auto (from the GGUF)"></label>`}
    <div class="btns"><button type="button" data-act="save">Save</button>${k==='vision'?'':'<button type="button" data-act="apply" class="primary">Save &amp; reload</button>'}</div>
    ${k==='reasoner'?'<div class="mc-n">Reasoning sampling, token budget and instructions: Settings → Reasoning.</div>':''}</details>`:''}</div>`;
}
function renderSlots(){
  if(!MS)return;
  $('#slots').innerHTML=['coder','reasoner','vision','image'].map(slotCard).join('');
  const n=MS.available.length;
  $('#mdirs').textContent=(n?n+' model file'+(n>1?'s':'')+' found in ':'No .gguf files found in ')+MS.dirs.join(' · ')+'.  ★ = looked for first as the reasoner.';
  $$('#slots details').forEach(d=>d.addEventListener('toggle',()=>{MOPEN[d.closest('.mc').dataset.slot]=d.open}));
  paintAuto();paintQuant();paintBench();
}
const readCfg=card=>{const o={};card.querySelectorAll('[data-f]').forEach(i=>{const f=i.dataset.f;o[f]=f==='flash_attn'?i.value==='1':f==='chat_format'?i.value.trim():+i.value});return o};
async function manage(k,label,fn){
  if(MOP||busy){MERR[k]='Wait for the current job to finish (or press Stop) first.';renderSlots();return}
  MOP=true;MERR[k]='';$('#pill').className='pill swap';$('#pilltx').textContent=label;
  $$('#slots button').forEach(b=>b.disabled=true);
  try{MS=await fn()}catch(e){MERR[k]=e.message}
  MOP=false;await refreshModels(true);if(refreshInfo)refreshInfo().catch(()=>{});
}
$('#slots').addEventListener('click',e=>{
  const b=e.target.closest('[data-act]');if(!b)return;
const card=b.closest('.mc'),
  k=card.dataset.slot,
  act=b.dataset.act,
  s=MS.slots[k],
  sel=card.querySelector('[data-pick]'),
  path=sel?.value||s.path,
  nm=(path
    ?path.split(/[\\/]/).pop().replace(/\.gguf$/i,'')
    :(sel?.selectedOptions[0]?.textContent||s.name||'model')
      .replace(/^★ /,'')
      .replace(/ · .*$/,''));  if(act==='load'){if(!path){MERR[k]='Choose a model file first.';renderSlots();return}
    manage(k,'Loading '+nm+'…',()=>post_('/api/model/load',{slot:k,path}))}
  else if(act==='unload')manage(k,'Unloading '+nm+'…',()=>post_('/api/model/unload',{slot:k}));
  else if(act==='reload')manage(k,'Reloading '+nm+'…',()=>post_('/api/model/load',{slot:k,force:true}));
  else if(act==='save')manage(k,'Saving settings…',()=>post_('/api/model/config',{slot:k,...readCfg(card)}));
  else if(act==='apply')manage(k,'Reloading '+nm+'…',()=>post_('/api/model/config',{slot:k,...readCfg(card),reload:true}));
});

function imageCard(){
  const s=MS.slots.image,[title,sub]=SLOT_INFO.image,canManage=CAPS.can_manage!==false;
  const badge=s.loaded?'In use':s.available?'On demand':'Not ready',cls=s.loaded?'on':'';
  const list=[...(MS.image_models||[])];if(s.path&&!list.some(m=>m.path.toLowerCase()===s.path.toLowerCase()))list.push({name:s.name,path:s.path,size:s.size});
  const pick=canManage&&list.length?'<select data-pick-img aria-label="Image model file">'+list.map(m=>`<option value="${esc(m.path)}"${m.path.toLowerCase()===(s.path||'').toLowerCase()?' selected':''}>${esc(m.name)}${m.size?' · '+fSize(m.size):''}</option>`).join('')+'</select>':'';
  return `<div class="mc" data-slot="image"><div class="mc-h">${title}<span class="bdg ${cls}">${badge}</span></div>
   <div class="mc-n">${sub}${s.available?'':'<br>'+esc(s.why||'')}${s.vae?'<br>VAE: '+esc(s.vae):''}</div>${pick}
   ${MERR.image?`<div class="err">${esc(MERR.image)}</div>`:''}<div class="mc-n">Size, steps, sampler and edit strength: Settings → Images.</div></div>`;
}
$('#slots').addEventListener('change',e=>{const sel=e.target.closest('[data-pick-img]');if(!sel)return;
  manage('image','Choosing '+(sel.selectedOptions[0]?.textContent||'image model').replace(/ · .*$/,'')+'…',()=>post_('/api/image/model',{path:sel.value}))});

/* startup: first model refresh + polling (kept here because refreshModels is defined in this file) */
refreshModels(true);setInterval(()=>{if(!document.hidden)refreshModels()},2500);
