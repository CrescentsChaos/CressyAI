/* ================= text & style ================= */
function formatPrompt(){
  const st=$('#fstyle').value,math=$('#fmath').value!=='0';
  if(st==='plain')return'Write plain conversational prose: no headings, tables, bold text or decorations unless the user asks for them. Put code only in fenced blocks.';
  const base='Format for easy reading: short paragraphs; bullet or numbered lists for parallel items or steps; **bold** only for key terms; fenced code blocks with a language tag; no filler openers or closers.'+(math?' Write math as LaTeX, $inline$ or $$display$$.':'');
  if(st!=='rich')return base;
  return base+' When it helps (not every time): ## headings for long answers; a markdown table to compare options; callouts written as a blockquote whose first line is [!NOTE], [!TIP], [!IMPORTANT], [!WARNING] or [!CAUTION]; task lists "- [ ] item" for checklists; ==highlight== around the one key takeaway; a one-line **TL;DR** at the top of long answers.';
}
function applyLook(){
  const FONTS={inter:'',serif:'"Source Serif 4",Georgia,"Times New Roman",serif',sora:'Sora,Inter,sans-serif',system:'system-ui,-apple-system,"Segoe UI",sans-serif',mono:'var(--mono)'};
  const ACC={blue:['#60a5fa','#2563eb','#2563eb','#0ea5e9'],teal:['#2dd4bf','#0d9488','#0d9488','#0ea5e9'],green:['#4ade80','#16a34a','#16a34a','#0d9488'],rose:['#fb7185','#e11d48','#e11d48','#f97316'],amber:['#fbbf24','#d97706','#d97706','#ef4444']};
  const r=document.documentElement.style,v=id=>$('#'+id).value,put=(k,x)=>x?r.setProperty(k,x):r.removeProperty(k);
  put('--ff',FONTS[v('ffont')]);put('--fs',v('fsize')==='15'?'':v('fsize')+'px');put('--lh',v('flh')==='1.7'?'':v('flh'));put('--cw',v('fwidth')==='820'?'':v('fwidth')+'px');
  const a=ACC[v('faccent')];
  if(a){const c=isDark()?a[0]:a[1];put('--acc',c);put('--user','linear-gradient(135deg,'+a[2]+','+a[3]+')');put('--grad','linear-gradient(135deg,'+c+','+a[3]+')')}
  else['--acc','--user','--grad'].forEach(k=>r.removeProperty(k));
}
applyLook();
['ffont','fsize','flh','fwidth','faccent'].forEach(i=>$('#'+i).addEventListener('change',applyLook));
const repaintAll=()=>msgs.forEach(m=>{if(m.el&&m.role==='assistant'&&!m.live)paint(m,false)});
$('#fmath').addEventListener('change',repaintAll);

const CO={note:['Note','info'],tip:['Tip','spark'],important:['Important','info'],warning:['Warning','alert'],caution:['Caution','alert']};
const CO_ALIAS={info:'note',hint:'tip','pro tip':'tip','heads up':'warning',danger:'caution',note:'note',tip:'tip',warning:'warning',important:'important',caution:'caution'};
function callouts(tpl){
  tpl.querySelectorAll('blockquote').forEach(bq=>{
    const p=bq.firstElementChild;if(!p||p.tagName!=='P')return;
    const n=document.createTreeWalker(p,NodeFilter.SHOW_TEXT).nextNode(),m=n&&n.data.match(/^\s*\[!(NOTE|TIP|IMPORTANT|WARNING|CAUTION)\][ \t]*\n?/i);
    if(m){const k=m[1].toLowerCase();n.data=n.data.slice(m[0].length);if(!p.textContent.trim()&&!p.children.length)p.remove();
      const t=document.createElement('div');t.className='co-t';t.innerHTML=ic(CO[k][1],16)+'<span>'+CO[k][0]+'</span>';bq.prepend(t);bq.className='callout '+k;return}
    const sg=p.firstChild;   // "> **Note:** ..." written by models that do not know the [!NOTE] form
    if(sg&&sg.nodeType===1&&sg.tagName==='STRONG'){const k=CO_ALIAS[sg.textContent.trim().replace(/:$/,'').toLowerCase()];if(k)bq.className='callout '+k}
  });
}
function tables(tpl){
  tpl.querySelectorAll('table').forEach(t=>{
    if(t.parentElement.classList.contains('tblw'))return;
    const w=document.createElement('div'),bar=document.createElement('div');w.className='tblw';bar.className='tt';
    bar.innerHTML='<button type="button" data-ta="copy">Copy table</button><button type="button" data-ta="csv">Download CSV</button>';
    const sc=document.createElement('div');sc.className='ts';t.replaceWith(w);sc.append(t);w.append(sc,bar);
  });
}
const csvCell=x=>/[",\n]/.test(x)?'"'+x.replace(/"/g,'""')+'"':x;
function sortTable(th){
  const t=th.closest('table'),body=t.tBodies[0];if(!body)return;
  const i=th.cellIndex,asc=th.dataset.sort!=='asc',val=r=>(r.cells[i]?.textContent||'').trim();
  const num=x=>/^[\s$€£+-]*\d[\d.,]*\s*[%kKmMbB]?\s*$/.test(x)?parseFloat(x.replace(/[^\d.+-]/g,'')):null;
  const rows=[...body.rows].map(r=>({r,v:val(r)})),numeric=rows.every(o=>o.v===''||num(o.v)!==null);
  rows.sort((a,b)=>numeric?(num(a.v)??-Infinity)-(num(b.v)??-Infinity):a.v.localeCompare(b.v,undefined,{numeric:true,sensitivity:'base'}));
  if(!asc)rows.reverse();rows.forEach(o=>body.append(o.r));
  t.querySelectorAll('th').forEach(h=>h.removeAttribute('data-sort'));th.dataset.sort=asc?'asc':'desc';
}
msgsEl.addEventListener('click',e=>{
  const b=e.target.closest('[data-ta]');
  if(b){const t=b.closest('.tblw')?.querySelector('table');if(!t)return;const rows=[...t.rows].map(r=>[...r.cells].map(c=>c.textContent.trim().replace(/\s+/g,' ')));
    if(b.dataset.ta==='csv')dl_('table.csv','\ufeff'+rows.map(r=>r.map(csvCell).join(',')).join('\n'));
    else{navigator.clipboard.writeText(rows.map(r=>r.join('\t')).join('\n'));b.textContent='Copied';setTimeout(()=>b.textContent='Copy table',1200)}
    return}
  const th=e.target.closest('.body th');if(th&&!th.closest('.msg.streaming'))sortTable(th);
});
const MARKRE=/\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]|\[(K\d{1,2}(?:\s*,\s*K?\d{1,2})*)\]|==([^=\s](?:[^=\n]*?[^=\s])?)==/g;

