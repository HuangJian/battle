"""hub —— hub 服务端进程的全部代码（2026-09-30 刀 1 出包；此前住 `remote/hub/` + `remote/hub_server.py`）。

## 包里有什么

```
hub/server.py            入口与门面（L7）：`python -m hub.server` + 逐个自别名 re-export（**零实现**）
hub/http_face.py         HTTP 面（L5）：来源判定 + HubHandler（五组路由混入的组装 + 通用助手）
hub/boot.py              引导链（L6）：as_hub / make_server / main（argparse + 单实例锁 + 端口守卫）
hub/{admin,schedule,result,blob,offline}.py   五组路由混入
hub/{store,store_*}.py   _JobStore 组合类 + 六个域混入
hub/{queue,queue_*}.py   _HubQueue 组合类 + 七个域混入 + 声明面（QueuePeer）
hub/auth.py              _AuthGuard + _is_loopback
hub/task_pack.py         任务包新鲜度门 / 缺包自愈门（纯判据叶子）
hub/{smoke_loopback,tunnel_ab_probe,backfill_offline}.py  站在门面上的运维/验收工具
```

**不 import `hub.server`** —— 实现模块反向依赖组装模块就成环（`tests/test_hub_routes_split.py` 与
`tests/test_hub_admin_split.py` 各有一条守卫；本仓对环的处理只有「下沉共同依赖」与「参数注入」两种）。

## 三个「站在门面上的工具」为什么住在本包（而不是 `remote/`）

它们都是「起一个**真** hub 服务」的运维工具：`smoke_loopback`（回环冒烟）· `tunnel_ab_probe`
（隧道 A/B 探针）· `backfill_offline`（补做已落地回传轮）。三者都 `from hub.server import _JobStore,
make_server`，若留在 `remote/` 就成了一条 `remote → hub` 的**向上边**（远程层不该依赖服务端）。
账本 `tests/helpers/remote_dag.py` 把它们登记在 L8（比 L7 的入口再高一格），方向因此严格向下。

## 依赖方向

`common (L0) < biz / 算法栈 (L1) < worker (L2) < remote (L3) < hub (L4)`
—— `hub/` 可以 import `remote/`（客户端线路、push 派发）、`biz/`、`common/`；**不得** import
`trainer/`。hub 与 trainer 同层且互为对端，实测零互引。
"""
