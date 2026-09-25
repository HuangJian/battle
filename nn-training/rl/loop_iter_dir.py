"""loop_iter_dir —— **本轮目录与产出健康** mixin（2026-09-25 从 rl/loop_core.py 拆出，S4 第二十刀）。

两个成员共享同一个判据源：**`self._traj_dir` 里有没有本轮的活**。

- `_prepare_iter_dir`：断点感知——若该迭代已有 wver 匹配的完整 shard（中途崩过）则**保留续跑**，
  否则清空重建（沙箱删除保护拦截时跳过，训练照常）。读的是 `completed_pairs` /
  `precollect_snapshot_wver`（D14 血缘过滤）。
- `_check_quota_incident`：同一目录的**反向**健康读数——连续两轮零 shard（`rl_s*_seed*`）⇒ 响亮
  告警（多课程切分下本机槽位被压到 0 时，训练看似在跑、实则在烧空转墙钟）。`rollout_src=node`
  轮本地本就该零 shard ⇒ 先排除（不然每轮假事故把真事故淹掉）。

## 宿主：`RoundSteps`

唯一父调用者是 `RoundSteps`（`step_prepare_iter` / `step_course_iter`）⇒ 挂调用者一侧
（`class RoundSteps(TrainingVolume, …, TrainingIterDir)`）。
"""

from __future__ import annotations

from typing import Any

import dist_common
from platform_utils import rmtree_best_effort
from rl.collect_only import precollect_snapshot_wver
from rl.log import log
from rl.resume import completed_pairs


class TrainingIterDir:
    """本轮目录（续跑保留 / 清空重建）与产出健康告警（2 方法；见本模块头注）。"""

    # 依赖的 `TrainingLoop` 实例属性（声明类型供 mypy/阅读）。与其它混入里的同类声明**有意并存**：
    # 混入的状态契约必须在**每个**文件里对 mypy 可见。用 `Any` 而不是精确类型，理由同
    # `rl/loop_volume.py`。
    args: Any
    _traj_dir: Any
    _course_fp: Any
    _corpus_fp: Any
    _extra_wver: Any
    _zero_shard_streak: Any

    def _check_quota_incident(self, it: int) -> None:
        """配额事故告警（plan P4-W3 / §3.4）：连续 2 轮零 shard 落盘 → 响亮警告行。

        多课程切分下若某课本机槽位被压到 0（或与别课抢核失败），表现为该课 traj
        连续无 shard：训练看似在跑、实则在烧空转墙钟。计数是 per-course 的（每个
        trainer 进程一本课），console 日志页直接可见本行（不建新通道）。

        M3：`rollout_src=node` 轮**本地本来就该零 shard**（采集在节点上，跑完即毁）——
        不排除就会每轮大喊「检查配额」（假事故），把真事故的告警淹掉。
        """
        if getattr(self, "_node_rollout", False):
            self._zero_shard_streak = 0
            return
        try:
            n = sum(1 for _ in self._traj_dir.rglob("rl_s*_seed*"))
        except OSError:
            n = 0
        self._zero_shard_streak = 0 if n else self._zero_shard_streak + 1
        if self._zero_shard_streak >= 2:
            log(
                f"[quota] WARN it{it}: 连续 {self._zero_shard_streak} 轮零 shard 落盘 "
                f"(course={getattr(self.args, 'course_name', '') or 'nocourse'}) — "
                "检查 courses.<课>.workers/local_slots 配额或节点可用性"
            )

    def _prepare_iter_dir(self, it: int) -> None:
        """rollout/ppo_backend 断点感知：若该迭代已有 wver 匹配的完整 shard（中途崩过），
        保留续跑（跳过已完成局 + 续 ppo_backend checkpoint）；否则清空重建。"""
        args = self.args
        traj_dir = self._traj_dir
        wver = dist_common.weights_fingerprint(args.out)
        # 吞吐 T4 提前预采：上一轮若在 epoch3 已 spawn，本轮对账还需接受快照 wver
        # （θ_{N,e3} ≈ θ_N 于最后 1 个 epoch 前）——否则预采首波被当"未完成"清场。
        extra_wver = precollect_snapshot_wver(args.out, it)
        self._extra_wver = extra_wver
        have_resume = bool(
            completed_pairs(
                traj_dir,
                wver,
                extra_wver=extra_wver,
                course_fp=self._course_fp,
                corpus_fp=self._corpus_fp,
            )
        )
        if have_resume:
            traj_dir.mkdir(parents=True, exist_ok=True)
            log(
                f"[run_rl] resume iteration {it}: keeping existing shards + ppo_backend checkpoint"
                + (f" (precollect snapshot wver {extra_wver[:12]}…)" if extra_wver else "")
            )
        else:
            if traj_dir.exists():
                # 沙箱删除保护拦截时跳过（保留旧目录，训练照常）
                rmtree_best_effort(traj_dir)
            traj_dir.mkdir(parents=True)
