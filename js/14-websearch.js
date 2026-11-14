/* ================= web search (SearXNG) ================= */
const _wl=LS('gc.web');
let webOn=_wl===null||_wl===undefined?true:!!_wl,
    webAuto=_wl===null||_wl===undefined,
    webOnce=null;const hostOf=u=>{try{return new URL(u).hostname.replace(/^www\./,'')}catch{return''}};
const okUrl=u=>/^https?:\/\//i.test(u||'');
const HEDGE=/\b(?:as of my (?:last )?(?:knowledge|training|update)|my (?:knowledge|training)(?: data)? (?:cut-?off|ends|only goes)|knowledge cut-?off|(?:i (?:can(?:no|')t|cannot|don't|do not|am unable to) (?:browse|access (?:the )?(?:internet|web|real-?time|current|live))|i don't have (?:access to )?(?:real-?time|current|live|up-to-date))|(?:no|without) (?:real-?time|internet) access)\b/i;
const FRESH=/\b(latest|newest|recent(?:ly)?|current(?:ly)?|today|tonight|yesterday|tomorrow|this (?:week|month|year|morning|weekend)|right now|news|headlines?|breaking|prices?|cost of|how much (?:is|does|are)|release[sd]?|version|updates?|updated|changelog|who (?:is|are|won|wins)|what happened|scores?|weather|forecast|stocks?|exchange rate|trending|rumou?rs?|announced?|20(?:2[4-9]|3\d))\b/i;
function pickUrls(t){const seen=new Set();return[...t.replace(/```[\s\S]*?```/g,' ').matchAll(/https?:\/\/[^\s<>"')\]]+/g)].map(x=>x[0].replace(/[.,;:!?]+$/,'')).filter(u=>!seen.has(u)&&seen.add(u)).slice(0,3)}
function makeQuery(text){
  const clean=x=>x.replace(/```[\s\S]*?```/g,' ').replace(/https?:\/\/\S+/g,' ').replace(/(?:^|\s)@[\w.\/-]+/g,' ').replace(/\s+/g,' ').trim();
  let q=clean(text);
  for(let i=0;i<3;i++)q=q.replace(/^(?:hey|hi|hello|ok|okay|so|and|also|please|pls|can you|could you|would you|will you|tell me|i want to know|i need to know|do you know|search(?: the web)?(?: for)?|look up|find(?: out)?|what is|what's|whats)\b[\s,:-]*/i,'');
  q=q.replace(/[?!.\s]+$/,'');
  if(q.split(' ').filter(Boolean).length<=3){   // short follow-up: borrow the topic from the previous question
    const prev=[...msgs].slice(0,-2).reverse().find(x=>x.role==='user');
    if(prev)q=(clean(prev.content).slice(0,120).replace(/[?!.\s]+$/,'')+' '+q).trim();
  }
  return q.slice(0,200)||clean(text).slice(0,200);
}
const CHIT=/^(?:hi|hello|hey|yo|thanks?|thank you|ok(?:ay)?|cool|nice|great|awesome|yes|no|yep|nope|sure|bye|good (?:morning|night|evening)|lol|got it|makes sense|perfect)\b[\s!.?,]*$/i;

const ABOUT_CHAT=/^(?:rewrite|rephrase|reword|shorten|expand|summari[sz]e|translate|proofread|make (?:it|that|this)|explain (?:that|this|it)|why did you|continue|go on|again|more|elaborate|try again|repeat|say that)\b/i;

function localOnly(text,typed,files){
  const t=(typed||text).trim();
  return !t||
    /^\/(?!web\b|search\b)/.test(t)||
    (files&&files.length>0)||
    text.includes('```')||
    CHIT.test(t)||
    ABOUT_CHAT.test(t)||
    (t.split(/\s+/).length<3&&!/[?]/.test(t))||
    planMode||
    imgMode;
}

function wantWeb(text,once,typed,files){
  if(once){const o=typeof once==='string'?{q:once}:{...once};if(!o.urls&&o.wiki===undefined&&o.q&&wantWiki(o.q,o.q,files))o.wiki=true;return o}
  if(/^Continue exactly where you stopped/.test(text))return null;
  const wiki=wantWiki(text,typed,files);let web=null;
  if(webOn){
    const urls=pickUrls(text);if(urls.length)return{urls};
    const mode=$('#wmode').value;
    if(mode==='always'||(mode==='eager'&&!localOnly(text,typed,files))||(mode==='auto'&&!text.includes('```')&&FRESH.test(text)))web={q:makeQuery(text)};
  }
  if(web)return wiki?{...web,wiki:true}:web;
  return wiki?{q:makeQuery(text),wiki:true,wikiOnly:true}:null;   // Wikipedia works even when SearXNG is off
}
const webCats=text=>{const c=$('#wcat').value;return c==='general'&&/\b(news|headlines?|breaking)\b/i.test(text)?'news,general':c};
function webBlock({q,urls,results,answer,budget,wikiOnly}){
  const read=results.filter(r=>r.text),per=Math.max(600,Math.floor(budget/Math.max(1,read.length))),d=new Date().toISOString().slice(0,10);
  const head=urls?`The user shared ${results.length>1?'these links':'a link'}; the page text is below (fetched ${d}). Base your answer on it and cite with [1], [2] right after the claim a source supports. If a page could not be read, say so.`
    :wikiOnly?`Wikipedia excerpts for \"${q}\" (fetched ${d}). Use them as reference for facts; they are encyclopedic and can lag behind recent events. Cite with [1], [2] right after the claim a source supports, cite nothing else, and if the excerpts do not answer the question, say so instead of guessing.`
    :`Live web results for "${q}" (fetched ${d}). Use them for anything that may have changed since your training. Cite with [1], [2] right after the claim a source supports, cite nothing else, and if the results do not answer the question, say so instead of guessing.${results.some(r=>/wikipedia\.org$/.test(r.domain||''))?' Wikipedia articles are in the list: prefer them for background facts and the other sources for anything recent.':''}`;
  const body=results.map((r,i)=>{
    let t=`[${i+1}] ${r.title} (${r.domain||hostOf(r.url)}${r.published?', '+r.published:''})`;
    if(r.snippet&&!urls)t+='\n'+r.snippet.slice(0,300);
    if(r.text)t+='\nPage text:\n'+r.text.slice(0,per);else if(r.read_error)t+='\n(Could not read this page: '+r.read_error+')';
    return t}).join('\n\n');
  return head+(answer?'\nQuick answer from the search engine: '+answer:'')+'\n\n'+body;
}
async function webStep(m,text,req){
  const urls=req.urls,wk=!urls&&!!req.wiki,wo=wk&&!!req.wikiOnly,flags={wiki:wk,wikiOnly:wo},q=urls?'':(req.q||makeQuery(text)),shown=urls?urls.join('  '):q;
  const set=v=>{m.web={...flags,...v};paintWeb(m)};
  const ph=()=>m.el.querySelector('.foot .ph');
  const say=l=>{if(m.stop1){m.stop1();m.stop1=null}const e=ph();if(e)e.textContent=l};
  const rest=()=>{const e=ph();if(e&&!m.stop1)m.stop1=ticker(e,PHRASES,2600)};
  set({busy:true,query:shown,urls,results:[]});say(urls?'Reading the link…':wo?'Looking it up on Wikipedia…':wk?'Searching the web and Wikipedia…':'Searching the web…');
  try{
    let d={},results;
    const call=async(path,body)=>{const r=await fetch(path,{method:'POST',headers:HDR,body:JSON.stringify(body),signal:ctl.signal});const j=await r.json().catch(()=>({}));if(!r.ok)throw new Error(j.detail||('the server answered '+r.status));return j};
    const soft=(k,pr)=>pr.then(x=>({[k]:x})).catch(e=>{if(e.name==='AbortError')throw e;return{[k+'Err']:e.message}});
    if(urls){d=await call('/api/web/read',{urls});results=d.pages.map((p,i)=>({title:p.title||hostOf(p.url||urls[i])||urls[i],url:p.url||urls[i],domain:hostOf(p.url||urls[i]),snippet:'',text:p.ok?p.text:'',read_error:p.ok?'':p.error}))}
    else{
      const jobs=[];
      if(!wo)jobs.push(soft('web',call('/api/web/search',{query:q,n:+$('#wn').value||6,read:+$('#wread').value,time_range:$('#wtime').value,categories:webCats(text)})));
      if(wk)jobs.push(soft('wiki',call('/api/wiki/search',{query:q,n:+$('#wikin').value||3,read:+$('#wikiread').value,lang:$('#wikilang').value||'en'})));
      const g=Object.assign({},...await Promise.all(jobs));
      let wr=g.wiki?.results||[];const real=wr.filter(r=>!r.disambiguation);if(real.length)wr=real;   // skip "may refer to" pages
      const seen=new Set(),key=u=>String(u).replace(/^https?:\/\/(www\.)?/i,'').replace(/\/$/,'').toLowerCase();
      results=[...wr,...(g.web?.results||[])].filter(r=>{const k=key(r.url);return !seen.has(k)&&seen.add(k)});
      d={seconds:Math.max(g.web?.seconds||0,g.wiki?.seconds||0),answer:g.web?.answer||''};
      const errs=[g.webErr,g.wikiErr].filter(Boolean);
      if(!results.length&&errs.length)throw new Error(errs.join(' '));
    }
    const budget=Math.min(+$('#wbudget').value||8000,Math.floor((info?.n_ctx||8192)*1.0));
    const kept=results.map(x=>({title:x.title,url:x.url,domain:x.domain||hostOf(x.url),snippet:(x.snippet||'').slice(0,220),published:x.published||'',read:!!x.text,err:x.read_error||''}));
    const reads=kept.filter(x=>x.read).length;
    if(!results.length){set({query:shown,urls,results:[],error:wo?'Wikipedia has no matching article.':'No results came back for that search.'});return{block:wo?`(A Wikipedia lookup for \"${q}\" found no matching article. Answer from your own knowledge and say that Wikipedia had nothing.)`:`(A live web search for \"${q}\" returned nothing. You have no fresh data, so say that plainly if the question needs it.)`}}
    set({query:shown,urls,results:kept,read:reads,seconds:d.seconds||0});
    return{block:webBlock({q,urls,results,answer:d.answer,budget,wikiOnly:wo})};
  }catch(e){
    if(e.name==='AbortError'){set({...(m.web||{}),busy:false,error:'Stopped.'});throw e}
    set({query:shown,urls,results:[],error:e.message});
    return{block:wo?`(A Wikipedia lookup was requested but failed: ${e.message} Answer from your own knowledge and say that the lookup failed.)`:`(A live web search was requested but failed: ${e.message} You have no fresh data. If the question needs it, say so; never pretend you searched.)`};
  }finally{rest()}
}
function addWeb(payload,w){
  const last=payload.messages[payload.messages.length-1];if(!last||last.role!=='user'||!w.block)return;
  last.content+='\n\n---\n'+w.block;
}
function paintWeb(m){
  const c=m.el?.querySelector('.web');if(!c)return;const w=m.web;
  if(!w){c.hidden=true;m._ws='';return}
  const sig=JSON.stringify([w.busy,w.error,w.query,w.results?.length,w.read]);if(sig===m._ws)return;m._ws=sig;
  c.hidden=false;c.classList.toggle('live',!!w.busy);
  const n=w.results?.length||0,q=c.querySelector('.wq'),pre=w.urls?'Reading ':w.wikiOnly?'Looking up Wikipedia for “':w.wiki?'Searching the web and Wikipedia for “':'Searching the web for “';
  q.textContent=w.busy?(w.urls?'Reading the link…':pre+w.query+'”…'):w.error==='Stopped.'?'Search stopped':(w.error&&!n)?(w.wikiOnly?'Wikipedia lookup failed':'Web search failed'):w.urls?'Read '+(w.read||0)+' of '+n+' link'+(n===1?'':'s'):(w.wikiOnly?'Wikipedia “':'Searched “')+w.query+'” · '+n+' source'+(n===1?'':'s')+(w.read?' · read '+w.read:'');
  q.title=q.textContent;q.classList.toggle('shimmer',!!w.busy);
  const l=c.querySelector('.web-l');l.innerHTML='';
  (w.results||[]).forEach((r,i)=>{
    const li=document.createElement('li'),a=document.createElement(okUrl(r.url)?'a':'div'),nn=document.createElement('span'),b=document.createElement('div'),t=document.createElement('div'),d=document.createElement('div');
    a.className='src';if(a.tagName==='A'){a.href=r.url;a.target='_blank';a.rel='noopener noreferrer'}
    nn.className='n';nn.textContent=i+1;t.className='t';t.textContent=r.title||r.url;d.className='d';d.textContent=[r.domain,r.published].filter(Boolean).join(' · ');
    if(r.read||r.err){const k=document.createElement('span');k.className='rd'+(r.err?' bad':'');k.textContent=r.read?'read':'not read';if(r.err)k.title=r.err;d.append(k)}
    b.append(t,d);if(r.snippet){const sn=document.createElement('div');sn.className='s';sn.textContent=r.snippet;b.append(sn)}
    a.append(nn,b);li.append(a);l.append(li)});
  const e=c.querySelector('.web-err'),bad=w.error&&w.error!=='Stopped.';e.hidden=!bad;e.textContent=bad?w.error:'';
  if(bad&&!m._wt)c.classList.add('open');
}
const WEB_SYS='This app searches the web for you. When a message ends with live web results, answer from them and cite [1], [2]. Never say you cannot browse or that your knowledge has a cutoff when results are present; if there are no results, say so plainly instead of guessing at recent facts.';
const todayLine=()=>'Today is '+new Date().toLocaleDateString('en-US',{weekday:'long',year:'numeric',month:'long',day:'numeric'})+'.';
async function webStatus(force){try{const r=await fetch('/api/web/status'+(force?'?force=1':''));return await r.json()}catch{return{enabled:false,ok:false,why:'The server did not answer.',url:''}}}
function paintWebSt(s){const e=$('#wst');e.textContent=!s.enabled?'● off':s.ok?'● connected':'● not reachable';e.className='wst '+(s.ok?'ok':'bad');$('#wnote').textContent=s.ok?'SearXNG at '+s.url+' is answering.':(s.why||'')}
const wikiActive=()=>($('#wikimode')?.value||'auto')!=='off';
function fineText(){const wk=wikiActive();$('.fine').textContent=webOn?'The model runs on your machine. Web is on: your search words go out through your SearXNG'+(wk?' and to Wikipedia':'')+'.':wk?'The model runs on your machine. Wikipedia lookups send your search words to wikipedia.org.':'Runs 100% on your machine. Nothing leaves it.'}
function setWeb(v,persist=true){
  webOn=v;
  $('#web').classList.toggle('on',v);
  $('#web').setAttribute('aria-pressed',v);
  if(persist)LS('gc.web',v);
  fineText();
}$('#web').onclick=async()=>{
  webAuto=false;const v=!webOn;
  if(v){note('Checking SearXNG…',true);const s=await webStatus(true);note();paintWebSt(s);
    if(!s.ok){toast(s.why||'Web search is not available.','bad');showTab('set');return}}
setWeb(v);
note(v?'Web is on: your questions are searched live unless they are about code or attached files. Use /web to force it.':'',v);
$('#input').focus();};
$('#wtest').onclick=async()=>{$('#wnote').textContent='Testing…';paintWebSt(await webStatus(true))};
$$('.tabs button').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.tab==='set')webStatus(false).then(paintWebSt)}));
setWeb(webOn,!webAuto);

if(webOn){
  webStatus(false).then(s=>{
    if(!s.ok){
      setWeb(false,!webAuto);
      note('Web search is waiting for SearXNG: '+
        (s.why||'not reachable')+
        ' (see Settings → Web search). It turns itself on once it answers.');
    }
  });
}

setInterval(()=>{
  if(webAuto&&!webOn&&!document.hidden){
    webStatus(false).then(s=>{
      if(s.ok&&webAuto){
        setWeb(true,false);
        note('Web search is on.',true);
      }
    });
  }
},15000);
