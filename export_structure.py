"""把缠论结构导出成前端可视化用的 JSON。

产出 out/structure.json：
  bars       原始 K 线（lightweight-charts 的 candlestick 数据）
  processed  包含处理后的 K 线（用于核对）
  fractals   分型标记（top / bottom）
  bis        笔（折线锚点 + 元数据）
  segments   线段（折线锚点 + 元数据）
  current    未完成的当前段
  stats      统计摘要
"""
from __future__ import annotations

import json
import os

from chan import BiDirection, SegmentDirection, build_structure

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "eth_1m_1000.json")
OUT_DIR = os.path.join(HERE, "out")
OUT = os.path.join(OUT_DIR, "structure.json")


def main() -> None:
    with open(DATA, encoding="utf-8") as fh:
        bars: list[dict] = json.load(fh)

    r = build_structure(bars)
    processed = r["inclusion"].processed
    fractals = r["fractals"]
    bis = r["bis"]
    segments = r["segments"]
    current = r["current_segment"]

    # 处理 K 线下标 -> 时间戳，供前端把结构点对回时间轴
    idx_to_ts = {p.index: p.end_ts for p in processed}

    def ts_of(ci: int) -> int:
        return idx_to_ts.get(ci, bars[-1]["ts"])

    out = {
        "meta": {
            "symbol": "ETH-USDT-SWAP",
            "bar": "1m",
            "count": len(bars),
            "start_ts": bars[0]["ts"],
            "end_ts": bars[-1]["ts"],
            "price_min": min(b["low"] for b in bars),
            "price_max": max(b["high"] for b in bars),
        },
        "bars": [
            {"ts": b["ts"], "open": b["open"], "high": b["high"],
             "low": b["low"], "close": b["close"]}
            for b in bars
        ],
        "processed": [
            {"ts": p.end_ts, "open": p.open, "high": p.high,
             "low": p.low, "close": p.close, "index": p.index}
            for p in processed
        ],
        "fractals": [
            {
                "type": f.type.value,
                "center_index": f.center_index,
                "ts": f.center_ts,
                "price": f.top if f.type.value == "TOP" else f.bottom,
                "top": f.top,
                "bottom": f.bottom,
                "status": f.status.value,
            }
            for f in fractals
        ],
        "bis": [
            {
                "id": b.id,
                "direction": b.direction.value,
                "start_ts": ts_of(b.start_center_index),
                "end_ts": ts_of(b.end_center_index),
                "start_index": b.start_center_index,
                "end_index": b.end_center_index,
                "start_price": b.start_price,
                "end_price": b.end_price,
                "state": b.state.value,
            }
            for b in bis
        ],
        "segments": [
            {
                "id": s.id,
                "direction": s.direction.value,
                "start_ts": ts_of(s.start_index),
                "end_ts": ts_of(s.end_index) if s.end_index is not None else None,
                "start_index": s.start_index,
                "end_index": s.end_index,
                "start_price": s.start_price,
                "end_price": s.end_price,
                "break_type": s.break_type.value,
                "state": s.state.value,
            }
            for s in segments
        ],
        "current": None if current is None else {
            "id": current.id,
            "direction": current.direction.value,
            "start_ts": ts_of(current.start_index),
            "start_price": current.start_price,
            "end_price": current.end_price,
            "state": current.state.value,
        },
        "stats": {
            "raw_bars": len(bars),
            "processed_bars": len(processed),
            "fractals": len(fractals),
            "bis": len(bis),
            "segments": len(segments),
            "bi_endpoint_revisions": r["bi_endpoint_revisions"],
        },
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))

    st = out["stats"]
    print(f"导出 {OUT}")
    print(f"  K线 {st['raw_bars']} -> 处理后 {st['processed_bars']} "
          f"(压缩 {100 - 100 * st['processed_bars'] / st['raw_bars']:.1f}%)")
    print(f"  分型 {st['fractals']} / 笔 {st['bis']} / 线段 {st['segments']} "
          f"/ 笔端点修订 {st['bi_endpoint_revisions']}")
    for k, s in enumerate(segments):
        print(f"  seg{k} {s.direction.value:4s} "
              f"{s.start_price:8.2f} -> {s.end_price:8.2f}  ({s.break_type.value})")


if __name__ == "__main__":
    main()
