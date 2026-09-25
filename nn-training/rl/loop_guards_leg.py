"""loop_guards_leg —— TrainingGuardsLeg mixin：**结果面**停腿（S4 第二十三刀）。

两条「已经烧掉的不值得再烧」的停腿守卫——都**读 eval_log 趋势**、都**顺手把云机达令
发下去**（停腿的意义就是**停止烧钱**：本地停了、云机接着领活就白停了）、都补一条
`ABORT` 判决：
  · `_kickstart_burn` —— 本腿 vs **自己的起点**（连续 points 个评估点低于基线 ⇒ 疑似回锚）；
  · `_paired_kill`    —— 本臂 vs **对端腿**（同 it 配对差连续 points 个点 < −margin ⇒ 杀臂）。

两者正交（一个问「我退了吗」，一个问「我比对照臂差吗」）；与
`rl/loop_guards_trip.py` 的**过程面**判据（kl/ent/Δ 显著度）不同轴。判据本体在
`rl/kickstart_burn.py` / `rl/paired_kill.py`；云机达令联动在组合根（`self._sync_cloud_halt`）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from rl.config import course_key_of
from rl.events import write_gate_verdict, write_kickstart_burn, write_paired_kill
from rl.gate_check import read_trend_rows
from rl.kickstart_burn import burn_overrides, burn_verdict
from rl.log import log
from rl.paired import declared_paired_seed, latest_run_start_seed, scan_paired_courses
from rl.paired_kill import paired_kill_overrides, paired_kill_verdict


class TrainingGuardsLeg:
    """结果面停腿 mixin（干烧回锚 / 配对杀臂）：读趋势，停腿 + 停云机。"""

    # 依赖的 TrainingLoop 实例属性（声明类型供 mypy/阅读；实际赋值在 TrainingLoop）
    args: Any
    _jsonl_path: Any
    _burn_streak: int
    _pair_kill_streak: int
    #: 共享 sink（真实现住组合根 `rl/loop_guards.py`）：账本视图增量 / 判决 → 云机达令。
    _ledger_apply: Any
    _sync_cloud_halt: Any

    def _kickstart_burn(self, it: int, dist_cfg: dict | None) -> bool:
        """§5 干烧熔断（结果面，plan/accident.plan.md §5.2）：返 True = 停腿告警。

        只在缰绳开着（`kickstart_ref`）时守——干烧是「锚主导更新把起点洗回去」的形态，
        没锚就没这回事。基线不靠人填：`eval_log.jsonl` 的 it0 行（课程 bc 权重的干净评估，
        即缰绳锚定的同一份权重）；连续 `points` 个评估点低于基线 `margin_pp` ⇒ 停腿。

        与 F4 过程熔断的分工：那个看更新健康度（kl/ent），这个看**结果有没有退回去**。
        停腿而不只是告警：C 事故那里两臂 × 12h 全是白烧，读数是 `it1` 就低的；单点低是
        噪声，连着三个点低是趋势（阈值走执行面 `courses.<课>.kickstart_burn`，缺席用常量）。

        账本行是唯一数据源（`eval_log.jsonl` + `read_trend_rows(..., include_baseline=True)`）
        ⇒ 重启可回放、控制台可复算；判据本体在 `rl/kickstart_burn.py`，此处只做
        「读 → 判 → 落账/日志」。
        """
        args = self.args
        if not bool(getattr(args, "kickstart_ref", False)):
            return False
        # 读 **eval_log.jsonl**（per-tick 的评估行落这里；`training_log.jsonl` 是训练事件册
        # ——与 `_gate` 同一个源文件、同一个读者，不另开第二个读法）。
        try:
            rows = read_trend_rows(
                getattr(self, "_traj_root", Path(str(self._jsonl_path)).parent) / "eval_log.jsonl",
                include_baseline=True,
            )
        except Exception as e:  # 读账本失败不得阻断训练（同 _gate 的兜底风格）
            log(f"[run_rl] WARN kickstart-burn 读账本失败（{type(e).__name__}: {e}）——本轮不判")
            return False
        margin_pp, points = burn_overrides(dist_cfg, course_key_of(args))
        v = burn_verdict(rows, points=points, margin_pp=margin_pp)
        if v.baseline is None:
            return False
        prev = int(getattr(self, "_burn_streak", 0) or 0)
        if v.streak != prev:
            # **状态转移才落账**（同 `_stop_loss` 的口径）：每轮都写会把账本淹掉，
            # 而 0 → 0 无需记录。日志也只在计数上升时说，别拿同一句话刷屏。
            if v.streak:
                log(
                    f"[run_rl] WARN kickstart-burn it{it}: 连续 {v.streak}/{points} 个评估点"
                    f"低于基线 {margin_pp:.1f}pp"
                    f"（基线 {v.baseline * 100:.1f}%，最新 {(v.last or 0.0) * 100:.1f}%）"
                    "——再低就停腿（疑似回锚）"
                )
            self._ledger_apply(
                write_kickstart_burn(self._jsonl_path, it, v.streak, v.baseline, v.last, margin_pp)
            )
        self._burn_streak = v.streak
        if not v.tripped:
            return False
        log(f"[run_rl] CRITICAL KICKSTART-BURN it{it}: {v.reason}")
        log(
            f"[run_rl] training PAUSED; weights kept at {args.out}; "
            "疑似回锚/塌陷——检查起点（bc 权重判决段读数）与 kk 初值是否匹配"
        )
        # 本地停腿（本轮即终点）之外，顺手把远端云机的达令也发下去（按课程，
        # 共享 hub 不连坐其它课；无 hub/提示模式自动短路）——停腿的意义就是**停止烧钱**，
        # 本地停了、云机接着领活就白停了。
        self._sync_cloud_halt(it, "ABORT")
        # kickstart_burn 事件已在上面「状态转移」处写过（不重复写）：这里只补判决。
        self._ledger_apply(
            write_gate_verdict(
                self._jsonl_path,
                it,
                "ABORT",
                f"kickstart-burn: {v.reason}",
                decider="loop",
            )
        )
        return True

    def _paired_kill(self, it: int, dist_cfg: dict | None) -> bool:
        """配对**中点杀臂**（结果面，plan/accident.plan.md 附 §5）：返 True = 停腿告警。

        事故：中点条件（同 it 配对差连续 2 点 <−3pp）在 it25+it30 触发，**凌晨没人执行**。
        计划原文的教训是「规则 Trustee 缺席 = 规则不存在」——这个守卫就是那个不用醒着的人：
        判据在 `rl/paired_kill.py`（纯函数），这里只做「读 → 判 → 落账/停腿」。

        与 `_kickstart_burn` 的分工：那个比的是**本腿 vs 自己的起点**（回锚/塌陷），
        这个比的是**本臂 vs 对端**（同 V 的另一条腿）——一个问「我退了吗」，一个问
        「我比对照臂差吗」。两者正交，都要。

        **前提闸**（配对差只有在同种子流下才有意义）：本课声明了 `paired_rotate_seed`，
        且对端账本末条 run_start 就是同一把 V；否则那两列读数来自不同种子流，Δ 不是配对差
        ——宁可不判，不用错配的读数杀掉一条正在跑的腿（同 §2.5「跨臂只告警不停止」的理由）。
        """
        declared = declared_paired_seed(getattr(self.args, "course_obj", None))
        if declared is None:
            return False  # 单腿口径：没有「对端」这回事
        self_name = str(getattr(self.args, "course", "") or "")
        siblings = scan_paired_courses(declared, self_name=self_name)
        if not siblings:
            return False  # 无对端：启动自检已响亮告警过（§2.5），这里无可比
        margin_pp, points = paired_kill_overrides(dist_cfg, course_key_of(self.args))
        traj_root = Path(str(getattr(self, "_traj_root", Path(str(self._jsonl_path)).parent)))
        try:
            own_rows = read_trend_rows(traj_root / "eval_log.jsonl")
        except Exception as e:  # 读账本失败不得阻断训练（同 _kickstart_burn 的兜底风格）
            log(f"[run_rl] WARN paired-kill 读本臂账本失败（{type(e).__name__}: {e}）——本轮不判")
            return False
        # 多看个对端课程时取「落后最深」的那一个上账（对端只两门时就是唯一那个）。
        best_peer = ""
        best = None
        for name, _v in siblings:
            peer_dir = traj_root.parent / name
            # 前提闸：对端**这一腿**确实在同一把 V 上（陈旧账本 / 未按课程文件起跑 ⇒ 不比）。
            seed = latest_run_start_seed(peer_dir)
            if seed is not None and int(seed) != int(declared):
                log(
                    f"[run_rl] paired-kill: 跳过对端 {name}——它账本末条 run_start.rotateSeed={seed}"
                    f" ≠ V={declared}（不同种子流的读数不成对，不拿它杀臂）"
                )
                continue
            try:
                peer_rows = read_trend_rows(peer_dir / "eval_log.jsonl")
            except Exception:
                continue
            v = paired_kill_verdict(own_rows, peer_rows, points=points, margin_pp=margin_pp)
            if v.delta_pp is None:
                log(f"[run_rl] paired-kill: {name} {v.reason}")
                continue
            if best is None or v.streak > best.streak or (v.tripped and not best.tripped):
                best, best_peer = v, name
        if best is None:
            return False
        streak = best.streak
        prev = int(getattr(self, "_pair_kill_streak", 0) or 0)
        if streak != prev:
            # **状态转移才落账**（同 `_stop_loss` / `_kickstart_burn`）：0 → 0 无需记录。
            if streak:
                log(
                    f"[run_rl] WARN paired-kill it{it}: 同 it 配对差连续 {streak}/{points} 个点"
                    f" < −{margin_pp:.1f}pp（对端 {best_peer}）——再低就杀臂"
                )
            self._ledger_apply(
                write_paired_kill(
                    self._jsonl_path,
                    it,
                    streak,
                    best_peer,
                    best.delta_pp,
                    best.own,
                    best.peer,
                    margin_pp,
                )
            )
        self._pair_kill_streak = streak
        if not best.tripped:
            return False
        reason = f"同 it 配对差连续 {streak} 个点 < −{margin_pp:.1f}pp（对端 {best_peer}）"
        log(f"[run_rl] CRITICAL PAIRED-KILL it{it}: {reason}")
        log(
            f"[run_rl] training PAUSED; weights kept at {self.args.out}; "
            f"对照臂 {best_peer} 仍在跑——检查本臂的奖励/超参变更是否真带来了分岔"
        )
        self._sync_cloud_halt(it, "ABORT")
        self._ledger_apply(
            write_gate_verdict(
                self._jsonl_path, it, "ABORT", f"paired-kill: {reason}", decider="loop"
            )
        )
        return True
