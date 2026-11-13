"""agent_meta —— 节点采样元数据账本（`dist-agent-meta.jsonl`）的唯一写面。

**它是什么**：每完成/失败一局远端采样，追加一行「哪个节点、哪一局、成没成、多久」。
控制台的巡检页面读它聚合成「各节点贡献/耗时」表（`mode` 列分 `rollout` / `eval` 桶），
排障时它是唯一能回答「这台机器到底在干活吗」的东西。

**为什么单独一层**（与 `biz/bc_ledger.py` / `biz/train_ledger.py` 同一条理由）：
写它有**三个调用者**——rollout 派发（`trainer/dispatch.py`）、干净评估派发
（`trainer/eval_dispatch.py` / `trainer/batch_runner.py`，经 `trainer/dispatch_queue.py` re-export）
与 BC 评估（`worker/bc_eval.py`，2026-11-13 接入）——
而写成 JSONL 的**协议只有一条**（单行、`ensure_ascii=False`、追加、父目录按需创建）。
历史状态是两份字形相同的实现（`trainer/dispatch.py::_record_agent_meta` 与
`trainer/dispatch_queue.py::_record_agent_meta`），差别只在哪份先被谁 import——正是「第二份真相」
的温床。

**三条口径**（改本模块前先想清楚这三条）：

1. **best-effort，绝不抛**：账本只服务于人的诊断，写不进去（磁盘满 / 权限 / 目录被
   并发删）**不得**影响采样与结算 ⇒ 吞 `OSError`。调用方在结算路径上，那里容不下异常。
2. **追加，绝不读改写**：写入口（`common.fs.append_jsonl`）按路径串行化——重叠的评估轮
   并发调用也不会互相覆盖行首（2026-11-02 z4-polish.it60）。账本仍可能被**别的进程**读取，
   读侧（`trainer/eval_course_once.py` 的重跑清理、巡检聚合）负责跳过坏行。
3. **一次 IO 一局一条**：写点在结算处，单局一次 `open`+`write`，成本可忽略——不要
   为了「省 IO」把它改成批量缓冲，那会让崩溃时丢掉最后几局（正是最想看的那几局）。
"""

from __future__ import annotations

import time
from pathlib import Path

from common.fs import append_jsonl

__all__ = ["AGENT_META_NAME", "record_agent_meta", "record_eval_failed"]

#: 账本文件名（与 `trainer/eval_course_once.py` 的重跑清理表、`tools/task.py::clean` 的孤儿表逐字一致）。
AGENT_META_NAME = "dist-agent-meta.jsonl"


def record_agent_meta(meta_path: str | Path, rec: dict) -> None:
    """追加一条节点采样元数据（best-effort：`OSError` 一律吞掉，见模块 docstring）。

    `rec` 的键由调用方决定，现行字段：
    `{node, mode, it, stage, seed, ok, [win, elapsedSec, wallSec | reason], ts}`。
    `elapsedSec` = 节点侧服务时长；`wallSec` = 训练机派发→结算墙钟（含网络）。
    本模块自身不加锁——串行化由 `common.fs` 的每路径写者负责，调用方无需再持锁。
    """
    try:
        append_jsonl(meta_path, rec)
    except OSError:
        pass


def record_eval_failed(
    meta_path: str | Path,
    it: int,
    task: tuple[int, int],
    node: str,
    err: str,
    errno_code: str = "",
    msg_head: str = "",
) -> None:
    """写一条 `ok=false` 的 eval meta（丢局/缺口原因）。

    plan/eval-gap-honesty D1：`reason` 取**头部**（errno + 首行）——旧的 `[-160:]` 只留栈尾，
    bun 的 `error: ENOENT … open '<path>'` 正好被切掉。`errno` / `msg_head` 是**纯新增键**，
    不参与任何公式（历史行读侧按 unknown 处理）。
    """
    record_agent_meta(
        meta_path,
        {
            "node": node,
            "mode": "eval",
            "it": it,
            "stage": task[0],
            "seed": task[1],
            "ok": False,
            "reason": f"{errno_code}: {msg_head}" if msg_head else err,
            "errno": errno_code,
            "msg_head": msg_head,
            "ts": time.strftime("%Y-%m-%d %H:%M:%S"),
        },
    )
