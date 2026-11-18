"""common/settings.py — 「同一根旋钮有几个读者」的 env 唯一解读处（P1-5 第一批）。

## 收什么、不收什么

只收**被 ≥2 个模块各解一次**的环境变量。只读一次、就地判的旋钮（`NN_GATE_NPROC` /
`PPO_XLA_DIAG` 这类）留在原地 —— 把它们也搬进来的话，本模块会变成又一层转发面，而「配置散读」
真正的事故形态是**同一个变量两个默认值**：值碰巧相同时两处都绿，谁改一边就悄悄分叉。

活例（本模块出生的理由）：`EVALBOARD_DATA` 原先在 `trainer/batch_store.data_root`（默认值来自
本模块的 `DEFAULT_DATA_ROOT`）与 `worker/eval_heartbeat.data_root`（默认值来自自己的
`_DEFAULT_ROOT`）各解一次，两处 docstring 都写着「与对方同口径」。口径写进注释不算单一实现。
`worker/` 不能 import `trainer/`（分层：L2 < L4）⇒ 真家只能在 L0。

`BATTLE_HUB_TOKEN` 是第二例：`remote/run_loop`（CLI 续跑）与 `remote/deliver_worker`（补传腿）
各抄了一遍 `(os.environ.get(...) or "").strip()`。

## 两条纪律

1. **不养模块级状态**（`common/__init__` 层契约 §3）：旋钮在**调用时**读，`import` 本模块不碰
   `os.environ`。函数收一个 `env=` 映射正是为此 —— 测试传字典即可，不必 `monkeypatch.setenv`，
   同一进程里并行跑的用例也不会互相踩。
2. **默认值只写一份**：常量在这里，别处一律 import。

## 为什么不是 `Settings` 对象

目标是「一个旋钮一处解读」，不是「一个对象穿透所有签名」。`tests/test_env_reads_budget.py`
（G13）按文件冻结直读次数并只许调小 —— 新旋钮要进本模块，先看它是不是真有两个读者。
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from common.paths import REPO_ROOT

#: EvalBoard 数据根（训练侧台账 / 心跳 / console 三处同读）。
EVALBOARD_DATA_ENV = "EVALBOARD_DATA"
EVALBOARD_DATA_ROOT = REPO_ROOT / "dashboard" / "data" / "evalboard"

#: hub Bearer token（notebook secret / Kaggle secret 的自然容器；不进命令行、不进任务包）。
HUB_TOKEN_ENV = "BATTLE_HUB_TOKEN"

#: 直推节点的**显式覆盖**（冒烟预演注入本机伪节点）。三个读者：`trainer/loop_transport`
#: （两处：候选表 / 传输可用性门）与 `trainer/bc_loop`（传输裁决）。原先三处各自
#: `os.environ.get(...)`，其中一处还包了 `str(... or "")` —— 同一根旋钮的归一化写了三遍。
PUSH_NODE_ENV = "REMOTE_PUSH_NODE"

#: 已经收进本模块的旋钮名（G13 的「单家」判据从这里取，别在门禁里另抄一份字面量）。
MIGRATED_ENV: tuple[str, ...] = (EVALBOARD_DATA_ENV, HUB_TOKEN_ENV, PUSH_NODE_ENV)


def evalboard_data_root(env: Mapping[str, str] | None = None) -> Path:
    """EvalStore 数据根：`env[EVALBOARD_DATA]` 覆盖，否则 `EVALBOARD_DATA_ROOT`。

    `env=None` = 现读 `os.environ`（调用时读，模块 import 期间不碰）。
    """
    raw = (env if env is not None else os.environ).get(EVALBOARD_DATA_ENV, "").strip()
    return Path(raw) if raw else EVALBOARD_DATA_ROOT


def hub_token_from_env(env: Mapping[str, str] | None = None) -> str:
    """`$BATTLE_HUB_TOKEN`（去首尾空白；未设 ⇒ 空串）。**只读环境变量**的理由见 `HUB_TOKEN_ENV`。"""
    return (env if env is not None else os.environ).get(HUB_TOKEN_ENV, "").strip()


def push_node_from_env(env: Mapping[str, str] | None = None) -> str:
    """`$REMOTE_PUSH_NODE`（去首尾空白；未设 / 空 ⇒ 空串 = 无覆盖）。

    只做「读 + 去空白」：要不要 `rstrip("/")` 由拼 URL 的调用方决定（这是一根旋钮，不是一个
    已归一化的 URL）。空串的语义是**显式无覆盖**，不是 "没配也能用"——三处读者都按
    「非空 ⇒ 独占候选 / 可用」判。
    """
    return (env if env is not None else os.environ).get(PUSH_NODE_ENV, "").strip()
