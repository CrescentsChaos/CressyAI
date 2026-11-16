// Nature page: Species, Nearby, Sightings, Identify. Every animal opens the merged iNaturalist + Wikipedia profile.
(()=>{
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const api=(p,b)=>fetch('/api/'+p,{method:b?'POST':'GET',headers:{'Content-Type':'application/json','X-Cressy':'1'},body:b?JSON.stringify(b):undefined})
  .then(async r=>{const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||'Request failed ('+r.status+')');return j});
const store={get:k=>{try{return JSON.parse(localStorage.getItem(k))}catch{return null}},set:(k,v)=>{try{localStorage.setItem(k,JSON.stringify(v))}catch{}}};
const S={tab:'species',loc:store.get('gc.natloc'),st:{}};
let searchTimer=0,searchSeq=0;
const TABS=[['species','Species'],['nearby','Nearby'],['sightings','Sightings'],['identify','Identify']];
document.body.innerHTML=`<div class="wrap"><div class="top"><img src="/assets/logo-mark.png" alt=""><b>Nature</b><a href="/">Back to chat</a></div>
<div class="tabs">${TABS.map(([k,l])=>`<button data-t="${k}">${l}</button>`).join('')}</div><div id="bar"></div><div id="out"></div></div>`;
const out=document.getElementById('out'),bar=document.getElementById('bar');
const busy=m=>out.innerHTML=`<p class="mut">${esc(m||'Loading…')}</p>`, fail=e=>out.innerHTML=`<div class="err">${esc(e.message||e)}</div>`;
const geo=()=>S.loc?{lat:S.loc.lat,lng:S.loc.lng,radius:S.loc.radius||25}:{};
const badge=s=>s&&s.name?`<span class="badge ${['CR','EN','VU'].includes(s.code)?'risk':''}">${esc(s.name)}</span>`:'';
const tile=(r,sub)=>`<button class="tile" data-id="${r.taxon_id||r.id}"><div class="ph" style="background-image:url('${esc(r.photo)}')"></div><div class="t"><h4>${esc(r.common||r.taxon||r.name)}</h4><small><i>${esc(r.scientific||r.name)}</i></small><div>${badge(r.status)} <small>${esc(sub||'')}</small></div></div></button>`;
const grid=(rows,f)=>rows.length?`<div class="grid">${rows.map(f).join('')}</div>`:'<p class="mut">Nothing found. Try a wider area or another name.</p>';
const wire=()=>out.querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>open(+b.dataset.id));

