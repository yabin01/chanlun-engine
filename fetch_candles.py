"""从 OKX 拉取 1000 根 1m K 线（随机历史窗口），写入 data/eth_1m_1000.json。

用法：
    python fetch_candles.py            # 随机窗口
    python fetch_candles.py --days 30  # 限定在最近 30 天内随机
    python fetch_candles.py --anchor 1790846340000   # 指定锚点(ms)，取该点之前 1000 根

要点（踩过的坑）：
  - CLI 用托管 Node 直调 dist/index.js，避开 okx.cmd 的中文路径问题。
  - 必须经 SSRUN 代理：HTTP_PROXY/HTTPS_PROXY=http://127.0.0.1:7890。
  - 单次上限 300 根；用 --after <ts> 取「严格早于 ts」的一批。
  - 输出为 OKX v5 原始数组 [ts,o,h,l,c,vol,volCcy,volCcyQuote,confirm]，新的在前。
"""
from __future__ import annotations

import argparse
import json
import os
import random
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "data", "eth_1m_1000.json")
BAK = os.path.join(HERE, "data", "eth_1m_1000.prev.json")

NODE = r"C:\Users\刘亚斌\.workbuddy\binaries\node\versions\22.22.2-3\node.exe"
CLI = (r"C:\Users\刘亚斌\.workbuddy\binaries\node\versions\22.22.2-3"
       r"\node_modules\@okx_ai\okx-trade-cli\dist\index.js")
INST = "ETH-USDT-SWAP"
BAR_MS = 60_000
WANT = 1000


def run_cli(after_ms: int | None, limit: int = 300) -> list[list]:
    env = dict(os.environ)
    env["HTTP_PROXY"] = env["HTTPS_PROXY"] = "http://127.0.0.1:7890"
    env["NO_PROXY"] = ""
    cmd = [NODE, CLI, "market", "candles", INST, "--bar", "1m",
           "--limit", str(limit), "--json"]
    if after_ms is not None:
        cmd += ["--after", str(after_ms)]
    for attempt in range(4):
        p = subprocess.run(cmd, capture_output=True, text=True, env=env,
                           encoding="utf-8", errors="replace", timeout=60)
        txt = p.stdout.strip()
        # 去掉 CLI 的版本提示/warn，只保留 JSON 数组
        i = txt.find("[")
        if i >= 0:
            try:
                data = json.loads(txt[i:])
                if isinstance(data, list) and data:
                    return data
            except json.JSONDecodeError:
                pass
        time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"抓取失败 after={after_ms}\nstdout={p.stdout[:400]}\n"
                       f"stderr={p.stderr[:400]}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=float, default=45.0,
                    help="随机窗口限定在最近 N 天内（默认 45）")
    ap.add_argument("--anchor", type=int, default=None,
                    help="指定锚点毫秒时间戳，取该点之前 1000 根")
    args = ap.parse_args()

    now_ms = int(time.time() * 1000)
    if args.anchor is not None:
        anchor = args.anchor
    else:
        lo = now_ms - int(args.days * 86400_000)
        hi = now_ms - WANT * BAR_MS - 60_000
        # 对齐到整分钟
        anchor = random.randint(lo, hi) // BAR_MS * BAR_MS
    print(f"锚点 = {anchor}  ({time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(anchor/1000))})")

    rows: list[list] = []
    cursor = anchor
    while len(rows) < WANT:
        batch = run_cli(cursor, 300)
        # 该批新的在前；过滤掉 >= cursor 的（保险）
        batch = [r for r in batch if int(r[0]) < cursor]
        if not batch:
            raise RuntimeError(f"该锚点无更多数据（已取 {len(rows)} 根），换个锚点")
        rows.extend(batch)
        cursor = min(int(r[0]) for r in batch)
        print(f"  已取 {len(rows)} 根，最早 {time.strftime('%m-%d %H:%M', time.gmtime(cursor/1000))}")
        time.sleep(0.25)

    rows = rows[:WANT]
    rows.sort(key=lambda r: int(r[0]))          # 升序

    bars = [{"ts": int(r[0]), "open": float(r[1]), "high": float(r[2]),
             "low": float(r[3]), "close": float(r[4]), "volume": float(r[5])}
            for r in rows]

    # 连续性校验
    gaps = [bars[i + 1]["ts"] - bars[i]["ts"] for i in range(len(bars) - 1)]
    bad_gaps = [g for g in gaps if g != BAR_MS]
    if bad_gaps:
        print(f"  ⚠ 存在 {len(bad_gaps)} 处非 1m 间隔：{bad_gaps[:5]}")

    if os.path.exists(OUT):
        os.replace(OUT, BAK)
        print(f"旧数据已备份 -> {os.path.basename(BAK)}")
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(bars, fh, ensure_ascii=False, separators=(",", ":"))

    t0, t1 = bars[0]["ts"], bars[-1]["ts"]
    print(f"\n写出 {OUT}")
    print(f"  {len(bars)} 根  "
          f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(t0/1000))} → "
          f"{time.strftime('%Y-%m-%d %H:%M', time.gmtime(t1/1000))} UTC")
    print(f"  价格 {bars[0]['open']:.2f} → {bars[-1]['close']:.2f}  "
          f"区间 [{min(b['low'] for b in bars):.2f}, {max(b['high'] for b in bars):.2f}]")


if __name__ == "__main__":
    main()
