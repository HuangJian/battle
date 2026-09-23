"""common —— nn-training 的**共享原语层**（stdlib-only；随 `code.zip` 下发）。

## 层契约（改本包之前先读这一节）

1. **只依赖 stdlib**（外加 `platform_utils` 这个同样 stdlib-only 的顶层模块）。
   本包会被 `remote.hub_client.pack_code_zip` 打进 `code.zip` 解到云机 `sys.path`
   ——那里没有 torch、没有 numpy、也没有 `rl/` 的运行前提。因此本包**不得** import
   `rl.*` / `remote.*` / `ppo.*` / `torch` / `numpy`，否则云端解包即 ImportError。
2. **本包不得反向 import 上层**（同上；依赖方向永远是 `上层 → common`）。
3. **无副作用**：import 本包不读环境变量、不开文件、不起线程、不发网络请求。
4. **无模块级可变状态**（`id_probe` 那种只读常量表除外）——只放纯函数与不可变常量。

## 为什么要有这一层（不是「多此一举的抽象」）

同名实现在本仓曾各写 2~4 份**且语义漂移**：

| 原语 | 曾有几份 | 漂移形态 |
|------|---------|---------|
| `sha256_file` | 3 份 + 1 处 inline | `dist_common.weights_fingerprint` / `remote.artifacts` / `remote.hub_client._sha256_file` |
| `bun_version` | 3 份 | 一处失败返 `"?"`、一处返 `""`；超时一处 10s、一处 30s |
| `exc_tail` | 2 份 | `remote/worker.py::_failure_detail` ↔ `rl/stream.py::_exc_tail`，后者的 docstring 明确写了「与前者同口径…故就地保留同款小助手」 |
| 原子写 | 2 份 | `remote/artifacts.atomic_write_bytes` ↔ `remote/hub_server._write_bytes` |
| 子进程捕获 | 13 处裸 `text=True` | 缺 `encoding=utf-8` ⇒ **静默丢输出**（`docs/nn/engineering.md` §19 / §30 记录的坑，grep 时赫然在列） |

「同口径就地保留」那句注释就是本包存在的理由：**口径写进注释不算单一实现**。
唯一实现 + 显式 UTF-8 捕获 = 这两类漂移同时消失。先例是 `pid_probe.pid_alive`
（其 docstring 的「唯一实现」论证，本包沿用同一模式）。

## 谁不能用本包（重要）

三个**从 GitHub raw 单独拉取**的引导模块必须保持零依赖，**不得** import 本包：

* `remote/tailscale_boot.py`
* `remote/notebook_boot.py`
* `remote/offline_boot.py`

理由见各自 docstring：cell 侧在拿到 `code.zip` **之前**就要 import 它们，那时
`common` / `remote` 都不在 `sys.path` 上。它们内部的重复是有意为之，不要「顺手合并」。

## 本包也是分层的**底**（S3，2026-09-23）

`common/` 不止装原语，它还承担「下沉纯逻辑、断开包循环」的职责：

```
L0  common/ · platform_utils · pid_probe · dist_common · schema   （stdlib-only）
L1  models/ · ppo/ · data/ · train/ · rl/ · scripts/              （纯逻辑）
L2  remote/ · 根入口                                              （传输 / 应用）
```

`protocol.py` 与 `game_watch.py` 原在 `remote/` 下，但 `rl/` 要 import 它们 ⇒ 与
`remote/ → rl/` 构成**包级循环**（靠 `rl/queue.py` 一处函数内延迟 import 维持表面平静）。
两者都是 stdlib-only 纯逻辑，因此下沉到本包：`remote.*` 与 `rl.*` 都能 import，
循环消失。方向由 `tests/test_layering.py` 守着（AST 扫依赖边 + 过渡白名单）。

> ⚠ 加模块到这里之前先问：**它是不是纯逻辑、零上层依赖？** 不是就不属于本包。

## 模块

| 模块 | 内容 |
|------|------|
| `common.hashing` | `sha256_bytes` / `sha256_file` |
| `common.proc` | `run_capture`（显式 UTF-8）/ `bun_version` / `version_mm` / `POPEN_NO_WINDOW` |
| `common.fs` | `atomic_write_bytes` / `atomic_write_json` / `append_jsonl` / `extract_tar_bytes` |
| `common.text` | `exc_tail`（异常 traceback 尾段） |
| `common.logutil` | `log_line` / `stamp`（带时间戳的行格式） |
| `common.protocol` | 远端 PPO/BC 作业**线协议**（manifest 校验 / payload 打包 / `data_fp` / 结果信封）——原 `remote/protocol.py` |
| `common.game_watch` | 单局子进程**停滞看门狗**的口径常量与四行日志——原 `remote/game_watch.py` |
"""

from __future__ import annotations

__all__: list[str] = []
