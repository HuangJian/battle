"""loop_eval —— **in-loop 评估链** mixin（2026-09-24 从 rl/loop_steps.py 拆出，S4 第十七刀）。

这一簇是 `TrainingSteps` 里**唯一一条真正的方法间调用链**，其余方法都是被轮内步骤各自调用的
叶子。链的形状：**派发（延迟到下一轮 rollout 收官）→ 尾巴收拢 → PPO 收官时 join / 交棒 →
收官 drain**：

```
_eval_policy_cfg ◄── _eval_join_soft_sec ◄── _sweep_eval_tail ◄── _dispatch_delayed_eval
                                                 ▲                            ▲
_join_eval ◄─────────────────────────────────────┘                            │
_eval_covered ◄── _drain_pending_eval ────────────────────────────────────────┘
```

## 依赖方向：调用者依赖被调用者

`rl/loop_steps.py` 里是 `class TrainingSteps(TrainingRemote, TrainingEval)` —— 本簇是**被调用
者**（轮内 `_dispatch_delayed_eval` / `_join_eval` 由 `RoundSteps` 调，收官
`_drain_pending_eval` 由 `TrainingLoop` 调），所以它是基类。**追加**在既有基类之后：两个混入
之间零重名、零互调、零 `super()` ⇒ 顺序今天完全惰性，没有理由去动已经写在文档与守卫里的
`TrainingRemote` 位置（见 tests/test_loop_transport_split.py 的 MRO 断言）。

## 状态归属：五个 eval 槽位随簇搬来（声明只有这一处）

`_eval_thread` / `_eval_gate` / `_eval_tail` / `_eval_tail_start` / `_eval_join_sec`。

它们在旧类里也只被这一簇读写，例外有两条、都**经继承**（不是重复声明）：`_log_report` 把
stream 报告里的 eval 线程句柄 pop 进 `_eval_thread`（R4：jsonl 写回前 join），
`_record_iteration` 读 `_eval_join_sec` 落账。

## `_eval_on_round` 的占位也随簇走

占位 body 用 `raise` 而不用 `...`：真实现在 `TrainingLoop` 本体（`rl/loop_core.py`），MRO 胜过
此处；万一 MRO 被改坏要**响亮失败**，而不是静默返回 falsy 把 eval 全关掉。把它放在**消费它的
模块**里比留在 `loop_steps` 更贴职责——读这一簇的人一眼看到契约。

## DI seam：本模块**没有**新的 patch 点

本簇对外的依赖全是**方法体内的延迟 import**（`rl.eval_dispatch` / `rl.eval_local` /
`rl.queue` / `rl.archive`），测试也一直 patch 那些**实现模块**（`monkeypatch.setattr(ed,
"dispatch_eval_bg", ...)`）⇒ 方法搬家不改变任何注入点。反过来，对 `rl.loop_steps.*` 的那些
注入**本来就是空操作**（这些名字从来没住在那儿）。唯一按路径读源码的守卫在
`tests/test_eval_a_once.py`，已随本刀重定向到本文件。
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any

from rl.eval_m1 import read_eval_summary
from rl.log import log


class TrainingEval:
    """in-loop 评估链 mixin：派发 → 尾巴收拢 → PPO 收官 join/交棒 → 收官 drain。

    被 `TrainingSteps` 继承（调用者依赖被调用者）；只有组合类 `TrainingLoop` 会被实例化。
    """

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）。
    # 与 rl/loop_steps.py 的同类声明**有意并存**：混入的状态契约必须在**每个**文件里对
    # mypy 可见（否则本文件里的 `self._report` 会被判成未声明属性），而运行期的唯一真相
    # 是那个被实例化的组合类。`_last_dist_cfg` / `_node_rollout` / `_stream_meta` 在本簇里
    # 走 `getattr` 缺省读（mypy 不看它们），声明是为了读代码的人。
    args: Any
    #: bun 可执行文件路径（TrainingLoop 持有；延迟 eval 派发传给评估子进程）。
    bun: str
    _traj_dir: Any
    _jsonl_path: Any
    _report: dict
    _last_dist_cfg: Any
    #: 本轮是否在节点采集（决定本机 eval 份额的放行档）。
    _node_rollout: bool
    #: 本轮流式采集的元信息（非 None ⇒ stream 轮，走另一条派发流）。
    _stream_meta: dict | None

    # ---- 本簇自己的状态（S4 第十七刀随簇搬来；旧类里也只被这一簇读写）--------------
    _eval_thread: threading.Thread | None
    _eval_gate: threading.Event | None
    #: 本轮收官时仍未结束的 eval 尾巴 `(thread, 派发时刻)`——交给下一轮 rollout 收官
    #: 这个自然边界收拢（`_sweep_eval_tail`，不站等）。None = 无尾巴。
    #: 给类级默认值（而不只声明类型）：裸构造的实例（单测脚手架）没有 __init__ 赋值。
    _eval_tail: tuple[Any, float] | None = None
    #: 本轮 eval 的派发时刻（尾巴的时间基准；None = 本轮未派发评估）。
    _eval_tail_start: float | None = None
    #: 本轮主链为 eval 站在外面等的秒数（缺省 0 = 不站等）；类级默认同上。
    _eval_join_sec: float = 0.0

    #: 本轮是否评估轮——真实现在 TrainingLoop 本体（loop_core.py），MRO 胜过
    #: 此处占位。body 用 raise 而不用 `...`：万一 MRO 被改坏，响亮失败而不是
    #: 静默返回 falsy 把 eval 全关掉。

    def _eval_on_round(self, it: int) -> bool:
        raise NotImplementedError(
            "TrainingEval._eval_on_round 被直接调用——MRO 破坏（真实现在 TrainingLoop）"
        )

    # ------------------------------------------------- in-loop eval 墙钟（2026-09-17）

    def _eval_policy_cfg(self) -> dict:
        """rl-config 的 policy 块（每轮热读；见 loop_core 的 `_last_dist_cfg`）。"""
        return (getattr(self, "_last_dist_cfg", None) or {}).get("policy") or {}

    def _eval_join_soft_sec(self) -> float:
        """PPO 收官后的软等上限（policy.evalJoinSoftSec；默认 30s，0 = 完全不站等）。

        2026-09-17 用户指令：先前的硬编码 180s 把 eval 尾巴整段暴露在 PPO 之后
        （本机份额又只在 `_join_eval` 才放行 ⇒ 叠加成 PPO 后的第二次串行等待）。
        """
        from rl.eval_local import eval_join_soft_sec

        return eval_join_soft_sec(self._eval_policy_cfg())

    # ★ 2026-09-21（§3 单一 PPO 路径）：这里原先还有 `_regate_local_eval()`（本机 PPO 接手 ⇒
    # 收回提前放行）与 `_local_gate_epoch_hook()`（末 early 个 epoch 放行本机份额）——两者
    # 都是「本机自己跑 PPO，所以本机核心要留给它」那套 R6 语义。PPO 恒在 worker 上跑之后
    # 本机没有 PPO 窗口可让，两个方法**零调用点**，一并删除（gate 与 `_join_eval` 收官放行
    # 保留：本机 eval 份额与**本机 rollout** 仍共用核心）。
    def _sweep_eval_tail(self) -> None:
        """上一轮 eval 尾巴的**自然收拢点**：下一轮 rollout 收官时（2026-09-17 用户指令）。

        为什么不是固定秒数：软等要么白站（尾巴早落地）要么丢（尾巴更晚），两个方向都
        不对。尾巴在下一轮整段采集期间有几分钟可用——它自己跑完就自己写 summary（
        wver 键控、续跑幂等），所以到这里通常只剩一次零成本观测/清账。**本函数不 join、
        不 sleep**：还在跑的（异常：节点慢/挂了）只打 WARN，由它自己的 `eval_window_sec`
        deadline 结束；`policy.evalJoinSoftSec>0` 时才走旧的「边界处最多补等 N 秒」。
        """
        pending = self._eval_tail
        self._eval_tail = None
        if pending is None:
            return
        thread, t_start = pending
        elapsed = time.time() - t_start
        window = float(getattr(self.args, "eval_window_sec", 1500) or 1500)
        if not thread.is_alive():
            log(f"[eval] tail settled during rollout (+{elapsed:.0f}s) — 已自落账")
            return
        from rl.eval_local import eval_tail_overran

        soft = self._eval_join_soft_sec()
        if soft > 0.0:
            # 应急旋钮：只在边界处补等（旧语义）；缺省 0 ⇒ 不进这个分支
            _t_join = time.time()
            thread.join(timeout=soft)
            waited = time.time() - _t_join
            self._eval_join_sec = round(self._eval_join_sec + waited, 1)
            if not thread.is_alive():
                log(f"[eval] tail settled at rollout boundary (+{elapsed:.0f}s, waited {waited:.1f}s)")
                return
        level = "WARN " if eval_tail_overran(t_start, window, time.time()) else ""
        log(
            f"[eval] {level}tail still running at rollout boundary "
            f"(alive {elapsed:.0f}s / window {window:.0f}s) — 继续后台消化，不阻塞主链"
        )

    def _join_eval(self, it: int) -> dict | None:
        """v3.12 eval 延迟化：eval 不阻塞训练主链（后台线程 + wver 键控）。

        门判定读 eval_log 的 eval_summary（iter 字段保留原轮号 + wver），晚入账只
        让判定窗口顺延，判据不变。**per-tick 不站等**（2026-09-17）：未收官的尾巴整根
        传给 `_sweep_eval_tail`，由下一轮 rollout 收官这个自然边界收拢——不站着等任何
        固定秒数。intent/goal 仍全预算 join（止损判门要吃同轮 summary）。
        """
        args = self.args
        if self._eval_gate is not None:
            self._eval_gate.set()
        eval_join_sec = 0.0
        eval_thread = self._eval_thread
        if eval_thread is not None and eval_thread.is_alive():
            budget = float(args.eval_window_sec) + 60.0
            if args.mode in ("intent", "goal"):
                # intent/goal：eval_summary 须在 jsonl 写回前结算（止损判门依赖）。
                log(
                    f"waiting up to {budget:.0f}s for clean-eval round before next "
                    f"weight distribution"
                )
                _t_join = time.time()
                eval_thread.join(timeout=budget)
                eval_join_sec = round(time.time() - _t_join, 1)
            else:
                # per-tick：不站等（缺省）→ 交棒；policy.evalJoinSoftSec>0 时才补等。
                soft = min(budget, self._eval_join_soft_sec())
                if soft > 0.0:
                    log(
                        f"[run_rl] eval deferred: soft-wait {soft:.0f}s for tail "
                        f"(policy.evalJoinSoftSec — 应急旋钮)"
                    )
                    _t_join = time.time()
                    eval_thread.join(timeout=soft)
                    eval_join_sec = round(time.time() - _t_join, 1)
                if eval_thread.is_alive():
                    # 交棒：下一轮 rollout 收官时收拢（_dispatch_delayed_eval 入口）
                    self._eval_tail = (eval_thread, self._eval_tail_start or time.time())
                    log(
                        "[run_rl] eval deferred: tail handed to next rollout boundary "
                        "（不站等；线程自己按 eval_window_sec 收尾并落账）"
                    )
        self._eval_tail_start = None
        self._eval_join_sec = eval_join_sec
        # intent/goal：回读该迭代 eval_summary（评估线程写入；止损判门的数据源）。
        eval_rec = (
            read_eval_summary(self._jsonl_path, it) if args.mode in ("intent", "goal") else None
        )
        # pace checkpoint（intent/goal 护栏）：iter5 首现通关。
        if args.mode in ("intent", "goal") and it == 5 and self._report.get("winRate", 0) <= 0:
            log("WARN pace: no clear by iter5 (rollout winRate=0) — investigate")
        return eval_rec

    def _dispatch_delayed_eval(self, it: int, dist_cfg: dict | None) -> None:
        """延迟 eval 派发（P0 修复）：本轮采集收官后，为上一轮已完成权重 W(it-1) 派发。

        旧语义在此处派发读活指针 = W(it-1) 却标 itN（标签超前一轮）；新语义标
        权重轮 M=it-1，读不可变归档（回落活指针 + WARN）。游戏仍藏进随后 PPO(it)
        空窗，wall 不变。未覆盖的对局由派发内幂等续跑；全覆盖即空转返回。
        仅 per-tick（intent/goal 走 m1 路径，it0 基线走独立流，均不动）。
        """
        from rl.eval_dispatch import dispatch_eval_bg, find_archive_weights, select_delayed_eval_it
        from rl.queue import RUN_ID

        args = self.args
        # 本轮采集刚落幕（rollout 收官）= 上一轮 eval 尾巴的自然收拢点：先收拢，再派新轮。
        self._sweep_eval_tail()
        if getattr(args, "mode", "per-tick") != "per-tick":
            # intent/goal m1 与 it0 基线走各自派发流，此处不碰（rollout_phase 已处理）。
            return
        self._eval_thread = None
        self._eval_gate = None
        m = select_delayed_eval_it(it, self._eval_on_round)
        if m is None:
            return
        src = find_archive_weights(
            str(getattr(args, "backup_dir", "") or ""),
            str(getattr(args, "backup_prefix", "") or ""),
            m,
        )
        from_archive = src is not None
        src_path = src if src is not None else str(args.out)
        if not from_archive:
            log(
                f"[eval] it{m}: 归档缺席（backup 失败？）——回落活指针 {src_path} 派发"
                "（wver 与离线复跑不可比，本轮 eval 仅供参考）"
            )
        from rl.eval_local import eval_local_early_epochs, local_gate_release_plan

        self._eval_gate = threading.Event()
        # 尾巴的窗口起点（收拢时判“是否跑过自己的窗口”）；只作时间基准，不参与等待。
        self._eval_tail_start = time.time()
        # 本机份额放行档（2026-09-17）：本轮本机不跑 PPO（远端 PPO / 整轮上云 / stream
        # 已在轮内跑完）⇒ 立刻放行（核心空闲，预留尾段即时开跑）；本机 PPO ⇒ 末 epoch
        # 放行（early=0 时维持 R6：_join_eval 才放行）。
        plan = local_gate_release_plan(
            # ★ 2026-09-21（§3）：PPO 恒在节点上跑（本机不跑 PPO）⇒ 恒为 immediate 档。
            # 不再从 `args.ppo` 推导（旗标已删，旧写法会恒判本机 PPO 而错拿 on_join）。
            ppo_remote=True,
            node_rollout=bool(getattr(self, "_node_rollout", False)),
            stream_round=getattr(self, "_stream_meta", None) is not None,
            early_epochs=eval_local_early_epochs(self._eval_policy_cfg()),
        )
        if plan == "immediate":
            self._eval_gate.set()
            log("[eval] 本机份额提前放行（本轮 PPO 不在本机跑）——reserved 尾段立即开跑")
        self._eval_thread = dispatch_eval_bg(
            self.bun,
            src_path,
            self._traj_dir,
            args,
            dist_cfg or {},
            f"{RUN_ID}.{it}",
            m,
            (self._report or {}).get("winRate"),
            local_gate=self._eval_gate,
        )
        log(
            f"[eval] it{m} dispatched from "
            f"{'archive' if from_archive else 'LIVE pointer'} {src_path} "
            f"(round it{it} PPO window)"
        )

    def _eval_covered(self, m: int, summaries: dict[int, list[dict]]) -> bool:
        """drain 覆盖判定：存在 dropped==0 的 summary 即完整（缺字段旧行按未覆盖）。"""
        rows = summaries.get(m, [])
        if not rows:
            return False
        return any(r.get("dropped") == 0 for r in rows)

    def _drain_pending_eval(self) -> None:
        """收官 drain（用户指令：最终轮立即 eval）：为最新已完成且无完整 summary
        的评估轮权重派发并等收官。串行执行（无 PPO 空窗可藏），等收官预算
        min(eval_window_sec + 60, 600)s，全程 best-effort 只记日志。
        smoke 轮 / 非 per-tick 直接跳过。
        """
        from rl.eval_dispatch import dispatch_eval_bg
        from rl.queue import RUN_ID

        args = self.args
        # 收官前先把在飞尾巴清账（同理：只观测/清账，不站等）。
        self._sweep_eval_tail()
        try:
            if getattr(args, "mode", "per-tick") != "per-tick" or getattr(args, "smoke", False):
                return
            eval_log = Path(self._traj_dir).parent / "eval_log.jsonl"
            summaries: dict[int, list[dict]] = {}
            try:
                with open(eval_log, encoding="utf-8") as jf:
                    for line in jf:
                        try:
                            r = json.loads(line)
                        except Exception:
                            continue
                        if r.get("event") == "eval_summary" and isinstance(r.get("iter"), int):
                            summaries.setdefault(int(r["iter"]), []).append(r)
            except OSError:
                pass
            arch_m: dict[int, str] = {}
            bdir = str(getattr(args, "backup_dir", "") or "")
            bpre = str(getattr(args, "backup_prefix", "") or "")
            if bdir and bpre:
                try:
                    from rl.archive import REPO_ROOT

                    root = REPO_ROOT
                except Exception:
                    root = None
                import os as _os
                import re as _re

                base = str(root / bdir) if root is not None and not _os.path.isabs(bdir) else bdir
                try:
                    pat = _re.compile(rf"^{_re.escape(bpre)}\.it(\d+)\..*\.json$")
                    for p in Path(base).glob(f"{bpre}.it*.*.json"):
                        mt = pat.match(p.name)
                        if mt:
                            kk, vv = int(mt.group(1)), str(p)
                            if kk not in arch_m:
                                arch_m[kk] = vv
                except OSError:
                    pass
            if not arch_m:
                log("[eval] drain: 无归档权重可评估——跳过")
                return
            cand = sorted(
                m
                for m in arch_m
                if m >= 1 and self._eval_on_round(m) and not self._eval_covered(m, summaries)
            )
            if not cand:
                log("[eval] drain: 评估轮权重均已完整 summary——无需收尾 eval")
                return
            if len(cand) > 1:
                log(f"[eval] drain: 旧缺口 {cand[:-1]} 留档（只收尾最新 it{cand[-1]}）")
            m = cand[-1]
            self._eval_gate = threading.Event()
            # 收官 drain 没有并发训练：立刻开闸，否则 local_worker 会等 gate 到 deadline
            # （2026-09-15 x3-power it30：远端 engine_epoch 全 mismatch + gate 未开 → 600s 零局）。
            self._eval_gate.set()
            self._eval_thread = dispatch_eval_bg(
                self.bun,
                arch_m[m],
                self._traj_dir,
                args,
                getattr(self, "_last_dist_cfg", None) or {},
                f"{RUN_ID}.{m}",
                m,
                None,
                local_gate=self._eval_gate,
            )
            budget = min(float(getattr(args, "eval_window_sec", 1800) or 1800) + 60.0, 600.0)
            log(f"[eval] drain: it{m} 收尾派发（archive），等收官 ≤{budget:.0f}s")
            self._eval_thread.join(timeout=budget)
            log(f"[eval] drain: it{m} 收尾结束（alive={self._eval_thread.is_alive()}）")
        except Exception as e:
            log(f"[eval] drain: 收尾 eval 失败（{type(e).__name__}: {e}）——不阻断收官")
