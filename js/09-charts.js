/* ---------- charts and diagrams ---------- */
const PALETTE=['#8b7cff','#2dd4bf','#38bdf8','#f59e0b','#f472b6','#34d399','#fb7185','#a3e635','#c084fc','#60a5fa','#fbbf24','#4ade80'];
const cssv=n=>getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const isDark=()=>th==='dark'||(th==='auto'&&matchMedia('(prefers-color-scheme: dark)').matches);
const vizEl=(kind,raw)=>{const d=document.createElement('div');d.className='viz';d.dataset.kind=kind;d.dataset.raw=raw;d.innerHTML=`<div class="vizbar"><span>${kind}</span><span class="vg"></span></div><div class="vizbody"><div class="vizload">Drawing ${kind}…</div></div>`;return d};
function buildChart(cv,c){
  const ink=cssv('--ink')||'#e9ecf5',mut=cssv('--mut')||'#8d95aa',line=cssv('--line')||'#252b3b',card=cssv('--card')||'#141822';
  const circ=['pie','doughnut','polarArea'].includes(c.type),rad=c.type==='radar'||c.type==='polarArea',multi=c.datasets.length>1;
  const ds=c.datasets.map((d,i)=>{const col=PALETTE[i%PALETTE.length];
    if(circ)return{label:d.label,data:d.data,backgroundColor:d.data.map((_,j)=>PALETTE[j%PALETTE.length]+'dd'),borderColor:card,borderWidth:2};
    const o={label:d.label,data:d.data,borderColor:col,backgroundColor:col+(c.type==='bar'||c.type==='scatter'?'cc':'33'),borderWidth:2};
    if(c.type==='line')Object.assign(o,{tension:.3,fill:c.fill,pointRadius:3,pointBackgroundColor:col});
    if(c.type==='radar')o.fill=true;if(c.type==='bar')Object.assign(o,{borderRadius:6,borderWidth:0});if(c.type==='scatter')o.pointRadius=5;return o});
  const ax=(t,extra)=>({ticks:{color:mut},grid:{color:line},title:{display:!!t,text:t,color:mut},...extra});
  const scales=circ&&c.type!=='polarArea'?{}:rad?{r:{grid:{color:line},angleLines:{color:line},pointLabels:{color:mut},ticks:{color:mut,backdropColor:'transparent'}}}:
    {x:ax(c.horizontal?c.yLabel:c.xLabel,{stacked:c.stacked,...(c.type==='scatter'?{type:'linear'}:{})}),y:ax(c.horizontal?c.xLabel:c.yLabel,{stacked:c.stacked,beginAtZero:c.type==='bar'})};
  return new Chart(cv,{type:c.type,data:{labels:c.type==='scatter'?undefined:c.labels,datasets:ds},
    options:{responsive:true,maintainAspectRatio:false,devicePixelRatio:Math.max(2,devicePixelRatio||1),indexAxis:c.horizontal?'y':'x',animation:{duration:350},
      plugins:{legend:{display:multi||circ,labels:{color:mut}},title:{display:!!c.title,text:c.title,color:ink,font:{size:14,weight:'600'}}},scales},
    plugins:[{id:'bg',beforeDraw(ch){const x=ch.ctx;x.save();x.globalCompositeOperation='destination-over';x.fillStyle=card;x.fillRect(0,0,ch.width,ch.height);x.restore()}}]});
}
let mmN=0;
async function mountOne(el){
  const kind=el.dataset.kind,raw=el.dataset.raw,body=el.querySelector('.vizbody'),vg=el.querySelector('.vg');vg.innerHTML='';
  const btn=(t,f)=>{const b=document.createElement('button');b.type='button';b.textContent=t;b.onclick=()=>f(b);vg.append(b);return b};
  const code=()=>{let p=body.querySelector('.vizcode');if(p){p.remove();return}p=document.createElement('pre');p.className='vizcode';p.textContent=raw;body.append(p)};
  const fail=msg=>{body.innerHTML='';const e=document.createElement('div');e.className='vizerr';e.textContent=msg;body.append(e);const p=document.createElement('pre');p.className='vizcode';p.textContent=raw;body.append(p)};
  if(kind==='chart'){
    const c=normChart(raw);if(c.error)return fail(c.error);
    try{await lib('chart')}catch(e){return fail(e.message)}
    body.innerHTML='<div class="cv"><canvas></canvas></div>';
    try{el._chart=buildChart(body.querySelector('canvas'),c)}catch(e){return fail('Could not draw the chart: '+e.message)}
    btn('Save PNG',()=>{const a=document.createElement('a');a.href=el._chart.toBase64Image('image/png',1);a.download=((c.title||'chart').replace(/[^\w-]+/g,'_')||'chart')+'.png';a.click()});
    btn('Spec',code);
  }else{
    try{await lib('mermaid')}catch(e){return fail(e.message)}
    const id='mm'+(++mmN);
    try{
      mermaid.initialize({startOnLoad:false,securityLevel:'strict',theme:isDark()?'dark':'neutral',fontFamily:'Inter, sans-serif'});   // 'strict' makes Mermaid sanitize labels and disable click handlers
      const{svg}=await mermaidRender(id,raw);
      body.innerHTML=svg;el._svg=svg;
      btn('Save SVG',()=>dl_('diagram.svg',el._svg));btn('Code',code);
    }catch(e){document.getElementById('d'+id)?.remove();fail('Diagram error: '+String(e.message||e).split('\n')[0])}
  }
}
function fixMermaid(src){   // models often leave ( ) or quotes bare inside node labels, which Mermaid rejects
  const q=l=>'"'+l.replace(/"/g,'#quot;')+'"';
  return src.split('\n').map(line=>line
    .replace(/(\b[\w-]+)\[(?!["(\[\/\\])([^\]\n]*[()"][^\]\n]*)\]/g,(m,id,l)=>id+'['+q(l)+']')
    .replace(/(\b[\w-]+)\{(?!["{])([^}\n]*[()"][^}\n]*)\}/g,(m,id,l)=>id+'{'+q(l)+'}')).join('\n');
}
async function mermaidRender(id,raw){
  try{return await mermaid.render(id,raw)}
  catch(e){const fx=fixMermaid(raw);if(fx===raw)throw e;document.getElementById('d'+id)?.remove();
    try{return await mermaid.render(id+'r',fx)}catch{document.getElementById('d'+id+'r')?.remove();throw e}}
}
function mountViz(root){root.querySelectorAll('.viz:not([data-m])').forEach(el=>{el.dataset.m='1';mountOne(el)})}
function remountViz(){$$('.viz').forEach(el=>{el._chart?.destroy?.();delete el.dataset.m;el.querySelector('.vizbody').innerHTML='<div class="vizload">Drawing…</div>'});mountViz(document)}
matchMedia('(prefers-color-scheme: dark)').addEventListener?.('change',()=>setTimeout(remountViz,0));
HELP+='\n\n**Workspace** — the model can create and edit files (review the diff, then *Save to project* or *Download*; everything lands in the **Changes** tab, with *Download all (.zip)*), run terminal commands (the **Terminal** tab, or the *Run* button on a `bash` block), draw charts and diagrams, and **plan first**: switch on *Plan* next to the paperclip, or use `/plan`. Alt+T opens the terminal.';
paintChanges();

