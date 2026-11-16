const $=s=>document.querySelector(s),$$=s=>[...document.querySelectorAll(s)],esc=s=>String(s).replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmtB=n=>n>=1e9?(n/1e9).toFixed(2)+' GB':(n/1e6).toFixed(0)+' MB';
const fmtS=n=>n<1024?n+' B':n<1048576?(n/1024).toFixed(0)+' KB':(n/1048576).toFixed(1)+' MB';
const dur=ms=>ms<1000?'<1s':ms<60000?(ms/1000).toFixed(1)+'s':Math.floor(ms/60000)+'m '+Math.round(ms%60000/1000)+'s';
const clock=s=>s<60?s+'s':Math.floor(s/60)+'m '+(s%60)+'s';
const FT={0:'F32',1:'F16',2:'Q4_0',3:'Q4_1',7:'Q8_0',8:'Q5_0',9:'Q5_1',10:'Q2_K',11:'Q3_K_S',12:'Q3_K_M',13:'Q3_K_L',14:'Q4_K_S',15:'Q4_K_M',16:'Q5_K_S',17:'Q5_K_M',18:'Q6_K'};
/*==PURE==*/
const LANGX={py:'python',js:'javascript',mjs:'javascript',cjs:'javascript',ts:'typescript',tsx:'typescript',jsx:'javascript',html:'xml',htm:'xml',xml:'xml',svg:'xml',css:'css',json:'json',sh:'bash',bash:'bash',bat:'dos',sql:'sql',java:'java',c:'c',h:'c',cpp:'cpp',go:'go',rs:'rust',yml:'yaml',yaml:'yaml',md:'markdown',cs:'csharp',php:'php',rb:'ruby',toml:'ini',ini:'ini',txt:'plaintext'};
const extOf=p=>(String(p).match(/\.([\w-]+)$/)||[])[1]?.toLowerCase()||'';
function cleanPath(p){
  p=String(p||'').trim().replace(/^["'`]+|["'`]+$/g,'').replace(/\\/g,'/').replace(/^(\.\/)+/,'').replace(/^\/+/,'');
  if(!p||p.length>200||/[\0<>|?*:]/.test(p)||p.split('/').some(s=>s===''||s==='..'))return'';
  return p;
}
/* what a fenced block is, from its info string (the class after "language-") */
function classify(cls){
  const l=String(cls||'');let m;
  if(m=l.match(/^(?:file|new|create|write):(.+)$/i)){const p=cleanPath(m[1]);return p?{kind:'file',path:p,lang:LANGX[extOf(p)]||'plaintext'}:{kind:'code',lang:'text'}}
  if(m=l.match(/^(?:edit|patch|update|modify):(.+)$/i)){const p=cleanPath(m[1]);return p?{kind:'edit',path:p,lang:LANGX[extOf(p)]||'plaintext'}:{kind:'code',lang:'text'}}
  if(m=l.match(/^[\w+#.-]+:(.+\.\w{1,8})$/)){const p=cleanPath(m[1]);if(p)return{kind:'file',path:p,lang:LANGX[extOf(p)]||'plaintext'}}
  if(/^chart(js)?$/i.test(l))return{kind:'chart',lang:'json'};
  if(/^mermaid$/i.test(l))return{kind:'mermaid',lang:'plaintext'};
  if(/^(bash|sh|shell|zsh|console|terminal|powershell|ps1|cmd|bat|batch)$/i.test(l))return{kind:'term',lang:l.toLowerCase()==='terminal'?'bash':l};
  return{kind:'code',lang:l||'text'};
}
/* SEARCH/REPLACE blocks */
function parseEdits(t){
  const L=String(t).replace(/\r\n/g,'\n').split('\n'),edits=[];let st=0,a=[],b=[],seen=false;
  for(const ln of L){
    if(st===0){if(/^<{5,9}\s*SEARCH\s*$/.test(ln)){st=1;a=[];b=[];seen=true}}
    else if(st===1){if(/^={5,9}\s*$/.test(ln))st=2;else a.push(ln)}
    else if(/^>{5,9}\s*REPLACE\s*$/.test(ln)){edits.push({search:a.join('\n'),replace:b.join('\n')});st=0}
    else b.push(ln);
  }
  return{edits,complete:st===0,seen};
}
function findAll(text,needle){const r=[];let i=-1;while((i=text.indexOf(needle,i+1))>=0){r.push(i);if(r.length>1)break}return r}
function applyEdits(base,edits){
  let text=base,applied=0,already=0;const errors=[];
  edits.forEach((e,n)=>{
    const tag='Edit '+(n+1)+': ';
    if(!e.search.trim()){errors.push(tag+'the SEARCH part is empty');return}
    const hits=findAll(text,e.search);
    if(hits.length===1){text=text.slice(0,hits[0])+e.replace+text.slice(hits[0]+e.search.length);applied++;return}
    if(hits.length>1){errors.push(tag+'the SEARCH text matches more than one place; it needs more surrounding lines');return}
    // tolerant match: ignore leading/trailing whitespace on each line
    const tl=text.split('\n'),sl=e.search.split('\n'),norm=x=>x.trim(),found=[];
    for(let i=0;i+sl.length<=tl.length;i++){let ok=true;for(let j=0;j<sl.length;j++)if(norm(tl[i+j])!==norm(sl[j])){ok=false;break}if(ok){found.push(i);if(found.length>1)break}}
    if(found.length===1){tl.splice(found[0],sl.length,...(e.replace===''?[]:e.replace.split('\n')));text=tl.join('\n');applied++;return}
    if(found.length>1){errors.push(tag+'the SEARCH text matches more than one place');return}
    if(e.replace.trim()&&text.includes(e.replace)){already++;return}
    errors.push(tag+'could not find the SEARCH text: "'+e.search.split('\n')[0].trim().slice(0,60)+'"');
  });
  return{text,applied,already,errors};
}
/* line diff: trims common ends, LCS on the middle, falls back to replace-all when huge */
function diffOps(a,b){
  const A=a.split('\n'),B=b.split('\n');let s=0;
  while(s<A.length&&s<B.length&&A[s]===B[s])s++;
  let ea=A.length,eb=B.length;while(ea>s&&eb>s&&A[ea-1]===B[eb-1]){ea--;eb--}
  const ops=[];for(let i=0;i<s;i++)ops.push({t:' ',s:A[i]});
  const X=A.slice(s,ea),Y=B.slice(s,eb),n=X.length,m=Y.length;
  if(n*m>4e6){X.forEach(x=>ops.push({t:'-',s:x}));Y.forEach(y=>ops.push({t:'+',s:y}))}
  else{
    const w=m+1,D=new Uint32Array((n+1)*w);
    for(let i=n-1;i>=0;i--)for(let j=m-1;j>=0;j--)D[i*w+j]=X[i]===Y[j]?D[(i+1)*w+j+1]+1:Math.max(D[(i+1)*w+j],D[i*w+j+1]);
    let i=0,j=0;
    while(i<n&&j<m){if(X[i]===Y[j]){ops.push({t:' ',s:X[i]});i++;j++}else if(D[(i+1)*w+j]>=D[i*w+j+1])ops.push({t:'-',s:X[i++]});else ops.push({t:'+',s:Y[j++]})}
    while(i<n)ops.push({t:'-',s:X[i++]});while(j<m)ops.push({t:'+',s:Y[j++]});
  }
  for(let i=ea;i<A.length;i++)ops.push({t:' ',s:A[i]});
  return ops;
}
function diffStat(ops){let add=0,del=0;ops.forEach(o=>{if(o.t==='+')add++;else if(o.t==='-')del++});return{add,del}}
function collapseOps(ops,ctx=3){   // keep changed lines plus context, mark the rest as a gap
  const keep=new Array(ops.length).fill(false);
  ops.forEach((o,i)=>{if(o.t!==' ')for(let k=Math.max(0,i-ctx);k<=Math.min(ops.length-1,i+ctx);k++)keep[k]=true});
  const out=[];let gap=0;
  ops.forEach((o,i)=>{if(keep[i]){if(gap){out.push({t:'~',s:gap+' unchanged lines'});gap=0}out.push(o)}else gap++});
  if(gap)out.push({t:'~',s:gap+' unchanged lines'});
  return out;
}
/* chart spec -> a normalized description the renderer can feed to Chart.js */
function normChart(raw){
  let s;try{s=typeof raw==='string'?JSON.parse(raw):raw}catch(e){return{error:'The chart block is not valid JSON ('+e.message+')'}}
  if(!s||typeof s!=='object'||Array.isArray(s))return{error:'The chart block must be a JSON object'};
  if(s.data&&!Array.isArray(s.data)&&typeof s.data==='object'){s={...s,...s.data}}
  const alias={column:'bar',columns:'bar',bars:'bar',horizontalbar:'hbar',barh:'hbar',hbar:'hbar',area:'area',lines:'line',donut:'doughnut',polar:'polararea',polararea:'polararea',pie:'pie',doughnut:'doughnut',radar:'radar',scatter:'scatter',line:'line',bar:'bar'};
  const type=alias[String(s.type||'bar').toLowerCase().replace(/[\s_-]/g,'')];
  if(!type)return{error:'Unknown chart type "'+s.type+'" (use bar, line, area, pie, doughnut, radar, polarArea or scatter)'};
  let ds=Array.isArray(s.datasets)?s.datasets:null;
  if(!ds&&s.series&&typeof s.series==='object')ds=Array.isArray(s.series)?s.series:Object.entries(s.series).map(([label,data])=>({label,data}));
  if(!ds){const v=s.values||(Array.isArray(s.data)?s.data:null)||s.y;if(Array.isArray(v))ds=[{label:s.label||s.title||'Value',data:v}]}
  if(!ds||!ds.length)return{error:'The chart has no data. Add "labels" and "datasets": [{"label": "...", "data": [...]}]'};
  if(ds.length>12)return{error:'Too many datasets (max 12)'};
  const sc=type==='scatter',out=[];
  for(const [i,d] of ds.entries()){
    const label=String(d&&d.label!=null?d.label:'Series '+(i+1)),arr=Array.isArray(d)?d:d&&d.data;
    if(!Array.isArray(arr)||!arr.length)return{error:'Dataset "'+label+'" has no data array'};
    if(arr.length>2000)return{error:'Too many points (max 2000)'};
    const data=[];
    for(const v of arr){
      if(sc){const p=Array.isArray(v)?{x:+v[0],y:+v[1]}:v&&typeof v==='object'?{x:+v.x,y:+v.y}:null;if(!p||!isFinite(p.x)||!isFinite(p.y))return{error:'Scatter points must be {"x":n,"y":n} or [x,y] ("'+label+'")'};data.push(p)}
      else if(v===null)data.push(null);
      else{const n=typeof v==='number'?v:typeof v==='string'&&v.trim()!==''?+v.replace(/,/g,''):NaN;if(!isFinite(n))return{error:'Dataset "'+label+'" has a non-numeric value: '+JSON.stringify(v)};data.push(n)}
    }
    out.push({label,data});
  }
  let labels=s.labels||s.x||s.categories;
  if(!sc){
    const n=Math.max(...out.map(d=>d.data.length));
    labels=Array.isArray(labels)?labels.map(String):[];
    while(labels.length<n)labels.push(String(labels.length+1));
  }
  return{type:type==='hbar'?'bar':type==='area'?'line':type==='polararea'?'polarArea':type,horizontal:type==='hbar',fill:type==='area',stacked:!!s.stacked,
    title:s.title?String(s.title):'',xLabel:s.xLabel||s.xlabel||s.xAxis||'',yLabel:s.yLabel||s.ylabel||s.yAxis||'',labels,datasets:out};
}
/* plan text -> list of step strings */
function parsePlan(t){
  const L=String(t).split('\n'),item=/^\s*(?:[-*+]\s+(?:\[[ xX]\]\s*)?|\d+[.)]\s+)(.+?)\s*$/,clean=x=>x.replace(/\*\*|__/g,'').trim();
  let i=L.findIndex(l=>/^#{1,4}\s*(?:\d+[.)]\s*)?(?:steps|implementation|tasks|plan)\b/i.test(l.trim()));const steps=[];
  if(i>=0){for(i++;i<L.length&&!/^#{1,4}\s/.test(L[i]);i++){const m=L[i].match(item);if(m&&!/^\s{2,}/.test(L[i]))steps.push(clean(m[1]))}}
  if(!steps.length)L.forEach(l=>{const m=l.match(/^\s*[-*]\s+\[[ xX]\]\s*(.+)$/);if(m)steps.push(clean(m[1]))});
  return steps.slice(0,30);
}
/* terminal output: drop colour codes, keep only the last \r-overwritten segment of each line */
const cleanOut=s=>String(s).replace(/\x1b\[[0-9;?]*[ -\/]*[@-~]/g,'').replace(/\x1b\][^\x07]*\x07/g,'').replace(/\r\n/g,'\n').replace(/[^\n]*\r(?!\n)/g,'');
/*==END PURE==*/

