"""remote/deliver_mode.py — 补传**档位机**（`plan/offline-deliver-full-and-gaps` §3.0，2026-10-24 修订）。

## 为什么只有两档

用户口径：**「网络顺畅就每一轮都回传；抖动严重就自动只发评估轮（整份 w/opt + 漏轮统计）；
网络恢复后继续每一轮都回传」**。

但「每一轮发什么」在 **2026-10-23** 已由 `plan/anchor-full-rounds` 定在**写盘时**：
锚点轮（首轮 ∪ `it % eval_every == 0`）= 整份 weight + 整份 opt；其余轮 = delta。
所以投递侧不再有「full（每轮整份）/ bulk（每轮 delta）」两档可选 —— 那两档发的是同一批
字节，只留一个标签没有意义。真正能选的是：

| 档 | 每轮线上字节 | 需要（15 s/轮） | 什么时候用 |
|---|---|---|---|
| `every` | ~405 KB（1/5 锚点 896 KB + 4/5 非锚点 277 KB，含 row） | ~27 KB/s | 网络能跟上产生速率 |
| `anchors` | ~203 KB（每 5 轮一次整份 + **每轮**统计 ~24 KB） | ~13.5 KB/s | 抖动 / 断连 / 更慢 |

（`SAFETY = 4/3` ⇒ 绝对阈值 36 KB/s / 19 KB/s，见 `EVERY_MIN_BPS` / `ANCHORS_MIN_BPS`。）

⚠ **不要**把 `full` 档理解成「每一轮都整份」：那需要把非锚点轮的 delta 在投递时重新展开，
会让 `anchor-full-rounds` 的 ~36% 省量当场归零（`deliver_parts.full_parts_for` 因此是死代码，
两个 docstring 都钉死不接线）。

## 断连是**独立一维**

带宽判据在断连时失效：一件 896 KB 的合体包在 80% 处断掉，带宽再高也要重来 896 KB。
所以 `disconnects >= DISCONNECT_MAX` 时无条件 `anchors`（且调用方分件发送）。

## 积压不参与判据（用户 2026-10-23 拍板：不设上限）

积压只做读数（`backlog` / `eta_sec`），不触发降级——用户要的是「恢复后按序把欠的轮全补上」。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from statistics import median

#: 每档每轮的**线上**字节（gz+base64 之后）。
#: `every` = 5 轮窗口（1 锚点整份 896 072 + 4 非锚点 delta 276 852）/ 5 ≈ 400 604，
#: 再加 `anchor-full-rounds` S6 的逐局明细（~4.3 KB/轮）⇒ 405 KB。
#: `anchors` = 1/5 锚点整份（~179 KB/轮均）+ 每轮统计批（`perGame` 保留，~24 KB/轮）。
BYTES_PER_ROUND: Mapping[str, int] = {
    "every": 405_000,
    "anchors": 203_000,
}

#: 由「最完整」到「最精简」；`choose_mode` 取可用档里最靠前的那个。
MODE_ORDER: tuple[str, ...] = ("every", "anchors")

#: 升/降档的余量系数（4/3）：实测吞吐要能覆盖本档需求的 1.33 倍才待在这一档。
SAFETY: float = 4.0 / 3.0

#: 各档的下限（`BYTES_PER_ROUND / 15 s × SAFETY`，**上取整到整 KB/s**）：
#: `every` = 405 000 / 15 × 4/3 = 36 000；`anchors` = 203 000 / 15 × 4/3 ≈ 18 044 ⇒ 19 000。
#: ⚠ 判档用的是 `need_bps × SAFETY`（随轮周期变），这两个常量只做**读数与 D9 的绝对数字验收**。
EVERY_MIN_BPS: int = 36_000
ANCHORS_MIN_BPS: int = 19_000

#: 断连判据：最近 `DISCONNECT_WINDOW_SEC` 内「建连失败 / 传输中断 / 超时」的次数。
#: ⚠ **内容错（409/413/422）不算断连** —— 那是数据问题，不是链路。
DISCONNECT_WINDOW_SEC: float = 60.0
DISCONNECT_MAX: int = 3

#: 切档的滞回：连续这么多拍都想要同一个新档才真的切（防抖动期反复横跳）。
SWITCH_CONFIRM: int = 3

#: 一轮的产生周期缺省（rollout 5 s + PPO 9 s + 落盘）；只用于拿不到 `row.wall_sec` 的冷启动。
DEFAULT_ROUND_PERIOD_SEC: float = 15.0


def need_bps(mode: str, *, round_period_sec: float) -> float:
    """这一档在这条腿上需要的**持续**吞吐（bytes/s）。

    `round_period_sec <= 0` ⇒ 退回 `DEFAULT_ROUND_PERIOD_SEC`（不除零、不返回 inf）。
    未知 mode ⇒ 按 `anchors` 算（最保守的已知档）。
    """
    period = float(round_period_sec or 0.0)
    if period <= 0:
        period = DEFAULT_ROUND_PERIOD_SEC
    return float(BYTES_PER_ROUND.get(str(mode), BYTES_PER_ROUND["anchors"])) / period


def choose_mode(
    avail_bps: float | None,
    *,
    disconnects: int = 0,
    round_period_sec: float = DEFAULT_ROUND_PERIOD_SEC,
) -> str:
    """下一拍该用哪个档（`every` 能跟上就 `every`，否则 `anchors`）。

    * `disconnects >= DISCONNECT_MAX` ⇒ **无条件 `anchors`**（与带宽无关）；
    * `avail_bps` 为 `None`（冷启动：一次成功投递都还没有）⇒ **`every`**：
      初始档本来就是 `every`（用户口径：顺畅就每轮发）；慢链路会在头几拍以失败/断连
      自然降档，不需要一个「先赌 277 KB」的中间档（那个档已经随 `bulk` 一起退场）。
    * `avail_bps <= 0`（测出来是零/负数）⇒ `anchors`。
    """
    if int(disconnects or 0) >= DISCONNECT_MAX:
        return "anchors"
    if avail_bps is None:
        return "every"
    avail = float(avail_bps)
    if avail <= 0:
        return "anchors"
    if avail >= need_bps("every", round_period_sec=round_period_sec) * SAFETY:
        return "every"
    return "anchors"


def eta_sec(backlog: int, *, mode: str, avail_bps: float | None) -> float | None:
    """按当前档位把积压发完还要多少秒；`avail_bps <= 0/None` ⇒ `None`（没法估）。

    `>3600` 的调用方应显示成「>1h」（避免假精确，见 plan §7.1）。
    """
    n = max(0, int(backlog or 0))
    if n == 0:
        return 0.0
    avail = float(avail_bps or 0.0)
    if avail <= 0:
        return None
    return n * float(BYTES_PER_ROUND.get(str(mode), BYTES_PER_ROUND["anchors"])) / avail


def eta_text(backlog: int, *, mode: str, avail_bps: float | None) -> str:
    """日志用的 ETA 文本（`-` = 估不出；`>1h` = 超过一小时）。"""
    sec = eta_sec(backlog, mode=mode, avail_bps=avail_bps)
    if sec is None:
        return "-"
    if sec > 3600:
        return ">1h"
    return f"{sec:.0f}s"


class ModeTracker:
    """档位机的**状态**（吞吐滑窗 + 断连滑窗 + 轮周期 + 当前档 + 滞回计数）。

    为什么单独一个类：`offline_deliver` 的 LOC 预算是 1000 行，而「滑窗怎么维护、滞回怎么算」
    是纯状态机，跟「怎么 POST」没有关系。零 IO（时钟与日志由调用方注入）。
    """

    #: 吞吐滑窗：最近几次成功投递的 Σbytes/Σ秒。
    WINDOW = 8
    #: 轮周期滑窗：最近几轮 `row.wall_sec` 的中位数（plan §7.1）。
    PERIOD_WINDOW = 5

    def __init__(
        self,
        *,
        adaptive: bool,
        now_fn,
        log: Callable[[str], None] | None = None,
    ) -> None:
        self.adaptive = bool(adaptive)
        self._now = now_fn
        self._log = log
        #: 自适应开启就从 `every` 起跑（用户口径：顺畅即每轮）；关掉 = 旧行为 `anchors`。
        self.mode = "every" if self.adaptive else "anchors"
        self._deliveries: list[tuple[float, int]] = []
        self._fails: list[float] = []
        self._periods: list[float] = []
        self._want = ""
        self._want_n = 0

    # ---- 记账 ----
    def note_delivery(self, nbytes: int, sec: float) -> None:
        self._deliveries.append((max(1e-6, float(sec)), max(0, int(nbytes))))
        if len(self._deliveries) > self.WINDOW:
            del self._deliveries[: len(self._deliveries) - self.WINDOW]

    def note_disconnect(self) -> None:
        now = self._now()
        self._fails.append(float(now))
        self._prune_fails(now)

    def note_round_period(self, sec: object) -> None:
        """记一轮的墙钟长度（`row.wall_sec`）；非法值忽略（不猜、不抛）。"""
        if isinstance(sec, bool) or not isinstance(sec, (int, float, str)):
            return
        try:
            v = float(sec)
        except (TypeError, ValueError):
            return
        if v <= 0:
            return
        self._periods.append(v)
        if len(self._periods) > self.PERIOD_WINDOW:
            del self._periods[: len(self._periods) - self.PERIOD_WINDOW]

    def _prune_fails(self, now: float) -> None:
        cutoff = float(now) - DISCONNECT_WINDOW_SEC
        self._fails = [t for t in self._fails if t >= cutoff]

    # ---- 读数 ----
    @property
    def avail_bps(self) -> float | None:
        if not self._deliveries:
            return None
        sec = sum(s for s, _b in self._deliveries)
        return (sum(b for _s, b in self._deliveries) / sec) if sec > 0 else None

    @property
    def disconnects(self) -> int:
        # ★ 2026-10-24 评审 P0：窗口必须在这里按当前时间裁剪。只在 `note_disconnect()` 里裁剪
        #   会让「3 次断连后网络恢复」永久停在 anchors —— 没有新失败就永远没有下一次裁剪。
        self._prune_fails(self._now())
        return len(self._fails)

    @property
    def round_period_sec(self) -> float:
        """最近 5 轮 `wall_sec` 的中位数；没有/全非法 ⇒ 缺省 15 s。"""
        return float(median(self._periods)) if self._periods else DEFAULT_ROUND_PERIOD_SEC

    # ---- 每拍一次 ----
    def tick_mode(self) -> str:
        if not self.adaptive:
            return "anchors"
        want = choose_mode(
            self.avail_bps,
            disconnects=self.disconnects,
            round_period_sec=self.round_period_sec,
        )
        if want == self.mode:
            self._want, self._want_n = "", 0
            return self.mode
        if want != self._want:
            self._want, self._want_n = want, 1
        else:
            self._want_n += 1
        if self._want_n < SWITCH_CONFIRM:
            return self.mode
        old, self.mode = self.mode, want
        self._want, self._want_n = "", 0
        if self._log:
            avail = self.avail_bps
            self._log(
                f"补传档位切换：{old} → {self.mode}"
                f"（avail={'-' if avail is None else f'{avail / 1024:.0f}K'}B/s"
                f"｜周期 {self.round_period_sec:.0f}s"
                f"｜断连 {self.disconnects}/{int(DISCONNECT_WINDOW_SEC)}s）"
            )
        return self.mode
