const fs = require('fs');
const path = require('path');

const target = 'dashboard/public/jarvis.html';
const current = fs.readFileSync(target, 'utf8');

// Check if it's already the new version
if (current.includes('JetBrains') && current.includes('Arkmurus Intelligence')) {
  console.log('Already updated - new dashboard is in place');
  process.exit(0);
}

console.log('Old dashboard detected - writing new version...');
console.log('Old size:', current.length, 'bytes');

// Write the new dashboard
const newDash = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Arkmurus Intelligence — Crucix</title>
<link href="https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@300;400;500&family=Syne:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{--bg:#0a0c10;--bg2:#0f1218;--bg3:#141820;--border:rgba(255,255,255,0.07);--border2:rgba(255,255,255,0.12);--text:#e8eaf0;--text2:#8b8f9e;--text3:#555a6b;--red:#e84545;--red-dim:rgba(232,69,69,0.15);--amber:#f0962a;--amber-dim:rgba(240,150,42,0.12);--blue:#4a9eff;--blue-dim:rgba(74,158,255,0.12);--green:#3ecf8e;--green-dim:rgba(62,207,142,0.12);--gold:#c9a84c;--mono:'JetBrains Mono',monospace;--sans:'Syne',sans-serif}
*{box-sizing:border-box;margin:0;padding:0}
body{background:var(--bg);color:var(--text);font-family:var(--sans);font-size:13px;min-height:100vh;overflow-x:hidden}
body::before{content:'';position:fixed;inset:0;background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,0,0,0.03) 2px,rgba(0,0,0,0.03) 4px);pointer-events:none;z-index:1000}
.header{display:flex;align-items:center;justify-content:space-between;padding:12px 20px;border-bottom:0.5px solid var(--border);background:var(--bg2);position:sticky;top:0;z-index:100}
.header-brand{display:flex;align-items:center;gap:10px}
.live-dot{width:7px;height:7px;border-radius:50%;background:var(--red);animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:0.3}}
.brand-name{font-size:13px;font-weight:700;letter-spacing:0.12em;color:var(--text)}
.brand-sub{font-size:10px;color:var(--text3);font-family:var(--mono)}
.header-stats{display:flex;gap:6px;align-items:center}
.hstat{font-family:var(--mono);font-size:10px;padding:3px 8px;border-radius:3px;border:0.5px solid}
.hstat.critical{color:var(--red);border-color:rgba(232,69,69,0.3);background:var(--red-dim)}
.hstat.ok{color:var(--green);border-color:rgba(62,207,142,0.3);background:var(--green-dim)}
.hstat.info{color:var(--blue);border-color:rgba(74,158,255,0.3);background:var(--blue-dim)}
.layout{display:grid;grid-template-columns:1fr 300px;gap:0;height:calc(100vh - 47px);overflow:hidden}
.main{overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px}
.sidebar{border-left:0.5px solid var(--border);overflow-y:auto;display:flex;flex-direction:column}
::-webkit-scrollbar{width:3px}
::-webkit-scrollbar-thumb{background:var(--border2);border-radius:2px}
.metrics{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}
.metric{background:var(--bg2);border:0.5px solid var(--border);border-radius:6px;padding:10px 12px}
.metric-label{font-family:var(--mono);font-size:9px;color:var(--text3);letter-spacing:0.08em;margin-bottom:5px;text-transform:uppercase}
.metric-val{font-size:22px;font-weight:700;line-height:1;margin-bottom:3px}
.metric-sub{font-family:var(--mono);font-size:9px;color:var(--text3)}
.val-warn{color:var(--red)}.val-amber{color:var(--amber)}.val-ok{color:var(--green)}.val-blue{color:var(--blue)}
.panel{background:var(--bg2);border:0.5px solid var(--border);border-radius:6px;overflow:hidden}
.panel-hdr{display:flex;align-items:center;justify-content:space-between;padding:7px 12px;border-bottom:0.5px solid var(--border)}
.panel-title{font-family:var(--mono);font-size:9px;letter-spacing:0.08em;color:var(--text2);text-transform:uppercase}
.panel-badge{font-family:var(--mono);font-size:9px;color:var(--text3)}
.map-wrap{position:relative;height:190px;background:#0d1117;overflow:hidden}
.map-wrap svg{width:100%;height:100%}
.map-legend{display:flex;gap:14px;padding:5px 12px;border-top:0.5px solid var(--border);background:var(--bg3)}
.leg-item{display:flex;align-items:center;gap:5px;font-family:var(--mono);font-size:9px;color:var(--text3)}
.leg-dot{width:6px;height:6px;border-radius:50%}
.sig-item{display:flex;gap:10px;padding:8px 12px;border-bottom:0.5px solid var(--border);transition:background 0.1s}
.sig-item:hover{background:var(--bg3)}
.sig-item:last-child{border-bottom:none}
.sig-dot{width:6px;height:6px;border-radius:50%;flex-shrink:0;margin-top:4px}
.sig-body{flex:1;min-width:0}
.sig-meta{display:flex;gap:6px;align-items:center;margin-bottom:3px}
.sig-src{font-family:var(--mono);font-size:9px;font-weight:500;color:var(--text3);letter-spacing:0.04em}
.sig-region{font-family:var(--mono);font-size:9px;padding:1px 5px;border-radius:2px}
.region-me{color:var(--red);background:var(--red-dim)}
.region-eu{color:var(--blue);background:var(--blue-dim)}
.region-as{color:var(--amber);background:var(--amber-dim)}
.region-af{color:var(--green);background:var(--green-dim)}
.sig-text{font-size:12px;color:var(--text);line-height:1.5}
.sig-time{font-family:var(--mono);font-size:9px;color:var(--text3);margin-top:3px}
.corr-list{padding:8px 12px;display:flex;flex-direction:column;gap:6px}
.corr-item{display:flex;align-items:center;gap:10px}
.corr-name{font-size:11px;color:var(--text2);width:120px;flex-shrink:0}
.corr-bar-bg{flex:1;height:3px;background:var(--border);border-radius:2px;overflow:hidden}
.corr-bar-fill{height:100%;border-radius:2px}
.fill-c{background:var(--red)}.fill-h{background:var(--amber)}.fill-m{background:var(--blue)}
.corr-lbl{font-family:var(--mono);font-size:9px;width:46px;text-align:right}
.lbl-c{color:var(--red)}.lbl-h{color:var(--amber)}.lbl-m{color:var(--blue)}
.mkt-item{display:flex;align-items:center;gap:8px;padding:7px 12px;border-bottom:0.5px solid var(--border)}
.mkt-item:last-child{border-bottom:none}
.mkt-prob{font-family:var(--mono);font-size:13px;font-weight:500;width:38px;flex-shrink:0}
.mkt-bar-bg{width:40px;height:3px;background:var(--border);border-radius:2px;overflow:hidden;flex-shrink:0}
.mkt-bar-fill{height:100%;border-radius:2px}
.mkt-q{font-size:11px;color:var(--text2);flex:1;overflow:hidden;white-space:nowrap;text-overflow:ellipsis}
.idea-item{display:flex;gap:10px;padding:8px 12px;border-bottom:0.5px solid var(--border)}
.idea-item:last-child{border-bottom:none}
.idea-tag{font-family:var(--mono);font-size:9px;padding:2px 6px;border-radius:2px;flex-shrink:0;margin-top:1px;border:0.5px solid}
.tag-long{color:var(--green);border-color:rgba(62,207,142,0.3);background:var(--green-dim)}
.tag-hedge{color:var(--blue);border-color:rgba(74,158,255,0.3);background:var(--blue-dim)}
.tag-watch{color:var(--amber);border-color:rgba(240,150,42,0.3);background:var(--amber-dim)}
.idea-body{flex:1}
.idea-title{font-size:12px;color:var(--text);margin-bottom:3px}
.idea-rationale{font-size:11px;color:var(--text3);line-height:1.4}
.sb-hdr{padding:8px 14px;display:flex;justify-content:space-between;align-items:center;background:var(--bg3);border-bottom:0.5px solid var(--border);position:sticky;top:0;z-index:5}
.sb-title{font-family:var(--mono);font-size:9px;letter-spacing:0.1em;color:var(--text3);text-transform:uppercase}
.news-item{padding:8px 14px;border-bottom:0.5px solid var(--border);cursor:pointer;transition:background 0.1s}
.news-item:hover{background:var(--bg3)}
.news-src{font-family:var(--mono);font-size:9px;color:var(--text3);margin-bottom:3px}
.news-text{font-size:11px;color:var(--text2);line-height:1.4}
.rgrid{display:grid;grid-template-columns:1fr 1fr;gap:4px;padding:8px}
.rcard{background:var(--bg3);border-radius:4px;padding:6px 8px;display:flex;justify-content:space-between;align-items:center}
.rcard-name{font-size:10px;color:var(--text2)}
.rcard-val{font-family:var(--mono);font-size:10px;font-weight:500}
.cmdbar{padding:10px 14px;border-top:0.5px solid var(--border);background:var(--bg2);display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.cmd{font-family:var(--mono);font-size:10px;padding:3px 8px;border-radius:3px;background:var(--bg3);border:0.5px solid var(--border2);color:var(--text2);cursor:pointer;transition:all 0.1s}
.cmd:hover{border-color:var(--blue);color:var(--blue)}
.sweep-btn{margin-left:auto;font-family:var(--mono);font-size:10px;padding:4px 12px;border-radius:3px;background:var(--blue-dim);border:0.5px solid var(--blue);color:var(--blue);cursor:pointer;transition:all 0.1s}
.sweep-btn:hover{background:var(--blue);color:var(--bg)}
.tooltip{position:absolute;background:var(--bg2);border:0.5px solid var(--border2);border-radius:4px;padding:7px 10px;font-size:11px;color:var(--text);pointer-events:none;max-width:220px;line-height:1.5;display:none;z-index:50}
.sweep-line{height:2px;background:var(--bg3);overflow:hidden}
.sweep-progress{height:100%;background:var(--blue);width:0%;transition:width 0.3s linear}
@media(max-width:900px){.layout{grid-template-columns:1fr;height:auto}.sidebar{border-left:none;border-top:0.5px solid var(--border)}.metrics{grid-template-columns:repeat(3,1fr)}}
</style>
</head>
<body>
<div class="header">
  <div class="header-brand">
    <div class="live-dot"></div>
    <div>
      <div class="brand-name">ARKMURUS INTELLIGENCE</div>
      <div class="brand-sub">CRUCIX · <span id="htime">--:-- UTC</span></div>
    </div>
  </div>
  <div class="header-stats">
    <span class="hstat critical" id="h-critical">-- critical</span>
    <span class="hstat info" id="h-sources">-- sources</span>
    <span class="hstat ok" id="h-ideas">-- ideas</span>
  </div>
</div>
<div class="sweep-line"><div class="sweep-progress" id="sweep-prog"></div></div>
<div class="layout">
<div class="main">
  <div class="metrics">
    <div class="metric"><div class="metric-label">VIX</div><div class="metric-val val-warn" id="m-vix">--</div><div class="metric-sub" id="m-vix-sub">volatility index</div></div>
    <div class="metric"><div class="metric-label">BRENT</div><div class="metric-val val-amber" id="m-brent">--</div><div class="metric-sub">crude $/bbl</div></div>
    <div class="metric"><div class="metric-label">WTI</div><div class="metric-val" id="m-wti">--</div><div class="metric-sub">crude $/bbl</div></div>
    <div class="metric"><div class="metric-label">OSINT signals</div><div class="metric-val val-blue" id="m-signals">--</div><div class="metric-sub" id="m-urgent">-- urgent</div></div>
    <div class="metric"><div class="metric-label">Direction</div><div class="metric-val" id="m-dir" style="font-size:16px;padding-top:4px">--</div><div class="metric-sub" id="m-delta">-- changes</div></div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 260px;gap:10px">
    <div class="panel">
      <div class="panel-hdr"><span class="panel-title">Conflict events · Liveuamap</span><span class="panel-badge" id="map-count">-- events</span></div>
      <div class="map-wrap">
        <svg viewBox="0 0 800 190" id="worldmap">
          <rect width="800" height="190" fill="#0d1117"/>
          <path d="M55,52 L95,49 L125,55 L155,51 L175,57 L195,53 L215,59 L235,55 L255,61 L245,77 L225,82 L205,79 L185,83 L165,79 L145,83 L125,79 L105,83 L85,79 L65,75 L55,67Z" fill="#151c28" stroke="#1e2840" stroke-width="0.5"/>
          <path d="M220,55 L260,51 L300,55 L340,51 L380,57 L400,53 L420,59 L440,55 L460,61 L480,57 L500,63 L490,82 L470,87 L450,83 L430,87 L410,83 L390,87 L370,83 L350,87 L330,82 L310,85 L290,81 L270,85 L250,81 L230,77Z" fill="#151c28" stroke="#1e2840" stroke-width="0.5"/>
          <path d="M460,55 L510,51 L550,55 L590,51 L630,57 L660,55 L690,61 L720,57 L750,63 L760,82 L740,87 L720,83 L700,87 L680,83 L660,87 L640,83 L620,89 L600,85 L580,89 L560,85 L540,89 L520,85 L500,81 L480,77 L460,73Z" fill="#151c28" stroke="#1e2840" stroke-width="0.5"/>
          <path d="M220,89 L260,87 L280,93 L270,113 L250,117 L230,115 L215,107 L210,97Z" fill="#151c28" stroke="#1e2840" stroke-width="0.5"/>
          <path d="M280,92 L330,89 L360,95 L370,115 L350,127 L320,132 L295,125 L275,112Z" fill="#151c28" stroke="#1e2840" stroke-width="0.5"/>
          <path d="M150,87 L190,85 L210,92 L200,112 L180,122 L155,117 L140,105Z" fill="#151c28" stroke="#1e2840" stroke-width="0.5"/>
          <path d="M560,87 L610,85 L650,91 L670,112 L650,127 L620,132 L590,125 L565,107Z" fill="#151c28" stroke="#1e2840" stroke-width="0.5"/>
          <g id="event-dots"></g>
        </svg>
        <div class="tooltip" id="map-tooltip"></div>
      </div>
      <div class="map-legend">
        <div class="leg-item"><div class="leg-dot" style="background:var(--red)"></div>critical</div>
        <div class="leg-item"><div class="leg-dot" style="background:var(--amber)"></div>high</div>
        <div class="leg-item"><div class="leg-dot" style="background:var(--blue)"></div>medium</div>
        <div style="margin-left:auto;font-family:var(--mono);font-size:9px;color:var(--text3)">hover for detail</div>
      </div>
    </div>
    <div style="display:flex;flex-direction:column;gap:10px">
      <div class="panel">
        <div class="panel-hdr"><span class="panel-title">Convergences</span><span class="panel-badge" id="corr-count">--</span></div>
        <div class="corr-list" id="corr-list"></div>
      </div>
      <div class="panel">
        <div class="panel-hdr"><span class="panel-title">Prediction markets</span><span class="panel-badge" id="mkt-count">--</span></div>
        <div id="mkt-list"></div>
      </div>
    </div>
  </div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:10px">
    <div class="panel">
      <div class="panel-hdr"><span class="panel-title">Urgent OSINT signals</span><span class="panel-badge" id="sig-count">--</span></div>
      <div id="signal-feed"></div>
    </div>
    <div class="panel">
      <div class="panel-hdr"><span class="panel-title">Intelligence opportunities</span><span class="panel-badge" id="ideas-count">--</span></div>
      <div id="idea-list"></div>
    </div>
  </div>
</div>
<div class="sidebar">
  <div style="border-bottom:0.5px solid var(--border)">
    <div class="sb-hdr"><span class="sb-title">Active regions</span><span style="font-family:var(--mono);font-size:9px;color:var(--text3)" id="sb-regions">--</span></div>
    <div class="rgrid" id="region-grid"></div>
  </div>
  <div style="flex:1;overflow-y:auto;display:flex;flex-direction:column">
    <div class="sb-hdr"><span class="sb-title">Intelligence feed</span><span style="font-family:var(--mono);font-size:9px;color:var(--text3)" id="sb-news">--</span></div>
    <div id="news-feed"></div>
  </div>
  <div class="cmdbar">
    <span class="cmd">/brief</span>
    <span class="cmd">/osint</span>
    <span class="cmd">/predict</span>
    <span class="cmd">/arms</span>
    <button class="sweep-btn" onclick="triggerSweep()">sweep ↻</button>
  </div>
</div>
</div>
<script>
const REGION_COORDS={'Middle East':{x:500,y:77},'Israel':{x:488,y:75},'Iran':{x:512,y:73},'Ukraine':{x:420,y:69},'Russia':{x:445,y:65},'East Asia':{x:612,y:72},'Africa':{x:310,y:107},'West Africa':{x:228,y:105},'Syria':{x:483,y:71},'Latin America':{x:188,y:112},'Eastern Europe':{x:398,y:67}};
function geoToSVG(lat,lng){return{x:Math.max(10,Math.min(790,((lng+180)/360)*800)),y:Math.max(10,Math.min(180,((90-lat)/180)*190))};}
function sColor(s){return s==='critical'?'var(--red)':s==='high'?'var(--amber)':'var(--blue)';}
function rClass(r){const l=(r||'').toLowerCase();if(l.includes('middle')||l.includes('iran')||l.includes('israel'))return'region-me';if(l.includes('europe')||l.includes('ukraine')||l.includes('russia'))return'region-eu';if(l.includes('asia')||l.includes('korea'))return'region-as';return'region-af';}
function updateHeader(d){document.getElementById('htime').textContent=new Date().toISOString().slice(11,16)+' UTC';const c=(d.correlations||[]).filter(x=>x.severity==='critical').length;document.getElementById('h-critical').textContent=c+' critical';document.getElementById('h-sources').textContent=(d.meta?.sourcesOk||0)+' sources';document.getElementById('h-ideas').textContent=(d.ideas?.length||0)+' ideas';}
function updateMetrics(d){const vix=d.fred?.find(f=>f.id==='VIXCLS');const e=d.energy||{};const u=d.tg?.urgent||[];const ds=d.delta?.summary||{};const v=vix?.value;document.getElementById('m-vix').textContent=v||'--';document.getElementById('m-vix-sub').textContent=v>25?'elevated risk':'normal range';document.getElementById('m-brent').textContent=e.brent?'$'+e.brent:'--';document.getElementById('m-wti').textContent=e.wti?'$'+e.wti:'--';document.getElementById('m-signals').textContent=d.tg?.posts||'--';document.getElementById('m-urgent').textContent=u.length+' urgent';const dm={'risk-off':'↓ risk-off','risk-on':'↑ risk-on','mixed':'↔ mixed'};document.getElementById('m-dir').textContent=dm[ds.direction]||'--';document.getElementById('m-dir').style.color=ds.direction==='risk-off'?'var(--red)':ds.direction==='risk-on'?'var(--green)':'var(--amber)';document.getElementById('m-delta').textContent=(ds.totalChanges||0)+' changes';}
function updateMap(d){const dots=document.getElementById('event-dots');dots.innerHTML='';const tt=document.getElementById('map-tooltip');const wrap=document.querySelector('.map-wrap');const evts=[];const live=d.sources?.Liveuamap?.updates||d.liveuamap?.updates||[];if(live.length>0){live.slice(0,20).forEach(e=>{if(e.lat&&e.lng)evts.push({...e,pos:geoToSVG(e.lat,e.lng)});});}(d.correlations||[]).forEach(c=>{const co=REGION_COORDS[c.region];if(co)evts.push({title:c.region,region:c.region,priority:c.severity,pos:co});});document.getElementById('map-count').textContent=evts.length+' events';evts.forEach((e,i)=>{const col=sColor(e.priority);const r=e.priority==='critical'?5:e.priority==='high'?4:3;const txt=e.title||e.region||'Event';if(e.priority==='critical'){const p=document.createElementNS('http://www.w3.org/2000/svg','circle');p.setAttribute('cx',e.pos.x);p.setAttribute('cy',e.pos.y);p.setAttribute('r',r);p.setAttribute('fill','none');p.setAttribute('stroke',col);p.setAttribute('stroke-width','0.5');p.innerHTML='<animate attributeName="r" values="'+r+';'+(r+10)+';'+r+'" dur="'+(1.8+i*0.15)+'s" repeatCount="indefinite"/><animate attributeName="opacity" values="0.6;0;0.6" dur="'+(1.8+i*0.15)+'s" repeatCount="indefinite"/>';dots.appendChild(p);}const dot=document.createElementNS('http://www.w3.org/2000/svg','circle');dot.setAttribute('cx',e.pos.x);dot.setAttribute('cy',e.pos.y);dot.setAttribute('r',r);dot.setAttribute('fill',col);dot.setAttribute('opacity','0.9');dot.style.cursor='pointer';dot.addEventListener('mouseenter',()=>{tt.innerHTML='<strong style="color:var(--text3);font-family:var(--mono);font-size:9px">'+(e.region||e.source||'')+'</strong><br>'+txt+'<br><span style="color:var(--text3);font-family:var(--mono);font-size:9px">'+(e.timestamp||e.date||'')+'</span>';tt.style.display='block';const mr=wrap.getBoundingClientRect();const cx=e.pos.x/800*mr.width;const cy=e.pos.y/190*(mr.height-26);tt.style.left=Math.min(cx+10,mr.width-230)+'px';tt.style.top=Math.max(cy-35,4)+'px';});dot.addEventListener('mouseleave',()=>{tt.style.display='none';});dots.appendChild(dot);});}
function updateCorrelations(d){const c=d.correlations||[];document.getElementById('corr-count').textContent=c.length+' regions';const el=document.getElementById('corr-list');if(!c.length){el.innerHTML='<div style="padding:12px;font-size:11px;color:var(--text3)">No convergences</div>';return;}el.innerHTML=c.slice(0,6).map(x=>{const p=x.severity==='critical'?100:x.severity==='high'?68:42;const fc=x.severity==='critical'?'fill-c':x.severity==='high'?'fill-h':'fill-m';const lc=x.severity==='critical'?'lbl-c':x.severity==='high'?'lbl-h':'lbl-m';return'<div class="corr-item"><span class="corr-name">'+x.region+'</span><div class="corr-bar-bg"><div class="corr-bar-fill '+fc+'" style="width:'+p+'%"></div></div><span class="corr-lbl '+lc+'">'+x.severity+'</span></div>';}).join('');}
function updateMarkets(d){const m=d.polymarket?.markets||d.sources?.Polymarket?.markets||[];document.getElementById('mkt-count').textContent=m.length+' active';const el=document.getElementById('mkt-list');if(!m.length){el.innerHTML='<div style="padding:10px 12px;font-size:11px;color:var(--text3)">Awaiting sweep</div>';return;}el.innerHTML=m.slice(0,5).map(x=>{const col=x.yesProb>=70?'var(--red)':x.yesProb>=50?'var(--amber)':'var(--blue)';return'<div class="mkt-item"><span class="mkt-prob" style="color:'+col+'">'+x.yesProb+'%</span><div class="mkt-bar-bg"><div class="mkt-bar-fill" style="width:'+x.yesProb+'%;background:'+col+'"></div></div><span class="mkt-q">'+x.question+'</span></div>';}).join('');}
function updateSignals(d){const u=d.tg?.urgent||[];document.getElementById('sig-count').textContent=u.length+' signals';const el=document.getElementById('signal-feed');if(!u.length){el.innerHTML='<div style="padding:12px;font-size:11px;color:var(--text3)">No urgent signals</div>';return;}el.innerHTML=u.slice(0,8).map((s,i)=>{const txt=(s.text||s).replace(/\n/g,' ');const ch=s.channel||'unknown';const rg=s.region||'';const col=i<3?'var(--red)':i<6?'var(--amber)':'var(--blue)';return'<div class="sig-item"><div class="sig-dot" style="background:'+col+'"></div><div class="sig-body"><div class="sig-meta"><span class="sig-src">'+ch+'</span>'+(rg?'<span class="sig-region '+rClass(rg)+'">'+rg+'</span>':'')+'</div><div class="sig-text">'+txt.substring(0,200)+'</div><div class="sig-time">'+new Date().toISOString().slice(11,16)+' UTC</div></div></div>';}).join('');}
function updateIdeas(d){const ideas=d.ideas||[];document.getElementById('ideas-count').textContent=ideas.length+' ideas';const el=document.getElementById('idea-list');if(!ideas.length){el.innerHTML='<div style="padding:12px;font-size:11px;color:var(--text3)">Generating...</div>';return;}el.innerHTML=ideas.slice(0,5).map(x=>{const tc=x.type==='long'?'tag-long':x.type==='hedge'?'tag-hedge':'tag-watch';return'<div class="idea-item"><span class="idea-tag '+tc+'">'+(x.type||'watch')+'</span><div class="idea-body"><div class="idea-title">'+x.title+'</div><div class="idea-rationale">'+(x.rationale||'').substring(0,120)+'</div></div></div>';}).join('');}
function updateRegions(d){const c=d.correlations||[];document.getElementById('sb-regions').textContent=c.length+' active';const el=document.getElementById('region-grid');el.innerHTML=c.map(x=>{const col=x.severity==='critical'?'var(--red)':x.severity==='high'?'var(--amber)':'var(--blue)';return'<div class="rcard"><span class="rcard-name">'+x.region+'</span><span class="rcard-val" style="color:'+col+'">'+x.severity+'</span></div>';}).join('');}
function updateNews(d){const f=d.newsFeed||d.news||[];document.getElementById('sb-news').textContent=f.length+' items';const el=document.getElementById('news-feed');if(!f.length){el.innerHTML='<div style="padding:12px;font-size:11px;color:var(--text3)">Loading...</div>';return;}el.innerHTML=f.slice(0,30).map(n=>{const h=n.headline||n.title||'';const s=n.source||n.outlet||'';const u=n.url||n.link||'#';return'<div class="news-item" onclick="window.open(\''+u+'\',\'_blank\')"><div class="news-src">'+s+'</div><div class="news-text">'+h.substring(0,120)+'</div></div>';}).join('');}
function renderAll(d){updateHeader(d);updateMetrics(d);updateMap(d);updateCorrelations(d);updateMarkets(d);updateSignals(d);updateIdeas(d);updateRegions(d);updateNews(d);animateSweep();}
let st=null;
function animateSweep(){const bar=document.getElementById('sweep-prog');bar.style.width='0%';const iv=5*60*1000;const s=Date.now();if(st)clearInterval(st);st=setInterval(()=>{const p=Math.min(100,((Date.now()-s)/iv)*100);bar.style.width=p+'%';if(p>=100)clearInterval(st);},500);}
async function fetchData(){try{const r=await fetch('/api/data');if(!r.ok)return;renderAll(await r.json());}catch(e){}}
async function triggerSweep(){const b=document.querySelector('.sweep-btn');b.textContent='sweeping...';b.style.opacity='0.6';try{await fetch('/api/sweep',{method:'POST'});setTimeout(()=>{b.textContent='sweep ↻';b.style.opacity='1';fetchData();},25000);}catch(e){b.textContent='sweep ↻';b.style.opacity='1';}}
function connectSSE(){const es=new EventSource('/events');es.onmessage=e=>{try{const m=JSON.parse(e.data);if(m.type==='update'&&m.data)renderAll(m.data);}catch(e){}};es.onerror=()=>{setTimeout(connectSSE,5000);};}
setInterval(()=>{document.getElementById('htime').textContent=new Date().toISOString().slice(11,16)+' UTC';},10000);
fetchData();connectSSE();
</script>
</body>
</html>`;

fs.writeFileSync(target, newDash);
console.log('New dashboard written:', fs.statSync(target).size, 'bytes');
console.log('Contains JetBrains:', newDash.includes('JetBrains'));
