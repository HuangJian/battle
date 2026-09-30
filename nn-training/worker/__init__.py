"""worker —— **本地 torch 训练全栈**（2026-09-30 刀 3 出包 · 刀 6 并算法栈 · 刀 7 收入口）。

口径（用户 2026-09-30）：「纯训练的内容都放在 worker 目录下 —— 与业务（游戏）逻辑无关的
训练代码」＋「worker 应该包含所有支持本地 torch 训练的所有代码」。**云机 worker = 本地
worker + remote** ⇒ `remote/` 只留跨端线路与云侧壳，训练栈整体坐在它下面。

```text
worker/
  train/ · ppo/ · models/ · data/ · scripts/   算法栈五包（刀 6 从 nn-training/ 顶层并入）
  <52 个训练侧单体>.py                          刀 6 从 biz/ 并入（gate_* / state_init /
                                               forensics / 结束门一族 / 训练侧账本 …）
  serve_pool.py · iter_rollout.py              节点侧执行体（刀 3 从 remote/ 并入）
  rl_config_schema.py                          rl-config 键白名单（刀 7 从 nn-training/ 顶层并入）
```

**分层位置**（`tests/test_layering.py` + `tests/helpers/remote_dag.py` 守着）：

```text
L0  common/                                    （本包的依赖：stdlib + common）
L1  biz/                                       （游戏业务：课程 / 奖励 / 关卡 / 账本）
L2  worker/                                    ★ 本包：本地 torch 训练全栈
L3  remote/                                    （云机线路 + 云引导 + 云 worker）
L4  hub/ · trainer/                            （trainer/：编排 + 六个入口）
```

> 2026-09-30（刀 3）：`serve_pool` / `iter_rollout` 从 `remote/` 搬进来，成为**顶层包**。
> 为什么不是「留在 `remote/` 里」：它们**零 `remote.*` 依赖**（只靠 `common/`），却被三层引用 ——
> 云机侧（`remote/offline_eval.py` 的离线 eval 腿、`remote/worker.py` 的云 worker）与
> trainer 侧（`trainer/dispatch.py`、`trainer/queue_local.py` 的本机槽位）。留在 `remote/` 里，
> 「谁在谁上面」就读不出来；搬出来并**坐在 `remote/` 下面**（L2），那四条边全部变成向下边。
>
> 2026-09-30（刀 6）：算法栈五包（`models` / `ppo` / `data` / `train` / `scripts`）与 52 个
> 训练侧单体从 `biz/` / 顶层一并并入 —— 本包从「节点侧执行体」升级为「本地 torch 训练全栈」，
> 而 `biz/` 只剩 12 个游戏业务模块。层号按拓扑秩重排（12 抬 / 6 压），仍坐在 `remote/` 下面。
>
> 2026-09-30（刀 7）：`rl_config_schema`（rl-config 键白名单，纯 stdlib）从 `nn-training/`
> 顶层并入 ⇒ **L0**（账本在册）。

**为什么在 `remote/` 下面而不是并列**：反向会立刻长出四条向上边（云机与 trainer 都要用它）。
`worker → remote` 是**禁止**的方向（本包不得 import `remote` / `hub`）——见
`tests/test_layering.py::test_worker_never_reaches_the_transport_or_hub`。

**本包与 `hub/` 的关系**：`hub/` 是服务端，与 `remote/` 同层（L4）之上；`worker/` 在它们**下面**，
两者互不 import（机械守着）。
"""
