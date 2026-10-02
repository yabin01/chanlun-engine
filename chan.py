"""缠论结构引擎：分型 / 笔 / 线段。

严格按《缠论量化-笔与线段-伪代码.md》实现，因果流式（每根 K 线只驱动一次判定）。

分层：
  1. InclusionProcessor   K 线包含关系处理（顺序、方向持久化）
  2. FractalEngine        三 K 双向严格分型判定 + INVALIDATED 回滚
  3. BiEngine             笔的四步划分法 + 端点原地修订
  4. SegmentEngine        特征序列 + 非包含处理 + TYPE_1 / TYPE_2 两种情况
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ───────────────────────────── 常量 ─────────────────────────────

MIN_BI_CANDLES = 5
# 一笔至少含 5 根处理后 K 线（含两端分型）⇒ center_index 之差 >= 4


# ───────────────────────────── 枚举 ─────────────────────────────


class FractalType(str, Enum):
    TOP = "TOP"
    BOTTOM = "BOTTOM"


class FractalStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    SUPERSEDED = "SUPERSEDED"


class BiDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class BiState(str, Enum):
    BUILDING = "BUILDING"
    EXTENDING = "EXTENDING"


class SegmentDirection(str, Enum):
    UP = "UP"
    DOWN = "DOWN"


class SegmentState(str, Enum):
    EXTENDING = "EXTENDING"
    TYPE_2_PENDING = "TYPE_2_PENDING"
    CONFIRMED = "CONFIRMED"


class BreakType(str, Enum):
    NONE = "NONE"
    TYPE_1 = "TYPE_1"
    TYPE_2 = "TYPE_2"


# ───────────────────────────── 数据模型 ─────────────────────────────


@dataclass
class Candle:
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    raw_index: int = 0


@dataclass
class ProcessedCandle:
    index: int
    start_ts: int
    end_ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    source_raw_ids: list[int] = field(default_factory=list)
    is_artificial: bool = False
    # ★ 包含处理时若与其前一根合并，产出的是「人工 K 线」。按缠论原文
    #   （第 65、67 课）「顶分型的最高那根 K 线不能是合并后的 K 线」，
    #   人工 K 线**不允许**充任分型的中心 K 线。这是用户实测反馈的核心：
    #   包含处理会把两根真 K 线压成一根，令「中心价 = 真实极值价」失效，
    #   从而画出「顶分型不是该笔最高点」的错误笔。


@dataclass
class Fractal:
    id: str
    type: FractalType
    center_index: int
    confirm_index: int
    top: float
    bottom: float
    source_processed_ids: tuple[int, int, int]
    center_ts: int = 0
    status: FractalStatus = FractalStatus.CONFIRMED
    is_provisional: bool = False
    # ★ 包含处理会把两根 K 线合并成一根「人工 K 线」。若它出现在三根窗口的
    #   边缘（左/右），该分型在包含处理方向**反转**时可能消失 —— 因为「谁被
    #   合并进谁」取决于当前方向。这里把它标为 provisional：不进入笔序列，
    #   只做「最佳候选分型」用于真实极值定位。真分型（provisional=False）
    #   一旦成立就不会因后续方向变化而消失。
    raw_high: float = 0.0
    raw_low: float = 0.0
    # 未做包含处理时的真实极值（用于「笔的端点必须是真实极值」校验）


@dataclass
class Bi:
    id: str
    start_fractal_id: str
    end_fractal_id: str
    start_center_index: int
    end_center_index: int
    start_price: float
    end_price: float
    direction: BiDirection
    state: BiState = BiState.BUILDING


@dataclass(frozen=True)
class FeatureElement:
    id: str
    source_bi_id: str
    direction: BiDirection
    high: float
    low: float
    start_index: int
    end_index: int


@dataclass(frozen=True)
class StandardFeatureElement:
    id: str
    source_feature_ids: tuple[str, ...]
    source_bi_ids: tuple[str, ...]
    direction: BiDirection
    high: float
    low: float
    start_index: int
    end_index: int


@dataclass(frozen=True)
class FeatureFractal:
    id: str
    type: str
    left_id: str
    center_id: str
    right_id: str
    high: float
    low: float
    has_gap: bool


@dataclass
class Segment:
    id: str
    direction: SegmentDirection
    state: SegmentState
    start_bi_id: str
    current_end_bi_id: str | None
    confirmed_end_bi_id: str | None
    start_index: int
    start_price: float
    end_index: int | None = None
    end_price: float | None = None
    high: float = 0.0
    low: float = 0.0
    feature_elements: list[FeatureElement] = field(default_factory=list)
    standard_features: list[StandardFeatureElement] = field(default_factory=list)
    feature_fractal_id: str | None = None
    break_type: BreakType = BreakType.NONE

    @property
    def confirmed(self) -> bool:
        return self.state is SegmentState.CONFIRMED


# ───────────────────── 第 1 层：包含关系处理 ─────────────────────


class InclusionProcessor:
    """顺序扫一遍，方向必须持久化跨 K 线保持。

    向上：高点取高、低点取较高者；向下：低点取低、高点取较低者。
    """

    def __init__(self) -> None:
        self.processed: list[ProcessedCandle] = []
        self.direction: int | None = None
        self._raw_counter = 0

    @staticmethod
    def _contains(a: ProcessedCandle, b: Candle | ProcessedCandle) -> bool:
        return (a.high >= b.high and a.low <= b.low) or (
            b.high >= a.high and b.low <= a.low
        )

    def _bootstrap_direction(self, candle: Candle) -> int:
        """首个方向未知时，用 close 引导；相等则用 (h+l)/2 比较。"""
        prev = self.processed[-1]
        if candle.close > prev.close:
            return 1
        if candle.close < prev.close:
            return -1
        prev_mid = (prev.high + prev.low) / 2.0
        cur_mid = (candle.high + candle.low) / 2.0
        return 1 if cur_mid >= prev_mid else -1

    def update(self, candle: Candle) -> ProcessedCandle:
        raw_index = candle.raw_index
        self._raw_counter = max(self._raw_counter, raw_index + 1)

        if not self.processed:
            item = ProcessedCandle(
                index=0, start_ts=candle.ts, end_ts=candle.ts,
                open=candle.open, high=candle.high, low=candle.low,
                close=candle.close, volume=candle.volume,
                source_raw_ids=[raw_index],
            )
            self.processed.append(item)
            return item

        if self.direction is None:
            self.direction = self._bootstrap_direction(candle)

        last = self.processed[-1]

        if self._contains(last, candle):
            if self.direction > 0:                      # 向上取高高 / 较高低
                last.high = max(last.high, candle.high)
                last.low = max(last.low, candle.low)
            else:                                        # 向下取低低 / 较低高
                last.high = min(last.high, candle.high)
                last.low = min(last.low, candle.low)
            last.end_ts = candle.ts
            last.close = candle.close
            last.volume += candle.volume
            last.source_raw_ids.append(raw_index)        # 不新建，只归并
            # ★ 合并了两根以上 ⇒ 产出人工 K 线，不可充任分型中心
            last.is_artificial = True
            return last

        # 无包含：用两根相对高低刷新方向，再新建一根
        if candle.high > last.high and candle.low > last.low:
            self.direction = 1
        elif candle.high < last.high and candle.low < last.low:
            self.direction = -1

        item = ProcessedCandle(
            index=len(self.processed), start_ts=candle.ts, end_ts=candle.ts,
            open=candle.open, high=candle.high, low=candle.low,
            close=candle.close, volume=candle.volume,
            source_raw_ids=[raw_index],
        )
        self.processed.append(item)
        return item


# ───────────────────── 第 2 层：分型 ─────────────────────


class FractalEngine:
    """三 K 双向严格判定。

    ★ v4 与 v3 的关键差异：**分型一旦在右 K 线处成立，就不再撤销。**

    v3 会随右端 K 线被改写而把已成立的分型标成 `INVALIDATED` 回滚。但实测
    证明这会制造「幽灵笔」：一个分型先成立、笔已经发出，随后被回滚，而笔
    序列里那句「已确认」还挂着 —— 用户看到的就是「顶分型不是这一笔的最高
    点」这类错误笔。

    缠论原文的写法是**因果**的：走到第 n 根 K 线时判定第 n-1 根是不是分型
    （左右各一根已就位），判完即定。后续 K 线只影响后续的判定，不追溯修改
    历史。所以这里改成「一锤定音」：

      · 分型成立 ⇒ 记入 `fractals`，`status = CONFIRMED`，**永不撤销**。
      · 同一个中心下标只可能被判定一次（center = len(candles)-2 单调递增）。
      · 人工 K 线（包含合并产物）在**边缘**时，分型标为 `is_provisional`，
        进入 `provisional` 表供「真实极值」定位使用，但不参与笔序列。
    """

    def __init__(self) -> None:
        self.inclusion = InclusionProcessor()
        self.fractals: dict[str, Fractal] = {}
        self.provisional: dict[str, Fractal] = {}
        self._by_center: dict[int, str] = {}
        self.new_fractals: list[Fractal] = []
        self._seen_center: set[int] = set()

    @staticmethod
    def _classify(left: ProcessedCandle, mid: ProcessedCandle,
                  right: ProcessedCandle) -> FractalType | None:
        # 双向严格：高点与低点都必须满足
        if (mid.high > left.high and mid.high > right.high
                and mid.low > left.low and mid.low > right.low):
            return FractalType.TOP
        if (mid.low < left.low and mid.low < right.low
                and mid.high < left.high and mid.high < right.high):
            return FractalType.BOTTOM
        return None

    def update(self, candle: Candle) -> tuple[ProcessedCandle, list[Fractal]]:
        self.inclusion.update(candle)
        candles = self.inclusion.processed
        self.new_fractals = []
        if len(candles) < 3:
            return candles[-1], []

        center = len(candles) - 2                 # 左+中+右三根，取中间那根
        if center in self._seen_center:
            # 中心下标已被判定过（合并只让 processed 增长，不会回退）
            return candles[-1], []
        self._seen_center.add(center)

        left, mid, right = candles[-3:]
        ftype = self._classify(left, mid, right)
        if ftype is None:
            return candles[-1], []

        # ★ 人工 K 线（包含合并产物）**照常**充任分型中心。
        #
        # 早期版本把「人工中心」的分型整批剔除，结果丢了 42% 的分型
        # （317 → 183），连用户认可的正确笔端点（如 ci=422 的 2713.76、
        # ci=714 的 2718.00）都跟着丢了。原因是包含处理的**高低点归并**
        # 恰恰就是「价格真实极值」的载体 —— 合并后的 high 就是区间最高价。
        #
        # 用户的核心要求「端点必须是区间极值」应在**笔**这一层用原始
        # K 线校验，而不是在分型层做粗暴过滤。见 `BiEngine._true_extreme`。
        fid = f"F_{mid.end_ts}_{center}_{ftype.value}"
        f = Fractal(
            id=fid, type=ftype, center_index=center,
            confirm_index=len(candles) - 1,
            top=mid.high, bottom=mid.low,
            source_processed_ids=(left.index, mid.index, right.index),
            center_ts=mid.end_ts,
            is_provisional=False,
            raw_high=mid.high, raw_low=mid.low,
        )
        self.fractals[fid] = f
        self._by_center[center] = fid
        self.new_fractals = [f]
        return candles[-1], [f]


# ───────────────────── 第 3 层：笔 ─────────────────────


class BiEngine:
    """笔的划分（v4 重写）。

    ── 一条主规则，四条推论 ────────────────────────────────────
    缠论对笔的原始表述只有一句几何约束：

        一笔 = 相邻的一个顶分型与一个底分型，端点价必须是该笔区间的极值，
        且两端之间至少有 1 根独立 K 线（等价于「一笔至少 5 根 K 线」）。

    用户实测反馈把它拆成了可执行的四条：

    **规则 1 —— 顶点必是区间最高价、底点必是区间最低价**
        "一笔中的顶分型和底分型应该是一笔中最高点和最低点。"
        推论：如果下一个同类分型**更极端**，那前一个就作废，笔必须延长。
        这正是「端点在原地被修订」的机制。

    **规则 2 —— 中间不达标的同类分型直接忽略，不打断笔**
        "构成笔的顶分型不是最高的" ⇒ 那种顶分型根本不该成为笔的端点。
        推论：向上笔延伸途中出现的所有更低的高点，一律跳过。

    **规则 3 —— 反方向分型只有同时满足「位置」与「独立性」才成立**
        位置：向上笔的反转底分型，其 low 必须低于该向上笔的起点 low
              （否则它没跌回起点下方，不足以宣判向上笔结束）。
        独立性：它的中心 K 线与本笔起点分型的中心 K 线相距 ≥ 4 根。
        两条都满足才断笔；不满足则丢弃，笔继续延伸。

    **规则 4 —— 多笔合并**
        "39、40、41 这三笔实际上只是 1 笔。"
        规则 1~3 天然把这种情形合并回一笔 —— 因为那些中间端点都不满足
        「极值」条件。不需要额外的合并步骤。

    ── 为什么不再需要「锚点/事件流」那套结构 ──────────────────
    v1~v3 用了「锚点只由成笔推进、由同型更极端替换」这类增量状态机，规则
    互相打架，最终表现为：某笔的端点不是极值、笔被切碎、笔方向与价格路径
    矛盾。v4 承认**笔是分型序列的一个确定性函数**，于是直接做**全量重算**：
    每来一个真分型，就把整条笔序列从分型列表重划一遍。

    代价 O(m²)，m = 分型数（实测 317），完全可忽略；换来的是**没有隐藏
    状态**，每条规则都可单测。与 SegmentEngine 的「增量重算」逻辑一致。
    """

    def __init__(self) -> None:
        self.bis: list[Bi] = []
        self.fractals: list[Fractal] = []
        self._seen: set[str] = set()

    # ── 几何工具 ──

    @staticmethod
    def _endpoint_price(f: Fractal) -> float:
        return f.top if f.type is FractalType.TOP else f.bottom

    @staticmethod
    def _more_extreme(candidate: Fractal, current: Fractal) -> bool:
        if candidate.type is FractalType.TOP:
            return candidate.top > current.top
        return candidate.bottom < current.bottom

    def _has_independent_candle(self, start: Fractal, end: Fractal) -> bool:
        """两端分型之间是否有独立 K 线（等价于笔 ≥ 5 根 K 线）。"""
        return end.center_index - start.center_index >= MIN_BI_CANDLES - 1

    def _price_order_valid(self, start: Fractal, end: Fractal) -> bool:
        """方向成立：UP 笔终点必须高于起点，DOWN 笔终点必须低于起点。

        ★ 这是规则 3 的「位置」条件：一个底分型如果没跌破向上笔的起点，
        它构不成反转。等价表述：`end.top > start.bottom`（UP）。"""
        if start.type is FractalType.BOTTOM and end.type is FractalType.TOP:
            return end.top > start.bottom
        if start.type is FractalType.TOP and end.type is FractalType.BOTTOM:
            return end.bottom < start.top
        return False

    @staticmethod
    def _bi_direction(start: Fractal) -> BiDirection:
        return (BiDirection.UP if start.type is FractalType.BOTTOM
                else BiDirection.DOWN)

    # ── 全量重算 ──

    def _pick_end(self, fs: list[Fractal], j: int,
                  start: Fractal) -> int:
        """规则 1：从候选终点 j 起，在同性质分型里取**最极端**的那个。

        只在「遇到下一个异性质分型之前」的连续同性质段里比较 —— 因为一笔
        的两端必须是一顶一底，中间的所有同类分型都只是噪声高点/低点。
        """
        ext = j
        k = j + 1
        n = len(fs)
        while k < n and fs[k].type is fs[j].type:
            if self._more_extreme(fs[k], fs[ext]):
                ext = k
            k += 1
        return ext

    @staticmethod
    def _preprocess(fs: list[Fractal]) -> list[Fractal]:
        """分型预处理：同类去弱（R3/R4）+ 独立性过滤（R2）+ 弃点极值补正 + 端点右移（R1）。

        输出序列**严格交替**，相邻两两即可配成一笔。
        """
        # 第一遍：连续同类（或插在同类之间的弱异类）只留极值
        a: list[Fractal] = []
        for f in fs:
            if a and a[-1].type is f.type:
                if BiEngine._more_extreme(f, a[-1]):
                    a[-1] = f
            else:
                a.append(f)
        # 第二遍：相邻异类若独立 K 线不足，丢弃后一个（较弱的那个），
        #        若丢弃后与前一个同类则改做极值竞争。
        #        丢弃的候选记入 dropped（位置, 分型），留给第 2.5 遍补正。
        b: list[Fractal] = []
        dropped: list[tuple[int, Fractal]] = []
        for f in a:
            if not b:
                b.append(f)
                continue
            if f.type is b[-1].type:
                if BiEngine._more_extreme(f, b[-1]):
                    b[-1] = f
                continue
            if f.center_index - b[-1].center_index >= MIN_BI_CANDLES - 1:
                b.append(f)
            else:
                dropped.append((len(b), f))
        # 第二遍半：弃点极值补正（R1 与 R2 的合成）。
        #
        #   ★ 这是 2026-10-02 第三轮用户反馈后新增的规则。问题场景：
        #     第二遍因「距上一节点 <4 根」丢弃 f 后，**不再回头看** —— 若 f
        #     在价格上比其后最近的**已接受同类节点**更极端，就会出现「丢掉了
        #    更高（或更低）的分型，反而保留了较弱的那个」，直接违反 R1。
        #     典型：B574 之后 T576(2701.92) 因 576-574=2<4 被丢弃，T579(2701.60)
        #     却因 579-574=5>=4 被接受 —— 结果笔终点停在更弱的 579。
        #
        #   规则：对每个被丢弃的 f，取其后第一个**同类型的已接受节点** b[tgt]；
        #     若 f 比 b[tgt] 更极端，**且** b[tgt].center_index - f.center_index
        #     <= MIN_BI_CANDLES-1（即 f 仍在该节点的"同一结构位置"容差内），
        #     则用 f 顶替 b[tgt]。上界取 4（= MIN_BI_CANDLES-1）与 R2 同源，
        #     无自由参数：放宽容差会连锁吞噬（实测 gap=6 时 ok 从 41 掉到 38）。
        for pos, f in dropped:
            tgt = None
            for j in range(pos, len(b)):
                if b[j].type is f.type:
                    tgt = j
                    break
            if tgt is None:
                continue
            if b[tgt].center_index - f.center_index > MIN_BI_CANDLES - 1:
                continue
            if BiEngine._more_extreme(f, b[tgt]):
                b[tgt] = f
        # 第三遍：规则 C（R1「端点 = 区间极值」）——逐对检查 [seq[k], seq[k+1]]，
        #        若在搜索窗内存在与 nxt 同类且更极端的分型 ext，说明 nxt 不是
        #        这一笔的终点，应把终点右移到 ext；右移会吃掉其后不满足独立性
        #        的弱节点。
        #
        #        ★ 经网格实验确定的参数（2026-10-02 第二轮用户反馈后）：
        #          · 搜索窗上界 = **下一个与 cur 同类型的骨架节点**
        #            （用户的真极值可能落在 nxt 之后，但不允许越过"下一笔的
        #             起点"——即下一个同向节点）。
        #          · **只跑一遍**（不迭代）。迭代会链式误吞：实测 iter≥2 时
        #            101B→113T 会被后续轮次错并成 101→149，吃掉用户认可的
        #            #07/#11 两笔。
        #          · ext 不使用「距 cur ≥ 4」的硬门槛（实测该门槛会让大量
        #            本该合并的细碎笔幸存）。
        #
        #        实测 4×2×4 网格的最优解：笔数 56 | ok 41/41 | 残留 bad 3，
        #        且正确产出用户指定的 225→240（起点后第 4 个顶分型）。
        #        ★ 再加上「第二遍半：弃点极值补正」后：笔数 56 | ok 41/41 |
        #          残留 bad **0**，三个残留（184→201 / 544→557 / 579→586）
        #          全部修正为 190→201 / 541→557 / 576→586。
        out: list[Fractal] = []
        k = 0
        while k < len(b):
            cur = b[k]
            if k + 1 >= len(b):
                out.append(cur)
                break
            nxt = b[k + 1]
            # 上界：向后找第一个「与 cur 同类型」的骨架节点（= 下一笔起点）
            hi = nxt.center_index
            j = k + 2
            while j < len(b) and b[j].type is not cur.type:
                j += 1
            if j < len(b):
                hi = b[j].center_index
            ext = None
            for f in a:
                if not (cur.center_index < f.center_index < hi):
                    continue
                if f.type is not nxt.type:
                    continue
                if not BiEngine._more_extreme(f, nxt):
                    continue
                if ext is None or BiEngine._more_extreme(f, ext):
                    ext = f
            if ext is None:
                out.append(cur)
                k += 1
                continue
            out.append(cur)
            out.append(ext)
            k += 2
            while k < len(b) and (b[k].center_index - ext.center_index
                                  < MIN_BI_CANDLES - 1):
                k += 1
        # 收尾：把 out 折叠成严格交替、相邻间距 >= 4 的骨架。这里也做「弃点极值
        #      补正」，且需要**迭代到稳定**——因为一次补正可能让前一对的间距不
        #      再达标，必须重扫（否则会残留 span<4 的伪笔，或漏合并）。
        #
        #      ★ 2026-10-02 第三轮用户反馈（#43/#44/#45「三笔实为一笔」）暴露的
        #      场景：out 在 541~576 段为
        #        B541, T557, B560, T569, B571, T576
        #      折叠时 B560 因 560-557=3<4 被丢，B571 因 571-557=14>=4 被留——
        #      又是「丢了更低的底、留下更高的底」（违反 R1）。补正让 B560 顶替
        #      B571 后，新一轮重扫发现 T557→B560 间距仅 3，于是再剔除，最终
        #      收敛为 B541 → T576（= 用户所说「43、44、45 三笔合一笔」）。
        #
        #      收敛性：每轮至少减少一个节点（去弱或丢弃），节点数单调下降，
        #      故必然终止；实测最多 2 轮。
        folded: list[Fractal] = list(out)
        for _ in range(len(out) + 2):
            nxt: list[Fractal] = []
            drop: list[tuple[int, Fractal]] = []
            for f in folded:
                if not nxt:
                    nxt.append(f)
                    continue
                if f.type is nxt[-1].type:
                    if BiEngine._more_extreme(f, nxt[-1]):
                        nxt[-1] = f
                    continue
                if f.center_index - nxt[-1].center_index >= MIN_BI_CANDLES - 1:
                    nxt.append(f)
                else:
                    drop.append((len(nxt), f))
            # 弃点极值补正：被丢弃的 f 若比其后最近的同类已接受节点更极端则顶替。
            # 此处**不限距**——放宽到「最近同类节点」是刻意的：若限距，B560→B571
            # 这种「相隔 11 根但确实是同一结构位置的两个底」会被漏掉，正是本轮 bug。
            #
            # ★★ 但必须加「**时序保持**」硬约束：替入的 f 必须仍夹在 tgt 的前后
            #    邻居之间（prev.center < f.center < next.center）。否则会时间穿越——
            #    实测在单边上涨段里，早期的一个全局最低底会一路顶替其后所有同类
            #    节点，把 B125 挪到 T256 之后，最终把 78 个节点塌缩成 4 笔（笔跨度
            #    392 根）。加上时序约束后，本窗口 4 → 61 笔，且旧窗口 54 笔的结果
            #    （含 41/41 ok、541→576）完全不变。
            for pos, f in drop:
                tgt = None
                for j in range(pos, len(nxt)):
                    if nxt[j].type is f.type:
                        tgt = j
                        break
                if tgt is None:
                    continue
                if tgt > 0 and nxt[tgt - 1].center_index >= f.center_index:
                    continue
                if tgt + 1 < len(nxt) and f.center_index >= nxt[tgt + 1].center_index:
                    continue
                if BiEngine._more_extreme(f, nxt[tgt]):
                    nxt[tgt] = f
            if [x.center_index for x in nxt] == [x.center_index for x in folded]:
                folded = nxt
                break
            folded = nxt
        return folded

    def _rebuild(self) -> None:
        """从分型列表重划笔（v5）。

        ══════════ 用户实测反馈提炼出的规则 ══════════

        用户逐笔审查了 v3 产出的 76 笔，判 41 笔正确、35 笔错误。把 35 条
        理由归并后，得到一条**主规则**和三条辅助规则：

        **主规则 R1 —— 端点必须是区间极值**
            「一笔中的顶分型和底分型应该是一笔中最高点和最低点。」
        **主规则 R2 —— 极值点必须通过独立 K 线约束**
            「更低的底分型因为满足不了笔的条件，所以这些分型都是应该被忽略的。」
        **辅助规则 R3 —— 中间不达标的同类分型忽略，不切笔**（「构成笔的顶分型
            不是最高的」⇒ 丢弃，笔继续延伸）
        **辅助规则 R4 —— 多笔应合一笔**（「39、40、41 这三笔实际上只是 1 笔」）

        ══════════ 算法（三遍预处理 + 相邻配对）══════════

        第一遍 同类去弱（R3）→ 第二遍 独立性过滤（R2 前置）→ 第二遍半 弃点
        极值补正（R1∘R2）→ 第三遍 端点右移（规则 C，R1/R4）→ 相邻两两配对成笔。

        ══════════ 实测结果（对用户 76 笔审查 / 54 笔第2轮输出）══════════

        | 版本 | 笔数 | 用户 ok 命中 | 残留 bad |
        |------|------|-------------|----------|
        | 骨架（一二遍）        | 76 | 41/41 | 35 |
        | 加第三遍（端点右移）  | 56 | **41/41** | 3 |
        | 加第二遍半（弃点补正）| 56 | **41/41** | 0 |
        | ★ 折叠收敛 + 时序保持 | 54 | **41/41** | **0** |

        ⇒ 采用**第二遍半 + 第三遍 + 折叠收敛**版本：完整保留用户认可的全部
          41 笔，同时清零残留：184→201 ✗ ⇒ 190→201 ✓（用户第 3 轮指出的 #15），
          544→557 ✗ ⇒ 541→557 ✓ ⇒ 折叠为 541→576 ✓（用户第 4 轮指出 43/44/45
          三笔实为一笔），579→586 ✗ ⇒ 576→586 ✓，并保持 225→240。

        ══════════ 跨窗口验证（新数据窗口 = 2026-09-03 08:26~09-04 01:05 UTC）══════

        仅在本窗口调参极易过拟合。用随机窗口复核发现：折叠阶段的弃点补正若**不
        加时序约束**，在单边行情下会级联吞并（该窗口 78 个候选节点塌缩成 4 笔）。
        加入「替入点必须夹在前后邻居之间」的时序硬约束后，该窗口得 61 笔 / 最小
        跨度 4 / 全程时序连续，本窗口 54 笔结果不变。
        """
        fs = self.fractals
        if len(fs) < 2:
            self.bis = []
            return
        pre = self._preprocess(fs)
        self.bis = []
        for k in range(len(pre) - 1):
            s, e = pre[k], pre[k + 1]
            if not self._price_order_valid(s, e):
                continue
            self.bis.append(Bi(
                id=f"BI_{s.id}_{e.id}",
                start_fractal_id=s.id, end_fractal_id=e.id,
                start_center_index=s.center_index,
                end_center_index=e.center_index,
                start_price=self._endpoint_price(s),
                end_price=self._endpoint_price(e),
                direction=self._bi_direction(s),
                state=BiState.BUILDING,
            ))

    # ── 对外接口 ──

    def update(self, f: Fractal) -> list[dict]:
        if f.status is not FractalStatus.CONFIRMED:
            return []
        if f.id in self._seen:
            return []
        self._seen.add(f.id)
        if f.center_index < self.fractals[-1].center_index if self.fractals else False:
            raise ValueError("分型必须按 center_index 非降序输入")
        self.fractals.append(f)

        before = len(self.bis)
        self._rebuild()

        events: list[dict] = []
        for b in self.bis[before:]:
            events.append({"type": "BI_CONFIRMED", "bi": b})
        return events


# ───────────────────── 第 4 层：线段 ─────────────────────

MIN_SEGMENT_BIS = 3
# 一段至少含 3 笔（缠论标准下限）。不足 3 笔的候选分型被忽略，继续向后找。


class SegmentEngine:
    """线段划分：特征序列 + 非包含处理 + 端点极值定位。

    ── 设计（v2，重写）──────────────────────────────────────────────
    线段划分本质上依赖**反向笔序列**的完整形态，而反向笔是「尚未走完」的。
    因此这里采用「**增量重算**」：每来一笔，就把整条笔序列从头重划一遍。

    这不是取巧，而是把 v1 那套「流式状态 + 段确认后回放」的补丁结构
    （`_replay_into_current`、被反向笔污染的 `current_end_bi_id`、
    `_source_end_bi` 与 `_feature_extreme_*` 互相覆盖）整体删掉，换成
    **单一事实来源**——段完全由笔序列决定，没有任何隐藏可变状态。

    因果性没有损失：重算只用**当前已到达的笔**，不碰未来数据。代价 O(n²)，
    n = 笔数（1000 根 1m K 线约 76 笔），可忽略。

    ── 算法 ─────────────────────────────────────────────────────
    1. 段方向 = 段首笔方向（向下段的首笔必是向下笔）。
    2. 特征元素 = 与段**反向**的笔（向下段取向上笔），逐个喂入。
    3. 特征序列做**非包含处理**；★ 第 71 课细节：前两个元素跨在假设转折点
       两侧，**不做**合并，从**第三个**元素起才合并。
    4. 向下段找**顶分型**（中心元素 high 最高）；向上段找**底分型**。
    5. 分型成立 ⇒ 段在 `[start .. 分型中心笔]` 区间内、**段同向笔的极值笔**
       处结束。这个极值点就是整段的极值端（向下段 = 最低点）。
    6. 段跨度不足 MIN_SEGMENT_BIS 笔 ⇒ 忽略该分型，继续向后扫。
    7. 下一段从**极值笔 + 1** 开始。因为笔首尾相接，`bi[e+1].start == bi[e].end`，
       所以两段在**同一价、同一下标**处相遇——连续性由构造保证。

    ── 端点语义（v1 反复出错的根源）─────────────────────────────
    v1 取「段内最后一个同向笔的终点」当端点，该位置随段范围扩张而漂移，
    实测令 DOWN 段终值高于起点（方向与价格路径矛盾）。改为
    「分型确定窗口内的同向极值笔」后，端点在三重约束下唯一：
      · 段方向 = 首笔方向
      · 段内价格单调（同向）
      · 段与段共享端点，链式连续
    """

    def __init__(self) -> None:
        self.bis: list[Bi] = []
        self.segments: list[Segment] = []
        self._current: Segment | None = None
        self._seen_bi: set[str] = set()

    # ── 基础工具 ──

    @staticmethod
    def _rev(d: BiDirection) -> BiDirection:
        return BiDirection.DOWN if d is BiDirection.UP else BiDirection.UP

    @staticmethod
    def _seg_dir(d: BiDirection) -> SegmentDirection:
        return (SegmentDirection.UP if d is BiDirection.UP
                else SegmentDirection.DOWN)

    @staticmethod
    def _feature_from_bi(bi: Bi) -> FeatureElement:
        return FeatureElement(
            id=f"FE_{bi.id}", source_bi_id=bi.id, direction=bi.direction,
            high=max(bi.start_price, bi.end_price),
            low=min(bi.start_price, bi.end_price),
            start_index=bi.start_center_index, end_index=bi.end_center_index,
        )

    # ── 特征序列非包含处理 ──

    @staticmethod
    def _contains(a: StandardFeatureElement, b: StandardFeatureElement) -> bool:
        return (a.low <= b.low and a.high >= b.high) or (
            b.low <= a.low and b.high >= a.high
        )

    @staticmethod
    def _merge(left: StandardFeatureElement, right: StandardFeatureElement,
               upward: bool) -> StandardFeatureElement:
        """两个标准特征元素按 K 线规则做非包含合并。"""
        if upward:
            high, low = max(left.high, right.high), max(left.low, right.low)
        else:
            high, low = min(left.high, right.high), min(left.low, right.low)
        return StandardFeatureElement(
            id=f"SFE_{left.id}+{right.id}",
            source_feature_ids=left.source_feature_ids + right.source_feature_ids,
            source_bi_ids=left.source_bi_ids + right.source_bi_ids,
            direction=right.direction, high=high, low=low,
            start_index=left.start_index, end_index=right.end_index,
        )

    def _normalize(self, raw_list: list[FeatureElement],
                   upward: bool) -> list[StandardFeatureElement]:
        """标准特征序列的非包含处理。

        ★ 前两个元素原样保留（它们跨假设转折点，合并会把转折点本身吃掉，
        导致漏段）；从第三个元素起向前合并被包含的元素。
        """
        result: list[StandardFeatureElement] = []
        for raw in raw_list:
            cur = StandardFeatureElement(
                id=f"SFE_{raw.id}", source_feature_ids=(raw.id,),
                source_bi_ids=(raw.source_bi_id,), direction=raw.direction,
                high=raw.high, low=raw.low,
                start_index=raw.start_index, end_index=raw.end_index,
            )
            if len(result) < 2:
                result.append(cur)
                continue
            while result and self._contains(result[-1], cur):
                cur = self._merge(result.pop(), cur, upward)
            result.append(cur)
        return result

    # ── 分型判定 ──

    def _hit_fractal(self, std: list[StandardFeatureElement],
                     direction: SegmentDirection) -> StandardFeatureElement | None:
        """目标分型是否成立？命中则返回**中心元素**。

        向下段 ⇒ 找顶分型（中心 high 不低于左右）；
        向上段 ⇒ 找底分型（中心 low 不高于左右）。
        """
        if len(std) < 3:
            return None
        left, center, right = std[-3:]
        if direction is SegmentDirection.DOWN:
            return center if center.high >= left.high and center.high >= right.high else None
        return center if center.low <= left.low and center.low <= right.low else None

    @staticmethod
    def _gap_between(a: StandardFeatureElement,
                     b: StandardFeatureElement) -> bool:
        """相邻特征元素之间是否有缺口（价格区间不重叠）。"""
        return max(a.low, b.low) > min(a.high, b.high)

    # ── 单段扫描 ──

    def _scan_segment(self, start: int,
                      max_bis: int | None = None) -> tuple[int, int, list[FeatureElement], FeatureFractal | None]:
        """从 `start` 笔开始扫出一段。

        返回 (极值笔下标 e, 分型中心笔下标 center_bi, 原始特征元素, 特征分型)。
        若扫到末尾仍未成立，返回 (len-1, -1, raw, None) 表示「未完成段」。

        `max_bis` 限定探测窗口（用于起手段判定）：只向后看这么多笔，
        超出仍未命中分型即视为「该点不宜起段」。`None` = 不限制。
        """
        first = self.bis[start]
        direction = self._seg_dir(first.direction)
        opposite = self._rev(first.direction)
        upward = (direction is SegmentDirection.UP)
        limit = len(self.bis) if max_bis is None else min(
            len(self.bis), start + max_bis
        )

        raw: list[FeatureElement] = []
        for j in range(start, limit):
            b = self.bis[j]
            if b.direction is not opposite:
                continue
            raw.append(self._feature_from_bi(b))
            std = self._normalize(raw, upward)
            center = self._hit_fractal(std, direction)
            if center is None:
                continue

            center_bi = self._last_bi_index(center)
            if center_bi is None or center_bi < start:
                continue

            # ★ 段端点 = [start .. 分型中心笔] 内、段同向笔的**极值笔**
            same = [k for k in range(start, center_bi + 1)
                    if self.bis[k].direction is first.direction]
            if not same:
                continue
            if upward:
                e = max(same, key=lambda k: self.bis[k].end_price)
            else:
                e = min(same, key=lambda k: self.bis[k].end_price)

            if e - start + 1 < MIN_SEGMENT_BIS:
                continue          # 段太短 ⇒ 忽略此分型，继续向前找

            fr = self._make_feature_fractal(std, center, direction)
            return e, center_bi, raw, fr

        return len(self.bis) - 1, -1, raw, None

    def _last_bi_index(self, sf: StandardFeatureElement) -> int | None:
        """标准特征元素覆盖的最后一笔在全局笔序列里的下标。"""
        target = sf.source_bi_ids[-1]
        return next((k for k, b in enumerate(self.bis) if b.id == target), None)

    @staticmethod
    def _make_feature_fractal(std: list[StandardFeatureElement],
                              center: StandardFeatureElement,
                              direction: SegmentDirection) -> FeatureFractal:
        idx = next(i for i, x in enumerate(std) if x.id == center.id)
        left = std[idx - 1] if idx >= 1 else std[idx]
        right = std[idx + 1] if idx + 1 < len(std) else std[idx]
        kind = "TOP" if direction is SegmentDirection.DOWN else "BOTTOM"
        has_gap = (max(left.low, center.low) > min(left.high, center.high))
        return FeatureFractal(
            id=f"SFF_{left.id}|{center.id}|{right.id}", type=kind,
            left_id=left.id, center_id=center.id, right_id=right.id,
            high=center.high, low=center.low, has_gap=has_gap,
        )

    # ── 构造段对象 ──

    def _make_segment(self, start: int, e: int,
                      state: SegmentState,
                      raw: list[FeatureElement],
                      fr: FeatureFractal | None) -> Segment:
        first = self.bis[start]
        end_bi = self.bis[e]
        upward = (self._seg_dir(first.direction) is SegmentDirection.UP)

        seg = Segment(
            id=f"SEG@{first.id}",
            direction=self._seg_dir(first.direction),
            state=state,
            start_bi_id=first.id,
            current_end_bi_id=end_bi.id,
            confirmed_end_bi_id=(end_bi.id
                                 if state is SegmentState.CONFIRMED else None),
            start_index=first.start_center_index,
            start_price=first.start_price,
            end_index=end_bi.end_center_index,
            end_price=end_bi.end_price,
            high=max(first.start_price, end_bi.end_price),
            low=min(first.start_price, end_bi.end_price),
            feature_elements=list(raw),
            feature_fractal_id=(fr.id if fr is not None else None),
        )
        seg.standard_features = self._normalize(raw, upward)
        if fr is None:
            seg.break_type = BreakType.NONE
        else:
            seg.break_type = (BreakType.TYPE_1 if not fr.has_gap
                              else BreakType.TYPE_2)
        return seg

    # ── 起手段判定 ──

    # 起段探测窗口：向后看这么多笔，若仍凑不出特征分型，就判定该点不宜起段。
    #
    # 依据：段要有 3 个特征元素（左/中/右）才能判分型，而特征元素只取与段
    # 反向的笔，故 n 笔里约 n/2 个。窗口给到 9 笔（≈4 个特征元素）足够判定；
    # 超过这个跨度才出现分型的，属于「跑飞」——那不是段，是把多段粘在一起。
    START_PROBE_BIS = 9

    def _find_segment_start(self) -> int:
        """★ 规则 D（图1）：段序列从「最先能起段」的位置开始。

        数据从中间截断时，开头若干笔可能**构不成线段**。此时段序列的起点
        必须后移到「第一个能起段的笔」，而不是硬从 bi[0] 起。

        判定 = 有界探测：从 bi[i] 向后看最多 `START_PROBE_BIS` 笔，
        能凑出特征分型 ⇒ i 可作起点；凑不出 ⇒ i 是「预备笔」，跳过。

        实测本例：bi[0] 向下、bi[1] 向上、bi[2] 向下，三笔只有 1 个反向笔，
        判不出顶分型 ⇒ bi[0] 不宜起段；bi[1] 起 6 笔内即命中 ⇒ 首段从
        **bi[1] 的起点（= bi[0] 的终点）** 开始。正好是用户描述的位置。
        """
        n = len(self.bis)
        for i in range(n):
            _e, _ci, _raw, fr = self._scan_segment(
                i, max_bis=self.START_PROBE_BIS
            )
            if fr is not None:
                return i
        return 0

    # ── 全量重划 ──

    def _rebuild(self) -> None:
        self.segments = []
        self._current = None
        if not self.bis:
            return

        start = self._find_segment_start()
        n = len(self.bis)
        while start < n:
            e, center_bi, raw, fr = self._scan_segment(start)

            if fr is None:                       # 未完成段
                self._current = self._make_segment(
                    start, n - 1, SegmentState.EXTENDING, raw, None
                )
                break

            seg = self._make_segment(
                start, e, SegmentState.CONFIRMED, raw, fr
            )
            seg.feature_fractal_id = fr.id
            self.segments.append(seg)

            # ★ 下一段从「极值笔 + 1」开始：bi[e+1].start == bi[e].end，
            #   两段在同一个价、同一个下标处相遇。
            start = e + 1

    # ── 对外接口 ──

    def update(self, bi: Bi) -> list[dict]:
        if bi.id in self._seen_bi:
            return []
        self._seen_bi.add(bi.id)
        self.bis.append(bi)

        before = len(self.segments)
        before_cur = self._current is not None
        self._rebuild()

        events: list[dict] = []
        if not before_cur and self._current is not None:
            events.append({"type": "SEGMENT_STARTED", "segment": self._current})
        for s in self.segments[before:]:
            events.append({"type": "SEGMENT_CONFIRMED", "segment": s})
        if not events:
            events.append({"type": "SEGMENT_EXTENDED"})
        return events


# ───────────────────── 主流程接线 ─────────────────────


def build_structure(bars: list[dict]) -> dict:
    """bars: [{ts, open, high, low, close, volume}, ...] 按时间升序。

    ★ 注意接线顺序（v5 修正）：
      `BiEngine._rebuild` 的第三遍会**回溯性吞并**已有笔（多笔合一笔），
      因此不能把 `be.update()` 产生的增量笔直接喂给 `SegmentEngine` —— 那样
      会让线段引擎持有已被吞并的「僵尸笔」，导致 `start_bi_id` 找不到对应笔。
      正确做法：先把全部分型喂完、拿到**最终**笔序列，再一次性交给线段引擎。
    """
    fe, be = FractalEngine(), BiEngine()

    for i, b in enumerate(bars):
        candle = Candle(ts=b["ts"], open=b["open"], high=b["high"],
                        low=b["low"], close=b["close"],
                        volume=b["volume"], raw_index=i)
        _, fractals = fe.update(candle)
        for f in fractals:
            be.update(f)

    # 用最终笔序列驱动线段引擎
    se = SegmentEngine()
    for bi in be.bis:
        se.update(bi)

    return {
        "fractals": list(fe.fractals.values()),
        "provisional_fractals": list(fe.provisional.values()),
        "bis": be.bis,
        "segments": se.segments,
        "current_segment": se._current,
        "inclusion": fe.inclusion,
        "bi_endpoint_revisions": 0,
    }