async function open(id){busy('Reading iNaturalist and Wikipedia…');try{const p=await api('animal/profile',{id,...geo()});out.scrollIntoView();show(p)}catch(e){fail(e)}}
function show(p){
  const m=p.season&&p.season.total?Math.max(...p.season.counts):0;
  out.innerHTML=`<p class="crumb mut">${p.classification.map(a=>`<button data-q="${esc(a.name)}">${esc(a.name)}</button>`).join(' › ')}</p>
  <div class="hero"><div><div class="img" id="hi" style="background-image:url('${esc(p.hero)}')"></div><div class="thumbs">${p.photos.map(x=>`<span data-u="${esc(x.url)}" title="${esc(x.credit)}" style="background-image:url('${esc(x.url)}')"></span>`).join('')}</div></div>
  <div><h2>${esc(p.common||p.name)}</h2><i>${esc(p.name)}</i> ${badge(p.status)}<dl class="facts">${p.facts.map(f=>`<dt>${esc(f.label)}</dt><dd>${esc(f.value)}</dd>`).join('')}</dl>
  <p>${esc(p.summary)}</p><p><button class="btn" id="cp">Copy for chat</button> <a class="btn" target="_blank" rel="noopener" href="${esc(p.links.inaturalist)}">iNaturalist</a> ${p.links.wikipedia?`<a class="btn" target="_blank" rel="noopener" href="${esc(p.links.wikipedia)}">Wikipedia</a>`:''}</p></div></div>
  ${p.sections.map(x=>`<div class="sec"><h3>${esc(x.title)}</h3><p>${esc(x.text)}</p></div>`).join('')}
  ${m?`<div class="sec"><h3>When it is seen</h3><div class="months">${p.season.counts.map(c=>`<span style="height:${Math.max(4,c/m*100)}%" title="${c}"></span>`).join('')}</div><div class="ml">${p.season.months.map(x=>`<span>${x}</span>`).join('')}</div></div>`:''}
  ${p.similar.length?`<div class="sec"><h3>Often confused with</h3>${grid(p.similar,r=>tile(r))}</div>`:''}
  <p class="mut">Sources: ${esc(p.sources.join(' + '))}. Photos: iNaturalist contributors${p.sources.includes('Wikipedia')?' and Wikimedia Commons':''}.</p>`;
  out.querySelectorAll('[data-u]').forEach(s=>s.onclick=()=>document.getElementById('hi').style.backgroundImage=`url('${s.dataset.u}')`);
  out.querySelectorAll('[data-q]').forEach(b=>b.onclick=()=>{S.tab='species';draw(b.dataset.q)});
  document.getElementById('cp').onclick=async e=>{try{await navigator.clipboard.writeText(p.markdown);e.target.textContent='Copied'}catch{e.target.textContent='Copy failed'}};
  wire();
}
function locBar(){return `<div class="bar"><input id="pl" placeholder="Place name (city, park…)" value="${esc(S.loc&&S.loc.name||'')}"><button class="btn" id="pb">Set place</button><button class="btn" id="gb">Use my location</button></div>`}
function locWire(go){
  const set=(l)=>{S.loc=l;store.set('gc.natloc',l);go()};
  document.getElementById('gb').onclick=()=>navigator.geolocation?navigator.geolocation.getCurrentPosition(p=>set({lat:+p.coords.latitude.toFixed(4),lng:+p.coords.longitude.toFixed(4),radius:25,name:'My location'}),()=>fail('Location was blocked. Search for a place instead.')):fail('This browser has no location support.');
  document.getElementById('pb').onclick=async()=>{const q=document.getElementById('pl').value.trim();if(!q)return;try{const r=(await api('inat/places',{query:q,limit:1})).results[0];if(!r||r.lat==null)return fail('No place found with that name.');set({lat:r.lat,lng:r.lng,radius:25,name:r.name})}catch(e){fail(e)}};
}
async function draw(q){
  document.querySelectorAll('.tabs button').forEach(b=>b.classList.toggle('on',b.dataset.t===S.tab));
  const t=S.tab;
  if(t==='species'){bar.innerHTML=`<div class="bar"><input id="q" type="search" autocomplete="off" placeholder="Search an animal, e.g. red fox or Panthera leo" value="${esc(q||'')}"><button class="btn pri" id="go">Search</button></div>`;
    const input=document.getElementById('q');
    const run=async()=>{const v=input.value.trim();if(!v)return;clearTimeout(searchTimer);const seq=++searchSeq;busy('Searching…');try{const r=await api('inat/search',{query:v,limit:12});
      if(seq!==searchSeq||v!==input.value.trim())return;
      if(!r.results.length)return out.innerHTML='<p class="mut">No species matched that name.</p>';
      out.innerHTML=grid(r.results,x=>tile(x,x.rank));wire()}catch(e){if(seq===searchSeq)fail(e)}};
    document.getElementById('go').onclick=run;
    input.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();run()}};
    input.oninput=()=>{clearTimeout(searchTimer);const v=input.value.trim();if(v.length<2){searchSeq++;out.innerHTML=v?'<p class="mut">Type at least two characters to search.</p>':'<p class="mut">Search by common or scientific name to open a full profile.</p>';return}searchTimer=setTimeout(run,350)};
    if(q)run();else out.innerHTML='<p class="mut">Search by common or scientific name to open a full profile.</p>';return}
  if(t==='identify'){bar.innerHTML='';return identifyView()}
  bar.innerHTML=locBar()+(S.loc?`<p class="loc">Showing results within ${S.loc.radius} km of ${esc(S.loc.name)}.</p>`:'');
  const go=()=>draw();locWire(go);
  if(!S.loc)return out.innerHTML='<p class="mut">Set a place to see what lives around it.</p>';
  busy();try{
    if(t==='nearby'){const r=await api('inat/nearby',{...geo(),iconic:['Aves','Mammalia','Reptilia','Amphibia','Actinopterygii','Insecta','Arachnida'],limit:30});out.innerHTML=`<p class="mut">${r.total_species.toLocaleString()} species recorded here.</p>`+grid(r.results,x=>tile(x,x.count+' sightings'))}
    else{const r=await api('inat/observations',{...geo(),iconic:['Aves','Mammalia','Reptilia','Amphibia','Actinopterygii','Insecta','Arachnida'],quality:'research',photos:true,limit:24});out.innerHTML=grid(r.results,x=>tile(x,x.observed+(x.place?' · '+x.place:'')))}
    wire()}catch(e){fail(e)}
}
const shrink=f=>new Promise((ok,no)=>{const i=new Image();i.onload=()=>{const k=Math.min(1,1280/Math.max(i.width,i.height)),c=document.createElement('canvas');c.width=i.width*k;c.height=i.height*k;c.getContext('2d').drawImage(i,0,0,c.width,c.height);ok(c.toDataURL('image/jpeg',.88))};i.onerror=()=>no(new Error('That file is not an image I can read.'));i.src=URL.createObjectURL(f)});
async function visionText(img){const r=await fetch('/api/vision',{method:'POST',headers:{'Content-Type':'application/json','X-Cressy':'1'},body:JSON.stringify({images:[{data:img}],hint:'Identify the animal. Give lines: COMMON: name, SCIENTIFIC: binomial, ALT: up to three look-alikes.'})});
  if(!r.ok)throw new Error('Vision model unavailable');let txt='',buf='';const rd=r.body.getReader(),dc=new TextDecoder();
  for(;;){const {done,value}=await rd.read();if(done)break;buf+=dc.decode(value,{stream:true});const parts=buf.split('\n\n');buf=parts.pop();
    for(const p of parts){const l=p.split('\n').find(x=>x.startsWith('data:'));if(!l)continue;try{const j=JSON.parse(l.slice(5));if(j.type==='token')txt+=j.text;if(j.type==='error')throw new Error(j.message)}catch(e){if(e.message&&!(e instanceof SyntaxError))throw e}}}
  return txt}
