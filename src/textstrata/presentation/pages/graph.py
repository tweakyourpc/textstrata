"""Interactive knowledge graph page."""

from __future__ import annotations

from ..skin import PAPER_SKIN, Skin, skin_vars

_GRAPH_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Knowledge Graph — TextStrata</title>
<style>
:root{__TEXTSTRATA_SKIN__--graph-edge:color-mix(in srgb,var(--muted) 58%,var(--border));--graph-edge-soft:color-mix(in srgb,var(--success) 42%,var(--border));--graph-node-stroke:var(--surface);--graph-orphan:var(--warning)}
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:var(--font-ui);background:var(--bg);color:var(--text);height:100vh;overflow:hidden}
#graph{width:100%;height:100vh}
svg{width:100%;height:100%}
.tooltip{position:absolute;background:var(--surface);color:var(--text);padding:8px 12px;border-radius:var(--radius);font-size:13px;pointer-events:none;opacity:0;transition:opacity .12s;border:1px solid var(--border);box-shadow:var(--card-shadow);max-width:280px;z-index:100}
.tooltip.show{opacity:1}
.tooltip h3{font-size:14px;margin-bottom:4px;color:var(--accent)}
.tooltip .meta{font-size:12px;color:var(--muted)}
.panel{position:fixed;background:var(--surface);border:1px solid var(--border);border-radius:var(--radius);font-size:12px;z-index:50;box-shadow:var(--card-shadow)}
#command{top:20px;left:20px;bottom:20px;width:250px;padding:12px;overflow-y:auto;display:flex;flex-direction:column;gap:10px}
#inspector{top:20px;right:20px;bottom:20px;width:290px;padding:14px;overflow-y:auto;display:none}
#inspector.show{display:block}
.panel h4{color:var(--accent);font-size:12px;text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px}
.panel h4:not(:first-child){margin-top:12px}
#q{width:100%;background:var(--surface-alt);border:1px solid var(--border);color:var(--text);padding:7px 10px;border-radius:var(--radius);font-size:13px}
#q:focus{outline:none;border-color:var(--accent)}
.row{display:flex;align-items:center;gap:8px;margin:2px 0;cursor:pointer;opacity:.7;padding:3px 5px;border-radius:6px}
.row:hover{opacity:1;background:var(--accent-soft)}
.row.active{opacity:1}
.row.off{opacity:.25;text-decoration:line-through}
.row .swatch{width:11px;height:11px;border-radius:50%;flex:none}
.row .n{margin-left:auto;color:var(--muted);font-size:11px}
.item-row{display:block;padding:4px 6px;border-radius:6px;cursor:pointer;color:var(--text);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.item-row:hover{background:var(--accent-soft);color:var(--text)}
.item-row small{color:var(--muted);display:block;font-size:10px}
.controls{position:fixed;top:20px;left:290px;display:flex;gap:8px;z-index:50}
.controls a,.controls button{background:var(--surface);color:var(--text);border:1px solid var(--border);padding:6px 14px;border-radius:var(--radius);text-decoration:none;font-size:13px;cursor:pointer}
.controls a:hover,.controls button:hover{background:var(--accent-soft)}
.controls button.active{background:var(--accent);border-color:var(--accent);color:white}
#status{position:fixed;bottom:20px;left:290px;right:330px;text-align:center;color:var(--muted);font-size:12px;z-index:50;pointer-events:none}
#insp-title{font-size:15px;color:var(--text);margin-bottom:2px;word-break:break-word}
#insp-id{font-size:10px;color:var(--muted);font-family:monospace;margin-bottom:8px;word-break:break-all}
.metric-grid{display:grid;grid-template-columns:1fr 1fr;gap:4px 10px;margin:8px 0}
.metric-grid div{font-size:11px;color:var(--muted)}
.metric-grid b{color:var(--text);font-weight:600}
.conn{padding:5px 6px;border-radius:6px;margin:2px 0;cursor:pointer}
.conn:hover{background:var(--accent-soft)}
.conn .t{color:var(--text);font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.conn:hover .t{color:var(--accent)}
.conn .why{font-size:10px;color:var(--success)}
.conn.explicit .why{color:var(--warning)}
.btnrow{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0}
.btnrow button{background:var(--accent-soft);border:1px solid var(--border);color:var(--text);padding:5px 10px;border-radius:7px;font-size:11px;cursor:pointer}
.btnrow button:hover{background:var(--accent);border-color:var(--accent);color:white}
.btnrow button b{color:var(--muted);font-weight:400;margin-left:3px}
.tagchip{display:inline-block;background:var(--accent-soft);border:1px solid var(--border);border-radius:99px;padding:1px 8px;margin:2px 2px 0 0;font-size:10px;color:var(--muted)}
kbd{background:var(--surface-alt);border:1px solid var(--border);border-radius:4px;padding:0 4px;font-size:10px;color:var(--muted)}
.hint{color:var(--muted);font-size:10px;margin-top:auto;padding-top:10px;line-height:1.7}
</style>
</head>
<body>
<div class="controls">
<a href="/">Library</a>
<button id="toggle-links" class="active">Links</button>
<button id="toggle-similarity">Similarity</button>
<button id="toggle-communities">Communities</button>
</div>
<div class="panel" id="command">
<input id="q" type="search" placeholder="Search nodes…  ( / )" autocomplete="off">
<div><h4>Start here</h4><div id="p-top"></div></div>
<div><h4>Needs attention</h4><div id="p-attn"></div></div>
<div><h4>Communities</h4><div id="p-comm"></div></div>
<div><h4>Types</h4><div id="p-types"></div></div>
<div class="hint"><kbd>/</kbd> search · <kbd>Enter</kbd>/<kbd>o</kbd> open · <kbd>f</kbd> focus · <kbd>x</kbd> expand · <kbd>c</kbd> community · <kbd>Esc</kbd> clear</div>
</div>
<div class="panel" id="inspector">
<div id="insp-title"></div>
<div id="insp-id"></div>
<div class="btnrow" id="insp-actions"></div>
<div class="metric-grid" id="insp-metrics"></div>
<div id="insp-tags"></div>
<h4 style="margin-top:12px">Connections — next hops</h4>
<div id="insp-conns"></div>
</div>
<div id="status"></div>
<div class="tooltip" id="tooltip"></div>
<div id="graph"></div>
<script src="https://d3js.org/d3.v7.min.js"></script>
<script>
(async()=>{
const data=await fetch('/api/textstrata/graph').then(r=>r.json());
const params=new URLSearchParams(location.search);
const types=[...new Set(data.nodes.map(n=>n.type))].sort();
const palette=["#e94560","#0f3460","var(--success)","var(--graph-orphan)","#7b2d8e","#2d8e7b","#e9c46a","#f4a261","#264653","#2a9d8f","#e76f51","#9c89b8","#70a288","#d5896f"];
const color=d3.scaleOrdinal().domain(types).range(palette.slice(0,types.length));
const commColors={};
(data.communities||[]).forEach((c,i)=>{commColors[c.label]=palette[(i+5)%palette.length]});
const nodeById={};data.nodes.forEach(n=>{nodeById[n.id]=n});
const links=data.links.map(l=>({source:l.source,target:l.target,weight:l.weight,reason:l.reason,type:'link'}));
const sim=data.similarity.map(e=>({source:e.source,target:e.target,weight:Math.round(e.score*5),reason:'similarity',type:'similarity',score:e.score,shared:e.shared||[]}));
let showLinks=true,showSim=false,showCommunities=false;
let selected=null,focusSet=null;
const hiddenTypes=new Set();
const width=window.innerWidth,height=window.innerHeight;
const svg=d3.select('#graph').append('svg').attr('width',width).attr('height',height);
const zoomLayer=svg.append('g');
svg.call(d3.zoom().scaleExtent([0.2,4]).on('zoom',e=>zoomLayer.attr('transform',e.transform)));
const defs=svg.append('defs');
['link','similarity'].forEach(t=>{defs.append('marker').attr('id','arrow-'+t).attr('viewBox','0 -5 10 10').attr('refX',20).attr('refY',0).attr('markerWidth',6).attr('markerHeight',6).attr('orient','auto').append('path').attr('d','M0,-5L10,0L0,5').attr('fill',t==='link'?'var(--graph-edge)':'var(--graph-edge-soft)')});
const linkGroup=zoomLayer.append('g');
const nodeGroup=zoomLayer.append('g');
const allEdges=[...links,...sim];
const simulation=d3.forceSimulation(data.nodes).force('link',d3.forceLink(allEdges).id(d=>d.id).distance(d=>d.type==='similarity'?180:120).strength(d=>d.type==='similarity'?d.score*0.3:d.weight*0.15)).force('charge',d3.forceManyBody().strength(-180)).force('center',d3.forceCenter(width/2,height/2)).force('collision',d3.forceCollide(25));
function nodeVisible(n){if(hiddenTypes.has(n.type))return false;if(focusSet&&!focusSet.has(n.id))return false;return true}
function edgeVisible(e){const s=e.source.id||e.source,t=e.target.id||e.target;return nodeVisible(nodeById[s])&&nodeVisible(nodeById[t])}
function activeEdges(){const r=[];if(showLinks)r.push(...links);if(showSim)r.push(...sim);return r}
function nodeFill(d){return showCommunities&&d.community?(commColors[d.community]||color(d.type)):color(d.type)}
function setStatus(msg){document.getElementById('status').textContent=msg}
function update(){
const active=activeEdges();
const lines=linkGroup.selectAll('line').data(active,d=>(d.source.id||d.source)+'|'+(d.target.id||d.target)+'|'+d.type);
lines.exit().remove();
lines.enter().append('line').merge(lines)
.attr('stroke',d=>d.type==='similarity'?'var(--graph-edge-soft)':'var(--graph-edge)')
.attr('stroke-width',d=>d.type==='similarity'?Math.max(0.5,d.score*3):d.weight)
.attr('stroke-opacity',d=>edgeVisible(d)?(d.type==='similarity'?0.4:0.6):0)
.attr('stroke-dasharray',d=>d.type==='similarity'?'4 3':'none')
.attr('marker-end',d=>d.type==='link'?'url(#arrow-link)':'url(#arrow-similarity)')
.style('pointer-events',d=>edgeVisible(d)?'auto':'none');
circles.attr('opacity',d=>nodeVisible(d)?(matchesQuery(d)?0.9:0.15):0.04)
.style('pointer-events',d=>nodeVisible(d)?'auto':'none')
.attr('fill',nodeFill)
.attr('stroke',d=>selected&&d.id===selected.id?'var(--accent)':(d.orphan?'var(--graph-orphan)':'var(--graph-node-stroke)'))
.attr('stroke-width',d=>selected&&d.id===selected.id?3:1.5)
.attr('stroke-dasharray',d=>d.orphan&&!(selected&&d.id===selected.id)?'3 2':'none');
labels.attr('opacity',d=>nodeVisible(d)&&(focusSet||(selected&&d.id===selected.id))?1:0);
simulation.nodes(data.nodes).force('link').links(active);simulation.alpha(0.25).restart();
}
let query='';
function matchesQuery(d){if(!query)return true;const q=query.toLowerCase();return d.id.toLowerCase().includes(q)||d.title.toLowerCase().includes(q)||d.tags.some(t=>t.toLowerCase().includes(q))}
const tooltip=d3.select('#tooltip');
const drag=d3.drag().on('start',(e,d)=>{if(!e.active)simulation.alphaTarget(0.3).restart();d.fx=d.x;d.fy=d.y}).on('drag',(e,d)=>{d.fx=e.x;d.fy=e.y}).on('end',(e,d)=>{if(!e.active)simulation.alphaTarget(0);d.fx=d.x;d.fy=d.y});
const circles=nodeGroup.selectAll('circle').data(data.nodes).join('circle')
.attr('r',d=>Math.max(5,Math.min(20,Math.sqrt((d.score||1)+5)*3)))
.attr('fill',nodeFill).attr('stroke','var(--graph-node-stroke)').attr('stroke-width',1.5).attr('opacity',.85).call(drag)
.on('mouseover',(e,d)=>{tooltip.classed('show',true).html('<h3>'+d.title+'</h3><div class="meta">'+d.type.replace(/_/g,' ')+' · score '+d.score+' · in '+d.in+' / out '+d.out+(d.orphan?' · <b style="color:var(--graph-orphan)">orphan</b>':'')+'</div>');d3.select(e.currentTarget).attr('stroke','var(--accent)').attr('stroke-width',2.5)})
.on('mousemove',(e)=>{tooltip.style('left',(e.pageX+14)+'px').style('top',(e.pageY-10)+'px')})
.on('mouseout',(e,d)=>{tooltip.classed('show',false);if(!(selected&&d.id===selected.id))d3.select(e.currentTarget).attr('stroke',d.orphan?'var(--graph-orphan)':'var(--graph-node-stroke)').attr('stroke-width',1.5)})
.on('click',(e,d)=>{e.stopPropagation();select(d)})
.on('dblclick',(e,d)=>{e.stopPropagation();window.location='/item/'+encodeURIComponent(d.id)});
const labels=nodeGroup.selectAll('text').data(data.nodes).join('text')
.text(d=>d.title.length>28?d.title.slice(0,26)+'…':d.title)
.attr('font-size',10).attr('fill','var(--text)').attr('dx',12).attr('dy',4).attr('opacity',0).style('pointer-events','none');
simulation.on('tick',()=>{linkGroup.selectAll('line').attr('x1',d=>d.source.x).attr('y1',d=>d.source.y).attr('x2',d=>d.target.x).attr('y2',d=>d.target.y);circles.attr('cx',d=>d.x).attr('cy',d=>d.y);labels.attr('x',d=>d.x).attr('y',d=>d.y)});
svg.on('click',()=>{deselect()});
function neighboursOf(id){const s=new Set([id]);allEdges.forEach(e=>{const a=e.source.id||e.source,b=e.target.id||e.target;if(a===id)s.add(b);if(b===id)s.add(a)});return s}
function connectionsOf(id){
const out=[];
links.forEach(e=>{const a=e.source.id||e.source,b=e.target.id||e.target;if(a===id)out.push({id:b,why:e.reason+' → · w'+e.weight,rank:e.weight+10,explicit:true});else if(b===id)out.push({id:a,why:'← '+e.reason+' · w'+e.weight,rank:e.weight+10,explicit:true})});
sim.forEach(e=>{const a=e.source.id||e.source,b=e.target.id||e.target;const other=a===id?b:(b===id?a:null);if(other&&!out.some(c=>c.id===other))out.push({id:other,why:'similar '+(e.score*100).toFixed(0)+'%'+(e.shared.length?' · '+e.shared.slice(0,3).join(' · '):''),rank:e.score*10,explicit:false})});
out.sort((a,b)=>b.rank-a.rank||a.id.localeCompare(b.id));return out;
}
function select(d){
selected=d;
const insp=document.getElementById('inspector');insp.classList.add('show');
document.getElementById('insp-title').textContent=d.title;
document.getElementById('insp-id').textContent=d.id;
const acts=document.getElementById('insp-actions');acts.innerHTML='';
[['Open','o',()=>{window.location='/item/'+encodeURIComponent(d.id)}],
 ['Focus','f',()=>focusOn(d)],
 ['Expand','x',()=>expandFocus()],
 ['Community','c',()=>focusCommunity(d.community)],
 ['Clear','Esc',()=>deselect()]].forEach(([label,key,fn])=>{
const b=document.createElement('button');b.innerHTML=label+' <b>'+key+'</b>';b.onclick=fn;acts.appendChild(b)});
const m=document.getElementById('insp-metrics');
m.innerHTML='<div>score <b>'+d.score+'</b></div><div>degree <b>'+d.degree+'</b></div><div>in / out <b>'+d.in+' / '+d.out+'</b></div><div>authority <b>'+(d.authority||0).toFixed(3)+'</b></div><div>hub <b>'+(d.hub||0).toFixed(3)+'</b></div><div>ingested <b>'+(d.ingested||'—')+'</b></div>'+(d.community?'<div style="grid-column:1/-1">community <b><a href="/community/'+encodeURIComponent(d.community)+'" style="color:var(--success)">'+(nodeById[d.community]?nodeById[d.community].title:d.community)+'</a></b></div>':'')+(d.orphan?'<div style="grid-column:1/-1;color:var(--graph-orphan)"><b>Orphan — link this note or add tags so it joins the mesh.</b></div>':'');
document.getElementById('insp-tags').innerHTML=d.tags.map(t=>'<span class="tagchip">'+t+'</span>').join('');
const conns=connectionsOf(d.id);
document.getElementById('insp-conns').innerHTML=conns.length?conns.slice(0,20).map((c,i)=>{const n=nodeById[c.id];if(!n)return'';return'<div class="conn'+(c.explicit?' explicit':'')+'" data-id="'+c.id+'"><div class="t">'+(i===0?'▸ ':'')+n.title+'</div><div class="why">'+c.why+'</div></div>'}).join(''):'<div style="color:var(--muted);font-size:11px">No connections. This note is isolated — consider adding tags or references.</div>';
document.querySelectorAll('#insp-conns .conn').forEach(el=>{el.onclick=()=>{const n=nodeById[el.dataset.id];if(n)select(n)}});
setStatus(d.title+(focusSet?' · focused: '+focusSet.size+' nodes':'')+' · dblclick or o to open');
update();
}
function deselect(){selected=null;focusSet=null;document.getElementById('inspector').classList.remove('show');setStatus('');update()}
function focusOn(d){focusSet=neighboursOf(d.id);setStatus('Focused on '+d.title+' · '+focusSet.size+' nodes · x to expand · Esc to clear');update()}
function expandFocus(){if(!focusSet){if(selected)focusOn(selected);return}const grown=new Set(focusSet);focusSet.forEach(id=>neighboursOf(id).forEach(n=>grown.add(n)));focusSet=grown;setStatus('Expanded · '+focusSet.size+' nodes · x to expand again · Esc to clear');update()}
function focusCommunity(label){if(!label)return;focusSet=new Set(data.nodes.filter(n=>n.community===label).map(n=>n.id));const comm=(data.communities||[]).find(c=>c.label===label);setStatus('Community: '+(comm?comm.anchor_title:label)+' · '+focusSet.size+' nodes · Esc to clear');update()}
function itemRow(id,sub){const n=nodeById[id];if(!n)return'';return'<div class="item-row" data-id="'+id+'">'+n.title+'<small>'+sub+'</small></div>'}
const topNodes=[...data.nodes].sort((a,b)=>b.score-a.score||a.id.localeCompare(b.id)).slice(0,5);
document.getElementById('p-top').innerHTML=topNodes.map(n=>itemRow(n.id,'score '+n.score+' · '+n.type.replace(/_/g,' '))).join('');
const attn=data.attention||{orphans:[],weak:[]};
document.getElementById('p-attn').innerHTML=(attn.orphans.slice(0,4).map(id=>itemRow(id,'orphan — no connections')).join(''))+(attn.weak.slice(0,4).map(id=>itemRow(id,'weakly connected')).join(''))||'<div style="color:var(--muted);font-size:11px;padding:2px 6px">All notes are connected.</div>';
document.getElementById('p-comm').innerHTML=(data.communities||[]).slice(0,8).map(c=>'<div class="row" data-comm="'+c.label+'"><div class="swatch" style="background:'+(commColors[c.label]||'var(--muted)')+'"></div><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap">'+c.anchor_title+'</span><span class="n">'+c.size+'</span></div>').join('');
document.getElementById('p-types').innerHTML=types.map(t=>'<div class="row active" data-type="'+t+'"><div class="swatch" style="background:'+color(t)+'"></div><span>'+t.replace(/_/g,' ')+'</span><span class="n">'+data.nodes.filter(n=>n.type===t).length+'</span></div>').join('');
document.querySelectorAll('#p-top .item-row,#p-attn .item-row').forEach(el=>{el.onclick=()=>{const n=nodeById[el.dataset.id];if(n){select(n);focusOn(n)}}});
document.querySelectorAll('#p-comm .row').forEach(el=>{el.onclick=()=>focusCommunity(el.dataset.comm)});
document.querySelectorAll('#p-types .row').forEach(el=>{el.onclick=()=>{const t=el.dataset.type;if(hiddenTypes.has(t)){hiddenTypes.delete(t);el.classList.remove('off')}else{hiddenTypes.add(t);el.classList.add('off')}update()}});
const qInput=document.getElementById('q');
qInput.addEventListener('input',()=>{query=qInput.value.trim();update()});
qInput.addEventListener('keydown',e=>{if(e.key==='Enter'){const hit=data.nodes.find(n=>matchesQuery(n)&&nodeVisible(n));if(hit)select(hit);e.preventDefault()}if(e.key==='Escape'){qInput.value='';query='';qInput.blur();update()}});
document.addEventListener('keydown',e=>{
if(e.target.tagName==='INPUT')return;
if(e.key==='/'){e.preventDefault();qInput.focus();return}
if(e.key==='Escape'){deselect();return}
if(!selected)return;
if(e.key==='o'||e.key==='Enter'){window.location='/item/'+encodeURIComponent(selected.id)}
if(e.key==='f')focusOn(selected);
if(e.key==='x')expandFocus();
if(e.key==='c')focusCommunity(selected.community);
});
document.getElementById('toggle-links').onclick=function(){showLinks=this.classList.toggle('active');update()};
document.getElementById('toggle-similarity').onclick=function(){showSim=this.classList.toggle('active');update()};
document.getElementById('toggle-communities').onclick=function(){showCommunities=this.classList.toggle('active');update()};
const focusParam=params.get('focus'),commParam=params.get('community');
if(focusParam&&nodeById[focusParam]){select(nodeById[focusParam]);focusOn(nodeById[focusParam])}
else if(commParam){focusCommunity(commParam)}
update();
})();
</script>
</body>
</html>"""


def render_graph_html(skin: Skin = PAPER_SKIN) -> str:
    return _GRAPH_HTML.replace("__TEXTSTRATA_SKIN__", skin_vars(skin))
