/* ================= Wikipedia (MediaWiki API): encyclopedic facts, independent of SearXNG ================= */
/* The lookup itself runs inside webStep (14-websearch.js); this file decides when to use it and runs the settings panel. */
const WIKI_Q=/\b(?:who(?:'s| is| was| were| are)|what(?:'s| is| are| was| were)|when (?:was|were|did|is)|where (?:is|was|are|did)|why (?:is|was|did|do|does)|how (?:does|do|did|many|much|old|tall|far|long|big|large|heavy)|tell me about|history of|biography|born|died|capital|population|invented|discovered|founded|defin(?:e|ition)|meaning of|difference between|explain|overview|facts? about|about)\b/i;
const WIKI_SKIP=/\b(?:you|your|yours|yourself|my|mine|this (?:code|file|chat|conversation|project)|the (?:code|file) (?:above|below))\b/i;
function wantWiki(text,typed,files){
  const mode=$('#wikimode').value;
  if(mode==='off'||/^Continue exactly where you stopped/.test(text)||pickUrls(text).length||localOnly(text,typed,files))return false;
  if(mode==='always')return true;
  const t=typed||text;
  return WIKI_Q.test(t)&&!WIKI_SKIP.test(t);
}
async function wikiStatus(force){try{const r=await fetch('/api/wiki/status?lang='+encodeURIComponent($('#wikilang').value||'en')+(force?'&force=1':''));return await r.json()}catch{return{enabled:false,ok:false,why:'The server did not answer.'}}}
function paintWikiSt(s){const e=$('#wikist');e.textContent=!s.enabled?'● off':s.ok?'● reachable':'● not reachable';e.className='wst '+(s.ok?'ok':'bad');$('#wikinote').textContent=s.ok?'Wikipedia ('+s.lang+') is answering.':(s.why||'')}
$('#wikitest').onclick=async()=>{$('#wikinote').textContent='Testing…';paintWikiSt(await wikiStatus(true))};
$('#wikilang').addEventListener('change',()=>wikiStatus(true).then(paintWikiSt));
$('#wikimode').addEventListener('change',fineText);
$$('.tabs button').forEach(b=>b.addEventListener('click',()=>{if(b.dataset.tab==='set')wikiStatus(false).then(paintWikiSt)}));
Object.assign(CMDS,{wiki:['d','Look this up on Wikipedia and answer with sources']});
SUGG.push(['Look up on Wikipedia','/wiki','Encyclopedia facts with numbered sources']);
HELP+='\n\n**Wikipedia** — questions that ask for facts ("who was…", "what is…", "history of…") are looked up on Wikipedia through its public API and answered from the articles, with clickable [1] [2] chips. It works with or without SearXNG. `/wiki your question` forces a lookup. *Settings → Wikipedia*: when to use it, language, how many articles to find and read in full. Your search words go to wikipedia.org; turn it off there or start with `--no-wiki` to stop that.';
fineText();
