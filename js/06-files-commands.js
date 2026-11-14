/* ---------- file browser with back / forward history ---------- */
let navStack=[''],navIdx=0,lsTok=0,items=[];
function crumbs(){
  const c=$('#crumbs'),segs=cwd?cwd.split('/'):[],root=(rootPath.split(/[\\/]/).filter(Boolean).pop())||'root';
  let h=segs.length?`<button type="button" data-p="" title="${esc(rootPath)}">${esc(root)}</button>`:`<span class="cur" title="${esc(rootPath)}">${esc(root)}</span>`;
  segs.forEach((s,i)=>{const p=segs.slice(0,i+1).join('/');h+='<span class="sep">/</span>'+(i===segs.length-1?`<span class="cur">${esc(s)}</span>`:`<button type="button" data-p="${esc(p)}">${esc(s)}</button>`)});
  c.innerHTML=h;c.scrollLeft=1e6;
}
let rootPath='';
function navUI(){$('#nb').disabled=navIdx<=0;$('#nf').disabled=navIdx>=navStack.length-1;$('#nu').disabled=!cwd;$('#nh').disabled=!cwd}
async function ls(p='',how='push'){
  const tok=++lsTok,box=$('#files');let r,d;
  try{r=await fetch('/api/fs?path='+encodeURIComponent(p));d=await r.json()}catch{if(!box.children.length)box.textContent='Server unreachable';return}
  if(tok!==lsTok)return;
  if(!r.ok){note(d.detail||'Cannot open that folder');return}   // keep the old listing on screen
  note();
  if(how==='push'&&navStack[navIdx]!==d.path){navStack=navStack.slice(0,navIdx+1);navStack.push(d.path);navIdx++}
  cwd=d.path;rootPath=d.root;items=d.items;crumbs();navUI();
  box.innerHTML=(cwd?`<div class="frow up" data-k="up" tabindex="0">${ic('left',16)}<span class="fn">..<em>parent folder</em></span></div>`:'')+
   (items.length?items.map((it,i)=>`<div class="frow${it.dir?' d':''}" data-i="${i}" tabindex="0">${ic(it.dir?'folder':'file',16)}<span class="fn">${esc(it.name)}</span>${it.dir?'':`<em>${fmtS(it.size)}</em>`}<button type="button" class="add" data-add="${i}">${it.dir?'Add all':'Add'}</button></div>`).join(''):'<div class="nohist">This folder is empty.</div>');
  box.classList.remove('fade');void box.offsetWidth;box.classList.add('fade');
}
const goBack=()=>{if(navIdx>0){navIdx--;ls(navStack[navIdx],'none')}};
const goFwd=()=>{if(navIdx<navStack.length-1){navIdx++;ls(navStack[navIdx],'none')}};
const goUp=()=>{if(cwd)ls(cwd.split('/').slice(0,-1).join('/'))};
$('#files').addEventListener('click',async e=>{
  const row=e.target.closest('.frow');if(!row)return;
  if(row.dataset.k==='up'){goUp();return}
  const it=items[+row.dataset.i],rel=(cwd?cwd+'/':'')+it.name;
  if(e.target.closest('[data-add]')){if(await attachPath(rel,it.dir)){row.classList.remove('ok');void row.offsetWidth;row.classList.add('ok')}return}
  if(it.dir)ls(rel);else if(await attachPath(rel,false)){row.classList.remove('ok');void row.offsetWidth;row.classList.add('ok')}
});
$('#crumbs').addEventListener('click',e=>{const b=e.target.closest('[data-p]');if(b)ls(b.dataset.p)});
$('#tab-files').addEventListener('keydown',e=>{
  if(e.target.matches('input,textarea'))return;
  if(e.key==='Backspace'||(e.altKey&&e.key==='ArrowLeft')){e.preventDefault();goBack()}
  else if(e.altKey&&e.key==='ArrowRight'){e.preventDefault();goFwd()}
  else if(e.key==='Enter'&&e.target.classList.contains('frow'))e.target.click();
});
$('#nb').onclick=goBack;$('#nf').onclick=goFwd;$('#nu').onclick=goUp;$('#nh').onclick=()=>ls('');
const pickFiles=()=>$('#fi').click();$('#pick').onclick=pickFiles;$('#attach').onclick=pickFiles;
$('#fi').onchange=e=>{addLocal([...e.target.files]);e.target.value=''};
$('#input').addEventListener('paste',e=>{const im=[...(e.clipboardData?.files||[])].filter(f=>IMG_RE.test(f.type));if(im.length){e.preventDefault();addLocal(im)}});
addEventListener('dragover',e=>e.preventDefault());
addEventListener('drop',e=>{e.preventDefault();addLocal([...e.dataTransfer.files])});
ls('');

