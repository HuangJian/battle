"""common/progress_hook.py —— 轮内进度打点的**上报口**（快照侧；★M3 / Q2）。

云机离线段的「谁在跑这门课」由 hub 的 hold 记账（plan/worker-type-dispatch-model §3-M1a），
而 hold 的活性**只看 `last_progress_at`**（900s 没进度 = stale ⇒ 别人可以自动接管）。心跳只刷
TTL、不算活性（§68：心跳活、进度死的教训）⇒ 一条长轮次（rollout + PPO，几十分钟起步）必须
**在轮内**报进度，否则「正在算」会被判成「掉线」，别处的盘就会把这段活接管走。

分工（F3 定案）：

  * **打点层**（拼 URL、节流、HTTP POST、409 分流、日志）住 `remote/offline_boot.py`：它是
    notebook 每次会话从 GitHub raw 刷新的那份，跟着 hub 的协议一起演进；
  * **本模块**是快照侧的**上报口**（`worker/iter_rollout.py` 的「每 N 局完」与
    `remote/plan_run.py` 的轮边界），随 `code.zip` 下发，可能是**旧版**——所以契约只有
    一个名字 + 一个签名，两边都不 import 对方。

契约（唯一真相；`tests/remote/test_offline_boot.py` 两个方向都钉住）::

    sys.modules["bcity_offline_progress"].note_progress(
        kind="", *, it=0, done=0, total=0, force=False
    ) -> bool        # True = 这一拍真发出去了（不是「hub 收了」）

没有注册者（本机训练 / 老快照 / 这一课没领到租约）⇒ `report()` 立刻返回 False：**一次 dict
查找之外零成本、绝不抛**。打点是观测，任何失败都不许影响训练（心跳线程那条纪律同一句）。
"""

from __future__ import annotations

import sys
from typing import Any

#: 打点层的**注册名**（`remote/offline_boot.py` 往 `sys.modules` 这个键下挂一个模块对象）。
#: 为什么用 `sys.modules` 而不是参数/环境变量：云机的 `run_loop_main(argv)` 是**同进程**调用
#: （argv 是字符串列表，递不进回调），而这一层与快照侧的版本各自独立演进——名字是唯一可
#: 稳定的接缝。boot 侧自带一份同名常量（它不许 import 本仓代码），由用例钉住两者逐字相同。
HOOK_NAME = "bcity_offline_progress"


def hook() -> Any | None:
    """当前注册的打点层（没注册 ⇒ None）。**只读**，不改 `sys.modules`。"""
    return sys.modules.get(HOOK_NAME)


def report(kind: str = "", *, it: int = 0, done: int = 0, total: int = 0, force: bool = False) -> bool:
    """上报一个「完成事件」→ 打点层回「这一拍发没发出去」（没注册/异常 ⇒ False）。

    `kind` 只进日志（打点层/现场），不上线：hub 的 `/offline/progress` 只吃 course + lease。
    事件粒度刻意**细**（每结算一局、每轮边界各一次）：节流是打点层的事（时间窗），这样
    「发不发」这一个决定只有一个地方做——上报口永远只报事实。
    """
    mod = hook()
    if mod is None:
        return False
    fn = getattr(mod, "note_progress", None)
    if fn is None:
        return False
    try:
        return bool(
            fn(kind, it=int(it), done=int(done), total=int(total), force=bool(force))
        )
    except Exception:  # 打点层的任何意外都不是训练的意外
        return False
