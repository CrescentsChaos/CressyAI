/* ================= GitHub (REST API): repos, files, issues, PRs, releases, repo search ================= */
/* The lookup itself runs inside webStep (14-websearch.js); this file decides when to use it and runs the settings panel. */
const GH_URL=/https?:\/\/(?:www\.)?(?:github\.com|raw\.githubusercontent\.com)\/[^\s<>"')\]]+/gi;
const GH_BARE=/(?:^|\s)((?:www\.)?github\.com\/[^\s<>"')\]]+)/gi;
const isGhUrl=u=>/^https?:\/\/(?:www\.)?(?:github\.com|raw\.githubusercontent\.com)\//i.test(u);
const GH_WORD=/\bgit ?hub\b|\b(?:repos?|repositor(?:y|ies))\b/i;
const GH_VERB=/\b(?:find|search|look(?:ing)? for|show|list|recommend|suggest|best|top|popular|trending|alternatives?|similar|open[- ]source|examples? of|projects?|librar(?:y|ies)|packages?|frameworks?|tools?)\b/i;
const GH_MINE=/\b(?:my|our|this|that|your)\s+(?:git ?hub\s+)?(?:repo|repos|repository|repositories)\b/i;
const GH_NOT_OWNER=new Set(['src','lib','app','apps','test','tests','docs','doc','dist','build','bin','assets','public','static','node_modules','usr','etc','var','home','tmp','opt','components','utils','scripts','pkg','cmd','internal','include','vendor','and','or','yes','no','input','output','he','she','w','n','i','s','http','https']);
/* links and owner/repo names in a text; a bare owner/repo only counts with a GitHub/repo word around it (or after /gh) */
function ghTargets(t,relaxed){
  t=t.replace(/```[\s\S]*?```/g,' ');
  const seen=new Set(),out=[],add=u=>{if(!seen.has(u)){seen.add(u);out.push(u)}};
  for(const m of t.matchAll(GH_URL))add(m[0].replace(/[.,;:!?]+$/,''));
  for(const m of t.matchAll(GH_BARE))add('https://'+m[1].replace(/[.,;:!?]+$/,''));
  if(out.length)return out.slice(0,3);
  if(relaxed||GH_WORD.test(t)){
    for(const m of t.matchAll(/(?:^|[\s("'`])([A-Za-z0-9][A-Za-z0-9-]{0,38})\/([A-Za-z0-9][A-Za-z0-9._-]{0,99})(?=$|[\s)"'`,;:!?])/g)){
      const o=m[1],r=m[2].replace(/\.$/,'');
      if(GH_NOT_OWNER.has(o.toLowerCase())||!/[A-Za-z]/.test(o+r))continue;
      add(o+'/'+r);
    }
  }
  return out.slice(0,3);
}
function wantGh(text,typed,files){
  if($('#ghmode').value==='off'||/^Continue exactly where you stopped/.test(text)||planMode||imgMode)return null;
  const t=(typed||text).trim();
  if(/^\/(?!gh\b|github\b|web\b|search\b|wiki\b)/.test(t))return null;
  const targets=ghTargets(text,false);
  if(targets.length)return{targets};
  if((files&&files.length)||text.includes('```'))return null;
  if(GH_WORD.test(t)&&GH_VERB.test(t)&&!GH_MINE.test(t))return{query:makeQuery(text)};
  return null;
}
function ghFromCommand(q){const targets=ghTargets(q,true);return targets.length?{targets}:{query:q}}
async function ghStatus(force){try{const r=await fetch('/api/github/status'+(force?'?force=1':''));return await r.json()}catch{return{enabled:false,ok:false,why:'The server did not answer.',token:false}}}
function paintGhSt(s){
  const e=$('#ghst');e.textContent=!s.enabled?'● off':s.ok?'● reachable':'● not reachable';e.className='wst '+(s.ok?'ok':'bad');
  let n=s.ok?'The GitHub API is answering.':(s.why||'');
  if(s.ok&&s.remaining!=null){const t=s.reset?new Date(s.reset*1000).toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'}):'';n+=' '+s.remaining+' of '+s.limit+' requests left this hour'+(t?' (resets '+t+')':'')+'.'}
  if(s.ok&&!s.token)n+=' No token set: start the server with the GITHUB_TOKEN variable for 5,000 per hour and your private repos.';
  $('#ghnote').textContent=n;
}
$('#ghtest').onclick=async()=>{$('#ghnote').textContent='Testing…';paintGhSt(await ghStatus(true))};
$('#ghmode').addEventListener('change',fineText);
$$('.tabs button').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.tab==='set')ghStatus(false).then(paintGhSt)}));
Object.assign(CMDS,{gh:['d','Read a GitHub repo, file, issue or PR link, or search repositories'],github:['d','Same as /gh']});
SUGG.push(['Look up on GitHub','/gh','Repos, files, issues and releases via the GitHub API']);
HELP+='\n\n**GitHub** — paste a GitHub link (repository, file, folder, issue, pull request, releases, user) or write `owner/repo` next to the word *github* or *repo*, and the model reads it through the GitHub REST API: description, stars, latest release, file list, README, file text, issue/PR discussion. Questions like "find a github repo for pdf parsing" search repositories. `/gh owner/repo`, `/gh <link>` or `/gh <what to search for>` forces it. *Settings → GitHub*: mode, repositories to find, READMEs to read. Without a token GitHub allows 60 requests per hour; start the server with the `GITHUB_TOKEN` variable for 5,000 and your private repos.';
fineText();
