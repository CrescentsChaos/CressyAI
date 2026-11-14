/* ================= images: Stable Diffusion generate + edit =================
   Server side: the coder writes the prompt, every LLM is unloaded, imagegen.py runs in its own process, the coder comes back. */
const IMGP=['Mixing pigments','Denoising','Dreaming in latents','Squinting at noise','Sketching pixels','Pushing pixels around','Developing the negative','Dabbing highlights','Blending colours','Painting happy little accidents','Sharpening details','Persuading noise to be a picture'];
function imgSettings(){
  const g=id=>$('#'+id).value,[w,h]=g('img_size').split('x').map(Number),sd=g('img_seed').trim();
  return{width:w||1024,height:h||1024,steps:+g('img_steps')||25,cfg:g('img_cfg')===''?7:+g('img_cfg'),sampler:g('img_sampler'),scheduler:g('img_sched'),
    seed:sd===''||isNaN(+sd)?-1:Math.trunc(+sd),count:Math.max(1,Math.min(4,Math.trunc(+g('img_count'))||1)),strength:+g('img_strength')||.6,
    enhance:g('img_enhance')==='1',negative:g('img_neg').trim()};
}
/* "/imagine ...", "/imgedit ...", or any plain message while Image mode is on */
function imgIntent(text){
  const mt=text.match(/^\/(imagine|img|draw|imgedit|retouch)\b\s*([\s\S]*)$/i);
  if(mt)return{kind:/^(imgedit|retouch)$/i.test(mt[1])?'edit':'gen',prompt:mt[2].trim()};
  if(!imgMode||text.startsWith('/'))return null;
  return{kind:att.some(a=>a.thumb)?'edit':'gen',prompt:text};
}
const lastGenImage=()=>{for(let i=msgs.length-1;i>=0;i--){const g=msgs[i].gen;if(g?.images?.length){const im=g.images[0];return{name:im.name,url:im.url,prompt:g.prompt,seed:im.seed}}}return null};
function paintEditChip(){
  const c=$('#editchip');c.hidden=!editFrom;c.innerHTML='';if(!editFrom)return;
  const im=document.createElement('img');im.alt='';im.src=editFrom.url;const t=document.createElement('span');t.textContent='Editing this image · describe the change';
  const x=document.createElement('button');x.type='button';x.innerHTML=ic('x',13);x.setAttribute('aria-label','Stop editing this image');x.onclick=()=>{editFrom=null;paintEditChip()};
  c.append(im,t,x);
}
function setImgMode(v){
  if(v&&!CAPS.image){toast('Image generation is unavailable: '+(CAPS.image_why||'not set up'),'bad');return}
  imgMode=v;$('#imgmode').classList.toggle('on',v);$('#imgmode').setAttribute('aria-pressed',v);
  $('#input').placeholder=v?'Image mode: describe the picture to make…  attach an image to edit it instead':DEFPH;
  if(!v&&planMode)setPlan(true);$('#input').focus();
}
$('#imgmode').onclick=()=>setImgMode(!imgMode);
function paintImgBtn(){const b=$('#imgmode');b.style.opacity=CAPS.image?'':'.5';if(!CAPS.image)b.title='Image generation is unavailable: '+(CAPS.image_why||'not set up')}
async function readSSE(resp,on){
  const rd=resp.body.getReader(),dec=new TextDecoder();let buf='';
  for(;;){const{done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});
    let i;while((i=buf.indexOf('\n\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+2);if(!line.startsWith('data: '))continue;let ev;try{ev=JSON.parse(line.slice(6))}catch{continue}on(ev)}}
}
async function sendImage({kind,prompt}){
  if(busy)return;
  if(!CAPS.image){toast('Image generation is unavailable: '+(CAPS.image_why||'not set up'),'bad');return}
  if(!prompt){toast(kind==='edit'?'Describe the change, e.g. /imgedit make it night':'Describe the image, e.g. /imagine a lighthouse at dawn','bad');return}
  let source=null,base='',files=[];
  if(kind==='edit'){
    const a=att.find(x=>x.thumb&&typeof x.image==='string'&&x.image.length>1);
    if(a){source={data:a.image};files=[a]}
    else{const g=editFrom||lastGenImage();if(!g){toast('Nothing to edit yet. Generate an image first, or attach one.','bad');return}source={name:g.name};base=g.prompt||''}
  }
  const typed=$('#input').value.trim();
  $('#input').value='';grow();pop.hidden=true;SENT.push(typed);si=0;
  if(files.length)att=att.filter(x=>!files.includes(x));renderTray();
  editFrom=null;paintEditChip();
  await startImage({kind,prompt,source,base_prompt:base},{userText:typed,files});
}
async function startImage(req,opt={}){
  if(busy)return;
  const r={...imgSettings(),...Object.fromEntries(Object.entries(req).filter(([,v])=>v!==undefined&&v!==null))};
  const u={role:'user',content:opt.userText,full:opt.userText,files:opt.files||[]};
  const g={kind:r.kind,prompt:r.prompt,negative:r.negative||'',images:[],source:r.source?.name?{name:r.source.name,url:'/generated/'+r.source.name}:null,
    params:{width:r.width,height:r.height,steps:r.steps,cfg:r.cfg,sampler:r.sampler,strength:r.kind==='edit'?r.strength:undefined,count:r.count}};
  const m={role:'assistant',content:'',full:'',stats:null,live:true,gen:g};
  msgs.push(u,m);addMsg(u);addMsg(m);setTitle();setBusy(true);ctl=new AbortController();toBottom();
  ctl.signal.addEventListener('abort',()=>{jpost('/api/image/cancel',{}).catch(()=>{})});
  const gp=m.gp={label:'Starting…',image:0,step:0,steps:0},pill=$('#pilltx');
  const ph=()=>m.el.querySelector('.foot .ph');
  const say=l=>{gp.label=l;if(m.stop1){m.stop1();m.stop1=null}const e=ph();if(e)e.textContent=l;pill.textContent=l;$('#pill').className='pill swap';repaint()};
  const resume=()=>{gp.label='Painting…';pill.textContent='Painting…';$('#pill').className='pill swap';const e=ph();if(e&&!m.stop1)m.stop1=ticker(e,IMGP,2600);repaint()};
  let pend=false;const repaint=()=>{if(!pend){pend=true;requestAnimationFrame(()=>{pend=false;paintGen(m)})}};
  let err=null,cancelled=false,fin=null;
  try{
    const body={mode:r.kind,prompt:r.prompt,negative:r.negative,base_prompt:r.base_prompt||'',width:r.width,height:r.height,steps:r.steps,cfg:r.cfg,
      sampler:r.sampler,scheduler:r.scheduler,seed:r.seed,count:r.count,strength:r.strength,enhance:r.enhance,source:r.source||null};
    const resp=await fetch('/api/image',{method:'POST',headers:HDR,body:JSON.stringify(body),signal:ctl.signal});
    if(!resp.ok){let d='';try{d=(await resp.json()).detail}catch{}throw new Error(d||('server answered '+resp.status))}
    await readSSE(resp,ev=>{
      if(ev.type==='stage')ev.stage==='paint'?resume():say(ev.label);
      else if(ev.type==='note')toast(ev.message);
      else if(ev.type==='prompt'){g.prompt=ev.prompt;g.negative=ev.negative||'';m.content=g.prompt;repaint()}
      else if(ev.type==='source'){g.source={name:ev.name,url:ev.url};repaint()}
      else if(ev.type==='progress'){if(ev.image!=null)gp.image=ev.image;gp.step=ev.step;gp.steps=ev.steps;repaint()}
      else if(ev.type==='image'){g.images.push({name:ev.name,url:ev.url,seed:ev.seed,w:ev.width,h:ev.height,black:!!ev.black});gp.step=0;gp.steps=0;repaint()}
      else if(ev.type==='error')err=ev.message;
      else if(ev.type==='cancelled')cancelled=true;
      else if(ev.type==='done')fin=ev;
    });
    if(!fin&&!err&&!cancelled)err='The image step ended early.';
  }catch(e){if(e.name==='AbortError')cancelled=true;else err=e.message}
  if(err)g.err=err;else if(cancelled&&!g.images.length)g.err='Stopped.';
  const bits=[];if(g.images.length)bits.push((CAPS.image_model||'Stable Diffusion').replace(/\.gguf$/i,''));
  if(fin)bits.push(fin.seconds+' s');if(cancelled&&g.images.length)bits.push('stopped');
  g.foot=bits.join('  ·  ');m.content=g.images.length?g.prompt:(g.err||'');m.stats=null;m.live=false;
  $('#pill').className='pill ok';paint(m,false);setBusy(false);save();refreshModels(true);
}
function imgRetry(m){   // the Retry button on a generated message: same prompt, new seed, replaces the last reply
  const g=m.gen,i=msgs.indexOf(m);if(busy||i<0)return;
  if(msgs[msgs.length-1]!==m){note('Only the last reply can be retried');return}
  const u=msgs[i-1];msgs.pop();m.el.remove();if(u?.role==='user'){msgs.pop();u.el.remove()}
  startImage(rerollReq(g),{userText:u?.content||('/imagine '+g.prompt),files:u?.files||[]});
}
const rerollReq=g=>({kind:g.kind,prompt:g.prompt,negative:g.negative,source:g.source?{name:g.source.name}:null,base_prompt:'',seed:-1,enhance:false,count:1,
  width:g.params.width,height:g.params.height,steps:g.params.steps,cfg:g.params.cfg,sampler:g.params.sampler,strength:g.params.strength});
function imgReroll(m){if(busy||!m.gen?.images?.length)return;startImage(rerollReq(m.gen),{userText:'Re-roll: '+m.gen.prompt})}
function imgVary(m,i){
  const g=m.gen,im=g.images[i];if(busy||!im)return;
  startImage({kind:'edit',prompt:g.prompt,negative:g.negative,source:{name:im.name},base_prompt:'',strength:.4,seed:-1,enhance:false,count:1,width:im.w,height:im.h,
    steps:g.params.steps,cfg:g.params.cfg,sampler:g.params.sampler},{userText:'Variation: '+g.prompt});
}
function imgEditFrom(m,i){
  const g=m.gen,im=g.images[i];if(!im)return;
  editFrom={name:im.name,url:im.url,prompt:g.prompt,seed:im.seed};paintEditChip();
  const t=$('#input');if(!/^\/(imgedit|retouch)\b/i.test(t.value)){t.value='/imgedit '+t.value.replace(/^\/\w+\s*/,'');grow()}
  t.focus();t.setSelectionRange(t.value.length,t.value.length);
}
const imgDownload=im=>{const a=document.createElement('a');a.href=im.url;a.download=im.name;document.body.append(a);a.click();a.remove()};
async function imgSave(im){
  try{const d=await post_('/api/image/save',{name:im.name});toast('Saved to '+d.path,'good');if(typeof ls==='function')ls(cwd,'replace')}catch(e){toast('Could not save: '+e.message,'bad')}
}
function imgRerollLast(){const m=[...msgs].reverse().find(x=>x.gen?.images?.length);if(!m){toast('No generated image yet','bad');return}imgReroll(m)}
function imgEditLast(){const g=lastGenImage();if(!g){toast('No generated image yet','bad');return}
  const m=[...msgs].reverse().find(x=>x.gen?.images?.length);imgEditFrom(m,0)}
function imgView(m,i){
  const g=m.gen;let k=i;const M=modal('Image');M.el.classList.add('lb');
  const img=document.createElement('img');M.body.append(img);
  const show=n=>{k=(n+g.images.length)%g.images.length;const im=g.images[k];img.src=im.url;img.alt=g.prompt;
    M.sub.textContent=`${im.w}×${im.h} · seed ${im.seed}`+(g.images.length>1?` · ${k+1}/${g.images.length}`:'')};
  M.btn('Download',()=>imgDownload(g.images[k]));M.btn('Edit this',()=>{M.close();imgEditFrom(m,k)});
  if(g.images.length>1){M.btn('‹ Previous',()=>show(k-1));M.btn('Next ›',()=>show(k+1))}
  const key=e=>{if(g.images.length>1&&(e.key==='ArrowLeft'||e.key==='ArrowRight')){e.preventDefault();show(k+(e.key==='ArrowLeft'?-1:1))}};
  addEventListener('keydown',key);M.onclose=()=>removeEventListener('keydown',key);show(i);
}
function giActs(m,i){
  const im=m.gen.images[i],d=document.createElement('div');d.className='gi-a';
  const mk=(icon,label,fn)=>{const b=document.createElement('button');b.type='button';b.title=label;b.setAttribute('aria-label',label);b.innerHTML=ic(icon,13)+'<span>'+label+'</span>';b.onclick=e=>{e.stopPropagation();fn()};d.append(b)};
  mk('expand','Enlarge',()=>imgView(m,i));mk('download','Download',()=>imgDownload(im));mk('pencil','Edit',()=>imgEditFrom(m,i));
  mk('spark','Vary',()=>imgVary(m,i));mk('refresh','Re-roll',()=>imgReroll(m));
  if(CAPS.write)mk('folder','Save',()=>imgSave(im));
  return d;
}
/* one card per message: a placeholder per expected image (with a live progress bar) that is swapped for the picture when it arrives */
function paintGen(m){
  const g=m.gen,p=g.params||{},col=m.el.querySelector('.col'),total=Math.max(1,m.live?(p.count||1):g.images.length);
  m.el.classList.add('genmsg');
  let box=m.gbox;
  if(!box||!box.isConnected){
    box=m.gbox=document.createElement('div');box.className='gen';
    box.innerHTML='<div class="gen-grid"></div><div class="gen-src" hidden></div><div class="gen-err" hidden></div><details class="gen-p" hidden><summary></summary><pre></pre></details>';
    col.insertBefore(box,col.querySelector('.body'));box._figs=[];
  }
  const grid=box.querySelector('.gen-grid');grid.className='gen-grid n'+Math.min(4,total);
  for(let i=0;i<total;i++){
    const im=g.images[i],state=im?'img':(m.live?'ph':'none');let f=box._figs[i];
    if(state==='none'){if(f){f.remove();box._figs[i]=null}continue}
    if(!f||f.dataset.st!==state||(im&&f.dataset.u!==im.url)){
      const nf=document.createElement('figure');nf.dataset.st=state;
      if(im){
        nf.className='gi';nf.dataset.u=im.url;nf.style.aspectRatio=im.w+'/'+im.h;
        const img=new Image();img.alt=g.prompt;img.src=im.url;img.onclick=()=>imgView(m,i);
        img.onerror=()=>{nf.className='gi gone';nf.textContent='This image file is no longer on disk.'};
        nf.append(img);
        if(im.black){const w=document.createElement('div');w.className='gi-w';w.textContent='The image is completely black. The SDXL VAE can overflow in half precision: start the server with --image-vae pointing at sdxl-vae-fp16-fix.safetensors.';nf.append(w)}
        nf.append(giActs(m,i));
      }else{
        nf.className='gi ph';nf.style.aspectRatio=(p.width||1)+'/'+(p.height||1);
        nf.innerHTML='<div class="gbar ind"><i></i></div><span class="glab"></span>';
      }
      if(f)f.replaceWith(nf);else{const next=box._figs.slice(i+1).find(Boolean);next?grid.insertBefore(nf,next):grid.append(nf)}
      box._figs[i]=nf;f=nf;
    }
    if(state==='ph'){
      const gp=m.gp||{},cur=i===(gp.image||0),bar=f.querySelector('.gbar'),lab=f.querySelector('.glab');
      const pct=cur&&gp.steps?Math.min(100,Math.round(gp.step/gp.steps*100)):0;
      bar.classList.toggle('ind',!pct);bar.firstChild.style.width=pct?pct+'%':'';
      const t=cur?(gp.steps?`Step ${gp.step} of ${gp.steps}`+(total>1?` · image ${i+1} of ${total}`:''):(gp.label||'Working…')):(i>(gp.image||0)?'Waiting for its turn':'');
      if(lab.textContent!==t)lab.textContent=t;
    }
  }
  while(box._figs.length>total)box._figs.pop()?.remove();
  const src=box.querySelector('.gen-src');src.hidden=!g.source;
  if(g.source&&src.dataset.u!==g.source.url){src.dataset.u=g.source.url;src.innerHTML='';const si_=new Image();si_.alt='';si_.src=g.source.url;si_.onerror=()=>si_.remove();const t=document.createElement('span');t.textContent='Edited from this image';src.append(si_,t)}
  const er=box.querySelector('.gen-err');er.hidden=!g.err;if(er.textContent!==(g.err||''))er.textContent=g.err||'';
  const d=box.querySelector('.gen-p');d.hidden=!g.prompt;
  if(g.prompt){const im0=g.images[0],bits=[];if(im0)bits.push('seed '+im0.seed+(g.images.length>1?'+':''),im0.w+'×'+im0.h);
    bits.push(p.steps+' steps',p.sampler);if(g.kind==='edit')bits.push('strength '+p.strength);
    const sm=d.querySelector('summary'),sv='Prompt  ·  '+bits.join(' · ');if(sm.textContent!==sv)sm.textContent=sv;
    const pr=d.querySelector('pre'),pv=g.prompt+(g.negative?'\n\nNegative: '+g.negative:'');if(pr.textContent!==pv)pr.textContent=pv}
}

/* ---------- reasoning: reasoner thinks (coder unloaded), coder answers with its notes ---------- */
let reasonOn=!!LS('gc.reason'),reasonOnce=false;
function setReason(v){reasonOn=v;$('#reason').classList.toggle('on',v);$('#reason').setAttribute('aria-pressed',v);LS('gc.reason',v)}
setReason(reasonOn);
$('#reason').onclick=()=>{
  const v=!reasonOn;
  if(v&&MS&&!MS.slots.reasoner.path){showTab('model');note('Choose a reasoning model in the Model tab first.');return}
  setReason(v);note(v?'Reason is on: the reasoning model thinks first, then the coder answers.':'',v);$('#input').focus();
};
const COMPLEX=/\b(why|how (do|can|should|would)|design|architect\w*|debug\w*|bug|error|fix|optimi[sz]e|refactor|implement\w*|trade-?offs?|compare|difference|best way|algorithm|plan)\b/i;
function wantReason(text,planOn,once){
  if(!reasonOn&&!once)return false;
  if(once||$('#rmode').value==='always')return true;
  return !!planOn||text.length>=160||text.includes('```')||COMPLEX.test(text);
}
function addNotes(payload,notes,cut){
  const last=payload.messages[payload.messages.length-1];if(!last||last.role!=='user')return;
  last.content+=`\n\n---\nPrivate planning notes from a separate reasoning model${cut?' (cut off at its token budget)':''}. Use what is correct, ignore what is not, and never mention or quote the notes:\n<notes>\n${notes.slice(0,8000)}\n</notes>`;
}
async function reasonStep(m,payload){
  const pill=$('#pilltx'),ph=()=>m.el.querySelector('.foot .ph');
  const say=l=>{if(m.stop1){m.stop1();m.stop1=null}const e=ph();if(e)e.textContent=l;pill.textContent=l;$('#pill').className='pill swap'};
  const rest=()=>{paintPill(true);const e=ph();if(e&&!m.stop1)m.stop1=ticker(e,PHRASES,2600)};
  let raw='',err=null,fin=null;
  const clean=()=>{let c=raw.replace(/^\s+/,'');if(c.length<7&&'<think>'.startsWith(c))return'';c=c.replace(/^<think>\s*/,'');const e=c.indexOf('</think>');return(e>=0?c.slice(0,e):c).trim()};
  const close=()=>{const n=clean();m.full=n?'<think>\n'+n+'\n</think>\n\n':''};
  const body={messages:payload.messages.filter(x=>x.role!=='system'),
    context:[$('#sys').value.trim(),$('#mem').value.trim()&&'Notes about the user:\n'+$('#mem').value.trim()].filter(Boolean).join('\n\n'),
    temperature:+$('#r_temperature').value,top_p:+$('#r_top_p').value,top_k:+$('#r_top_k').value,repeat_penalty:+$('#r_repeat_penalty').value,
    max_tokens:+$('#r_max_tokens').value,system:$('#r_sys').value.trim()};
  try{
    const r=await fetch('/api/reason',{method:'POST',headers:HDR,body:JSON.stringify(body),signal:ctl.signal});
    if(!r.ok){let d='';try{d=(await r.json()).detail}catch{}throw new Error(d||('server answered '+r.status))}
    const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){const{done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});
      let i;while((i=buf.indexOf('\n\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+2);if(!line.startsWith('data: '))continue;
        let ev;try{ev=JSON.parse(line.slice(6))}catch{continue}
        if(ev.type==='stage')say(ev.label);
        else if(ev.type==='token'){raw+=ev.text;const c=clean();if(c)m.full='<think>\n'+c}
        else if(ev.type==='error')err=ev.message;
        else if(ev.type==='done')fin=ev}}
    if(!fin&&!err)err='the reasoning step ended early';
  }catch(e){
    if(e.name==='AbortError'){close();rest();throw e}
    err=e.message;
  }
  close();rest();
  const notes=clean();
  if(err)note('Reasoning '+(notes?'was cut short: ':'skipped: ')+err);
  return{notes,cut:fin?.finish_reason==='length'};
}

