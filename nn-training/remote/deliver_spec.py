"""`OfflineDeliverer` 的构造期旋钮（P0-3：20 参 → 一个对象）与它们的缺省值。

为什么单独成文件（不是「为拆而拆」）：`remote/offline_deliver.py` 的 LOC 预算是
`< 1000`（同一份理由见 `remote/deliver_wire.py` 的模块头 —— 本文件就是按那条纪律
再搬一次）。本类与其缺省常量的依赖面只有 stdlib + `common.logutil`，搬出来不增加
任何一条真实依赖边；留在腿本体只会让「加一个旋钮」再撞一次预算。

字段分档（与 `OfflineDeliverer` 的文档同口径）：

  · 身份 —— `base_url` / `token` / `run_id` / `artifacts_dir` / `course` / `hub_lease` /
    `worker_name`；
  · 网络与探测 —— `timeout` / `probe_ttl` / `probe_fail_ttl` / `sync_cap`；
  · 备份抽屉 —— `backup_root` / `backup_grace`；
  · 模式与观测 —— `eval_every` / `metrics_parts` / `adaptive` / `background`；
  · 注入点 —— `opener` / `now_fn` / `log`。

后 8 个在旧位置表里全是可选参数，且类型相近（三个 `float` 超时、两个 `float` 宽限）；
全关键字调用掩盖了「传错一格」的形态 —— 写错名字会静默落缺省值（例如把 `probe_ttl`
写成 `probe_fail_ttl`，抖动恢复窗口就从 60s 变 15s，只有现场能看出来）。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from common.logutil import tag_logger

#: 探活结果的缓存秒数（训练一轮动辄几分钟，没必要每轮都探两次：`sync()` 前探一次即可，
#: 只读的 `/ping` 也便宜——但 hub 不可达时每次 `urlopen` 都要等超时，才是真成本）。
PROBE_TTL_SEC = 60.0
#: 探活**失败**时的负缓存秒数（比成功短）：抖动恢复的窗口按秒算，而 60s 的负缓存会让
#: 「隧道刚回来」这段最该补投的时间白白跳过（plan §4.2-1；评审 F2）。
#: 抽屉（网络盘）的**宽限**（秒）：一轮要在盘上躺这么久还没送出去，才值得备一份。
BACKUP_GRACE_SEC = 300.0
PROBE_FAIL_TTL_SEC = 15.0
#: 单次 `sync()` 最多投递几轮（少量多次：每轮 ~1.9MB + 往返）。
SYNC_CAP = 4
#: 单件上传的**上限**超时（秒）。⚠ `socket.settimeout` 语义 —— 约束**每一次**连接/send/recv
#: 的阻塞时长，**不是整请求墙钟预算**（plan/offline-deliver-stall §1.1）。
HTTP_TIMEOUT_SEC = 60.0
_log_default = tag_logger("deliver", lambda: time)


@dataclass(frozen=True, kw_only=True)
class OfflineDelivererSpec:
    """`OfflineDeliverer.__init__` 的旋钮袋（P0-3：20 参 → 一个对象）。"""

    base_url: str
    token: str
    run_id: str
    artifacts_dir: str | Path
    course: str = ""
    hub_lease: str = ""
    worker_name: str = ""
    backup_root: str | Path | None = None
    backup_grace: float = BACKUP_GRACE_SEC
    timeout: float = HTTP_TIMEOUT_SEC
    probe_ttl: float = PROBE_TTL_SEC
    probe_fail_ttl: float = PROBE_FAIL_TTL_SEC
    sync_cap: int = SYNC_CAP
    eval_every: int = 0
    metrics_parts: bool = False
    adaptive: bool = False
    background: bool = False
    opener: Callable[[str, bytes, dict, float], tuple[int, bytes]] | None = None
    now_fn: Callable[[], float] | None = None
    log: Callable[[str], None] = _log_default
