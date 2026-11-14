/* ---------- flicker-free markdown: build off-screen, touch only what changed ---------- */
function healMd(t){
  const fences=(t.match(/^ {0,3}(```|~~~)/gm)||[]).length;if(fences%2)return t;
  const k=t.lastIndexOf('\n\n'),cut=k<0?0:k+2,head=t.slice(0,cut),tail=t.slice(cut);
  let x=tail;if((x.replace(/```/g,'').match(/`/g)||[]).length%2)x+='`';
  if((x.match(/\*\*/g)||[]).length%2)x+='**';
  return head+x;
}
function bar(pre,code){
  const k=classify((code.className.match(/language-(\S+)/)||[])[1]||''),lang=k.lang,raw=()=>code.dataset.raw??code.innerText;
  pre.dataset.kind=k.kind;if(k.path)pre.dataset.path=k.path;
  if(k.kind==='file'||k.kind==='edit')code.className='language-'+(k.kind==='edit'?'plaintext':k.lang);
  const bar=document.createElement('div');bar.className='codebar';
  const s=document.createElement('span');s.textContent=k.kind==='file'?k.path:k.kind==='edit'?'edit · '+k.path:lang;
  const mk=(icon,text,fn,title)=>{const b=document.createElement('button');b.type='button';b.innerHTML=ic(icon,13)+text;if(title)b.title=title;b.onclick=()=>fn(b);return b};
  const cp=mk('copy','Copy',b=>{navigator.clipboard.writeText(raw());b.innerHTML=ic('check',13)+'Copied';setTimeout(()=>b.innerHTML=ic('copy',13)+'Copy',1200)});
  const g=document.createElement('span');g.style.display='flex';g.style.gap='6px';
  if(k.kind==='file'){
    g.append(mk('download','Save…',()=>actFile(pre,'save'),'Review the change and save it into the project folder'),mk('download','Download',()=>actFile(pre,'download'),'Download this file'));
    if(/\.(html?|svg)$/i.test(k.path))g.prepend(mk('right','Preview',()=>preview(/svg$/i.test(k.path)?'svg':'html',raw())));
  }else if(k.kind==='edit')g.append(mk('pencil','Review edit',()=>actFile(pre,'save'),'Show the diff, then save or download the edited file'));
  else{
    g.append(mk('wrap','Wrap',()=>pre.classList.toggle('wrap'),'Toggle line wrapping'));
    g.append(mk('download','Save',()=>dl_('snippet.'+(EXT[lang]||'txt'),raw()),'Download as a file'));
    if(/^(html|svg|js|javascript)$/.test(lang))g.prepend(mk('right',/^js|^javascript/.test(lang)?'Run':'Preview',()=>preview(lang,raw())));
    if(k.kind==='term')g.prepend(mk('right','Run',()=>runFromBlock(pre),'Run in the project folder'));
  }
  g.append(cp);bar.append(s,g);pre.prepend(bar);
}
const sameNode=(x,y)=>x.nodeType===y.nodeType&&(x.nodeType!==1||x.tagName===y.tagName);
function syncAttrs(x,y){
  for(const a of [...x.attributes])if(!y.hasAttribute(a.name))x.removeAttribute(a.name);
  for(const a of [...y.attributes])if(x.getAttribute(a.name)!==a.value)x.setAttribute(a.name,a.value);
}
function syncPre(x,y){
  if(x.dataset.kind!==y.dataset.kind||x.dataset.path!==y.dataset.path){x.replaceWith(y);return}
  const xc=x.querySelector('code'),yc=y.querySelector('code');
  if(!xc||!yc){x.replaceWith(y);return}
  if(xc.dataset.raw!==yc.dataset.raw){
    xc.className=yc.className;xc.textContent=yc.dataset.raw;xc.dataset.raw=yc.dataset.raw;
    delete xc.dataset.hl;xc.removeAttribute('data-highlighted');
    const l=x.querySelector('.codebar span'),nl=y.querySelector('.codebar span');if(l&&nl&&l.textContent!==nl.textContent)l.textContent=nl.textContent;
  }
}
function morph(dst,src){
  const a=[...dst.childNodes],b=[...src.childNodes];
  for(let i=0;i<Math.max(a.length,b.length);i++){
    const x=a[i],y=b[i];
    if(!y){x.remove();continue}
    if(!x){dst.append(y);continue}
    if(!sameNode(x,y)){x.replaceWith(y);continue}
    if(x.nodeType!==1){if(x.data!==y.data)x.data=y.data;continue}
    if(x.classList.contains('math')||y.classList.contains('math')){if(x.classList.contains('math')&&y.classList.contains('math')){if(x.dataset.tex!==y.dataset.tex||x.dataset.d!==y.dataset.d)x.replaceWith(y)}else x.replaceWith(y);continue}
    if(x.classList.contains('viz')!==y.classList.contains('viz')){x._chart?.destroy?.();x.replaceWith(y);continue}
    if(x.classList.contains('viz')){if(x.dataset.raw!==y.dataset.raw){x._chart?.destroy?.();x.replaceWith(y)}continue}
    if(x.tagName==='PRE'){syncPre(x,y);continue}
    syncAttrs(x,y);morph(x,y);
  }
}
function addCaret(tpl){   // blinking cursor at the end of the last paragraph / list item
  let n=tpl;for(;;){const l=n.lastElementChild;if(l&&/^(UL|OL|LI|BLOCKQUOTE)$/.test(l.tagName))n=l;else break}
  const l=n.lastElementChild,t=l&&/^(P|H[1-6])$/.test(l.tagName)?l:(n!==tpl&&!l?n:null);
  if(t){const c=document.createElement('span');c.className='caret';t.append(c)}
}
function setBody(b,md,streaming,m){
  const open=streaming&&((md.match(/^ {0,3}(```|~~~)/gm)||[]).length%2===1);
  const tpl=document.createElement('div');tpl.innerHTML=render(streaming?healMd(md):md);
  const all=[...tpl.querySelectorAll('pre')];all.forEach((pre,i)=>{const code=pre.querySelector('code');if(!code)return;code.dataset.raw=code.textContent;
    const k=classify((code.className.match(/language-(\S+)/)||[])[1]||'');
    if((k.kind==='chart'||k.kind==='mermaid')&&!(open&&i===all.length-1)){pre.replaceWith(vizEl(k.kind,code.textContent));return}
    bar(pre,code)});
callouts(tpl);
tables(tpl);
inlineMarks(tpl, m?.web?.results, m?.kb?.hits);  if(streaming&&!open&&md)addCaret(tpl);
  morph(b,tpl);mountViz(b);mountMath(b);
  const pres=[...b.querySelectorAll('pre code')];
  pres.forEach((code,i)=>{if(code.dataset.hl||(open&&i===pres.length-1))return;try{hljs.highlightElement(code)}catch{}code.dataset.hl='1'});
}
function foot(m){
  if(m.gen)return m.gen.foot||'';
  if(m.files?.length)return'Attached: '+m.files.map(f=>f.name).join(', ');
  const s=m.stats;if(!s)return'';
  const p=[];if(s.completion_tokens!=null)p.push(s.completion_tokens+' tokens');
  if(s.tps)p.push(s.tps.toFixed(1)+' tok/s');if(s.ttft!=null)p.push(s.ttft.toFixed(2)+' s to first token');
  if(s.trimmed)p.push(s.trimmed+' old msgs trimmed');if(s.finish_reason==='length')p.push('stopped at token limit');if(s.stopped)p.push('stopped');
  return p.join('  ·  ');
}
/* status row (rotating text + clock) lives in the footer slot, which always keeps its height, so nothing shifts when it appears or goes */
function startStatus(m){
  const f=m.el.querySelector('.foot');
  f.innerHTML='<span class="status"><span class="orb"></span><span class="ph shimmer"></span><span class="dots"><i></i><i></i><i></i></span><span class="clk"></span></span>';
  m.st=f.firstChild;m.stop1=ticker(f.querySelector('.ph'),m.gen?IMGP:PHRASES,2600);
  const t0=performance.now(),upd=()=>{
    m.st.querySelector('.clk').textContent=clock(((performance.now()-t0)/1000)|0);
    if(m.t0&&!m.thinkMs){const tt=m.el.querySelector('.think-time');if(tt)tt.textContent=clock(((performance.now()-m.t0)/1000)|0)}
  };
  upd();m.clk=setInterval(upd,1000);
}
function endStatus(m){
  if(m.stop1){m.stop1();m.stop1=null}if(m.clk){clearInterval(m.clk);m.clk=null}if(m.tstop){m.tstop();m.tstop=null}m.st=null;
}
function paintThink(m,s,live){
  const t=m.el.querySelector('.think');if(!t)return;
  const has=s.think.length>0||(live&&/<think>/.test(m.content));
  t.hidden=!has;if(!has)return;
  const thinking=live&&!s.done;
  if(thinking&&!m.t0)m.t0=performance.now();
  if(m.t0&&!m.thinkMs&&(s.done||!live))m.thinkMs=performance.now()-m.t0;
  t.classList.toggle('live',thinking);m.el.classList.toggle('thinking',thinking);
  if(thinking&&!m.tstop)m.tstop=ticker(t.querySelector('.ph'),THINK,2200);
  if(!thinking&&m.tstop){m.tstop();m.tstop=null}
  const dl=t.querySelector('.done-l'),lab=m.thinkMs?'Thought for '+dur(m.thinkMs):'Reasoning';if(dl.textContent!==lab)dl.textContent=lab;
  if(!thinking)t.querySelector('.think-time').textContent='';
  const sb=t.querySelector('.think-s');
  if(m._tt!==s.think){const near=sb.scrollHeight-sb.scrollTop-sb.clientHeight<40;m._tt=s.think;sb.textContent=s.think;if(thinking&&near)sb.scrollTop=sb.scrollHeight}
  if(!m.touched)t.classList.toggle('open',thinking);
}
function paint(m,live){
  const el=m.el,b=el.querySelector('.body'),f=el.querySelector('.foot');el.classList.toggle('streaming',!!live);
  if(m.role==='user'){if(b.textContent!==m.content)b.textContent=m.content;const t=foot(m);if(f.textContent!==t)f.textContent=t;paintImgs(m);return}
  const s=split(m.content,live);
  if(m.gen)paintGen(m);
  paintThink(m,s,live);
  paintWeb(m);paintKbCard(m);
  setBody(b,s.ans,live,m);
  if(live){if(!m.st)startStatus(m)}
  else{endStatus(m);const t=foot(m);if(f.textContent!==t)f.textContent=t;paintPlan(m)}
}
/* images on a user message: thumbnail + the details the vision model extracted (live while it reads) */
function paintImgs(m){
  const col=m.el.querySelector('.col'),ims=(m.files||[]).filter(f=>f.thumb||typeof f.image==='string');let box=col.querySelector('.imgs');
  if(!ims.length){box?.remove();return}
  if(!box||box.children.length!==ims.length){
    box?.remove();box=document.createElement('div');box.className='imgs';
    ims.forEach(f=>{const w=document.createElement('div'),i=document.createElement('img'),d=document.createElement('details');w.className='im';
      i.alt=f.name;i.src=f.thumb||f.image;d.append(document.createElement('summary'),document.createElement('pre'));w.append(i,d);box.append(w)});
    col.insertBefore(box,col.querySelector('.body'));
  }
  [...box.children].forEach((w,k)=>{const f=ims[k],d=w.querySelector('details'),sm=d.firstChild,pr=d.lastChild;
    sm.textContent=f.busy?'Reading '+f.name+'…':'Image details · '+f.name;
    if(pr.textContent!==(f.desc||''))pr.textContent=f.desc||'';
    d.hidden=!f.desc&&!f.busy;
    if(f.busy&&!d.dataset.t){d.open=true;d.dataset.t='1'}else if(!f.busy&&d.dataset.t==='1'){d.open=false;d.dataset.t='2'}
    if(f.busy)pr.scrollTop=pr.scrollHeight;
  });
}
const THINKHTML=`<div class="think" hidden><button type="button" class="think-h"><span class="orb"></span><span class="ph shimmer"></span><span class="done-l"></span><span class="think-time"></span><span class="chev">${ic('chev',15)}</span></button><div class="think-w"><div><div class="think-s"></div></div></div></div>`;
const KBHTML=`<div class="kbcard" hidden><button type="button" class="kb-h"><span class="gl">${ic('book',15)}</span><span class="wq"></span><span class="chev">${ic('chev',15)}</span></button><div class="kb-w"><div><ol class="kb-l"></ol><div class="kb-err" hidden></div></div></div></div>`;
const WEBHTML=`<div class="web" hidden><button type="button" class="web-h"><span class="gl">${ic('globe',15)}</span><span class="wq"></span><span class="chev">${ic('chev',15)}</span></button><div class="web-w"><div><ol class="web-l"></ol><div class="web-err" hidden></div></div></div></div>`;
function addMsg(m){
  $('#empty')?.remove();
  const d=document.createElement('div'),a=m.role==='assistant';d.className='msg '+m.role;
d.innerHTML=(a?'<div class="av"><img src="/assets/logo-mark.png" alt=""></div>':'')+'<div class="col">'+(a?THINKHTML+KBHTML+WEBHTML:'')+'<div class="body"></div><div class="plan" hidden></div><div class="foot"></div><div class="acts"></div></div>';  msgsEl.append(d);m.el=d;
  if(a)d.querySelector('.think-h').onclick=()=>{m.touched=true;d.querySelector('.think').classList.toggle('open')};
  if(a)d.querySelector('.kb-h').onclick=()=>{m._kt=true;d.querySelector('.kbcard').classList.toggle('open')};
  if(a)d.querySelector('.web-h').onclick=()=>{m._wt=true;d.querySelector('.web').classList.toggle('open')};
  paint(m,!!m.live);acts(m);
}
