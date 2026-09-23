"""agent_meta —— 节点采样元数据账本（`dist-agent-meta.jsonl`）的唯一写面。

**它是什么**：每完成/失败一局远端采样，追加一行「哪个节点、哪一局、成没成、多久」。
控制台的巡检页面读它聚合成「各节点贡献/耗时」表（`mode` 列分 `rollout` / `eval` 桶），
排障时它是唯一能回答「这台机器到底在干活吗」的东西。

**为什么单独一层**（与 `rl/bc_ledger.py` / `rl/train_ledger.py` 同一条理由）：
写它有**两个调用者**——rollout 派发（`rl/dispatch.py`）与干净评估派发
（`rl/eval_dispatch.py` / `rl/batch_eval.py`，经 `rl/queue.py` re-export）——
而写成 JSONL 的**协议只有一条**（单行、`ensure_ascii=False`、追加、父目录按需创建）。
历史状态是两份字形相同的实现（`rl/dispatch.py::_record_agent_meta` 与
`rl/queue.py::_record_agent_meta`），差别只在哪份先被谁 import——正是「第二份真相」
的温床。

**三条口径**（改本模块前先想清楚这三条）：

1. **best-effort，绝不抛**：账本只服务于人的诊断，写不进去（磁盘满 / 权限 / 目录被
   并发删）**不得**影响采样与结算 ⇒ 吞 `OSError`。调用方在结算路径上，那里容不下异常。
2. **追加，绝不读改写**：账本可能被别的进程（多课程 supervisor）同时追加。读侧
   （`eval_course_once.py` 的重跑清理、巡检聚合）负责跳过坏行。
3. **一次 IO 一局一条**：写点在结算处，单局一次 `open`+`write`，成本可忽略——不要
   为了「省 IO」把它改成批量缓冲，那会让崩溃时丢掉最后几局（正是最想看的那几局）。
"""

from __future__ import annotations

from pathlib import Path

from common.fs import append_jsonl

__all__ = ["AGENT_META_NAME", "record_agent_meta"]

#: 账本文件名（与 `eval_course_once.py` 的重跑清理表、`task.py::clean` 的孤儿表逐字一致）。
AGENT_META_NAME = "dist-agent-meta.jsonl"


def record_agent_meta(meta_path: str | Path, rec: dict) -> None:
    """追加一条节点采样元数据（best-effort：`OSError` 一律吞掉，见模块 docstring）。

    `rec` 的键由调用方决定，现行字段：
    `{node, mode, it, stage, seed, ok, [win, elapsedSec, wallSec | reason], ts}`。
    `elapsedSec` = 节点侧服务时长；`wallSec` = 训练机派发→结算墙钟（含网络）。
    放锁内调用可保证顺序（调用方负责），本模块自身不加锁——它是纯追加。
    """
    try:
        append_jsonl(meta_path, rec)
    except OSError:
        pass
