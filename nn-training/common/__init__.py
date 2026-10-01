"""common —— nn-training 的**共享原语层**（stdlib-only；随 `code.zip` 下发）。

## 层契约（改本包之前先读这一节）

1. **只依赖 stdlib**（包**内**互相 import 不算越界 —— 2026-09-30 刀 2 之后
   `platform_utils` / `pid_probe` / `schema` / `distribution` 等同层模块都住在本包里了）。
   本包会被 `remote.hub_client.pack_code_zip` 打进 `code.zip` 解到云机 `sys.path`
   ——那里没有 torch、没有 numpy、也没有 `trainer/` 的运行前提。因此本包**不得** import
   `trainer.*` / `remote.*` / `ppo.*` / `torch` / `numpy`，否则云端解包即 ImportError。
2. **本包不得反向 import 上层**（同上；依赖方向永远是 `上层 → common`）。
3. **无副作用**：import 本包不读环境变量、不开文件、不起线程、不发网络请求。
4. **无模块级可变状态**（`pid_probe` 那种只读常量表除外）——只放纯函数与不可变常量。

## 为什么要有这一层（不是「多此一举的抽象」）

同名实现在本仓曾各写 2~4 份**且语义漂移**：

| 原语 | 曾有几份 | 漂移形态 |
|------|---------|---------|
| `sha256_file` | 3 份 + 1 处 inline | `common.distribution.weights_fingerprint` / `remote.artifacts` / `remote.hub_client._sha256_file` |
| `bun_version` | 3 份 | 一处失败返 `"?"`、一处返 `""`；超时一处 10s、一处 30s |
| `exc_tail` | 2 份 | `remote/worker.py::_failure_detail` ↔ `trainer/stream.py::_exc_tail`，后者的 docstring 明确写了「与前者同口径…故就地保留同款小助手」 |
| 原子写 | 2 份 | `remote/artifacts.atomic_write_bytes` ↔ `hub.server._write_bytes` |
| 子进程捕获 | 13 处裸 `text=True` | 缺 `encoding=utf-8` ⇒ **静默丢输出**（`docs/nn/engineering.md` §19 / §30 记录的坑，grep 时赫然在列） |

「同口径就地保留」那句注释就是本包存在的理由：**口径写进注释不算单一实现**。
唯一实现 + 显式 UTF-8 捕获 = 这两类漂移同时消失。先例是 `common.pid_probe.pid_alive`
（其 docstring 的「唯一实现」论证，本包沿用同一模式）。

## 谁不能用本包（重要）

三个**从 GitHub raw 单独拉取**的引导模块必须保持零依赖，**不得** import 本包：

* `remote/tailscale_boot.py`
* `remote/notebook_boot.py`
* `remote/offline_boot.py`

理由见各自 docstring：cell 侧在拿到 `code.zip` **之前**就要 import 它们，那时
`common` / `remote` 都不在 `sys.path` 上。它们内部的重复是有意为之，不要「顺手合并」。

## 本包也是分层的**底**（S3，2026-09-23；2026-09-30 刀 2 收口）

`common/` 不止装原语，它还承担「下沉纯逻辑、断开包循环」的职责。**2026-09-30 之后，
本包就是全部 L0**——L0 不再散在 `nn-training/` 根下（`tests/test_layering.py` 里那条
「根下不留旧名」的守卫抓着）：

```
L0  common/                                  （stdlib-only；仓库内只许 import 自己）
L1  biz/ · models/ ppo/ data/ train/ scripts/  （纯逻辑：领域判据 + 算法栈）
L2  worker/                                  （节点侧执行体：iter_rollout · serve_pool）
L3  remote/                                  （跨端线路 + 云引导 + 云 worker）
L4  hub/ · trainer/                          （hub 服务端 / 本机训练编排）
```

两次「下沉」的共同理由 = **断开包级循环**：

* `protocol.py` / `game_watch.py` 原在 `remote/` 下，但 `rl/` 要 import 它们 ⇒ 与
  `remote/ → rl/` 构成包级循环（当时靠 `trainer/queue.py` 一处函数内延迟 import 维持表面平静）；
* `net_http.py` / `_instance_lock.py` / `_port_guard.py` 原在 `remote/` 下，却同时被 `hub/`
  与根入口（两个不同层级）引用 —— 它们是 stdlib-only 原语，坐在底层最自然。
  后两个同时**去掉了文件名里的下划线私有前缀**（`common/instance_lock.py` /
  `common/port_guard.py`）：同一个包里的其他模块本来就要用它们，`_` 前缀名实不符。

方向由 `tests/test_layering.py` 与 `tests/helpers/remote_dag.py` 守着（AST 扫全部依赖边，
含函数内的延迟 import）。

> ⚠ 加模块到这里之前先问：**它是不是纯逻辑、零上层依赖？** 不是就不属于本包。

## 模块

| 模块 | 内容 |
|------|------|
| `common.hashing` | `sha256_bytes` / `sha256_file` |
| `common.proc` | `run_capture`（显式 UTF-8）/ `bun_version` / `version_mm` / `POPEN_NO_WINDOW` |
| `common.fs` | `atomic_write_bytes` / `atomic_write_json` / `append_jsonl` / `extract_tar_bytes` |
| `common.text` | `exc_tail`（异常 traceback 尾段） |
| `common.logutil` | `log_line` / `stamp`（带时间戳的行格式） |
| `common.protocol` | 远端 PPO/BC 作业**线协议**（manifest 校验 / job 身份 / payload 打包 / `data_fp` / 结果信封 / `coef_active`）——原 `remote/protocol.py`；失败类型与线格式已下沉（见下两行），留 `X as X` 门面 |
| `common.errors` | **失败类型族**（`ProtocolError` / `RetryableError` / `UnreapableChildError` / `JobCancelledError` / `JobFailedError` / `CodeChangedError`）——每条继承线 = 一个处置分支。**零依赖叶子**（S5 第六刀） |
| `common.wire_codec` | **传输编码 / 线格式**（v1 gzip+base64 编解码 + v2 裸二进制 `BRV2`/`BRJ2`）。依赖 = stdlib + `common.errors`（S5 第六刀） |
| `common.job_identity` | **job 身份**（幂等键 `idempotency_key` / `job_id` / 发布端撞名守卫 `collision_rows` + 扫描面常量）。stdlib-only 叶子（S5 第七刀） |
| `common.payload` | **语料归档**（`pack_payload` / `unpack_payload` 双读 tar.xz+legacy zip / `find_payload` / 容器名常量）。依赖 = stdlib + `common.errors`（S5 第八刀） |
| `common.manifest` | **job manifest 契约**（`PROTO` / 角色词汇 / `MANIFEST_*` schema / kind→role / TS·plan 产物契约 / rollout 规格校验 / shard 命名与 `data_fp`）。依赖 = stdlib + `common.errors`（S5 第九刀） |
| `common.game_watch` | 单局子进程**停滞看门狗**的口径常量与四行日志——原 `remote/game_watch.py` |
| `common.distribution` | 分布式采样 trainer 侧公共工具（codeHash 配方 / 结果容器 v1+v2 / 取任务 / 权重下发 / 节点探活 / 主动升级）——原 `dist_common.py`（**去 `dist_` 前缀**，2026-09-30 刀 2） |
| `common.shard` | shard 清单 + 远端结果容器校验 + 唯一落盘出口——原 `dist_shard.py`（同上） |
| `common.weights_ledger` | 进程内权重下发账本（同 it 补波复用）——原 `dist_weights_ledger.py`（同上） |
| `common.schema` | 张量布局常量（`OBS_CHANNELS` / `MOVE_DIM` / `SCALAR_LAYOUT` …）——TS 侧 `src/nn/{obs-encoder,infer}.ts` 逐字镜像 |
| `common.platform_utils` | 跨平台子进程 / 核数（`effective_cores`，cgroup 配额优先）/ `rmtree_best_effort` |
| `common.pid_probe` | `pid_alive`（唯一实现；进程重用判别） |
| `common.log_bundle` | 纯文本攒行（无状态） |
| `common.jsonc` | JSONC 解析（`dashboard/src/core/jsonc.ts` 的权威）——原 `common/jsonc.py` |
| `common.net_http` | 回环 HTTP **绕代理**（4 处消费者跨 3 层）——原 `remote/net_http.py` |
| `common.instance_lock` | 单实例锁（`O_CREAT|O_EXCL` + 可接管陈旧锁）——原 `remote/_instance_lock.py`（去 `_` 前缀） |
| `common.port_guard` | 双监听守卫（Windows `SO_REUSEADDR` 允许双绑 ⇒ bind 前探测）——原 `remote/_port_guard.py`（同上） |
"""

from __future__ import annotations

__all__: list[str] = []
