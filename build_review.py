"""生成「逐笔审查」页面：out/bi-review.html（自包含单文件）。

用法：逐笔点选 → 标「正确 / 错误」→ 错误的可填原因 → 导出 JSON。
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
D = os.path.join(HERE, "out", "bi-review-data.json")
LIB = os.path.join(HERE, "vendor", "lightweight-charts.standalone.production.js")
OUT = os.path.join(HERE, "out", "bi-review.html")


def main() -> None:
    with open(D, encoding="utf-8") as fh:
        data = fh.read()
    with open(LIB, encoding="utf-8") as fh:
        lib = fh.read()
    html = TPL.replace("__LIB__", lib).replace("__DATA__", data)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(html)
    print(f"写出 {OUT}  ({len(html)/1024:.0f} KB)")


TPL = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>逐笔审查 · ETH-USDT-SWAP · 1m</title>
<style>
  :root{--bg:#fff;--panel:#f7f7f8;--bd:#e3e5e8;--tx:#1a1a1a;--mu:#6b7280;
        --ok:#10b981;--bad:#ef4444;--skip:#9ca3af;--hl:#f59e0b;}
  *{box-sizing:border-box}
  html,body{margin:0;height:100%}
  body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC",
       "Microsoft YaHei",sans-serif;background:var(--bg);color:var(--tx);
       display:flex;flex-direction:column;height:100vh;overflow:hidden;font-size:13px}
  header{padding:9px 14px;border-bottom:1px solid var(--bd);background:var(--panel);
         display:flex;align-items:center;gap:12px;flex-wrap:wrap;flex-shrink:0}
  h1{font-size:14px;margin:0;font-weight:600}
  .prog{display:flex;gap:9px;font-size:12px;color:var(--mu);align-items:center}
  .prog b{font-variant-numeric:tabular-nums}
  .btns{margin-left:auto;display:flex;gap:6px;flex-wrap:wrap}
  button{font:inherit;font-size:12px;padding:5px 11px;border:1px solid var(--bd);
         border-radius:6px;background:#fff;cursor:pointer;white-space:nowrap}
  button:hover{border-color:#b9bcc2}
  button.pri{background:#1a1a1a;border-color:#1a1a1a;color:#fff}
  #main{flex:1;display:flex;min-height:0}
  /* 左侧：笔清单 */
  #list{width:232px;border-right:1px solid var(--bd);overflow-y:auto;flex-shrink:0;
        background:#fcfcfd}
  .row{padding:5px 10px;border-bottom:1px solid #f0f1f3;cursor:pointer;
       display:flex;align-items:center;gap:6px;font-size:12px;line-height:1.35}
  .row:hover{background:#f3f4f6}
  .row.sel{background:#1a1a1a;color:#fff}
  .row.sel .dim{color:#c9ccd1}
  .row .no{font-weight:600;font-variant-numeric:tabular-nums;min-width:24px}
  .row .dir{font-size:11px;padding:1px 5px;border-radius:3px;font-weight:600}
  .row .dir.UP{background:#fee2e2;color:#b91c1c}
  .row .dir.DOWN{background:#d1fae5;color:#047857}
  .row.sel .dir{background:rgba(255,255,255,.18);color:#fff}
  .row .dim{color:var(--mu);font-size:11px;font-variant-numeric:tabular-nums}
  .row .mk{margin-left:auto;font-size:12px}
  .row .mk.ok{color:var(--ok)} .row .mk.bad{color:var(--bad)}
  .row .warn{color:var(--hl);font-size:11px}
  /* 右侧 */
  #right{flex:1;display:flex;flex-direction:column;min-width:0}
  #chart{flex:1;min-height:0;position:relative}
  #chartEl{position:absolute;inset:0;z-index:1}
  #ovl{position:absolute;inset:0;pointer-events:none;z-index:9}
  #bar{border-top:1px solid var(--bd);background:var(--panel);padding:9px 14px;
       display:flex;gap:10px;align-items:flex-start;flex-wrap:wrap;flex-shrink:0}
  #info{font-size:12px;line-height:1.6;min-width:250px}
  #info .k{color:var(--mu)}
  #info b{font-variant-numeric:tabular-nums}
  .act{display:flex;gap:6px}
  .act button.on-ok{background:var(--ok);border-color:var(--ok);color:#fff}
  .act button.on-bad{background:var(--bad);border-color:var(--bad);color:#fff}
  #reason{flex:1;min-width:200px;min-height:34px;padding:6px 9px;font:inherit;
          font-size:12px;border:1px solid var(--bd);border-radius:6px;resize:vertical}
  .nav{display:flex;gap:6px;align-items:center}
  .legend{font-size:11px;color:var(--mu);display:flex;gap:12px;flex-wrap:wrap}
  .sw{display:inline-block;width:14px;height:0;border-top-width:3px;border-top-style:solid}
</style>
</head>
<body>
<header>
  <h1>逐笔审查 <span style="font-size:12px;font-weight:500;color:var(--hl);border:1px solid var(--hl);border-radius:4px;padding:1px 6px;margin-left:6px">新窗口复核 · 2026-09-03</span> · <span id="sym"></span></h1>
  <div class="prog">
    <span>共 <b id="tot"></b> 笔</span>
    <span style="color:var(--ok)">正确 <b id="cok">0</b></span>
    <span style="color:var(--bad)">错误 <b id="cbad">0</b></span>
    <span>未判 <b id="cnone"></b></span>
    <span style="color:var(--hl)">可疑 <b id="cwarn"></b></span>
  </div>
  <div class="btns">
    <button id="prev">← 上一笔</button>
    <button id="next">下一笔 →</button>
    <button id="jumpNextWarn">跳到下一个可疑</button>
    <button id="export" class="pri">导出判定 JSON</button>
    <button id="reset">清空</button>
  </div>
</header>
<div id="main">
  <div id="list"></div>
  <div id="right">
    <div id="chart">
      <div id="chartEl"></div>
      <svg id="ovl"></svg>
    </div>
    <div id="bar">
      <div id="info"></div>
      <div class="act">
        <button id="bOk">✓ 这一笔正确</button>
        <button id="bBad">✗ 这一笔错误</button>
        <button id="bNone">清除判定</button>
      </div>
      <textarea id="reason" placeholder="若判为「错误」，请写原因（例如：这里不该成笔 / 端点应落在 XX 分型 / 中间还有一笔被吃掉 …）"></textarea>
    </div>
  </div>
</div>

<script>__LIB__</script>
<script>
const DATA = __DATA__;
const sec = ms => Math.floor(ms/1000);
const BIS = DATA.bis;
const KEY = 'chan-bi-review-0903';

/* ── 判定状态 ── */
let verdict = {};                                  // no -> {v:'ok'|'bad', r:''}
try { verdict = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch(e) { verdict = {}; }
const save = () => localStorage.setItem(KEY, JSON.stringify(verdict));

let sel = 0;
const chartEl = document.getElementById('chartEl');
const wrapEl  = document.getElementById('chart');
const ovl     = document.getElementById('ovl');

/* ── 图表 ── */
const chart = LightweightCharts.createChart(chartEl, {
  layout:{background:{type:'solid',color:'#fff'},textColor:'#333',fontSize:11,
          fontFamily:'-apple-system,"PingFang SC","Microsoft YaHei",sans-serif'},
  grid:{vertLines:{color:'#f5f6f7'},horzLines:{color:'#f5f6f7'}},
  rightPriceScale:{borderColor:'#e3e5e8',scaleMargins:{top:.12,bottom:.12}},
  timeScale:{borderColor:'#e3e5e8',timeVisible:true,secondsVisible:false},
  crosshair:{mode:LightweightCharts.CrosshairMode.Normal},
});
const cs = chart.addCandlestickSeries({
  upColor:'#ef4444',downColor:'#10b981',
  wickUpColor:'#ef4444',wickDownColor:'#10b981',borderVisible:false});
cs.setData(DATA.bars.map(b=>({time:sec(b.ts),open:b.open,high:b.high,
                              low:b.low,close:b.close})));

/* 全部笔：淡灰底 */
const graySeries = BIS.map(b => {
  const s = chart.addLineSeries({color:'#d1d5db',lineWidth:1,
    priceLineVisible:false,lastValueVisible:false,
    crosshairMarkerVisible:false,pointMarkersVisible:false});
  s.setData([{time:sec(b.start_ts),value:b.start_price},
             {time:sec(b.end_ts),value:b.end_price}]);
  return s;
});
/* 选中笔：高亮 */
let hlSeries = null;

/* ── 左侧清单 ── */
const listEl = document.getElementById('list');
function renderList() {
  listEl.innerHTML = '';
  BIS.forEach(b => {
    const v = verdict[b.no];
    const warn = b.flag_span_min || b.flag_shorter_than_prev_reverse;
    const d = document.createElement('div');
    d.className = 'row' + (b.no === sel ? ' sel' : '');
    d.dataset.no = b.no;
    d.innerHTML =
      `<span class="no">${b.no}</span>` +
      `<span class="dir ${b.direction}">${b.direction === 'UP' ? '上' : '下'}</span>` +
      `<span class="dim">${b.start_price.toFixed(1)}→${b.end_price.toFixed(1)}</span>` +
      (warn ? `<span class="warn" title="可疑">●</span>` : '') +
      `<span class="mk ${v ? v.v : ''}">${v ? (v.v==='ok'?'✓':'✗') : ''}</span>`;
    d.onclick = () => select(b.no);
    listEl.appendChild(d);
  });
}
function scrollSel() {
  const el = listEl.querySelector(`.row[data-no="${sel}"]`);
  if (el) el.scrollIntoView({block:'nearest'});
}

/* ── 选中逻辑 ── */
function select(no) {
  if (no < 0 || no >= BIS.length) return;
  sel = no;
  const b = BIS[no];

  // 高亮线
  if (hlSeries) chart.removeSeries(hlSeries);
  hlSeries = chart.addLineSeries({
    color: b.direction === 'UP' ? '#dc2626' : '#059669',
    lineWidth: 3, priceLineVisible:false, lastValueVisible:false,
    crosshairMarkerVisible:false, pointMarkersVisible:false});
  hlSeries.setData([{time:sec(b.start_ts),value:b.start_price},
                    {time:sec(b.end_ts),value:b.end_price}]);

  // 视野：该笔前后各留 60% 窗口
  const lo = b.start_ts - (b.end_ts - b.start_ts) * 0.7;
  const hi = b.end_ts + (b.end_ts - b.start_ts) * 0.7;
  chart.timeScale().setVisibleRange({from: sec(lo), to: sec(hi)});

  // 信息面板
  const v = verdict[no];
  document.getElementById('info').innerHTML =
    `<b>第 ${no} 笔</b> · ${b.direction === 'UP' ? '向上笔' : '向下笔'}
     <span class="k">（段 #${b.segment_no ?? '—'}）</span><br>
     <span class="k">端点分型</span>
     ${b.start_fractal ? b.start_fractal.type : '?'}(ci=${b.start_index})
     → ${b.end_fractal ? b.end_fractal.type : '?'}(ci=${b.end_index})<br>
     <span class="k">价格</span> <b>${b.start_price.toFixed(2)} → ${b.end_price.toFixed(2)}</b>
     <span class="k">跨度</span> <b>${b.span}</b> 根<br>
     ${b.flag_span_min ? '<span style="color:var(--hl)">● 恰为最小跨度</span> ' : ''}
     ${b.flag_shorter_than_prev_reverse ? '<span style="color:var(--hl)">● 明显短于前一反向笔</span>' : ''}`;

  document.getElementById('reason').value = v && v.r ? v.r : '';
  document.getElementById('bOk').className  = v && v.v === 'ok'  ? 'on-ok'  : '';
  document.getElementById('bBad').className = v && v.v === 'bad' ? 'on-bad' : '';

  renderList(); scrollSel(); redrawOverlay(); updateCounts();
}

/* ── 分型叠加（只画当前视野附近）── */
function redrawOverlay() {
  ovl.innerHTML = '';
  const w = wrapEl.clientWidth, h = wrapEl.clientHeight;
  ovl.setAttribute('viewBox', `0 0 ${w} ${h}`);
  const ts = chart.timeScale();
  DATA.fractals.forEach(f => {
    const x = ts.timeToCoordinate(sec(f.ts));
    if (x === null || x < -15 || x > w + 15) return;
    const y = cs.priceToCoordinate(f.price);
    if (y === null) return;
    const up = f.type === 'TOP';
    const cy = up ? y - 9 : y + 9;
    const p = document.createElementNS('http://www.w3.org/2000/svg','path');
    p.setAttribute('d', up
      ? `M${x},${cy-4} L${x+4},${cy+3} L${x-4},${cy+3} Z`
      : `M${x},${cy+4} L${x+4},${cy-3} L${x-4},${cy-3} Z`);
    p.setAttribute('fill', up ? '#f59e0b' : '#8b5cf6');
    p.setAttribute('opacity','.85');
    ovl.appendChild(p);
  });
}

/* ── 判定 ── */
function setVerdict(v) {
  const b = BIS[sel];
  const r = document.getElementById('reason').value.trim();
  if (v === null) delete verdict[b.no];
  else verdict[b.no] = {v, r};
  save(); select(sel);
}
document.getElementById('bOk').onclick   = () => setVerdict('ok');
document.getElementById('bBad').onclick  = () => setVerdict('bad');
document.getElementById('bNone').onclick = () => { document.getElementById('reason').value=''; setVerdict(null); };
document.getElementById('reason').addEventListener('input', () => {
  const b = BIS[sel];
  if (verdict[b.no]) { verdict[b.no].r = document.getElementById('reason').value.trim(); save(); }
});

/* ── 计数 ── */
function updateCounts() {
  let ok=0, bad=0, warn=0;
  BIS.forEach(b => { if (b.flag_span_min || b.flag_shorter_than_prev_reverse) warn++; });
  Object.values(verdict).forEach(v => { if (v.v==='ok') ok++; else if (v.v==='bad') bad++; });
  document.getElementById('tot').textContent = BIS.length;
  document.getElementById('cok').textContent = ok;
  document.getElementById('cbad').textContent = bad;
  document.getElementById('cnone').textContent = BIS.length - ok - bad;
  document.getElementById('cwarn').textContent = warn;
}

/* ── 导航 ── */
document.getElementById('prev').onclick = () => select(sel - 1);
document.getElementById('next').onclick = () => select(sel + 1);
document.getElementById('jumpNextWarn').onclick = () => {
  for (let i = 1; i <= BIS.length; i++) {
    const k = (sel + i) % BIS.length;
    const b = BIS[k];
    if (b.flag_span_min || b.flag_shorter_than_prev_reverse) { select(k); return; }
  }
};

/* ── 导出 ── */
document.getElementById('export').onclick = () => {
  const payload = {
    exported_at: new Date().toISOString(),
    symbol: DATA.meta.symbol, bar: DATA.meta.bar,
    total: BIS.length,
    verdicts: BIS.map(b => {
      const v = verdict[b.no];
      return {
        no: b.no, direction: b.direction,
        start_index: b.start_index, end_index: b.end_index,
        start_price: +b.start_price.toFixed(2), end_price: +b.end_price.toFixed(2),
        span: b.span,
        verdict: v ? v.v : 'unjudged',
        reason: v && v.r ? v.r : '',
      };
    }).filter(x => x.verdict !== 'unjudged'),
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], {type:'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'bi-review-verdicts.json';
  a.click();
};

document.getElementById('reset').onclick = () => {
  if (!confirm('清空全部判定？')) return;
  verdict = {}; save(); select(sel);
};

/* ── 键盘 ── */
document.addEventListener('keydown', e => {
  const tag = (e.target.tagName || '').toLowerCase();
  if (tag === 'textarea' || tag === 'input') return;
  if (e.key === 'ArrowLeft')  { select(sel - 1); e.preventDefault(); }
  if (e.key === 'ArrowRight') { select(sel + 1); e.preventDefault(); }
  if (e.key === 'o' || e.key === 'O') setVerdict('ok');
  if (e.key === 'x' || e.key === 'X') setVerdict('bad');
});

/* ── 初始化 ── */
document.getElementById('sym').textContent =
  DATA.meta.symbol + ' · ' + DATA.meta.bar + ' · ' + DATA.meta.count + ' 根';
renderList(); select(0); updateCounts();
const ro = new ResizeObserver(() => {
  chart.applyOptions({width:wrapEl.clientWidth, height:wrapEl.clientHeight});
  redrawOverlay();
});
ro.observe(wrapEl);
chart.applyOptions({width:wrapEl.clientWidth, height:wrapEl.clientHeight});
chart.timeScale().subscribeVisibleLogicalRangeChange(redrawOverlay);
</script>
</body>
</html>
"""


if __name__ == "__main__":
    main()
