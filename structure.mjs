// Independent generic functional diagram. No paper sample, layer thickness or fabrication claim.
export const layers=[
 {id:'source',en:'Source',name:'源極',use:'主要電子傳輸路徑的一端，連接外部電路；圖上的端點不代表特定接觸製程。'},
 {id:'drain',en:'Drain',name:'汲極',use:'與源極之間的偏壓提供傳輸驅動。實際端點及場分布需要真實結構才能分析。'},
 {id:'gate',en:'Gate',name:'閘極',use:'透過電場調整通道電荷與導電狀態，不是主電流必須經過的機械閥門。'},
 {id:'barrier',en:'Barrier',name:'AlGaN位障',use:'參與異質結構帶邊、極化與電位環境。它不會憑空創造電子，也不單獨決定面密度。'},
 {id:'channel',en:'Channel',name:'GaN通道區',use:'主要電子傳輸的功能區域，靠近異質介面。與下方GaN區的分界是教學上的功能區分。'},
 {id:'2deg',en:'2DEG',name:'二維電子氣',use:'介面附近受垂直侷限的電子分布；有實際厚度，不是另外成長的材料層。'},
 {id:'buffer',en:'Buffer',name:'緩衝區',use:'參與銜接成長材料、處理缺陷、應力及下方隔離。不同設計的材料與功能範圍可不同。'},
 {id:'substrate',en:'Substrate',name:'基板',use:'承載成長結構；此圖不指定基板材料、晶向、成核層或任何論文的特定樣品。'}
];
const label=(x,y,s,small=false)=>`<text x="${x}" y="${y}" class="${small?'small-label':''}">${s}</text>`;
const band=(id,y,h,s,fill)=>`<g data-layer="${id}"><rect class="band" x="45" y="${y}" width="550" height="${h}" rx="6" fill="${fill}"/>${label(65,y+h*.65,s)}</g>`;
export function structureSvg(){
 let b='';
 for(const [id,x,s]of [['source',45,'Source'],['gate',245,'Gate'],['drain',455,'Drain']])b+=`<g data-layer="${id}"><rect class="band terminal-band" x="${x}" y="20" width="135" height="48" rx="6" fill="#183549"/>${label(x+15,52,s)}</g>`;
 b+=band('barrier',90,52,'AlGaN / Barrier','#20566a');
 b+=band('channel',150,65,'GaN / Channel','#1c565d');
 b+='<g data-layer="2deg"><path class="electron-band" d="M290 161H568"/>'+label(355,191,'2DEG')+'</g>';
 b+=band('buffer',225,65,'Buffer','#243e56')+band('substrate',300,60,'Substrate','#303e50');
 return `<svg viewBox="0 0 640 390" role="img" aria-label="通用AlGaN/GaN HEMT功能示意；無指定樣品、厚度或基板材料">${b}</svg>`;
}
