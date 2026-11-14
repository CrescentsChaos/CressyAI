/* ---------- chats ---------- */
/* history lives on the server (data/chats/*.json); CH is the in-memory copy every function below reads */
let CH=[],chatQ=Promise.resolve(),chatPend=0,chatOK=true;
const chats=()=>CH;
async function chatApi(method,url,body){
  const r=await fetch(url,{method,headers:HDR,body:body===undefined?undefined:JSON.stringify(body)});let d={};try{d=await r.json()}catch{}
  if(!r.ok)throw new Error(d.detail||('server answered '+r.status));return d;
}
function chatSync(fn){   // one request at a time, in order, so a quick save never overtakes an earlier one
  chatPend++;
  chatQ=chatQ.then(fn).then(()=>{chatOK=true},e=>{if(chatOK){chatOK=false;note("Couldn't save this chat to the server ("+e.message+"). It stays in this tab and the next save retries.")}}).finally(()=>{chatPend--});
  return chatQ;
}
const putChat=c=>chatSync(()=>chatApi('PUT','/api/chats/'+encodeURIComponent(c.id),c));
const delChat=id=>chatSync(()=>chatApi('DELETE','/api/chats/'+encodeURIComponent(id)));
async function initChats(){
  try{
    const d=await chatApi('GET','/api/chats'),mine=new Set(CH.map(c=>c.id));
    CH=[...CH,...(d.chats||[]).filter(c=>!mine.has(c.id))];
    const old=LS('gc.chats');   // chats saved in this browser before history moved to the server
    if(Array.isArray(old)&&old.length){
      const have=new Set(CH.map(c=>c.id)),add=old.filter(c=>c&&c.id&&Array.isArray(c.msgs)&&!have.has(c.id));
      if(add.length){await chatApi('POST','/api/chats/import',{chats:add});CH=[...CH,...add]}
      try{localStorage.removeItem('gc.chats')}catch{}
      if(add.length)note(add.length+' saved chat'+(add.length>1?'s':'')+' moved to the server',true);
    }
  }catch(e){note('Could not load saved chats from the server: '+e.message)}
  hist_();
}
addEventListener('focus',()=>{   // pick up chats saved from another tab or device
  if(busy||chatPend)return;
  chatApi('GET','/api/chats').then(d=>{if(busy||chatPend)return;const open=CH.find(c=>c.id===cid);
    CH=d.chats||[];if(open&&!CH.some(c=>c.id===open.id))CH.unshift(open);hist_()}).catch(()=>{});
});
const titleOf=()=>{const f=msgs.find(m=>m.role==='user');return(f?.content||'').replace(/\s+/g,' ').slice(0,60)||'New chat'};
const setTitle=()=>{$('#ttl').textContent=titleOf()};
function save(){
  if(!msgs.length)return;
  if(!cid)cid=Date.now().toString(36);
  const old=chats().find(c=>c.id===cid),rest=chats().filter(c=>c.id!==cid);
const rec={
  id:cid,
  pin:old?.pin,
  updated:Date.now(),
  title:titleOf().slice(0,48),
  msgs:msgs.map(({role,content,files,stats,thinkMs,plan,planDone,gen,web,kb})=>({
    role,
    content,
    stats,
    thinkMs,
    plan,
    planDone,
    gen,
    web:web&&!web.busy?web:undefined,
    kb:kb&&!kb.busy?kb:undefined,
    files:(files||[]).map(x=>({
      name:x.name,
      tokens:x.tokens,
      text:'',
      image:x.image?1:undefined,
      thumb:x.thumb,
      desc:x.desc
    }))
  }))
};

CH=[rec,...rest];
LS('gc.chats',CH.slice(0,50));
hist_();
putChat(rec);}
function load(id){
  const c=chats().find(x=>x.id===id);if(!c)return;if(busy)ctl.abort();
  cid=id;resetWS();msgs=c.msgs.map(m=>({...m,full:m.content}));msgsEl.innerHTML='';msgs.forEach(addMsg);ctx(0);hist_();setTitle();toBottom();
  if(innerWidth<=900)app.classList.remove('lopen');
}
function newChat(){if(busy)ctl.abort();save();cid=null;resetWS();msgs=[];att=[];renderTray();renderEmpty();ctx(0);hist_();setTitle();$('#input').focus();app.classList.remove('lopen')}
function hist_(){
  const b=$('#hist');b.innerHTML='';const q=$('#hq').value.trim().toLowerCase();
  const all=chats(),cs=all.filter(c=>!q||c.title.toLowerCase().includes(q)||c.msgs.some(m=>m.content.toLowerCase().includes(q))).sort((x,y)=>(!!y.pin)-(!!x.pin));
  if(!cs.length){b.innerHTML='<div class="nohist">'+(q?'No chats match.':'No saved chats yet.')+'</div>';return}
  cs.forEach(c=>{const r=document.createElement('div');r.className='hi'+(c.id===cid?' on':'');
    const n=document.createElement('button');n.type='button';n.className='t';n.textContent=c.title;n.title=c.title;n.onclick=()=>load(c.id);
    const p=document.createElement('button');p.type='button';p.className='pn'+(c.pin?' on':'');p.innerHTML=ic('spark',13);p.title=c.pin?'Unpin':'Pin to top';p.setAttribute('aria-label',p.title);
    p.onclick=()=>{CH=all.map(y=>y.id===c.id?{...y,pin:!y.pin}:y);putChat(CH.find(y=>y.id===c.id));hist_()};
    const x=document.createElement('button');x.type='button';x.className='x';x.innerHTML=ic('trash',14);x.setAttribute('aria-label','Delete chat');
    x.onclick=()=>{if(!confirm('Delete this chat?'))return;CH=CH.filter(y=>y.id!==c.id);delChat(c.id);if(c.id===cid){cid=null;resetWS();msgs=[];renderEmpty();setTitle()}hist_()};
    r.append(n,p,x);b.append(r)})
}
$('#hq').oninput=hist_;
$('#bk').onclick=()=>dl_('cressyai-chats.json',JSON.stringify({app:'cressyai',chats:chats(),memory:$('#mem').value},null,1));
$('#rs').onclick=()=>$('#rsf').click();
$('#rsf').onchange=async e=>{try{const d=JSON.parse(await e.target.files[0].text()),have=new Set(chats().map(c=>c.id));
const add=(d.chats||[]).filter(
  c=>c&&c.id&&Array.isArray(c.msgs)&&!have.has(c.id)
);

CH=[...CH,...add];

if(add.length){
  LS('gc.chats',[...chats(),...add].slice(0,100));
  chatSync(()=>chatApi('POST','/api/chats/import',{chats:add}));
}  if(d.memory&&!$('#mem').value.trim()){$('#mem').value=d.memory;saveSet()}hist_();note(add.length+' chats restored',true)}catch{note('That file is not a valid backup')}e.target.value=''};
/* ---------- message actions ---------- */
function acts(m){
  const a=m.el.querySelector('.acts'),mk=(i,t,f)=>{const b=document.createElement('button');b.type='button';b.title=t;b.innerHTML=ic(i,14)+'<span>'+t+'</span>';b.onclick=()=>f(b);a.append(b);return b};
  mk('copy','Copy',b=>{navigator.clipboard.writeText(m.content);b.innerHTML=ic('check',14)+'<span>Copied</span>';setTimeout(()=>b.innerHTML=ic('copy',14)+'<span>Copy</span>',1200)});
  m.role==='assistant'?mk('refresh','Retry',()=>regen(m)):mk('pencil','Edit',()=>edit(m));
  mk('trash','Delete',()=>{if(busy)return;const i=msgs.indexOf(m);if(i>=0)msgs.splice(i,1);m.el.remove();if(!msgs.length)renderEmpty();setTitle();save()});
}
function regen(m){
  if(busy)return;
  if(m.gen)return imgRetry(m);
  if(msgs[msgs.length-1]!==m){note('Only the last reply can be retried');return}
  msgs.pop();m.el.remove();const u=msgs.pop();u.el.remove();
  att=u.files||[];renderTray();$('#input').value=u.content;planOnce=!!m.plan;
  if(m.web?.urls?.length)webOnce={urls:m.web.urls};else if(m.web?.query)webOnce=m.web.wiki?{q:m.web.query,wiki:true,wikiOnly:!!m.web.wikiOnly}:m.web.query;
  if(m.kb?.query)kbOnce=m.kb.query;
  send(true);
}
function edit(m){
  const i=msgs.indexOf(m);if(busy||i<0)return;
  msgs.slice(i).forEach(x=>x.el.remove());msgs.length=i;att=m.files||[];renderTray();$('#input').value=m.content;grow();$('#input').focus();
  if(!msgs.length)renderEmpty();setTitle();save();
}
const lastReply=()=>[...msgs].reverse().find(m=>m.role==='assistant');
function exportMd(){dl_('chat.md',msgs.map(m=>`## ${m.role==='user'?'You':'Model'}\n\n${m.content}\n`).join('\n'))}
/* ---------- settings ---------- */
const SET=['mem','sys','temperature','top_p','top_k','repeat_penalty','max_tokens','tools','confirmrun','runtimeout','rmode','r_temperature','r_top_p','r_top_k','r_repeat_penalty','r_max_tokens','r_sys','img_size','img_steps','img_cfg','img_sampler','img_sched','img_seed','img_count','img_strength','img_enhance','img_neg','wmode','wikimode','wikilang','wikin','wikiread','wn','wread','wbudget','wtime','wcat','kbk','kbbudget','fstyle','fmath','ffont','fsize','flh','fwidth','faccent'];

$('#mode').innerHTML=Object.keys(MODES).map(k=>`<option>${k}</option>`).join('');const saveSet=()=>LS('gc.set',{...Object.fromEntries(SET.map(i=>[i,$('#'+i).value])),mode:$('#mode').value});
const st=LS('gc.set');
if(st){SET.forEach(i=>{if(st[i]!=null)$('#'+i).value=st[i]});if(st.mode&&MODES[st.mode]!=null)$('#mode').value=st.mode}else{$('#mode').value='Code helper';$('#sys').value=MODES['Code helper']}
$('#mode').onchange=()=>{$('#sys').value=MODES[$('#mode').value];saveSet()};
$('#preset').onchange=e=>{const p=PRE[e.target.value];if(p)['temperature','top_p','top_k','repeat_penalty'].forEach((i,j)=>$('#'+i).value=p[j]);saveSet()};
SET.forEach(i=>$('#'+i).addEventListener('change',saveSet));$('#sys').addEventListener('input',saveSet);$('#r_sys').addEventListener('input',saveSet);$('#mem').addEventListener('input',saveSet);

