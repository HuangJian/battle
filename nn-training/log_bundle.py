"""log_bundle.py — 把「同一件事的碎日志」攒成**一行**（2026-09-24，云端日志节食）。

## 为什么需要它

Kaggle / Colab 上的一次离线整段训练要跑几小时，控制台的日志面板是**流式**的：每多一行就
多一个 DOM 节点，几小时下来把浏览器拖死（用户 2026-09-24 报障：「log 刷屏几小时后把浏览器
卡死」）。而刷屏的那几族行有个共同点 —— **没有一行是「必须立刻知道」的**：

  * 启动事实（编译缓存开没开、设备是不是 TPU、opt/demo 从哪来、payload/code/ts_code 命中没
    命中、prune 删了几个目录、本轮上云几局、shard 装载进度…）⇒ 全是同一阶段的读数集合；
  * XLA 步耗诊断（默认开！192 步 ≈ 23 行/轮）；
  * epoch 收尾行（每 epoch 一行）+ PPO 完成行；
  * rollout 的进度行（已按 60s 节流）与设置/看门狗/池/收尾行。

阶段结束（或每 60s 心跳）时一次说完，**信息量不变、行数降一到两个数量级**。

## 契约

* `add(key, value)`：按 key **就地替换**（同一件事的后续读数覆盖前一个，位置不变）——所以
  「装载 128/336 → 256/336 → 336/336」最终只剩最后那个值。
* `final_only=True`：只在 `emit`（阶段完成）里出现，**心跳不带**（如「单局耗时分布」在跑完
  之前根本不存在）。
* `emit(head)`：打一行 `head｜k=v｜k=v` 并**清空**、标记完成（之后再 `beat` 不会打）。
  没有任何内容时**不打空行**（返回 False）。
* `beat(head, now=…)`：未完成时的心跳，按 `every`（缺省 60s）节流；**不清空**（这样完成那一行
  仍然带着启动事实）。判据只看墙钟间隔。
* 时钟可注入（`clock=`）：心跳节流因此是**纯函数式**可测的，不需要 sleep —— 本仓
  `tests/`/`e2e/` 有静态守卫禁止拿 sleep 当同步（`docs/nn/engineering.md §21`）。

线程安全：rollout 那条腿的结果是在 `as_completed` 主循环里拿的、装载进度也在同一个线程；
但日志也可能被池里的线程碰到，故内部加一把小锁（每条日志一次，与「起一个进程跑一局」
相比可以忽略）。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

#: 心跳缺省间隔（秒）——与 `ppo/engine.py::HB_SEC`、`remote/game_watch.py::PROGRESS_LOG_SEC`
#: 同一个口径（用户 2026-09-23 定的「每分钟一句就够」）。
BEAT_SEC = 60.0
#: 段分隔符（全角竖线：`k=v` 自身不会用到它）。
SEP = "｜"


class LogBundle:
    """攒一阶段的行 + 在阶段边界/心跳打一行（契约见模块 docstring）。"""

    def __init__(
        self,
        log: Callable[[str], None] | None = None,
        *,
        sep: str = SEP,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._log = log
        self._sep = sep
        self._clock = clock if clock is not None else time.time
        #: `[key, value, final_only]` —— 保序 + 按 key 就地替换。
        self._parts: list[list[object]] = []
        self._index: dict[str, int] = {}
        #: `[text, final_only]` —— 无键自由文本（按内容去重、保序）。
        self._notes: list[list[object]] = []
        self._lock = threading.Lock()
        self._done = False
        self._last_beat = self._clock()

    # ────────────────────────── 攒 ──────────────────────────

    def bind(self, log: Callable[[str], None]) -> LogBundle:
        """接上日志出口（外部先建对象、拿到 jid/log 之后再绑时用）。"""
        self._log = log
        return self

    def add(self, key: str, value: object, *, final_only: bool = False) -> None:
        """记一条 `key=value`；同 key 再记 = **就地替换**（位置不变）。"""
        text = "" if value is None else str(value)
        with self._lock:
            i = self._index.get(key)
            if i is None:
                self._index[key] = len(self._parts)
                self._parts.append([key, text, final_only])
            else:
                self._parts[i][1] = text
                # 一旦标过 final_only 就不再降级（同一件事的后一次读数是「更完整」的那个）
                self._parts[i][2] = bool(self._parts[i][2]) or final_only

    def note(self, text: str, *, final_only: bool = False) -> None:
        """无键自由文本（如看门狗口径）；同内容只留一份，保序。"""
        if not text:
            return
        with self._lock:
            for row in self._notes:
                if row[0] == text:
                    row[1] = bool(row[1]) or final_only
                    return
            self._notes.append([text, final_only])

    def pending(self) -> bool:
        """还有没有攒着没打的东西。"""
        with self._lock:
            return bool(self._parts or self._notes)

    def text(self, *, include_final: bool = True) -> str:
        """当前攒下的部分拼成一行（不含 head）；`include_final=False` = 心跳视图。"""
        with self._lock:
            return self._join(include_final=include_final)

    # ────────────────────────── 打 ──────────────────────────

    def emit(self, head: str = "") -> bool:
        """阶段完成：一行打完并清空。没有内容时**不打**（返回 False）。"""
        with self._lock:
            if self._done or not (self._parts or self._notes):
                return False
            body = self._join(include_final=True)
            self._parts = []
            self._index = {}
            self._notes = []
            self._done = True
        self._write(head, body)
        return True

    def beat(self, head: str = "", *, every: float = BEAT_SEC, now: float | None = None) -> bool:
        """未完成时的心跳（按 `every` 节流；只打非 `final_only` 的部分，**不清空**）。

        调用方在**真实事件**（每局结算、每 chunk 收尾）里调它，不是定时器 —— 所以既没有
        「睡等」，也不会因为一轮跑得快而漏掉收尾（收尾恒走 `emit`）。
        """
        t = self._clock() if now is None else float(now)
        with self._lock:
            if self._done or (t - self._last_beat) < float(every):
                return False
            body = self._join(include_final=False)
            if not body:
                return False
            self._last_beat = t
        self._write(head, body)
        return True

    # ────────────────────────── 内部 ──────────────────────────

    def _join(self, *, include_final: bool) -> str:
        """调用方持锁（`_parts`/`_notes` 的读契约）。"""
        chunks = [
            f"{row[0]}={row[1]}" for row in self._parts if include_final or not row[2]
        ]
        chunks += [str(row[0]) for row in self._notes if include_final or not row[1]]
        return self._sep.join(chunks)

    def _write(self, head: str, body: str) -> None:
        line = f"{head}{self._sep}{body}" if head and body else (head or body)
        if self._log is not None:
            self._log(line)
