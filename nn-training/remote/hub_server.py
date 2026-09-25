"""remote/hub_server.py — hub-server 的**入口与门面**（S4 第十六刀收口之后的形状）。

本模块现在**只有 20 行代码**：起进程（`python -m remote.hub_server`）与本仓库其余部分取名字的
入口（`from remote.hub_server import make_server` / `hs._JobStore` / `hs.HubHandler` …）。
实现全部在 `remote/hub/` 下，按「谁对什么负责」分档：

```
remote/hub/http_face.py   HTTP 面（L5）：来源判定（CF 头 / 回环豁免）+ HubHandler
                          （五组路由混入的组装 + 通用助手 _auth_ok/_json/_bytes/_job_or_404…）
remote/hub/boot.py        引导链（L6）：as_hub / make_server / main（argparse + 锁 + 端口守卫 + 发现线程）
remote/hub/{admin,schedule,result,blob,offline}.py   五组路由混入（第十一刀）
remote/hub/{store,store_*}.py                        _JobStore 组合类 + 六个域混入（第十四刀）
remote/hub/{queue,queue_*}.py                        _HubQueue 组合类 + 七个域混入 + 声明面（第十五刀）
remote/hub/auth.py                                   _AuthGuard + _is_loopback（第十五刀）
```

## 这个模块的**唯一职责**：名字

约 20 个测试、`e2e/`、`remote/smoke_loopback.py`、`remote/tunnel_ab_probe.py` 与 dashboard 的
监督器都从 `remote.hub_server` 取名字/取路径，控制台的进程 spec 也写死了
`-m remote.hub_server`。所以搬迁之后这里**逐个自别名 re-export**（`X as X`）——
**名字是契约，位置不是**（第十刀起的老规矩，本刀第三十一次兑现）。

⚠ 两条纪律（第十三刀「名字 ≠ 注入点」的推论）：

  ① **常量读得到，patch 要改路**。`hs.SEND_TIMEOUT_SEC` 拿到的是**同一个对象**（`is` 成立），
     但它的**唯一读者**是 `hub/http_face.py::HubHandler._bytes` —— 那个函数体里读的是
     *http_face 的模块全局*。所以 `monkeypatch.setattr("remote.hub_server.SEND_TIMEOUT_SEC", …)`
     会**静默失效**（名字还在、没人读它）。全仓唯一的这处 patch 已随实现迁到
     `remote.hub.http_face.SEND_TIMEOUT_SEC`（`tests/test_hub_entry_split.py` 钉住这条）。
  ② **不留无读者的转发名**。本模块只暴露下面 `__all__` 里那批**有读者**的名字 —— 一个没有读者
     的转发名就是一个静默空操作的 patch 目标（第十刀的 `_prefetch_fill` 教训）。

## 为什么 hub_server 是 L7（全仓最深的一层之一）

`boot`(L6) 与 `http_face`(L5) 都在本模块**下面** ⇒ 秩 = 1 + 6 = 7；`smoke_loopback` /
`tunnel_ab_probe` 又站在本模块上 ⇒ 它们 8。层号是算出来的（`tests/helpers/remote_dag.py`），
不是贴上去的。
"""

from __future__ import annotations

