"""worker —— **节点侧执行体**（2026-09-30 刀 3）。

本包只装一件事：**在节点/本机上真的把一局（或一轮）跑起来**。

```
serve_pool.py    节点长驻 worker 池（`--serve` 协议；同质入口 serve-any）
iter_rollout.py  节点侧「本轮 rollout」执行器（看门狗 / 补局 / 聚合报告 / 收尾）
```

**分层位置**（`tests/test_layering.py` + `tests/helpers/remote_dag.py` 守着）：

```
L0  common/                                    （本包的依赖：stdlib + common）
L1  biz/ · models/ · ppo/ · data/ · train/ · scripts/
L2  worker/                                    ★ 本包：节点侧执行体
L3  remote/                                    （云机线路 + 云引导 + 云 worker）
L4  hub/ · trainer/
```

> 2026-09-30（刀 3）：两个模块从 `remote/` 搬进来，成为**顶层包**。
> 为什么不是「留在 `remote/` 里」：它们**零 `remote.*` 依赖**（只靠 `common/`），却被三层引用 ——
> 云机侧（`remote/offline_eval.py` 的离线 eval 腿、`remote/worker.py` 的云 worker）与
> trainer 侧（`trainer/dispatch.py`、`trainer/queue_local.py` 的本机槽位）。留在 `remote/` 里，
> 「谁在谁上面」就读不出来；搬出来并**坐在 `remote/` 下面**（L2），那四条边全部变成向下边。

**为什么在 `remote/` 下面而不是并列**：反向会立刻长出四条向上边（云机与 trainer 都要用它）。
`worker → remote` 是**禁止**的方向（本包不得 import `remote` / `hub`）——见
`tests/test_layering.py::test_worker_never_reaches_the_transport_or_hub`。

**本包与 `hub/` 的关系**：`hub/` 是服务端，与 `remote/` 同层（L4）之上；`worker/` 在它们**下面**，
两者互不 import（机械守着）。
"""
