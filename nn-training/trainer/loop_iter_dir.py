"""loop_iter_dir —— **本轮目录与产出健康**：协作对象 `IterWorkspace`（2026-11-28，P0-1 第一刀）。

两个成员共享同一个判据源：**`traj_dir` 里有没有本轮的活**。

- `prepare`：断点感知——若该迭代已有 wver 匹配的完整 shard（中途崩过）则**保留续跑**，
  否则清空重建（沙箱删除保护拦截时跳过，训练照常）。读的是 `completed_pairs` /
  `precollect_snapshot_wver`（D14 血缘过滤）。
- `check_quota`：同一目录的**反向**健康读数——连续两轮零 shard（`rl_s*_seed*`）⇒ 响亮
  告警（多课程切分下本机槽位被压到 0 时，训练看似在跑、实则在烧空转墙钟）。`rollout_src=node`
  轮本地本就该零 shard ⇒ 先排除（不然每轮假事故把真事故淹掉）。

## 为什么从混入改成协作对象（P0-1 第一刀）

原先这是 `RoundSteps` 的一个基类（`TrainingIterDir`），靠 `TrainingLoop.__init__` 在**同一实例**
上赋的 `self._traj_dir` / `self._course_fp` / … 工作——谁提供这些槽位没有任何静态声明，
改一处必须读遍 12 个混入。现在：

* **状态归自己**：`_zero_shard_streak` 只存这里（原先在 `loop_core._init_ledger_state` 赋初值）；
* **依赖有声明**：`IterWorkspaceHost` 是 `Protocol`，宿主必须提供的读面逐项列出，mypy 可见；
* **组装一处**：`TrainingLoop.__init__` 造 `self.iter_ws = IterWorkspace(self)`；
* **调用点不动**：组合根留两个一行转发壳（`_prepare_iter_dir` / `_check_quota_incident`），
  轮内步骤与既有用例的 patch 点照旧命中。壳不是逻辑，`test_loop_core_tail_split` 用 AST
  钉住「壳体恰好一行委派」。

`extra_wver` 仍是**宿主所有**的跨簇槽位（`loop_volume` / `loop_dispatch` 也读它），
所以由本对象经 `host.extra_wver` 写回，而不是搬进来。

## DI seam / 模块全局

`common.distribution` · `log` · `rmtree_best_effort` · `precollect_snapshot_wver` ·
`completed_pairs` 是**本模块**的模块全局（patch 只有打在这里才生效）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import common.distribution
from common.log import log
from common.platform_utils import rmtree_best_effort
from trainer.collect_only import precollect_snapshot_wver
from worker.resume import completed_pairs


class IterWorkspaceHost(Protocol):
    """`IterWorkspace` 需要的宿主读面（`TrainingLoop` 用 property 提供，全部只读 + 一个可写槽）。"""

    args: Any
    #: 本轮预采快照 wver（本对象写、`TrainingVolume` / `TrainingDispatch` 读）——**唯一可写**项。
    extra_wver: str | None

    # 其余全是只读读数：宿主用 property 提供（每轮会重指，不许在 `__init__` 里拷一份）。
    @property
    def traj_dir(self) -> Path:
        """本轮目录（`traj_root/it{it}`）。"""
        ...

    @property
    def course_fp(self) -> str | None:
        """D14 **文件**血缘（课程文件 sha256；`None` = 无课程，旧行为不过滤）。"""
        ...

    @property
    def corpus_fp(self) -> str | None:
        """语料身份指纹（`None` 视为 `""`）。"""
        ...

    @property
    def state_init(self) -> bool:
        """D14 初始化状态过滤是否开启。"""
        ...

    @property
    def node_rollout(self) -> bool:
        """本轮是否在节点采集（本地零 shard 是预期）。"""
        ...


@dataclass(frozen=True)
class QuotaView:
    """配额事故计数的公开读数（用例 / 诊断共用；不改变判据）。"""

    zero_shard_streak: int


class IterWorkspace:
    """本轮目录（续跑保留 / 清空重建）与产出健康告警（2 方法；见本模块头注）。"""

    def __init__(self, host: IterWorkspaceHost) -> None:
        self._host = host
        #: 连续零 shard 落盘的轮数（per-course；原先住在组合根 `__init__`）。
        self._zero_shard_streak = 0

    def quota_state(self) -> QuotaView:
        """连续零 shard 的轮数（只读；`check_quota` 的唯一状态读数）。"""
        return QuotaView(zero_shard_streak=self._zero_shard_streak)

    def check_quota(self, it: int) -> None:
        """配额事故告警（plan P4-W3 / §3.4）：连续 2 轮零 shard 落盘 → 响亮警告行。

        多课程切分下若某课本机槽位被压到 0（或与别课抢核失败），表现为该课 traj
        连续无 shard：训练看似在跑、实则在烧空转墙钟。计数是 per-course 的（每个
        trainer 进程一本课），console 日志页直接可见本行（不建新通道）。

        M3：`rollout_src=node` 轮**本地本来就该零 shard**（采集在节点上，跑完即毁）——
        不排除就会每轮大喊「检查配额」（假事故），把真事故的告警淹掉。
        """
        if self._host.node_rollout:
            self._zero_shard_streak = 0
            return
        try:
            n = sum(1 for _ in self._host.traj_dir.rglob("rl_s*_seed*"))
        except OSError:
            n = 0
        self._zero_shard_streak = 0 if n else self._zero_shard_streak + 1
        if self._zero_shard_streak >= 2:
            log(
                f"[quota] WARN it{it}: 连续 {self._zero_shard_streak} 轮零 shard 落盘 "
                f"(course={getattr(self._host.args, 'course_name', '') or 'nocourse'}) — "
                "检查 courses.<课>.workers/local_slots 配额或节点可用性"
            )

    def prepare(self, it: int) -> None:
        """rollout/ppo_backend 断点感知：若该迭代已有 wver 匹配的完整 shard（中途崩过），
        保留续跑（跳过已完成局 + 续 ppo_backend checkpoint）；否则清空重建。"""
        args = self._host.args
        traj_dir = self._host.traj_dir
        wver = common.distribution.weights_fingerprint(args.out)
        # 吞吐 T4 提前预采：上一轮若在 epoch3 已 spawn，本轮对账还需接受快照 wver
        # （θ_{N,e3} ≈ θ_N 于最后 1 个 epoch 前）——否则预采首波被当"未完成"清场。
        extra_wver = precollect_snapshot_wver(args.out, it)
        self._host.extra_wver = extra_wver
        have_resume = bool(
            completed_pairs(
                traj_dir,
                wver,
                extra_wver=extra_wver,
                course_fp=self._host.course_fp,
                corpus_fp=self._host.corpus_fp,
                state_init=self._host.state_init,
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
