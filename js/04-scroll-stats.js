/* ---------- scrolling ---------- */
const toBottom=()=>{stick=true;chat.scrollTop=chat.scrollHeight;$('#down').hidden=true};
chat.addEventListener('scroll',()=>{stick=chat.scrollHeight-chat.scrollTop-chat.clientHeight<48;$('#down').hidden=stick},{passive:true});
chat.addEventListener('wheel',e=>{if(e.deltaY<0)stick=false},{passive:true});
chat.addEventListener('touchmove',()=>{stick=chat.scrollHeight-chat.scrollTop-chat.clientHeight<48},{passive:true});
new ResizeObserver(()=>{if(stick)chat.scrollTop=chat.scrollHeight}).observe(msgsEl);
$('#down').onclick=toBottom;

/* ---------- empty state ---------- */
const SUGG=[['Explain code','/explain','Paste code and get a step-by-step walkthrough'],['Fix a bug','/fix','Drop in code or an error and find the cause'],['Review my diff','/review','Reviews your working git diff for bugs and security'],['Write tests','/test','Unit tests with edge cases for your code'],['Commit message','/commit','Conventional Commit from your staged diff'],['Regex helper','/regex','Describe the pattern, get the regex plus examples']];
const greet=()=>{const h=new Date().getHours();return h<5?'Burning the midnight oil':h<12?'Good morning':h<18?'Good afternoon':'Good evening'};
const HERO=['Plan a feature','Create a file','Edit a file','Make a chart','Explain code','Fix a bug'];   // the rest stay available through /commands
function renderEmpty(txt){
  msgsEl.innerHTML=`<div class="hero" id="empty"><img class="hlogo" src="/assets/logo.png" alt="CressyAI"><h1>${greet()}.<br><b>What are we building?</b></h1><p>${txt||'Your private coding helper. Attach files with @, run commands with /, and everything stays on this machine.'}</p><div class="cards">${HERO.map(n=>SUGG.findIndex(x=>x[0]===n)).filter(i=>i>=0).map(i=>{const[t,c,d]=SUGG[i];return`<button type="button" class="sc" data-s="${i}"><b>${t}</b><span>${d}</span><code>${c}</code></button>`}).join('')}</div></div>`;
  stick=false;chat.scrollTop=0;$('#down').hidden=true;
}
msgsEl.addEventListener('click',e=>{const b=e.target.closest('[data-s]');if(!b)return;const i=$('#input');i.value=SUGG[+b.dataset.s][1]+' ';grow();i.focus();suggest()});

/* ---------- stats panel ---------- */
function setDl(el,rows){el.innerHTML=rows.map(([k,v])=>`<dt>${esc(k)}</dt><dd>${esc(v??'–')}</dd>`).join('')}
function showInfo(i){
  info=i;const md=i.metadata||{},a=md['general.architecture']||'?',k=x=>md[a+'.'+x];
  const bpw=i.n_params?(i.file_size*8/i.n_params).toFixed(2):null,q=FT[md['general.file_type']]||md['general.file_type'];
  setDl($('#info'),[['Status',i.loaded?'loaded':'not loaded (use the Models section above)'],['Name',md['general.name']||i.file],['File',i.file],['Size',fmtB(i.file_size)],['Architecture',a],
   ['Parameters',i.n_params?(i.n_params/1e9).toFixed(2)+' B':null],['Quantization',q],
   ['Bits per weight',bpw],['Layers',k('block_count')],['Embedding size',i.n_embd],
   ['Heads / KV heads',k('attention.head_count')+' / '+(k('attention.head_count_kv')||'–')],['Vocabulary',i.n_vocab?.toLocaleString()],
   ['Trained context',k('context_length')],['Loaded context',i.n_ctx],['GPU layers',i.n_gpu_layers],['Threads',i.n_threads],['Load time',i.load_seconds+' s'],['Vision (on demand)',i.vision||'not found']]);
  const f=$('#mf').value.toLowerCase();
  setDl($('#meta'),Object.entries(md).filter(([k])=>k.toLowerCase().includes(f)));
  paintPill();
  ctx(0);
}
function ctx(used){if(info?.n_ctx&&used>info.n_ctx*.85)note('Context almost full. Type /compact to summarize this chat and free space.');
  const n=info?.n_ctx||0,w=n?Math.min(100,used/n*100)+'%':'0';$('#ctxbar').style.width=w;$('#ctxmini').style.width=w;
  $('#ctxtxt').textContent=used.toLocaleString()+' / '+n.toLocaleString()+' tokens';
}
function spark(){
  const h=hist.slice(-30);if(h.length<2){$('#spark').innerHTML='';return}
  const mx=Math.max(...h),mn=Math.min(...h),r=(mx-mn)||1;
  const pts=h.map((v,i)=>`${(i/(h.length-1)*296+2).toFixed(1)},${(36-(v-mn)/r*30).toFixed(1)}`).join(' ');
  $('#spark').innerHTML=`<polyline points="${pts}" fill="none" stroke="var(--acc)" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>`;
}
function params(){const g=id=>+$('#'+id).value;return{temperature:g('temperature'),top_p:g('top_p'),top_k:g('top_k'),repeat_penalty:g('repeat_penalty'),max_tokens:g('max_tokens')}}
function setBusy(b){busy=b;const g=$('#go');g.classList.toggle('stop',b);g.setAttribute('aria-label',b?'Stop':'Send')}

