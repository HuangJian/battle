"""remote/deliver_mode.py — 补传**档位机**（`plan/offline-deliver-full-and-gaps` §3.0，2026-10-23）。

## 为什么有这个文件

用户口径：**「网络顺畅就每一轮都回传；抖动严重就自动只发评估轮（整份 w/opt + 漏轮统计）；
网络恢复后继续每一轮都回传」」。 而原实现 `offline_deliver._sync:1038` 无条件只取
`pending_anchors()` —— 那是**降级档**的选法被当成了常态。

档位必须是**观测结论**而不是旋钮：每一拍拿「实测吞吐 vs 本档每轮字节」判能不能跟上。
本模块只有纯函数（无 IO、无时钟、无随机），时钟与记账由调用方提供 —— 便于单测把三档
与断连判据逐条钉住。

## 三档

| 档 | 每轮线上字节 | 需要（15 s/轮） | 什么时候用 |
|---|---|---|---|
| `full` | 896 072（整份 w + 整份 opt） | 59.7 KB/s | 正常档（用户口径 >500 KB/s） |
| `bulk` | 276 852（delta w + delta opt + row） | 18.5 KB/s | 晚高峰（用户口径 <50 KB/s） |
| `anchors` | ~180 000（每 5 轮整份 + 每轮 metrics） | 12 KB/s | 断连 / 更慢 |

## 断连是**独立一维**

带宽判据在断连时失效：一件 896 KB 的合体包在 80% 处断掉，带宽再高也要重来 896 KB。
所以 `disconnects ≥ DISCONNECT_MAX` 时无条件 `anchors`（且调用方分件发送）。

## 积压不参与判据（用户 2026-10-23 拍板：不设上限）

积压只做读数（`backlog` / `eta_sec`），不触发降级——用户要的是「恢复后按序把欠的轮全补上」。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

#: 每档每轮的**线上**字节（gz+base64 之后；2026-10-22 按真 `encode_*` 函数实测）。
BYTES_PER_ROUND: Mapping[str, int] = {
    "full": 896_072,
    "bulk": 276_852,
    "anchors": 180_000,
}

#: 由「最完整」到「最精简」；`choose_mode` 取可用档里最靠前的那个。
MODE_ORDER: tuple[str, ...] = ("full", "bulk", "anchors")

#: 升档/降档的余量系数（4/3）：实测吞吐要能覆盖本档需求的 1.33 倍才考虑待在这一档。
SAFETY: float = 4.0 / 3.0

#: 各档的下限（`BYTES_PER_ROUND / 15 s × SAFETY` = 79 650.8 / 24 653.1，**上取整到整 KB/s**）。
#: ⚠ 判档用的是 `need_bps × SAFETY`（随轮周期变），这两个常量只做**读数与 D9 的绝对数字验收**
#: ——它们比动态判据略严（差几百 B/s），所以用它们验收不会得出「常量说行、动态说不行」。
FULL_MIN_BPS: int = 80_000
BULK_MIN_BPS: int = 25_000

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
    """下一拍该用哪个档（`full` > `bulk` > `anchors`，取能跟上的最完整档）。

    * `disconnects >= DISCONNECT_MAX` ⇒ **无条件 `anchors`**（与带宽无关，见模块 docstring）；
    * `avail_bps` 为 `None`（冷启动：一次成功投递都还没有）⇒ **`bulk`**：
      先用 277 KB 探一次路把吞吐测出来，下一拍再决定升不升 `full`。直接上 `full` 的话
      首次就要赌 896 KB，慢链路必然先付一次超时。
    * `avail_bps <= 0`（测出来是零/负数）⇒ `anchors`。
    """
    if int(disconnects or 0) >= DISCONNECT_MAX:
        return "anchors"
    if avail_bps is None:
        return "bulk"
    avail = float(avail_bps)
    if avail <= 0:
        return "anchors"
    # 阈值是**动态的**（`need_bps` 随轮周期变）：一条 60 s/轮的慢腿不该被「15 s/轮」的绝对阈值
    # 钉死。`FULL_MIN_BPS` / `BULK_MIN_BPS` 只是**缺省轮周期（15 s）下的等价绝对阈值**——
    # 写在这里是为了读数与单测能对得上（D9 直接拿 500/50/5 KB/s 三档验收）。
    for mode in ("full", "bulk"):
        want = need_bps(mode, round_period_sec=round_period_sec) * SAFETY
        if avail >= want:
            return mode
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
    """档位机的**状态**（两个滑窗 + 当前档 + 滞回计数）。

    为什么单独一个类：`offline_deliver` 的 LOC 预算是 1000 行，而「滑窗怎么维护、滞回怎么算」
    是纯状态机，跟「怎么 POST」没有关系。零 IO（时钟与日志由调用方注入）。
    """

    #: 吞吐滑窗：最近几次成功投递的 Σbytes/Σ秒。
    WINDOW = 8

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
        self.mode = "anchors"
        self._deliveries: list[tuple[float, int]] = []
        self._fails: list[float] = []
        self._want = ""
        self._want_n = 0

    # ---- 记账 ----
    def note_delivery(self, nbytes: int, sec: float) -> None:
        self._deliveries.append((max(1e-6, float(sec)), max(0, int(nbytes))))
        if len(self._deliveries) > self.WINDOW:
            del self._deliveries[: len(self._deliveries) - self.WINDOW]

    def note_disconnect(self) -> None:
        now = self._now()
        self._fails.append(now)
        cutoff = now - DISCONNECT_WINDOW_SEC
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
        return len(self._fails)

    # ---- 每拍一次 ----
    def tick_mode(self) -> str:
        if not self.adaptive:
            return "anchors"
        want = choose_mode(self.avail_bps, disconnects=self.disconnects)
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
                f"｜断连 {self.disconnects}/{int(DISCONNECT_WINDOW_SEC)}s）"
            )
        return self.mode
