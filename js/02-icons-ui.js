/* icons */
const P={plus:'<path d="M12 5v14M5 12h14"/>',copy:'<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',check:'<path d="M20 6 9 17l-5-5"/>',
refresh:'<path d="M3 12a9 9 0 0 1 15-6.7L21 8"/><path d="M21 3v5h-5"/><path d="M21 12a9 9 0 0 1-15 6.7L3 16"/><path d="M8 16H3v5"/>',pencil:'<path d="M17 3a2.8 2.8 0 0 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
trash:'<path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>',
folder:'<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.7-.9l-.8-1.2A2 2 0 0 0 7.9 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>',
file:'<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>',
left:'<path d="m12 19-7-7 7-7"/><path d="M19 12H5"/>',right:'<path d="M5 12h14"/><path d="m12 5 7 7-7 7"/>',up:'<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
home:'<path d="m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><path d="M9 22V12h6v10"/>',search:'<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
clip:'<path d="m21.4 11-9.2 9.2a6 6 0 0 1-8.5-8.5l9.2-9.2a4 4 0 0 1 5.7 5.7l-9.2 9.2a2 2 0 0 1-2.8-2.8l8.5-8.5"/>',stop:'<rect x="6" y="6" width="12" height="12" rx="2.5" fill="currentColor"/>',
panel:'<rect x="3" y="3" width="18" height="18" rx="3"/><path d="M15 3v18"/>',menu:'<path d="M4 6h16M4 12h16M4 18h16"/>',down:'<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
spark:'<path d="M12 3l1.9 5.1L19 10l-5.1 1.9L12 17l-1.9-5.1L5 10l5.1-1.9z"/><path d="M19 3v4M21 5h-4"/>',x:'<path d="M18 6 6 18M6 6l12 12"/>',
chev:'<path d="m6 9 6 6 6-6"/>',download:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m7 10 5 5 5-5"/><path d="M12 15V3"/>',
sun:'<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4"/>',
moon:'<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',half:'<circle cx="12" cy="12" r="9"/><path d="M12 3a9 9 0 0 1 0 18z" fill="currentColor"/>',
keys:'<rect x="2" y="6" width="20" height="12" rx="2"/><path d="M6 10h.01M10 10h.01M14 10h.01M18 10h.01M7 14h10"/>'};
Object.assign(P,{
  book:'<path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V3H6.5A2.5 2.5 0 0 0 4 5.5z"/><path d="M20 17v4H6.5A2.5 2.5 0 0 1 4 18.5"/>',
  upload:'<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><path d="m17 8-5-5-5 5"/><path d="M12 3v12"/>',
  list:'<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',image:'<rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="9" cy="9" r="2"/><path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>',
expand:'<path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/>',
globe:'<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/>',
info:'<circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/>',
alert:'<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"/><path d="M12 9v4M12 17h.01"/>',
wrap:'<path d="M3 6h18M3 12h15a3 3 0 0 1 0 6h-5M3 18h4"/><path d="m15 15-2 3 2 3"/>'});
const ic=(n,s=16)=>`<svg width="${s}" height="${s}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">${P[n]}</svg>`;
$$('[data-ic]').forEach(e=>e.innerHTML=ic(e.dataset.ic,+e.dataset.s||16));

let refreshInfo=null,info=null,msgs=[],busy=false,ctl=null,hist=[],stick=true,att=[],cwd='',imgMode=false,editFrom=null;
const chat=$('#chat'),msgsEl=$('#msgs'),app=$('#app');
const LS=(k,v)=>{try{if(v===undefined)return JSON.parse(localStorage.getItem(k));localStorage.setItem(k,JSON.stringify(v))}catch{return null}};

