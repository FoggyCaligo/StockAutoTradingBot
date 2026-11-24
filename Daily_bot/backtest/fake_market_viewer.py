"""Self-contained per-day chart and order-book playback (no CDN or network)."""
from __future__ import annotations

import json
from pathlib import Path


def write_viewer(path: Path, series: dict, config: dict, session: str) -> None:
    payload = json.dumps({"series": series, "config": config, "session": session}, separators=(",", ":")).replace("<", "\\u003c")
    path.write_text(_HTML.replace("__PAYLOAD__", payload), encoding="utf-8")


_HTML = r'''<!doctype html>
<html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Daily Bot · Synthetic Market</title>
<style>
*{box-sizing:border-box}body{margin:0;background:#0c1521;color:#ecf2f8;font:15px system-ui,sans-serif}main{max-width:1300px;margin:auto;padding:32px}header{display:flex;justify-content:space-between;gap:20px;align-items:center}h1{font-size:28px;margin:8px 0}p{color:#9fb3c8;line-height:1.6}.tag{color:#67e8b3;font-size:12px;letter-spacing:2px}.controls{display:flex;gap:12px;align-items:center;flex-wrap:wrap;margin:24px 0}select,button{background:#1a2d42;color:white;border:1px solid #36516e;border-radius:7px;padding:10px}button{cursor:pointer}input{accent-color:#67e8b3;flex:1;min-width:160px}.grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(310px,1fr);gap:20px}.card{background:#122234;border:1px solid #293e55;border-radius:12px;padding:20px}canvas{display:block;width:100%;height:400px;touch-action:none;cursor:crosshair}.legend{display:flex;gap:20px;color:#9fb3c8;font-size:13px}.green{color:#67e8b3}.gold{color:#dfb86a}.stats{display:flex;justify-content:space-between;margin:0 0 18px;gap:12px}.value{font-size:25px;font-weight:600}.label{font-size:12px;color:#9fb3c8}table{width:100%;border-collapse:collapse;font-variant-numeric:tabular-nums}th,td{padding:7px 5px;text-align:right;border-bottom:1px solid #23374d;font-size:13px}th{color:#9fb3c8}td.ask{color:#f08b8d}td.bid{color:#80affc}footer{margin-top:22px;color:#9fb3c8;font-size:12px;line-height:1.7}details{margin-top:16px}pre{white-space:pre-wrap}@media(max-width:850px){main{padding:18px}.grid{grid-template-columns:1fr}canvas{height:300px}header{display:block}}
</style>
<main><header><div><div class="tag">DAILY BOT / SYNTHETIC LAB</div><h1>가상 시장 · 호가 재생</h1><p id="subtitle"></p></div><div class="tag">SIMULATION ONLY</div></header>
<div class="controls"><select id="ticker" aria-label="종목"></select><button id="play">▶ 재생</button><select id="speed" aria-label="재생 속도"><option value="1">1 틱 / 프레임</option><option value="10" selected>10 틱 / 프레임</option><option value="100">100 틱 / 프레임</option></select><input type="range" id="tick" min="0" value="0" aria-label="틱 시점"><span id="clock"></span></div>
<div class="grid"><section class="card"><div class="stats"><div><div class="label">최근 체결가</div><div class="value" id="price"></div></div><div><div class="label">첫 기록 대비</div><div class="value" id="change"></div></div><div><div class="label">틱 체결량</div><div class="value" id="volume"></div></div></div><canvas id="chart" aria-label="일중 체결가와 주문 기준선 그래프"></canvas><div class="legend"><span class="green">━ 체결가</span><span class="gold">┄ 주문 기준선</span><span>클릭·드래그하여 시점 선택</span></div></section>
<section class="card"><div class="stats"><strong>10단계 호가</strong><span id="spread" class="label"></span></div><table><thead><tr><th>매도 잔량</th><th>가격</th><th>매수 잔량</th></tr></thead><tbody id="book"></tbody></table></section></div>
<footer>현재가는 마지막 체결 가격입니다. 주문 기준선은 가격을 직접 바꾸지 않고 주문 수량과 방향에 영향을 줍니다. 체결이 없으면 현재가는 유지됩니다.<br>장중 연속매매의 가격·시간 우선순위를 적용한 무작위 모형입니다. 시가·종가 단일가, VI, 거래정지, 실제 시장 분포 보정 및 봇 주문의 시장충격은 포함하지 않습니다.<details><summary>생성 설정</summary><pre id="config"></pre></details></footer></main>
<script>
const DATA=__PAYLOAD__;
const $=id=>document.getElementById(id),fmt=n=>Number(n).toLocaleString('ko-KR');
const sel=$('ticker'),slider=$('tick'),canvas=$('chart'),ctx=canvas.getContext('2d');
for(const name of Object.keys(DATA.series)){const o=document.createElement('option');o.value=name;o.textContent=name;sel.append(o)}
$('subtitle').textContent=DATA.session+' · '+Object.keys(DATA.series).length+'종목 · seed '+DATA.config.seed;
$('config').textContent=JSON.stringify(DATA.config,null,2);
let playing=false, frameTime=0;
function rows(){return DATA.series[sel.value]}
function clock(s){const total=9*3600+Math.floor(s);return [Math.floor(total/3600),Math.floor(total%3600/60),total%60].map(v=>String(v).padStart(2,'0')).join(':')}
function draw(){const a=rows();const i=Math.min(+slider.value,a.length-1),r=a[i];
slider.max=a.length-1;$('clock').textContent=clock(r[0])+' · '+(i+1)+' / '+a.length;
$('price').textContent=fmt(r[1]);$('change').textContent=((r[1]/a[0][1]-1)*100).toFixed(2)+'%';$('volume').textContent=fmt(r[5]);
$('spread').textContent=r[3].length&&r[4].length?'스프레드 '+fmt(r[4][0][0]-r[3][0][0]):'한쪽 호가 없음';
const tbody=$('book');tbody.replaceChildren();
function bookRow(q,p,b,cls){const tr=document.createElement('tr');for(const v of [q,p,b]){const td=document.createElement('td');td.textContent=v===''?'':fmt(v);td.className=cls;tr.append(td)}tbody.append(tr)}
for(const [p,q] of [...r[4]].reverse())bookRow(q,p,'','ask');
for(const [p,q] of r[3])bookRow('',p,q,'bid');
const w=canvas.clientWidth,h=canvas.clientHeight,dpr=window.devicePixelRatio||1;canvas.width=w*dpr;canvas.height=h*dpr;ctx.scale(dpr,dpr);
const l=66,right=w-15,top=20,bottom=h-30;let lo=Infinity,hi=-Infinity;
for(const row of a){lo=Math.min(lo,row[1],row[2]);hi=Math.max(hi,row[1],row[2])}const margin=Math.max((hi-lo)*.08,1);lo-=margin;hi+=margin;
const x=j=>l+j/Math.max(1,a.length-1)*(right-l),y=p=>bottom-(p-lo)/(hi-lo)*(bottom-top);
ctx.font='11px system-ui';for(let k=0;k<5;k++){const p=lo+(hi-lo)*k/4,yy=y(p);ctx.strokeStyle='#273b50';ctx.beginPath();ctx.moveTo(l,yy);ctx.lineTo(right,yy);ctx.stroke();ctx.fillStyle='#9fb3c8';ctx.fillText(fmt(Math.round(p)),0,yy+4)}
for(const [col,color] of [[2,'#dfb86a'],[1,'#67e8b3']]){ctx.strokeStyle=color;ctx.lineWidth=1.5;ctx.setLineDash(col===2?[5,4]:[]);ctx.beginPath();const stride=Math.max(1,Math.floor(a.length/(w*2)));for(let j=0;j<a.length;j+=stride){if(j===0)ctx.moveTo(x(j),y(a[j][col]));else ctx.lineTo(x(j),y(a[j][col]))}ctx.lineTo(x(a.length-1),y(a.at(-1)[col]));ctx.stroke()}
ctx.setLineDash([]);ctx.strokeStyle='#fff8';ctx.beginPath();ctx.moveTo(x(i),top);ctx.lineTo(x(i),bottom);ctx.stroke();ctx.fillStyle='#67e8b3';ctx.beginPath();ctx.arc(x(i),y(r[1]),4,0,Math.PI*2);ctx.fill();ctx.fillStyle='#9fb3c8';ctx.fillText('09:00',l,h-8);ctx.fillText(clock(a.at(-1)[0]),right-52,h-8);
}
slider.oninput=draw;sel.onchange=()=>{slider.value=0;draw()};$('play').onclick=()=>{playing=!playing;$('play').textContent=playing?'❚❚ 일시정지':'▶ 재생';if(playing&&+slider.value>=rows().length-1)slider.value=0};
function animate(t){if(playing&&t-frameTime>=60){slider.value=Math.min(rows().length-1,+slider.value+Number($('speed').value));frameTime=t;draw();if(+slider.value>=rows().length-1){playing=false;$('play').textContent='▶ 재생'}}requestAnimationFrame(animate)}
let dragging=false;function seek(e){const rect=canvas.getBoundingClientRect();slider.value=Math.round(Math.max(0,Math.min(1,(e.clientX-rect.left-66)/(rect.width-81)))*(rows().length-1));draw()}
canvas.onpointerdown=e=>{dragging=true;canvas.setPointerCapture(e.pointerId);seek(e)};canvas.onpointermove=e=>{if(dragging)seek(e)};canvas.onpointerup=()=>dragging=false;window.onresize=draw;
draw();requestAnimationFrame(animate);
</script></html>'''
