"""缠论引擎不变式测试。

运行：python test_chan.py
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, ".")

from chan import (  # noqa: E402
    MIN_BI_CANDLES, BiDirection, SegmentDirection,
    build_structure,
)


def load_bars() -> list[dict]:
    with open("data/eth_1m_1000.json", encoding="utf-8") as fh:
        return json.load(fh)


def main() -> int:
    bars = load_bars()
    r = build_structure(bars)
    fr, bis, segs, incl = r["fractals"], r["bis"], r["segments"], r["inclusion"]
    P = incl.processed
    failures: list[str] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        print(f"  {'PASS' if ok else 'FAIL'}  {name} {detail}")
        if not ok:
            failures.append(name)

    print(f"\n数据: {len(bars)} 原始 K 线 -> {len(P)} 处理后 "
          f"(压缩 {100 - 100 * len(P) / len(bars):.1f}%)")
    print(f"分型 {len(fr)} / 笔 {len(bis)} / 线段 {len(segs)} "
          f"/ 笔端点修订 {r['bi_endpoint_revisions']}\n")

    print("— 包含处理 —")
    check("间隔方向持久化（无 None）", incl.direction in (1, -1))
    check("处理后 K 线均非空", all(p.high >= p.low for p in P))
    check("source_raw_ids 完备",
          sum(len(p.source_raw_ids) for p in P) == len(bars))
    check("source_raw_ids 覆盖全部原始序号",
          sorted(x for p in P for x in p.source_raw_ids) == list(range(len(bars))))

    print("\n— 分型 —")
    check("分型索引在 [1, len-2] 内",
          all(1 <= f.center_index <= len(P) - 2 for f in fr))
    check("顶分型 top > bottom", all(f.top > f.bottom for f in fr))
    check("确认位置 = 中心 + 1",
          all(f.confirm_index == f.center_index + 1 for f in fr))
    check("顶底数量基本均衡",
          abs(sum(1 for f in fr if f.type.value == "TOP")
              - sum(1 for f in fr if f.type.value == "BOTTOM")) <= len(fr) * 0.2)

    print("\n— 笔 —")
    check("1 方向严格交替",
          all(bis[i].direction != bis[i + 1].direction for i in range(len(bis) - 1)))
    check(f"2 每笔跨度 >= {MIN_BI_CANDLES} 根",
          all(b.end_center_index - b.start_center_index >= MIN_BI_CANDLES - 1
              for b in bis))
    check("3 笔首尾相接",
          all(bis[i].end_center_index == bis[i + 1].start_center_index
              for i in range(len(bis) - 1)))
    check("4 价格序：UP 涨 / DOWN 跌",
          all((b.end_price > b.start_price) if b.direction is BiDirection.UP
              else (b.end_price < b.start_price) for b in bis))
    check("5 索引与分型一致",
          all(b.end_center_index > b.start_center_index for b in bis))

    print("\n— 线段 —")
    # 6 段方向与价格路径一致
    bad6 = [s for s in segs
            if (s.end_price > s.start_price) != (s.direction is SegmentDirection.UP)]
    check("6 段方向与价格路径一致", not bad6, f"违规 {len(bad6)}")

    # 7 首尾严格相接
    check("7 线段首尾严格相接",
          all(abs(segs[i].end_price - segs[i + 1].start_price) < 1e-9
              and segs[i].end_index == segs[i + 1].start_index
              for i in range(len(segs) - 1)))

    # 8 段方向交替
    check("8 线段方向严格交替",
          all(segs[i].direction != segs[i + 1].direction
              for i in range(len(segs) - 1)))

    # 9 每段至少 3 笔
    def bis_in(s):
        i0 = next(i for i, b in enumerate(bis) if b.id == s.start_bi_id)
        i1 = next(i for i, b in enumerate(bis) if b.id == s.confirmed_end_bi_id)
        return bis[i0:i1 + 1]
    check("9 每段至少 3 笔", all(len(bis_in(s)) >= 3 for s in segs),
          f"最少 {min(len(bis_in(s)) for s in segs)} 笔")

    # 10 段终点 = 段内最后一个同向笔的 END，且为该方向极值
    #
    # 几何：向上段 = S X S X …，最高点在最后一个 S 的 END 处（紧随的 X 起于同价）。
    ok10 = True
    for s in segs:
        i0 = next(i for i, b in enumerate(bis) if b.id == s.start_bi_id)
        i1 = next(i for i, b in enumerate(bis) if b.id == s.confirmed_end_bi_id)
        seq = bis[i0:i1 + 1]
        fwd = (BiDirection.UP if s.direction is SegmentDirection.UP
               else BiDirection.DOWN)
        same = [b for b in seq if b.direction is fwd]
        if not same:
            ok10 = False
            continue
        last_same = same[-1]
        ext = (max(b.end_price for b in same) if fwd is BiDirection.UP
               else min(b.end_price for b in same))
        if abs(s.end_price - last_same.end_price) > 1e-9:
            ok10 = False
        if s.end_index != last_same.end_center_index:
            ok10 = False
        if abs(ext - s.end_price) > 1e-9:
            ok10 = False
    check("10 段终点=末同向笔终点且为段内极值", ok10)

    # 11 段方向 = 起始笔方向（段的第一笔决定段方向）
    check("11 段方向=起始笔方向",
          all(bis[next(i for i, b in enumerate(bis) if b.id == s.start_bi_id)].direction
              is (BiDirection.UP if s.direction is SegmentDirection.UP
                  else BiDirection.DOWN)
              for s in segs))

    # 11b 段链首尾相接（价格与索引都严格相等）
    check("11b 段链索引也相接",
          all(segs[i].end_index == segs[i + 1].start_index
              for i in range(len(segs) - 1)))

    # 12 段起点 = 前一段终点（整条链连续）
    chain_ok = True
    for i in range(len(segs) - 1):
        a, b = segs[i], segs[i + 1]
        if abs(a.end_price - b.start_price) > 1e-9:
            chain_ok = False
    check("12 段链价格连续", chain_ok)

    # 13 段跨越区间内不会出现「终点之后更极端「反方向」超出起点」
    check("13 段终点价位于段区间内",
          all(all(p.low - 1e-9 <= s.end_price <= p.high + 1e-9 for p in P
                  if p.index == s.end_index) for s in segs))

    # ── 起手规则（v3）──
    print("\n— 起手规则 —")

    # 14 规则 B/C：笔的独立 K 线约束 —— 反向分型之间必须有 >= 4 根处理后 K 线。
    #    等价于「一笔至少 5 根 K 线（含两端分型）」。
    check(f"14 笔端点间独立 K 线 >= {MIN_BI_CANDLES - 1}",
          all(b.end_center_index - b.start_center_index >= MIN_BI_CANDLES - 1
              for b in bis))

    # 15 规则 B/C（合成数据）：底分型起 → 中间若干「无独立K线」的顶底分型被忽略
    #    → 笔以第三个顶分型结束。
    synth = [
        (2700, 2695), (2695, 2690), (2690, 2685),
        (2660, 2655),                       # ci=3  B0 底
        (2665, 2660), (2670, 2665),
        (2690, 2685),                       # ci=6  T1 顶
        (2686, 2681),                       # ci=7  B1（与 T1 无独立K线）
        (2692, 2687),                       # ci=8  T2 顶（与 B1 无独立K线）
        (2688, 2683),                       # ci=9  B2（与 T2 无独立K线）
        (2700, 2695),                       # ci=10 T3 顶
        (2697, 2692), (2695, 2690), (2693, 2688),   # T3 后的独立 K 线
        (2689, 2684),                       # ci=14 B3 底
        (2691, 2686), (2693, 2688),
    ]
    sbars = [{"ts": i * 60000, "open": h, "high": h, "low": l, "close": l,
              "volume": 0.0} for i, (h, l) in enumerate(synth)]
    sr = build_structure(sbars)
    sb, sfr = sr["bis"], sorted(sr["fractals"], key=lambda x: x.center_index)
    ok15 = False
    if sb:
        b0 = sb[0]
        st = next((f for f in sfr if f.id == b0.start_fractal_id), None)
        en = next((f for f in sfr if f.id == b0.end_fractal_id), None)
        ok15 = (st is not None and en is not None
                and st.center_index == 3 and st.type.value == "BOTTOM"
                and en.center_index == 10 and en.type.value == "TOP"
                and b0.direction is BiDirection.UP)
    check("15 图2 规则：底分型起、跳过中间无独立K线的分型、第三个顶分型止",
          ok15, f"-> {len(sb)} 笔")

    # 16 规则 D：段序列起点应落在「最先能起段」的笔上。
    #    本例首段不从 bi[0] 起（bi[0..2] 三笔构不成段），而是从 bi[1] 起。
    if segs:
        s0 = segs[0]
        i0 = next(i for i, b in enumerate(bis) if b.id == s0.start_bi_id)
        check("16 图1 规则：首段起点后移到可起段处（非 bi[0]）",
              i0 >= 1
              and abs(s0.start_price - bis[i0 - 1].end_price) < 1e-9,
              f"首段起于 bi[{i0}]，前 {i0} 笔为预备笔")

    # 17 规则 D 的推论：段序列起点之前的笔不参与任何段
    if segs:
        first_bi_idx = next(i for i, b in enumerate(bis)
                            if b.id == segs[0].start_bi_id)
        covered = set()
        for s in segs:
            a = next(i for i, b in enumerate(bis) if b.id == s.start_bi_id)
            z = next(i for i, b in enumerate(bis) if b.id == s.confirmed_end_bi_id)
            covered |= set(range(a, z + 1))
        check("17 预备笔不落入任何段",
              all(i not in covered for i in range(first_bi_idx)))

    print()
    if failures:
        print(f"✗ {len(failures)} 项失败: {failures}")
        return 1
    print("✓ 全部不变式通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
