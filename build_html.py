"""把结构 JSON + lightweight-charts 库打包成一个自包含 HTML。

产出 out/chan-chart.html —— 双击即可在浏览器打开，无需服务器、无需联网。

图层（自下而上）：
  1. K 线（lightweight-charts candlestick）
  2. 分型标记（SVG 叠加层：顶分型朝上三角 / 底分型朝下三角）
  3. 笔（细折线，逐笔着色）
  4. 线段（粗折线，逐段着色；未完成段用虚线）

另附图层开关与统计面板。
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
STRUCT = os.path.join(HERE, "out", "structure.json")
LIB = os.path.join(HERE, "vendor", "lightweight-charts.standalone.production.js")
OUT = os.path.join(HERE, "out", "chan-chart.html")


def main() -> None:
    with open(STRUCT, encoding="utf-8") as fh:
        data = fh.read()
    with open(LIB, encoding="utf-8") as fh:
        lib = fh.read()

    html = TEMPLATE.replace("__LIB__", lib).replace("__DATA__", data)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"写出 {OUT}  ({len(html) / 1024:.0f} KB)")


TEMPLATE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>缠论结构 · ETH-USDT-SWAP · 1m · 1000 根</title>
<style>
  :root {
    --bg:#fff; --panel:#f7f7f8; --border:#e3e5e8;
    --text:#1a1a1a; --muted:#6b7280;
  }
  * { box-sizing: border-box; }
  html, body { margin:0; padding:0; height:100%; }
  body {
    font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
                "Hiragino Sans GB","Microsoft YaHei",sans-serif;
    background:var(--bg); color:var(--text);
    display:flex; flex-direction:column; height:100vh; overflow:hidden;
  }
  header {
    padding:11px 18px; border-bottom:1px solid var(--border);
    display:flex; align-items:center; gap:16px; flex-wrap:wrap;
    background:var(--panel); flex-shrink:0;
  }
  h1 { font-size:14.5px; margin:0; font-weight:600; }
  .meta { font-size:12px; color:var(--muted); font-variant-numeric:tabular-nums; }
  .stats { display:flex; gap:13px; flex-wrap:wrap; }
  .stat { font-size:12px; color:var(--muted); }
  .stat b { color:var(--text); font-variant-numeric:tabular-nums; font-weight:600; }
  .toggles { display:flex; gap:6px; margin-left:auto; flex-wrap:wrap; }
  .tg {
    font-size:12px; padding:4px 11px; border:1px solid var(--border);
    border-radius:999px; background:#fff; cursor:pointer; user-select:none;
    color:var(--muted); transition:all .12s; white-space:nowrap;
  }
  .tg:hover { border-color:#c9ccd1; }
  .tg.on { background:#1a1a1a; border-color:#1a1a1a; color:#fff; }
  .tg i { display:inline-block; width:7px; height:7px; border-radius:50%;
          margin-right:5px; vertical-align:middle; }
  #wrap { flex:1; min-height:0; position:relative; }
  /* ★ z-index 必须显式设置：lightweight-charts 在 #chart 内插入多个绝对定位
     canvas，若不抬升 overlay，SVG 会被 canvas 盖住（实测分型一个都画不出来）。 */
  #chart   { position:absolute; inset:0; z-index:1; }
  #overlay { position:absolute; inset:0; pointer-events:none; z-index:9; }
  footer {
    padding:7px 18px; border-top:1px solid var(--border); background:var(--panel);
    font-size:11.5px; color:var(--muted); display:flex; gap:16px;
    flex-wrap:wrap; flex-shrink:0;
  }
  .legend { display:flex; gap:14px; flex-wrap:wrap; }
  .legend span { display:inline-flex; align-items:center; gap:5px; }
  .swatch { width:16px; height:0; border-top-width:3px; border-top-style:solid; }
  .dot { width:9px; height:9px; border-radius:2px; display:inline-block;
         transform:rotate(45deg); }
</style>
</head>
<body>
<header>
  <h1>缠论结构 · <span id="sym"></span></h1>
  <div class="meta" id="range"></div>
  <div class="stats" id="stats"></div>
  <div class="toggles">
    <div class="tg on" data-layer="fractals"><i style="background:#f59e0b"></i>分型</div>
    <div class="tg on" data-layer="bis"><i style="background:#3b82f6"></i>笔</div>
    <div class="tg on" data-layer="segments"><i style="background:#059669"></i>线段</div>
    <div class="tg" data-layer="onlySeg"><i style="background:#1a1a1a"></i>只看线段</div>
  </div>
</header>
<div id="wrap">
  <div id="chart"></div>
  <svg id="overlay"></svg>
</div>
<footer>
  <div class="legend">
    <span><span class="swatch" style="border-color:#ef4444"></span>阳线</span>
    <span><span class="swatch" style="border-color:#10b981"></span>阴线</span>
    <span><span class="swatch" style="border-color:#3b82f6"></span>笔（上升笔深蓝 / 下降笔浅蓝）</span>
    <span><span class="swatch" style="border-color:#dc2626"></span>线段（向上红 / 向下绿）</span>
    <span><span class="dot" style="background:#f59e0b"></span>顶分型</span>
    <span><span class="dot" style="background:#8b5cf6"></span>底分型</span>
  </div>
  <div style="margin-left:auto">滚轮缩放 · 拖拽平移 · 双击复位</div>
</footer>

<script>__LIB__</script>
<script>
const DATA = __DATA__;
const sec = ms => Math.floor(ms / 1000);

const chartEl = document.getElementById('chart');
const wrapEl  = document.getElementById('wrap');
const overlay = document.getElementById('overlay');

const chart = LightweightCharts.createChart(chartEl, {
  layout: { background: { type:'solid', color:'#ffffff' }, textColor:'#333',
            fontSize:11,
            fontFamily:'-apple-system,"PingFang SC","Microsoft YaHei",sans-serif' },
  grid: { vertLines:{ color:'#f0f1f3' }, horzLines:{ color:'#f0f1f3' } },
  rightPriceScale: { borderColor:'#e3e5e8', scaleMargins:{ top:0.08, bottom:0.08 } },
  timeScale: { borderColor:'#e3e5e8', timeVisible:true, secondsVisible:false,
               rightOffset:6, barSpacing:3 },
  crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
});

// ── 1. K 线 ──
const candleSeries = chart.addCandlestickSeries({
  upColor:'#ef4444', downColor:'#10b981',
  wickUpColor:'#ef4444', wickDownColor:'#10b981', borderVisible:false,
});
candleSeries.setData(DATA.bars.map(b => ({
  time: sec(b.ts), open:b.open, high:b.high, low:b.low, close:b.close,
})));

// ── 2. 笔 & 3. 线段 ──
const biSeriesList = [], segSeriesList = [];

function poly(points, color, width, dashed) {
  const s = chart.addLineSeries({
    color, lineWidth: width,
    lineStyle: dashed ? LightweightCharts.LineStyle.Dashed
                      : LightweightCharts.LineStyle.Solid,
    priceLineVisible:false, lastValueVisible:false,
    crosshairMarkerVisible:false, pointMarkersVisible:false,
  });
  s.setData(points);
  return s;
}

DATA.bis.forEach(b => {
  const color = b.direction === 'UP' ? '#3b82f6' : '#a5b4fc';
  biSeriesList.push(poly([
    { time: sec(b.start_ts), value: b.start_price },
    { time: sec(b.end_ts),   value: b.end_price   },
  ], color, 2, false));
});

DATA.segments.forEach(s => {
  const color = s.direction === 'UP' ? '#dc2626' : '#059669';
  segSeriesList.push(poly([
    { time: sec(s.start_ts), value: s.start_price },
    { time: sec(s.end_ts),   value: s.end_price   },
  ], color, 3, false));
});
if (DATA.current) {
  const c = DATA.current;
  const color = c.direction === 'UP' ? '#dc2626' : '#059669';
  segSeriesList.push(poly([
    { time: sec(c.start_ts), value: c.start_price },
    { time: sec(DATA.meta.end_ts), value: c.end_price },
  ], color, 3, true));
}

// ── 4. 分型（SVG 叠加层）──
function redrawOverlay() {
  overlay.innerHTML = '';
  const w = wrapEl.clientWidth, h = wrapEl.clientHeight;
  overlay.setAttribute('viewBox', `0 0 ${w} ${h}`);
  overlay.setAttribute('width', w);
  overlay.setAttribute('height', h);
  if (!layerOn.fractals) return;

  const ts = chart.timeScale();
  let drawn = 0;
  DATA.fractals.forEach(f => {
    const x = ts.timeToCoordinate(sec(f.ts));
    if (x === null || x === undefined || x < -15 || x > w + 15) return;
    const y = candleSeries.priceToCoordinate(f.price);
    if (y === null || y === undefined) return;
    const up = f.type === 'TOP';
    const cy = up ? y - 10 : y + 10;
    const p = document.createElementNS('http://www.w3.org/2000/svg', 'path');
    p.setAttribute('d', up
      ? `M${x},${cy-4.5} L${x+4.5},${cy+3.5} L${x-4.5},${cy+3.5} Z`
      : `M${x},${cy+4.5} L${x+4.5},${cy-3.5} L${x-4.5},${cy-3.5} Z`);
    p.setAttribute('fill', up ? '#f59e0b' : '#8b5cf6');
    p.setAttribute('opacity', '0.9');
    p.setAttribute('stroke', '#fff');
    p.setAttribute('stroke-width', '0.6');
    overlay.appendChild(p);
    drawn++;
  });
  overlay.dataset.drawn = drawn;
}

// ── 图层开关 ──
const layerOn = { fractals:true, bis:true, segments:true, onlySeg:false };
document.querySelectorAll('.tg').forEach(el => {
  el.addEventListener('click', () => {
    const k = el.dataset.layer;
    layerOn[k] = !layerOn[k];
    el.classList.toggle('on', layerOn[k]);
    if (k === 'bis')      biSeriesList.forEach(s => s.applyOptions({ visible: layerOn.bis }));
    if (k === 'segments') segSeriesList.forEach(s => s.applyOptions({ visible: layerOn.segments }));
    if (k === 'onlySeg') {
      layerOn.bis = !layerOn.onlySeg;
      biSeriesList.forEach(s => s.applyOptions({ visible: layerOn.bis }));
      const btg = document.querySelector('.tg[data-layer="bis"]');
      btg.classList.toggle('on', layerOn.bis);
      candleSeries.applyOptions({ visible: !layerOn.onlySeg });
      candleSeries.applyOptions({ visible: !layerOn.onlySeg });
    }
    redrawOverlay();
  });
});

// ── 头部 ──
const fmt = ms => {
  const d = new Date(ms), p = n => String(n).padStart(2,'0');
  return `${d.getUTCFullYear()}-${p(d.getUTCMonth()+1)}-${p(d.getUTCDate())} `
       + `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}`;
};
document.getElementById('sym').textContent = DATA.meta.symbol + ' · ' + DATA.meta.bar;
document.getElementById('range').textContent =
  `${fmt(DATA.meta.start_ts)} → ${fmt(DATA.meta.end_ts)} UTC`;
const st = DATA.stats;
document.getElementById('stats').innerHTML = `
  <div class="stat">K线 <b>${st.raw_bars}</b> → 包含处理后 <b>${st.processed_bars}</b></div>
  <div class="stat">分型 <b>${st.fractals}</b></div>
  <div class="stat">笔 <b>${st.bis}</b></div>
  <div class="stat">线段 <b>${st.segments}</b> + 1 未完成</div>
  <div class="stat">笔端点修订 <b>${st.bi_endpoint_revisions}</b></div>`;

// ── 联动重绘 ──
chart.timeScale().subscribeVisibleLogicalRangeChange(redrawOverlay);
const ro = new ResizeObserver(() => { chart.applyOptions({
  width: wrapEl.clientWidth, height: wrapEl.clientHeight }); redrawOverlay(); });
ro.observe(wrapEl);

chart.applyOptions({ width: wrapEl.clientWidth, height: wrapEl.clientHeight });
chart.timeScale().fitContent();
[0, 80, 250, 600, 1200].forEach(t => setTimeout(redrawOverlay, t));

document.getElementById('wrap').addEventListener('dblclick', () => {
  chart.timeScale().fitContent();
  setTimeout(redrawOverlay, 40);
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
