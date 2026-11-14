/* ---------- sending + smooth streaming ----------
   Tokens land in m.full. A rAF loop reveals them at an eased pace (faster when the backlog grows, snapped to word
   boundaries) so text flows steadily instead of arriving in bursts; markdown is re-rendered at most ~22x/s. */
async function send(raw,opts={}){
let text=$('#input').value.trim();
const typed=text;

if((!text&&!att.length)||busy)return;

const gi=text?imgIntent(text):(att.some(a=>a.thumb)?imgIntent(text):null);
if(gi)return sendImage(gi);

if(!text){
  text=att.some(a=>a.thumb)
    ?'What is in this image? Summarize it and tell me what we could do with it.'
    :'Read the attached file(s) and summarize them.';
}

SENT.push(text);
si=0;  if(raw!==true){text=await expand(text);if(text==null)return}
  $('#input').value='';grow();pop.hidden=true;
  const planOn=!opts.noPlan&&(planMode||planOnce);planOnce=false;
  const u={role:'user',content:text,full:text,files:att},m={role:'assistant',content:'',full:'',shown:0,stats:null,live:true,plan:planOn||undefined};
  msgs.push(u,m);addMsg(u);addMsg(m);setTitle();
  att=[];renderTray();setBusy(true);ctl=new AbortController();toBottom();
  const rOnce=reasonOnce;reasonOnce=false;
const rt=/^Continue exactly where you stopped/.test(text)?null:await routeStep(text,typed);

const doReason=!/^Continue exactly where you stopped/.test(text)&&
  (wantReason(text,planOn,rOnce)||!!rt?.reason);

const webReq=wantWeb(text,webOnce,typed,u.files);
webOnce=null;

const kbReq=wantKb(text,kbOnce);
kbOnce=null;  if(u.files.some(f=>typeof f.image==='string'&&f.image.length>1&&!f.desc)&&!await analyze(u,m,typed,doReason))return;
  const mem=$('#mem').value.trim(),seen=msgs.some(x=>x.files?.some(f=>f.desc)),
sys=[
  $('#sys').value.trim(),
  toolsPrompt(),
  formatPrompt(),
  todayLine(),
  (webOn||webReq)&&WEB_SYS,
  seen&&IMG_SYS,
  planOn&&PLAN_PROMPT,
  mem&&'Standing notes about the user (always respect them):\n'+mem
].filter(Boolean).join('\n\n');  const payload={messages:[...(sys?[{role:'system',content:sys}]:[]),...msgs.slice(0,-1).map(x=>({role:x.role,content:x.role==='user'?withFiles(x.content,x.files||[]):asst(x)}))],...params()};
  const t0=performance.now();let tf=0,n=0,last=t0,acc=0,lastR=0,dirty=false,fin=false,aborted=false,raf=0,done;
  const drained=new Promise(r=>done=r);
  const loop=ts=>{
    const dt=Math.min(80,ts-last);last=ts;
    const back=m.full.length-m.shown;
    if(back>0){
      acc+=(fin?Math.max(4000,back*8):Math.max(55,back*4.5))*dt/1000;const k=Math.floor(acc);
      if(k>0){acc-=k;let to=Math.min(m.full.length,m.shown+k);
        if(to<m.full.length&&!fin){const sp=m.full.slice(to,to+12).search(/\s/);if(sp>0)to+=sp}
        m.shown=to;m.content=m.full.slice(0,to);dirty=true}
    }
    if(dirty&&(ts-lastR>=45||fin)){dirty=false;lastR=ts;paint(m,true);
      if(tf&&n>1)$('#tps').textContent=((n-1)/((performance.now()-tf)/1000)).toFixed(1);$('#ctok').textContent=n}
    if(fin&&m.shown>=m.full.length){done();return}
    raf=requestAnimationFrame(loop);
  };
  raf=requestAnimationFrame(loop);
  const handle=ev=>{
    if(ev.type==='token'){if(!tf)tf=performance.now();n++;m.full+=ev.text}
    else if(ev.type==='error')m.full+='\n\n> **Error:** '+ev.message;
    else if(ev.type==='done'){m.stats=ev;if(ev.tps){hist.push(ev.tps);spark();$('#tps').textContent=ev.tps.toFixed(1)}
      $('#ttft').textContent=ev.ttft?.toFixed(2)??'–';$('#ptok').textContent=ev.prompt_tokens??'–';$('#ctok').textContent=ev.completion_tokens;ctx(ev.ctx_used||0)}
  };
  try{
    if(kbReq){const kr=await kbStep(m,text,kbReq);if(kr)addWeb(payload,kr)}
    if(webReq){const wr=await webStep(m,text,webReq);if(wr)addWeb(payload,wr)}
    if(doReason){const rr=await reasonStep(m,payload);if(rr.notes)addNotes(payload,rr.notes,rr.cut)}
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload),signal:ctl.signal});
    if(!r.ok)m.full+='\n\n> **Error:** '+(await r.text()).slice(0,200);
    else{const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
      for(;;){const{done:d,value}=await rd.read();if(d)break;buf+=dec.decode(value,{stream:true});
        let i;while((i=buf.indexOf('\n\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+2);if(line.startsWith('data: ')){try{handle(JSON.parse(line.slice(6)))}catch{}}}}}
  }catch(e){if(e.name==='AbortError'){aborted=true;m.stats=m.stats||{completion_tokens:n,stopped:true,ttft:tf?(tf-t0)/1000:null}}else m.full+='\n\n> **Connection error:** '+e.message}
  fin=true;
  if(aborted||document.hidden){m.shown=m.full.length;cancelAnimationFrame(raf);done()}
  await drained;cancelAnimationFrame(raf);
  m.content=m.full;m.live=false;paint(m,false);setBusy(false);save();refreshModels();
  if(!aborted&&webOn&&!webReq&&!m.web&&HEDGE.test(m.full)&&!localOnly(text,typed,u.files)&&msgs[msgs.length-1]===m){note('That answer sounded unsure about recent facts, so it is searching the web and answering again…',true);webOnce=makeQuery(text);regen(m);return}
  if(m.stats?.finish_reason==='length'){const b=document.createElement('button');b.type='button';b.innerHTML=ic('right',14)+'<span>Continue</span>';b.onclick=()=>{b.remove();$('#input').value='/continue';send()};m.el.querySelector('.acts').append(b)}
}
const asst=x=>x.gen?`[Generated ${x.gen.images?.length||0} image(s) with Stable Diffusion from this prompt: ${x.gen.prompt}]`:(split(x.content).ans||x.content);
const IMG_SYS='The user can attach images. A separate vision model reads each image first; its extracted description appears in the message under "Image:". Treat that description as what the picture contains and work from it. Never say you cannot see images; if a detail is missing from the description, say so.';
/* image step: coder out -> Qwen2.5-VL reads the image(s) -> VL out -> coder back in (all done by the server) */
async function analyze(u,m,typed,reasonNext){
  const todo=u.files.filter(f=>typeof f.image==='string'&&f.image.length>1&&!f.desc);
  const ph=m.el.querySelector('.foot .ph'),pill=$('#pilltx'),pill0=pill.textContent;
  const say=l=>{if(m.stop1){m.stop1();m.stop1=null}if(ph){ph.textContent=l}pill.textContent=l;$('#pill').className='pill swap'};
  const rest=()=>{pill.textContent=pill0;$('#pill').className='pill ok';todo.forEach(f=>f.busy=false)};
  let err=null,fin=false;
  try{
    const r=await fetch('/api/vision',{method:'POST',headers:HDR,body:JSON.stringify({images:todo.map(f=>({name:f.name,data:f.image})),hint:u.content.slice(0,600),keep_unloaded:!!reasonNext}),signal:ctl.signal});
    if(!r.ok){let d='';try{d=(await r.json()).detail}catch{}throw new Error(d||('server answered '+r.status))}
    const rd=r.body.getReader(),dec=new TextDecoder();let buf='';
    for(;;){const{done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});
      let i;while((i=buf.indexOf('\n\n'))>=0){const line=buf.slice(0,i);buf=buf.slice(i+2);if(!line.startsWith('data: '))continue;
        let ev;try{ev=JSON.parse(line.slice(6))}catch{continue}
        const f=todo[ev.index];
        if(ev.type==='stage')say(ev.label);
        else if(ev.type==='image_start'&&f){f.desc='';f.busy=true;paintImgs(u)}
        else if(ev.type==='token'&&f){f.desc+=ev.text;paintImgs(u)}
        else if(ev.type==='image_done'&&f){f.busy=false;f.desc=f.desc.trim()||'(the vision model returned no details for this image)';
          if(ev.finish_reason==='length')f.desc+='\n[details cut off at the vision token limit]';paintImgs(u)}
        else if(ev.type==='error')err=ev.message;
        else if(ev.type==='done')fin=true}}
    if(err)throw new Error(err);
    if(!fin)throw new Error('the vision step ended early');
  }catch(e){
    rest();paintImgs(u);endStatus(m);
    msgs.splice(msgs.indexOf(u),2);u.el.remove();m.el.remove();   // nothing was sent: put the message back in the composer
    att=u.files;renderTray();$('#input').value=typed;grow();setBusy(false);setTitle();if(!msgs.length)renderEmpty();
    note(e.name==='AbortError'?'Image reading stopped. Your message is back in the box.':'Image reading failed: '+e.message);
    return false;
  }
  rest();paintImgs(u);
  if(ph)m.stop1=ticker(ph,PHRASES,2600);   // back to the normal "Cooking…" ticker while the coder answers
  return true;
}
const note=(t,good)=>{const n=$('#note');n.textContent=t||'';n.classList.toggle('good',!!good)};
function withFiles(text,files){
  files=files.filter(f=>f.text||f.desc);if(!files.length)return text;
  const blk=(label,body)=>{const fence='`'.repeat((body.match(/`+/g)||[]).reduce((m,s)=>Math.max(m,s.length+1),3));return `${label}\n${fence}\n${body}\n${fence}`};
  return files.map(f=>f.desc?blk(`Image: ${f.name} (described by the vision model; you cannot see the picture, this extracted description is all you have)`,f.desc):blk(`File: ${f.name}`,f.text)).join('\n\n')+'\n\n'+text;
}
function renderTray(){
  const t=$('#tray');t.innerHTML='';t.hidden=!att.length;if(!att.length)return;
  att.forEach((a,i)=>{const c=document.createElement('span');c.className='chip';
    if(a.thumb){const im=document.createElement('img');im.className='th';im.alt='';im.src=a.thumb;c.append(im)}else c.innerHTML=ic('file',13);
    const lb=document.createElement('span');lb.textContent=a.thumb?a.name+(a.desc?' · read':' · image'):a.name+' · '+a.tokens.toLocaleString()+' tok'+(a.truncated?' (truncated)':'');c.append(lb);
    const x=document.createElement('button');x.type='button';x.innerHTML=ic('x',13);x.setAttribute('aria-label','Remove '+a.name);
    x.onclick=()=>{att.splice(i,1);renderTray()};c.append(x);t.append(c)});
  const txt=att.filter(a=>!a.thumb),ni=att.length-txt.length,tot=txt.reduce((s,a)=>s+a.tokens,0),n=info?.n_ctx||0,s=document.createElement('span');
  s.className='tot'+(n&&tot>n*.7?' warn':'');
  s.textContent=(txt.length?`${tot.toLocaleString()} of ${n.toLocaleString()} context tokens`+(n&&tot>n*.7?' – too large, remove files or raise --n-ctx':''):'')
    +(ni&&att.some(a=>a.thumb&&!a.desc)?(txt.length?'  ·  ':'')+'images are read by the vision model when you send (the coder is swapped out for a moment)':'');
  if(s.textContent)t.append(s);
}
function addAtt(f){if(!att.some(a=>a.name===f.name))att.push(f);renderTray()}
async function attachPath(rel,isDir){
  note();const r=await fetch((isDir?'/api/folder?path=':'/api/file?path=')+encodeURIComponent(rel));
  const d=await r.json();if(!r.ok){note(rel+': '+d.detail);return false}
  if(isDir){d.files.forEach(addAtt);note(`${d.files.length} files added`+(d.skipped?`, ${d.skipped} skipped (binary or over the limit)`:''),true)}else{addAtt(d);note('Added '+d.name,true)}
  return true;
}
const IMG_RE=/^image\/(png|jpe?g|gif|webp|bmp)$/,IMG_MAX=1280,MAX_IMGS=4;
const readURL=f=>new Promise((ok,no)=>{const r=new FileReader();r.onload=()=>ok(r.result);r.onerror=()=>no(r.error);r.readAsDataURL(f)});
const loadImg=src=>new Promise((ok,no)=>{const i=new Image();i.onload=()=>ok(i);i.onerror=()=>no(new Error('could not read this image'));i.src=src});
async function addPdf(f){
  if(f.size>20*1024*1024){note(f.name+': PDF is too large to attach (maximum 20 MB).');return}
  let r;try{r=await fetch('/api/pdf',{method:'POST',headers:{'Content-Type':'application/pdf','X-Cressy':'1'},body:f})}
  catch(e){note(f.name+': '+e.message);return}
  let d;try{d=await r.json()}catch{note(f.name+': Server returned an invalid response.');return}
  if(!r.ok){note(f.name+': '+(d.detail||'Could not read PDF'));return}
  addAtt({...d,name:f.name});note('Added '+f.name+' ('+d.pages+' pages read)',true);
}
async function addImage(f){
  if(!CAPS.vision&&!CAPS.image){note('Images need the Qwen2.5-VL models or a Stable Diffusion model: '+(CAPS.vision_why||'not found'));return}
  if(!CAPS.vision){note('Images need the Qwen2.5-VL models: '+(CAPS.vision_why||'not found'));return}
  if(att.filter(a=>a.thumb).length>=MAX_IMGS){note('Up to '+MAX_IMGS+' images per message');return}
  try{
    const raw=await readURL(f),img=await loadImg(raw),w=img.naturalWidth,h=img.naturalHeight,k=Math.min(1,IMG_MAX/Math.max(w,h));
    let url=raw;   // png/jpeg within the size limit go through untouched; everything else is re-encoded (webp/gif/bmp, or too big)
    if(k<1||!/^image\/(png|jpeg)$/.test(f.type)){
      const c=document.createElement('canvas');c.width=Math.max(1,Math.round(w*k));c.height=Math.max(1,Math.round(h*k));
      const x=c.getContext('2d');x.fillStyle='#fff';x.fillRect(0,0,c.width,c.height);x.drawImage(img,0,0,c.width,c.height);url=c.toDataURL('image/jpeg',.92);
    }
    const t=Math.min(1,200/Math.max(w,h)),c2=document.createElement('canvas');c2.width=Math.max(1,Math.round(w*t));c2.height=Math.max(1,Math.round(h*t));
    const x2=c2.getContext('2d');x2.fillStyle='#fff';x2.fillRect(0,0,c2.width,c2.height);x2.drawImage(img,0,0,c2.width,c2.height);
    const name=f.name&&f.name!=='image.png'?f.name:'pasted-'+Date.now().toString(36)+'.png';
    addAtt({name,image:url,thumb:c2.toDataURL('image/jpeg',.65),tokens:0});note('Added '+name+' (read when you send)',true);
  }catch(e){note((f.name||'image')+': '+e.message)}
}
async function addLocal(files){
  note();for(const f of files){
    if(IMG_RE.test(f.type)){await addImage(f);continue}
    if(f.type==='application/pdf'||/\.pdf$/i.test(f.name)){await addPdf(f);continue}
    let text=await f.text(),truncated=false;
    if(text.includes('\u0000'))note(f.name+': binary file, skipped');
    else{if(text.length>2e5){text=text.slice(0,2e5);truncated=true}
      const r=await fetch('/api/tokens',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({text})});
      addAtt({name:f.name,text,truncated,tokens:(await r.json()).tokens})}
  }
}