/* ---------- loading texts ---------- */
const PHRASES=['Cooking','Brewing','Simmering','Marinating','Percolating','Whisking','Conjuring','Fermenting','Noodling','Pondering','Untangling','Distilling','Sautéing tokens','Stirring the weights','Reticulating splines','Consulting the rubber duck','Warming up the neurons','Compiling thoughts','Caramelizing the logic','Kneading the context','Polishing semicolons','Summoning bits','Baking a response','Forging an answer','Spinning up','Seasoning the syntax','Rolling out the dough','Decanting ideas'];
const THINK=['Thinking','Mulling it over','Connecting dots','Chewing on it','Weighing options','Deep in thought','Brewing an idea','Following a hunch','Turning it over','Sketching it out'];
function ticker(el,list,ms){   // rotates the label with a soft blur/slide, never repeats back to back, no layout jump
  let q=[],alive=true,cur='';
  const next=()=>{if(!q.length){q=list.slice().sort(()=>Math.random()-.5);if(q[q.length-1]===cur)q.reverse()}return cur=q.pop()};
  el.textContent=next();
  const id=setInterval(()=>{if(!alive)return;el.classList.add('out');setTimeout(()=>{if(!alive)return;el.textContent=next();el.classList.remove('out')},210)},ms||2600);
  return()=>{alive=false;clearInterval(id)};
}

/* ---------- reasoning split ---------- */
function split(c,live){
  if(live)c=c.replace(/<\/?(?:t(?:h(?:i(?:n(?:k)?)?)?)?)?$/,'');   // hide a half-typed <think> tag
  let think='',done=false,ans=c;
  const o=c.indexOf('<think>'),e=c.indexOf('</think>');
  if(o>=0){const st=o+7;if(e>o){think=c.slice(st,e);done=true;ans=c.slice(0,o)+c.slice(e+8)}else{think=c.slice(st);ans=c.slice(0,o)}}
  else if(e>=0){think=c.slice(0,e);done=true;ans=c.slice(e+8)}
  return{think:think.trim(),done,ans:ans.replace(/^\s+/,'')};
}
function stashMath(t,list){   // pull LaTeX out before markdown sees it (underscores and stars would be eaten)
  const fe=[...t.matchAll(/^ {0,3}(```|~~~)/gm)],cut=fe.length%2?fe[fe.length-1].index:t.length,tail=t.slice(cut);
  const tok=(raw,x,d)=>{list.push({raw,tex:x.trim(),d});return 'zzmath'+(list.length-1)+'zz'};
  const parts=t.slice(0,cut).split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]+`)/);
  for(let i=0;i<parts.length;i+=2)parts[i]=parts[i]
    .replace(/\$\$([\s\S]+?)\$\$/g,(m,x)=>tok(m,x,1))
    .replace(/\\\[([\s\S]+?)\\\]/g,(m,x)=>tok(m,x,1))
    .replace(/\\\(([\s\S]+?)\\\)/g,(m,x)=>tok(m,x,0))
    .replace(/(?<![\\$\w])\$(?![\s$])([^$\n]+?)(?<![\s\\])\$(?![\d\w$])/g,(m,x)=>(/[\\^_{}=+*\/<>-]|[a-zA-Z]/.test(x)&&!/^[\d.,]+$/.test(x))?tok(m,x,0):m);
  return parts.join('')+tail;
}
function render(t){
  try{
    const list=[],src=$('#fmath')?.value==='0'?t:stashMath(t,list);
    let h=DOMPurify.sanitize(marked.parse(src,{gfm:true,breaks:false}));
    if(list.length)h=h.split(/(<pre[\s\S]*?<\/pre>|<code[\s\S]*?<\/code>)/).map((seg,i)=>seg.replace(/zzmath(\d+)zz/g,(m,k)=>{const e=list[+k];return !e?m:i%2?esc(e.raw):'<span class="math" data-d="'+e.d+'" data-tex="'+esc(e.tex)+'">'+esc(e.tex)+'</span>'})).join('');
    return h;
  }catch{return '<p style="white-space:pre-wrap">'+esc(t)+'</p>'}
}
