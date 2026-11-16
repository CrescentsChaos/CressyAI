/* ---------- open any folder at any time, and keep the file list fresh by itself ---------- */
function folderModal(){
  const M=modal('Open folder');M.sub.textContent='The file list, terminal, tools and agent mode work in this folder';
  const mkEl=(t,c,x)=>{const e=document.createElement(t);if(c)e.className=c;if(x!==undefined)e.textContent=x;return e};
  const row=mkEl('div','fpath'),inp=mkEl('input'),go=mkEl('button','','Go');
  inp.type='text';inp.spellcheck=false;inp.placeholder='Type or paste a folder path, then press Enter';go.type='button';row.append(inp,go);
  const places=mkEl('div','fplaces'),list=mkEl('div','flist'),err=mkEl('div','agwarn');err.hidden=true;
  M.body.append(row,places,list,err);
  let cur='',sep='/';
  const join=(a,b)=>a.endsWith(sep)?a+b:a+sep+b;
  const dirRow=(label,hint,path)=>{
    const r=mkEl('div','frow d');r.tabIndex=0;r.innerHTML=ic('folder',16);r.append(mkEl('span','fn',label));
    if(hint)r.append(mkEl('em','',hint));
    r.onclick=()=>show(path);r.onkeydown=e=>{if(e.key==='Enter')show(path)};return r;
  };
  async function show(p){
    let r,d;
    try{r=await fetch('/api/browse?path='+encodeURIComponent(p||''));d=await r.json()}catch{err.hidden=false;err.textContent='Server unreachable';return}
    if(!r.ok){err.hidden=false;err.textContent=d.detail||'Cannot open that folder';return}
    err.hidden=!d.error;err.textContent=d.error||'';
    cur=d.path;sep=d.sep||'/';inp.value=d.path;
    places.replaceChildren(...[...(d.places||[]),...(d.drives||[]).map(x=>({name:x,path:x}))].map(pl=>{
      const b=mkEl('button','',pl.name);b.type='button';b.title=pl.path;b.onclick=()=>show(pl.path);return b}));
    list.replaceChildren();
    if(d.parent)list.append(dirRow('..','parent folder',d.parent));
    d.dirs.forEach(n=>list.append(dirRow(n,'',join(d.path,n))));
    if(!d.dirs.length)list.append(mkEl('div','nohist','No sub-folders here'));
    list.scrollTop=0;
  }
  go.onclick=()=>show(inp.value.trim());
  inp.onkeydown=e=>{if(e.key==='Enter'){e.preventDefault();show(inp.value.trim())}};
  M.btn('Open this folder',async b=>{b.disabled=true;const ok=await openFolder(inp.value.trim()||cur);b.disabled=false;if(ok)M.close()},'primary');
  M.btn('Cancel',M.close);
  show(CAPS.root||'');inp.focus();
}
async function openFolder(path){
  if([...WS.values()].some(e=>!e.saved)&&!confirm('Some files from the chat are not saved yet and belong to the current folder. Open the other folder anyway?'))return false;
  try{
    const r=await jpost('/api/root',{path}),d=await r.json().catch(()=>({}));
    if(!r.ok)throw new Error(d.detail||'HTTP '+r.status);
    CAPS.root=d.root;resetWS();navStack=[''];navIdx=0;FILES=null;await ls('','none');
    if(typeof paintCaps==='function')paintCaps();
    if(refreshInfo)refreshInfo().catch(()=>{});
    if(typeof memRefresh==='function')memRefresh().catch(()=>{});
    toast('Opened '+d.root,'good');return true;
  }catch(e){toast(e.message,'bad');return false}
}
$('#openfolder').onclick=folderModal;

/* the file list refreshes by itself: a cheap fingerprint of the open folder is polled and the list is redrawn only when it changes */
let fsBusy=false;
async function pollFiles(){
  if(fsBusy||document.hidden)return;fsBusy=true;
  try{
    const r=await fetch('/api/fs/stamp?path='+encodeURIComponent(cwd));if(!r.ok)return;const d=await r.json();
    if(rootPath&&d.root!==rootPath){CAPS.root=d.root;navStack=[''];navIdx=0;FILES=null;await ls('','none');if(typeof paintCaps==='function')paintCaps();return}   // opened from another tab
    if(d.missing){if(cwd){FILES=null;await ls('','none')}return}
    if(d.sig&&d.sig!==fsSig){const box=$('#files'),y=box.scrollTop;FILES=null;await ls(cwd,'auto');box.scrollTop=y}
  }catch{}finally{fsBusy=false}
}
setInterval(pollFiles,2000);
addEventListener('focus',pollFiles);document.addEventListener('visibilitychange',()=>{if(!document.hidden)pollFiles()});
HELP+='\n\n**Open folder:** the *Open folder…* button in the Files tab switches the project folder to any folder on this computer. The file list refreshes by itself when files are created, changed or deleted, including by agent mode.';
