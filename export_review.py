"""导出「逐笔审查」页面数据。

产出 out/bi-review-data.json：
  - meta      标的信息
  - bis       每笔的完整定义 + 自动计算的几何指标（用于页面高亮可疑笔）
  - bars      原始 K 线
  - processed 包含处理后 K 线（画分型用）
  - fractals  分型
"""
from __future__ import annotations

import json
import os

from chan import BiDirection, FractalType, SegmentDirection, build_structure

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "eth_1m_1000.json")
OUT_DIR = os.path.join(HERE, "out")
OUT = os.path.join(OUT_DIR, "bi-review-data.json")


def main() -> None:
    with open(DATA, encoding="utf-8") as fh:
        bars: list[dict] = json.load(fh)

    r = build_structure(bars)
    processed = r["inclusion"].processed
    fractals = {f.id: f for f in r["fractals"]}
    bis = r["bis"]
    segments = r["segments"]

    idx_to_ts = {p.index: p.end_ts for p in processed}

    def ts_of(ci: int) -> int:
        return idx_to_ts.get(ci, bars[-1]["ts"])

    # 每笔归属的线段（用于页面按段着色）
    seg_of_bi: dict[str, int] = {}
    for k, s in enumerate(segments):
        bi_ids = [b.id for b in bis]
        try:
            a = bi_ids.index(s.start_bi_id)
            z = bi_ids.index(s.confirmed_end_bi_id)
        except ValueError:
            continue
        for i in range(a, z + 1):
            seg_of_bi[bis[i].id] = k
    cur = r["current_segment"]
    if cur is not None:
        bi_ids = [b.id for b in bis]
        try:
            a = bi_ids.index(cur.start_bi_id)
            for i in range(a, len(bis)):
                seg_of_bi[bis[i].id] = len(segments)
        except ValueError:
            pass

    out_bis = []
    for k, b in enumerate(bis):
        sf = fractals.get(b.start_fractal_id)
        ef = fractals.get(b.end_fractal_id)
        span = b.end_center_index - b.start_center_index
        # 同向价格序：UP 应涨、DOWN 应跌
        price_ok = (b.end_price > b.start_price
                    if b.direction is BiDirection.UP
                    else b.end_price < b.start_price)
        # 反向笔长度比：该笔反向前一笔的长度（用于发现「过短反向笔」）
        prev_len = None
        if k > 0:
            prev = bis[k - 1]
            prev_len = abs(prev.end_price - prev.start_price)
        cur_len = abs(b.end_price - b.start_price)
        out_bis.append({
            "no": k,
            "direction": b.direction.value,
            "start_ts": ts_of(b.start_center_index),
            "end_ts": ts_of(b.end_center_index),
            "start_index": b.start_center_index,
            "end_index": b.end_center_index,
            "start_price": b.start_price,
            "end_price": b.end_price,
            "span": span,
            "start_fractal": None if sf is None else {
                "type": sf.type.value, "top": sf.top, "bottom": sf.bottom},
            "end_fractal": None if ef is None else {
                "type": ef.type.value, "top": ef.top, "bottom": ef.bottom},
            "segment_no": seg_of_bi.get(b.id),
            "flag_span_min": span == 4,          # 恰为最小跨度
            "flag_price_order_bad": not price_ok,
            "flag_shorter_than_prev_reverse": (
                prev_len is not None and cur_len < prev_len * 0.35
            ),
        })

    out = {
        "meta": {
            "symbol": "ETH-USDT-SWAP", "bar": "1m",
            "count": len(bars),
            "start_ts": bars[0]["ts"], "end_ts": bars[-1]["ts"],
            "min_bi_candles": 5,
        },
        "bars": [{"ts": b["ts"], "open": b["open"], "high": b["high"],
                  "low": b["low"], "close": b["close"]} for b in bars],
        "processed": [{"ts": p.end_ts, "index": p.index, "high": p.high,
                       "low": p.low, "open": p.open, "close": p.close}
                      for p in processed],
        "fractals": [{"type": f.type.value, "ts": f.center_ts,
                      "price": f.top if f.type is FractalType.TOP else f.bottom,
                      "status": f.status.value} for f in r["fractals"]],
        "bis": out_bis,
        "segment_count": len(segments),
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))

    n_span_min = sum(1 for b in out_bis if b["flag_span_min"])
    n_bad = sum(1 for b in out_bis if b["flag_price_order_bad"])
    n_short = sum(1 for b in out_bis if b["flag_shorter_than_prev_reverse"])
    print(f"导出 {OUT}")
    print(f"  笔 {len(out_bis)}  |  恰为最小跨度(span=4) {n_span_min}  "
          f"|  价格序异常 {n_bad}  |  短于前一反向笔 35% {n_short}")


if __name__ == "__main__":
    main()