function inlineMarks(tpl,srcs,kbs){
  // [1] -> web source chips, [K1] -> knowledge source chips,
  // ==text== -> highlight; built before the diff so streaming never flickers
  const w=document.createTreeWalker(tpl,NodeFilter.SHOW_TEXT,{acceptNode:n=>n.parentElement.closest('pre,code,a,kbd,.math')?NodeFilter.FILTER_REJECT:NodeFilter.FILTER_ACCEPT}),nodes=[];
  for(let n;n=w.nextNode();)nodes.push(n);
  for(const n of nodes){
    const s=n.data;MARKRE.lastIndex=0;if(!MARKRE.test(s))continue;MARKRE.lastIndex=0;
    const f=document.createDocumentFragment();let last=0,m;
    while(m=MARKRE.exec(s)){
      if(m[1]!==undefined){
        const nums=m[1].split(/\s*,\s*/).map(Number);
        if(!srcs?.length||!nums.every(k=>k>=1&&k<=srcs.length&&okUrl(srcs[k-1].url)))continue;
        f.append(s.slice(last,m.index));last=m.index+m[0].length;
        nums.forEach(k=>{const r=srcs[k-1],a=document.createElement('a');a.className='cite';a.href=r.url;a.target='_blank';a.rel='noopener noreferrer';a.title=(r.title||'')+(r.domain?' — '+r.domain:'');a.textContent=k;f.append(a)});
}else if(m[2]!==undefined){
  const nums=m[2].split(/\s*,\s*/).map(x=>+x.replace(/^K/i,''));
  if(!kbs?.length||!nums.every(k=>k>=1&&k<=kbs.length))continue;

  f.append(s.slice(last,m.index));
  last=m.index+m[0].length;

  nums.forEach(k=>{
    const r=kbs[k-1],
      a=document.createElement('a');

    a.className='cite';
    a.href='#';
    a.dataset.k=k;
    a.title=r.doc+(r.page?' · p. '+r.page:'')+(r.section?' — '+r.section:'');
    a.textContent='K'+k;
    f.append(a);
  });
}else{
  f.append(s.slice(last,m.index));
  last=m.index+m[0].length;

  const mk=document.createElement('mark');
  mk.textContent=m[3];
  f.append(mk);
}    }
    if(!last)continue;f.append(s.slice(last));n.replaceWith(f);
  }
}
function mountMath(root){
  const els=[...root.querySelectorAll('.math:not([data-r])')];if(!els.length)return;
  if(!document.getElementById('katexcss')){const l=document.createElement('link');l.id='katexcss';l.rel='stylesheet';l.href='https://cdnjs.cloudflare.com/ajax/libs/KaTeX/0.16.9/katex.min.css';document.head.append(l)}
  lib('katex').then(()=>els.forEach(el=>{if(el.dataset.r||!el.isConnected)return;
    try{katex.render(el.dataset.tex,el,{displayMode:el.dataset.d==='1',throwOnError:false,strict:'ignore',trust:false});el.dataset.r='1'}catch{el.dataset.r='0'}})).catch(()=>{});
}

/* ---------- discoverability: commands, help, empty state ---------- */
Object.assign(CMDS,{web:['d','Look this up on the web (SearXNG) and answer with sources'],search:['d','Same as /web']});
SUGG.push(['Search the web','/web','Pull live results through your SearXNG']);
HERO[HERO.indexOf('Edit a file')]='Search the web';
HELP+='\n\n**Web search** — turn on **Web** next to the paperclip. Questions that need fresh info (news, prices, versions, "latest…") are searched through your SearXNG and answered from the sources, with clickable [1] [2] chips; paste a link and it is read. `/web your question` forces a search for one message. Search words leave your machine through your SearXNG; the model itself stays local. Set it up under *Settings → Web search*.\n\n**Text & style** — *Settings → Text & style* changes response style (balanced / rich / plain), font, size, line spacing, reading width and accent colour. Replies can use callouts (`> [!TIP]`), sortable tables with *Copy table* / *Download CSV*, task lists, `==highlight==`, LaTeX math (`$x^2$`, `$$...$$`) and a *Wrap* button on code blocks.';

