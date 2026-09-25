"""loop_remote —— **远端 PPO 腿的组合根**（2026-09-25 S4 第二十二刀收口）。

本文件在 2026-09-23 的 S4 第二步从 `rl/loop_steps.py` 接走 13 个方法的远端 PPO 腿
（862 行连通分量）；第二十二刀再把这条**连通分量按判据同源切成四簇**，各自成模块：

| 混入 | 判据 | 模块 |
|---|---|---|
| `TrainingRemotePush` | 把一份 job 送到节点（提交 / 首发 / 取回） | `rl/loop_remote_push.py` |
| `TrainingRemoteJob` | 一份远端 PPO job 的四步 + 组合入口 | `rl/loop_remote_job.py` |
| `TrainingRemoteFail` | 远端失败的唯一处置策略 | `rl/loop_remote_fail.py` |
| `TrainingRemoteDrive` | 谁驱动这条腿（轮内 / 整轮 / 整段） | `rl/loop_remote_drive.py` |

依赖是一条链（**调用者依赖被调用者**）：`Push ← Job ← Drive`，`Fail` 独立、与 `Job` 并列
成为 `Drive` 的第二个基类。本文件只剩组合根、**零方法**：`class TrainingRemote(TrainingRemoteDrive)`。

`TrainingSteps` / `TrainingLoop` 的基类元组因此**一行不改**（`__mro__[1]` 仍是本模块的
`TrainingRemote`），四个「继承真混入」的测试宿主也不动——这正是把组合根留在
`rl/loop_remote.py` 的理由：既有 `from rl.loop_remote import TrainingRemote` 调用点零迁移。

⚠ DI seam 已随实现分散到四个新模块（每个模块的同名全局是**各自真实的注入点**）：
`rl.loop_remote_push._push_submit` / `_push_wait_result` · `rl.loop_remote_drive` 的
`dist_common` / `_run_wait_sec` 等。patch 旧的 `rl.loop_remote.*` 现在会变成**静默空操作**
——见 `tests/test_loop_remote_split.py`。
"""

from __future__ import annotations

from rl.loop_remote_drive import TrainingRemoteDrive


class TrainingRemote(TrainingRemoteDrive):
    """远端 PPO 腿 mixin（组合根）：直推腿 ← job 四步 ← 三个驱动入口 + 失败策略。

    被 `TrainingSteps` 继承（调用者依赖被调用者）；只有组合类 `TrainingLoop` 会被实例化。
    """