function identifyView(){
  out.innerHTML=`<div class="drop" id="dz"><p>Drop or choose a photo of an animal</p><input type="file" accept="image/*" id="fi"><div id="pv"></div>
  <p><label><input type="checkbox" id="vm" checked> Also read the photo with the vision model (swaps models for a moment)</label></p></div><div id="res"></div>`;
  const dz=document.getElementById('dz'),res=document.getElementById('res');
  const run=async f=>{if(!f)return;try{const img=await shrink(f);document.getElementById('pv').innerHTML=`<img src="${img}" alt="Your photo">`;
    let vt='';if(document.getElementById('vm').checked){res.innerHTML='<p class="mut">Reading the photo…</p>';try{vt=await visionText(img)}catch(e){vt=''}}
    res.innerHTML='<p class="mut">Matching against iNaturalist, Wikipedia and local records…</p>';
    const r=await api('animal/identify',{image:img,vision:vt,...geo()});
    res.innerHTML=(r.notes.length?`<p class="mut">${esc(r.notes.join(' '))}</p>`:'')+r.results.map(c=>`<div class="cand" data-id="${c.id}"><div class="ph" style="background-image:url('${esc(c.photo)}')"></div><div class="b"><b>${esc(c.common||c.name)}</b> <i class="mut">${esc(c.name)}</i><br><small class="mut">${esc(c.evidence.join(' · '))}</small></div><span class="badge">${c.confidence} · ${c.score}</span></div>`).join('');
    wireRes(res)}catch(e){res.innerHTML=`<div class="err">${esc(e.message)}</div>`}};
  const wireRes=r=>r.querySelectorAll('[data-id]').forEach(b=>b.onclick=()=>open(+b.dataset.id));
  document.getElementById('fi').onchange=e=>run(e.target.files[0]);
  dz.ondragover=e=>{e.preventDefault();dz.classList.add('on')};dz.ondragleave=()=>dz.classList.remove('on');dz.ondrop=e=>{e.preventDefault();dz.classList.remove('on');run(e.dataTransfer.files[0])};
}
document.querySelectorAll('.tabs button').forEach(b=>b.onclick=()=>{S.tab=b.dataset.t;draw()});
draw();
})();
