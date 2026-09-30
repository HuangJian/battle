"""remote —— 跨端传输与云机执行体（`plan/remote-ppo-architecture.md`）。

子模块全部 torch-free（hub 侧免 torch，D2）；云端 worker（`remote/worker.py`）的 torch 依赖
延迟到 `run_job` 内导入。

**分层位置**（`tests/test_layering.py` + `tests/helpers/remote_dag.py` 守着）：

```
L0  common/ · common.platform_utils · common.pid_probe · schema · dist_*
L1  biz/ · models/ · ppo/ · data/ · train/ · scripts/
L2  worker/                      （iter_rollout · serve_pool：节点侧执行体）
L3  remote/                      （本包：线路 / 云引导 / 云 worker）
L4  hub/ · trainer/              （2026-09-30 刀 1：hub 从本包**出包**，见下）
```

> 2026-09-30（刀 1，hub 出包）：hub 服务端（原 `remote/hub/` + `remote/hub_server.py` + 三个
> 站在门面上的运维工具）已搬到顶层 `hub/` 包，与本包并列在 L4。本包因此只剩「跨端线路 +
> 云机引导 + 云 worker」三件事。`remote/hub_client.py` / `remote/hub_http.py` **不动**：
> 它们是**客户端**侧（节点/训练进程连 hub 用的线路），不是服务端。
>
> 2026-09-30（刀 3，`worker/` 出包）：节点侧执行体（`iter_rollout` / `serve_pool`）搬到
> 顶层 `worker/` 包，坐在本包**下面**（L2）。理由是它们**零 `remote.*` 依赖**（只靠 `common/`），
> 却被本包（`offline_eval` 的离线 eval 腿、`worker` 的云 worker）与 trainer（`trainer/dispatch` /
> `trainer/queue_local` 的本机槽）同时引用 ⇒ 留在包里「谁在谁上面」读不出来，坐下面四条边全朝下。
> `remote/worker.py`（**云** worker）**留在本包**：它是云机上的作业壳，与 `worker/`（节点长驻池）
> 是两件不同的事——名字相近但层号相反（L5 vs L2）。

两个本来属于「纯逻辑」的子模块已下沉 `common/`：`protocol.py` / `game_watch.py`（它们被 `rl/`
引用，与 `remote/ → rl/` 构成包级循环 ⇒ 循环已由下沉消除，不再靠函数内延迟 import 维持）。

三个引导模块是**结构性例外**：`tailscale_boot.py` / `notebook_boot.py` / `offline_boot.py`
从 GitHub raw 单独拉取（cell 拿到 `code.zip` 之前就要 import），因此不得 import
其他 `remote.*`（也不得 import `common`）——其内部重复是有意为之，别顺手合并。
"""