/* ---------- commands, modes, presets ---------- */
const SENT=[];let si=0,cid=null,FILES=null;
const EXT={python:'py',javascript:'js',js:'js',typescript:'ts',ts:'ts',tsx:'tsx',jsx:'jsx',html:'html',css:'css',json:'json',bash:'sh',sh:'sh',sql:'sql',java:'java',c:'c',cpp:'cpp',go:'go',rust:'rs',yaml:'yml',yml:'yml',markdown:'md',diff:'diff',csharp:'cs',php:'php',ruby:'rb'};
const MODES={'General':'','Code helper':'You are a senior software engineer helping a junior/intermediate developer. Be concise and correct. Use fenced code blocks with a language tag. State assumptions, point out edge cases and security issues, and say when you are unsure instead of guessing.','Code reviewer':'You are a strict but kind code reviewer. List issues by severity (bug, security, performance, readability), cite the snippet, and propose a concrete fix.','Debugger':'You are a debugging expert. List the most likely causes in order of probability and how to verify each one. Ask for missing info if needed.','Tutor':'You are a patient programming tutor. Explain step by step with small examples and end with one check-in question.'};
const PRE={Precise:[.2,.9,40,1.1],Balanced:[.7,.95,40,1.1],Creative:[1,.98,60,1.05]};
const CMDS={explain:['c','Explain what this code does step by step for a junior developer. Note pitfalls.'],fix:['c','Find the bug(s) in this code or error. Explain the cause, then give corrected code.'],review:['c','Review this code for bugs, security, performance and readability. Be specific and propose fixes.'],test:['c','Write unit tests for this code (pytest/Jest unless another framework is implied). Cover edge cases.'],refactor:['c','Refactor for readability and maintainability without changing behaviour. Briefly explain each change.'],doc:['c','Add clear docstrings/comments and a short usage example.'],bigo:['c','Analyze time and space complexity and suggest improvements.'],commit:['c','Write a Conventional Commits message (subject <=72 chars, optional body) for this diff.'],regex:['d','Write a regular expression for the following, with a short explanation and 3 test examples'],sql:['d','Write an SQL query for the following (state the dialect you assume)'],shell:['d','Give the shell command for the following and explain each flag'],convert:['d','Convert the code below to the language named first, keeping behaviour identical'],eli5:['d','Explain simply, with an analogy and a tiny code example'],name:['d','Suggest 8 good names (variables/functions/classes) for']};
Object.assign(MODES,{
'Writer':'You are an expert writing partner. Write clear, natural prose in the requested tone. Match length to the request, avoid filler and cliches, and offer one short alternative only when useful.',
'Analyst':'You are a careful data and business analyst. Work step by step, show calculations, state assumptions, present results in tables when helpful, and flag anything that cannot be concluded from the data given.',
'Study buddy':'You are a patient tutor. Explain from first principles with simple examples, check understanding with a short question at the end, and correct mistakes kindly.',
'Brainstorm':'You are a creative brainstorming partner. Give varied, concrete ideas (not generic ones), group them, and mark the 2-3 most promising with a one-line reason.',
'Translator':'You are a professional translator. Translate faithfully and naturally, keep tone and formatting, and add a short note only for ambiguous phrases.'});
Object.assign(CMDS,{
summarize:['d','Summarize the following in 5 short bullets, then a one-sentence takeaway'],
tldr:['d','Give a TL;DR in at most two sentences for'],
rewrite:['d','Rewrite this to be clearer and more concise while keeping the meaning'],
proofread:['d','Proofread this. Fix grammar, spelling and punctuation, then list the changes you made'],
translate:['d','Translate to the language named on the first line (or to English if none is named), keeping tone and formatting'],
email:['d','Write a clear, polite, professional email for this situation. Include a subject line'],
outline:['d','Create a structured outline (headings and sub-points) for'],
plan:['d','Make a practical step-by-step plan with milestones and rough time estimates for'],
brainstorm:['d','Brainstorm 10 distinct, concrete ideas for'],
proscons:['d','Compare options with a pros and cons table, then give a recommendation for'],
table:['d','Organize the following information into a clean markdown table'],
quiz:['d','Write a 5-question quiz with answers and short explanations on'],
steps:['d','Explain, as numbered steps a beginner can follow, how to'],
compact:['d','Summarize this chat to free up context space'],continue:['d','Keep going after a reply was cut off'],remember:['d','Save a fact to Memory so it is used in every chat'],think:['d','Reason first for this one message: the reasoning model thinks (coder unloaded meanwhile), then the coder answers']});
SUGG.push(['Summarize text','/summarize','Paste an article or attach a file'],['Write an email','/email','Describe the situation, get a ready draft'],['Make a plan','/plan','Turn a goal into steps and milestones'],['Brainstorm','/brainstorm','Ten concrete ideas on any topic']);
Object.assign(CMDS,{
chart:['d','Make a chart (reply with one short sentence and a single chart block) from the data or request below. Use only the numbers given'],
diagram:['d','Draw the requested diagram (reply with one short sentence and a single mermaid block). Choose the clearest Mermaid 12 diagram type. For UML use-case diagrams, use Mermaid 12 syntax beginning with usecase-beta, declare actors with actor ID("Display name"), put use cases inside systemBoundary "System name" ... end, and connect them with -->. Use ..> : include TARGET or ..> : extend TARGET for those relationships, and --|> for generalization.'],
newfile:['d','Create the file(s) described below. Use one file: block per file with the complete contents'],
edit:['d','Change the attached file(s) as requested below. Use edit: blocks with SEARCH/REPLACE sections, not a full rewrite'],
run:['d','Give me the terminal command(s), in one bash block, to do this']});
SUGG.push(
  ['Plan a feature','/plan','Get a step-by-step plan first, then approve it'],
  ['Make a chart','/chart','Bar, line, pie and more from your data'],
  ['Create a diagram','/diagram','Flowcharts, UML use-case and more'],
  ['Create a file','/newfile','Generate files you can save or download'],
  ['Edit a file','/edit','Attach a file, describe the change, review the diff']
);Object.assign(CMDS,{imagine:['d','Generate an image with Stable Diffusion XL from your description (aliases: /img, /draw)'],
imgedit:['d','Edit the attached image, or the last generated one, as you describe (alias: /retouch)']});
SUGG.push(['Generate an image','/imagine','Stable Diffusion XL on your machine'],['Edit an image','/imgedit','Change the last or an attached picture']);
let HELP=`**Slash commands** (paste code after the command, attach files, or use the last code block in the chat)\n\n`+Object.entries(CMDS).map(([k,[t,d]])=>`- \`/${k}\` ${t==='c'?'code':'description'} — ${d}`).join('\n')+`\n\n\`/commit\` with nothing else uses your **staged** git diff; \`/review\` uses your **working** diff.\n\n**@file** — type \`@path/to/file.py\` to attach a file (Tab completes).\n\n**Files panel:** click a folder to enter it, **Back** (button, Backspace or Alt+←) to return, Alt+→ forward, breadcrumbs to jump up.\n\n**Images:** attach, drop or paste a PNG / JPG / WebP / GIF. Qwen2.5-VL reads it first (the coder is unloaded meanwhile), is unloaded again, then the coder comes back and answers using what was extracted. Open *Image details* under your message to see it.\n\n**Reasoning:** the *Reason* button (or \`/think\`) makes a separate reasoning model (gemma-4-12b-it / Qwen3-8B from your models folder) think the task through first while the coder stays unloaded; then the coder loads and answers using its notes. Load, unload and tweak every model in the *Model* tab.\n\n**Shortcuts:** Enter send · Shift+Enter newline · Esc stop / close popup · ↑ recall previous message · Alt+N new chat · Alt+L focus input · Alt+R retry last reply · Alt+C copy last reply · Alt+E export chat · Alt+S tools panel · Alt+B chat list · Alt+/ this help · Alt+I image mode · Alt+G new seed for the last image · Alt+V edit the last image`;
HELP+=`\n\n**Images (Stable Diffusion XL):** \`/imagine a lighthouse at dawn, oil painting\` makes an image; \`/imgedit make it night\` edits the attached image, or the last one generated. The *Image* button (Alt+I) turns every message into an image prompt, and an attached picture is then edited instead. Under each image: enlarge, download, edit, vary, re-roll (new seed) and save to the project. The coder writes the Stable Diffusion prompt for you (turn that off in Settings → Images); it is unloaded while the image is made and comes back afterwards. Editing is img2img: describe how the picture should look afterwards, and raise or lower *Edit strength* to change more or less.`;
function preview(lang,code){
  let html=code;
  if(/^(js|javascript)$/.test(lang))html='<body style="font:14px monospace;padding:12px"><pre id=o style="white-space:pre-wrap"></pre><script>const o=document.getElementById("o"),p=(...a)=>o.textContent+=a.map(x=>typeof x==="object"?JSON.stringify(x):x).join(" ")+"\\n";console.log=p;onerror=m=>p("Error: "+m);try{'+code+'\n}catch(e){p("Error: "+e.message)}<\/script>';
  else if(lang==='svg')html='<body style="margin:0;display:grid;place-items:center;height:100vh">'+code;
  const d=document.createElement('div');d.className='pv';
  d.innerHTML='<div class="pvb"><div class="pvh"><b>'+(/^js|^javascript/.test(lang)?'Output':'Preview')+'</b><span>sandboxed, runs in your browser only</span><button type="button" aria-label="Close">'+ic('x',16)+'</button></div><iframe sandbox="allow-scripts allow-forms allow-modals"></iframe></div>';
  d.querySelector('iframe').srcdoc=html;
  const close=()=>{d.remove();removeEventListener('keydown',k,true)},k=e=>{if(e.key==='Escape'){e.stopPropagation();close()}};
  addEventListener('keydown',k,true);d.querySelector('button').onclick=close;d.onclick=e=>{if(e.target===d)close()};document.body.append(d)
}
async function complete(messages,max){
  const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},signal:ctl.signal,body:JSON.stringify({messages,max_tokens:max,temperature:.2})});
  const rd=r.body.getReader(),dec=new TextDecoder();let out='',buf='';
  for(;;){const{done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});let i;
    while((i=buf.indexOf('\n\n'))>=0){const l=buf.slice(0,i);buf=buf.slice(i+2);if(l.startsWith('data: ')){try{const e=JSON.parse(l.slice(6));if(e.type==='token')out+=e.text;else if(e.type==='error')throw new Error(e.message)}catch(x){if(x.message&&!(x instanceof SyntaxError))throw x}}}}
  return split(out).ans||out;
}
async function compact(){
  if(busy||msgs.length<2){note('Nothing to compact yet');return}
  setBusy(true);ctl=new AbortController();note('Summarizing this chat…',true);
  try{
    const hist=msgs.map(x=>({role:x.role,content:x.role==='user'?withFiles(x.content,x.files||[]):asst(x)}));
    const sum=await complete([{role:'system',content:'You compress conversations. Keep every decision, fact, name, file name, code detail and open task. Be brief.'},...hist,{role:'user',content:'Summarize everything above as compact bullet notes so the conversation can continue from them alone.'}],600);
    const a='Understood. I have the summary and will continue from there.';
    msgs=[{role:'user',content:'Summary of our conversation so far:\n\n'+sum,full:sum},{role:'assistant',content:a,full:a}];
    msgsEl.innerHTML='';msgs.forEach(addMsg);ctx(0);save();note('Chat compacted',true);
  }catch(e){note(e.name==='AbortError'?'Cancelled':'Compact failed: '+e.message)}
  setBusy(false);
}
function dl_(n,t){const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([t]));a.download=n;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),1000)}
function localMsg(md){addMsg({role:'assistant',content:md,full:md,stats:null});toBottom()}
function lastCode(){for(let i=msgs.length-1;i>=0;i--){const m=[...msgs[i].content.matchAll(/```[\w+-]*\n([\s\S]*?)```/g)];if(m.length)return m.pop()[1].trim()}return''}
async function expand(t){
  const mt=t.match(/^\/(\w+)\s*([\s\S]*)$/);
  if(mt&&mt[1]==='compact'){$('#input').value='';grow();compact();return null}
  if(mt&&mt[1]==='continue'){return 'Continue exactly where you stopped. Do not repeat anything.'}
  if(mt&&mt[1]==='plan'){const g=mt[2].trim();if(!g&&!att.length){note('Describe what to build after /plan');return null}planOnce=true;return g||'Plan the work described in the attached file(s).'}
  if(mt&&mt[1]==='think'){const g=mt[2].trim();if(!g&&!att.length){note('Describe the task after /think');return null}reasonOnce=true;return g||'Think through the work described in the attached file(s) and answer it.'}
  if(mt&&mt[1]==='remember'){const f=mt[2].trim();if(!f){note('Write a fact after /remember');return null}
    const m=$('#mem');m.value=(m.value.trim()?m.value.trim()+'\n':'')+'- '+f;saveSet();$('#input').value='';grow();localMsg('Saved to **Memory**: '+f);return null}
  if(mt&&mt[1]==='help'){$('#input').value='';grow();localMsg(HELP);return null}
  if(mt&&mt[1]==='kb'){const q=mt[2].trim();if(!q){note('Write your question after /kb');return null}
    if(!kbActive().length){note('Add documents in the Knowledge tab first.');showTab('kb');return null}kbOnce=q;return q}
  if(mt&&mt[1]==='wiki'){const q=mt[2].trim();if(!q){note('Write what to look up after /wiki');return null}webOnce={q,wiki:true,wikiOnly:true};return q}
  if(mt&&(mt[1]==='web'||mt[1]==='search')){const q=mt[2].trim();if(!q){note('Write what to look up after /'+mt[1]);return null}webOnce=q;return q}
  if(mt&&CMDS[mt[1]]){
    const[k,tpl]=CMDS[mt[1]];let rest=mt[2].trim();
    if(k==='d'){if(!rest&&!att.length){note('Add text after /'+mt[1]+' or attach a file');return null}return rest?tpl+':\n'+rest:tpl+' (see the attached file(s)).'}
    if(!rest&&!att.length){
      const g={commit:'staged',review:'work'}[mt[1]];
      if(g){const r=await fetch('/api/git?mode='+g),d=await r.json();if(!r.ok){note(d.detail);return null}addAtt(d)}
      else{rest=lastCode();if(!rest){note('Paste code after /'+mt[1]+', attach a file, or use @file');return null}}
    }
    return tpl+(rest?'\n\n```\n'+rest+'\n```':'\n\n(See the attached file(s).)');
  }
  for(const p of new Set([...t.matchAll(/(?:^|\s)@([\w.\/-]+)/g)].map(x=>x[1]))){const r=await fetch('/api/file?path='+encodeURIComponent(p));if(r.ok)addAtt(await r.json())}
  return t;
}