# ── 门面（**全部**是自别名 re-export，按模块路径排序 —— 分组说明在下面每行末尾的注释里）。
#
# 四组来源：
#   * `hub.boot`      —— 引导链（第十六刀）：`as_hub` / `make_server` / `main`；
#   * `hub.http_face` —— HTTP 面（第十六刀）：`HubHandler` + 来源判定 + `SEND_*`；
#   * `hub.auth` / `hub.queue` / `hub.store` / `hub.store_leases` —— 状态类与调度面
#     （第十四·十五刀）。
#
# 反向禁止：`remote/hub/*` **不得** import 本模块（否则与「本模块 import 它们」成环，而本仓的
# 环只有「下沉共同依赖」与「参数注入」两种处理方式，`DEFERRED_CYCLES` 至今为空）。
from remote.hub.auth import _AuthGuard as _AuthGuard
from remote.hub.auth import _is_loopback as _is_loopback
from remote.hub.boot import DISCOVER_SCAN_SEC as DISCOVER_SCAN_SEC
from remote.hub.boot import as_hub as as_hub
from remote.hub.boot import main as main
from remote.hub.boot import make_server as make_server
from remote.hub.http_face import CF_SOURCE_HEADER as CF_SOURCE_HEADER
from remote.hub.http_face import SEND_CHUNK as SEND_CHUNK
from remote.hub.http_face import SEND_LOG_MIN_BYTES as SEND_LOG_MIN_BYTES
from remote.hub.http_face import SEND_TIMEOUT_SEC as SEND_TIMEOUT_SEC
from remote.hub.http_face import HubHandler as HubHandler
from remote.hub.http_face import _is_ip_literal as _is_ip_literal
from remote.hub.http_face import attributed_source as attributed_source
from remote.hub.queue import _HubQueue as _HubQueue
from remote.hub.store import _JobStore as _JobStore
from remote.hub.store_leases import (
    FREEZE_AFTER_RECLAIMS as FREEZE_AFTER_RECLAIMS,  # 测试从这里取（名字是契约，位置不是）
)
from remote.hub.store_leases import (
    ClaimOutcome as ClaimOutcome,  # 同上：测试拿 `hs.ClaimOutcome` 与 store_leases 对账
)
from remote.hub.task_pack import (
    _TASK_PACK_TRIGGERS as _TASK_PACK_TRIGGERS,
)

# 任务包新鲜度门 / 缺包自愈门 / 清单读数（2026-09-24~25 的取包链）：实现住 `hub/task_pack.py`
# （叶子模块，因为路由混入与队列混入两侧都要它）。这里**逐个自别名 re-export** —— 它们是
# `tests/test_offline_task_{pack,queue}.py` 的读名字面（patch 账本、触发计数、纯判据都要能
# 单独钉），与 `_JobStore` 那批同一条理由：**名字是契约，位置不是**。
from remote.hub.task_pack import (
    TASK_PACK_INDEX_NAME as TASK_PACK_INDEX_NAME,
)
from remote.hub.task_pack import (
    TASK_PACK_MISS_TRIGGER_LIMIT as TASK_PACK_MISS_TRIGGER_LIMIT,
)
from remote.hub.task_pack import (
    TASK_PACK_STALE_THROTTLE_SEC as TASK_PACK_STALE_THROTTLE_SEC,
)
from remote.hub.task_pack import (
    TASK_PACK_STALE_TRIGGER_LIMIT as TASK_PACK_STALE_TRIGGER_LIMIT,
)
from remote.hub.task_pack import (
    decide_task_pack as decide_task_pack,
)
from remote.hub.task_pack import (
    reset_task_pack_miss_triggers as reset_task_pack_miss_triggers,
)
from remote.hub.task_pack import (
    reset_task_pack_triggers as reset_task_pack_triggers,
)
from remote.hub.task_pack import (
    task_pack_stale_reason as task_pack_stale_reason,
)
from remote.hub.task_pack import (
    trigger_task_bundle_export as trigger_task_bundle_export,
)

#: 本门面**对外承诺**的名字全集（即上面每一条 re-export 的目标）。
#: `tests/test_hub_entry_split.py` 正面钉住「这个集合 == 实际暴露的集合」：删一条 re-export
#: 就红（而不是等到某个测试 ImportError 才发现），多一条无名读者也红（死门面）。
__all__ = [
    "CF_SOURCE_HEADER",
    "DISCOVER_SCAN_SEC",
    "FREEZE_AFTER_RECLAIMS",
    "SEND_CHUNK",
    "SEND_LOG_MIN_BYTES",
    "SEND_TIMEOUT_SEC",
    "TASK_PACK_INDEX_NAME",
    "TASK_PACK_MISS_TRIGGER_LIMIT",
    "TASK_PACK_STALE_THROTTLE_SEC",
    "TASK_PACK_STALE_TRIGGER_LIMIT",
    "_TASK_PACK_TRIGGERS",
    "ClaimOutcome",
    "HubHandler",
    "_AuthGuard",
    "_HubQueue",
    "_JobStore",
    "_is_ip_literal",
    "_is_loopback",
    "as_hub",
    "attributed_source",
    "decide_task_pack",
    "main",
    "make_server",
    "reset_task_pack_miss_triggers",
    "reset_task_pack_triggers",
    "task_pack_stale_reason",
    "trigger_task_bundle_export",
]


if __name__ == "__main__":
    main()
