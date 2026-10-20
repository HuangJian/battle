"""remote/upload_worker.py — 结果回传腿的**独立子进程**（PPO worker 的传输隔离面）。

> 设计：`plan/ppo-worker-transfer-isolation.plan.md`。父侧代理 = `remote/upload_proc.py`。

**为什么要有它**：PPO worker 的回传一直住在训练进程的后台线程里（`remote/result_upload.py`），
`json.dumps(result)`（线上体 ~1.6MB）+ `pack_result_v2`（gzip）+ HTTP 每一步都压在训练线程的
GIL / CPU 上。自主 worker 的补传腿 2026-10-07 已经搬进子进程（`remote/deliver_worker.py`，
`plan/offline-deliver-isolation` §P1），本条把 PPO worker 的同一条腿也搬出去——理由逐字相同：
**同进程这条通道本身就是风险面**，搬走 = 永久关闭。

**形态**：`python -m remote.upload_worker`（无 argv）：

  * **stdin**：父侧每行一个 JSON 任务（`jid` / `base_url` / `token` / `result` / `lease_token` /
    `claim_mode` / `worker_id`）。token **只走这条管道**，不进 argv（`/proc/<pid>/cmdline` 全机
    可读），也不进环境变量（父侧可能同时握多个 hub 的 token，env 只有一个槽位）。
  * **stdout**：普通行 = 日志（父侧原样转发）；`@@upload@@ {json}` = **落定行**（父侧据此记账
    并跑 `on_settled`）。
  * **stdin EOF = 父进程没了** ⇒ 把手上那份发完即退出 0（不空转、不等人）。

**边界（刻意不做）**：

  * **不重试、不判断成败语义** —— `post_result` 自带指数退避 / 409 幂等 / 403 backup 丢弃，
    本文件只负责「调用 + 把结果原样交回」。重写一套重试必然与它分叉。
  * **不落盘** —— 结果的最贵副本在父进程手里（`_persist_result` 已落 `<job_dir>/_result.json`，
    失败还有 `backup/`）；子进程挂了由父侧**同步补发**（幂等）。
  * **不做 idle-timeout** —— 它的生命周期完全由父侧的 stdin 决定（无父即无活）。
"""

from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

#: 落定行的前缀（父侧 `upload_proc` 的解析口径；两边同源 = `result_upload.OUTCOME_PREFIX`，
#: L0 契约模块 —— 子进程与父侧进程都只从它 import，不互相 import）。
from remote.result_upload import OUTCOME_PREFIX


def _log(msg: str) -> None:
    print(f"[upload] {msg}", flush=True)


def _post_one(task: dict[str, Any], post_result) -> dict[str, Any]:
    """发一份结果 ⇒ 落定行（永不把异常带出去：失败也是一条落定）。"""
    from remote import wire

    jid = str(task.get("jid") or "")
    t0 = time.time()
    status: int | None = None
    error = ""
    try:
        status = post_result(
            str(task.get("base_url") or ""),
            str(task.get("token") or ""),
            jid,
            task.get("result") or {},
            lease_token=str(task.get("lease_token") or ""),
            mode=str(task.get("claim_mode") or "ok"),
            worker_id=str(task.get("worker_id") or ""),
            log=_log,
        )
    except BaseException as e:  # 重试耗尽 / 确定性拒绝 / 传输崩：一律算失败
        error = f"{type(e).__name__}: {e}"
        # 响亮（与 `ResultUploader._call` 同一行文案 + 同一条理由：主循环已经往前跑了，
        # 这一行是唯一信号；否则只有 25 分钟后的租约过期才看得出来）。
        _log(
            f"★ 结果回传失败 job {jid}（{time.time() - t0:.1f}s）：{error} — "
            "本份结果**没有**送达 hub（主循环已往前跑；训练侧要等租约过期才看得出来）"
        )
    # 传输账**交回父侧**（`plan/ppo-worker-transfer-isolation` §1-4）：本进程的 `_WIRE` 是
    # 自己那份，父侧打 job 行时读不到 ⇒ 取走并随落定行送回去（父侧 `_wire_add` / `_wire_note_wait`
    # 重放）。桶已 pop，重放不会重复计。
    w = wire._wire_take(jid)
    return {
        "jid": jid,
        "status": int(status) if status is not None else None,
        "error": error,
        "seconds": round(time.time() - t0, 3),
        "wire": {
            "segs": {str(k): [int(v[0]), float(v[1])] for k, v in (w.get("segs") or {}).items()},
            "waits": float(w.get("waits", 0.0) or 0.0),
            "failed": {
                "n": int((w.get("failed") or {}).get("n", 0) or 0),
                "bytes": int((w.get("failed") or {}).get("bytes", 0) or 0),
                "sec": float((w.get("failed") or {}).get("sec", 0.0) or 0.0),
            },
        },
    }


def main() -> int:
    """读 stdin 的任务行 → 发 → 写落定行。EOF（父进程收线/消失）即返回 0。"""
    # 管道编码钉死 utf-8（Windows 控制台是 gb2312；父侧按 utf-8 解码，AGENTS §17.6）。
    # `getattr` 而不是直接 `.reconfigure`：流可能是被测试/壳替換的 TextIO（mypy 也会报
    # union-attr）—— 没有这个方法就跳过，编码退化成运行环境的缺省（不影响协议，落定行是纯 ASCII）。
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    # 导入放在这里：`boot ok` 必须在**依赖链能 import** 之后才打（父侧靠它判「真的活着」）。
    from remote.job_lifecycle import post_result

    _log(f"回传腿已就绪（pid={os.getpid()}，hub 由父侧逐份下发）")
    print(f"[upload] boot ok {os.getpid()}", flush=True)
    for line in sys.stdin:
        s = line.strip()
        if not s:
            continue
        try:
            task = json.loads(s)
        except ValueError as e:
            _log(f"WARN 任务行解析失败（忽略）：{type(e).__name__}: {e}")
            continue
        if not isinstance(task, dict):
            _log("WARN 任务行不是对象（忽略）")
            continue
        rec = _post_one(task, post_result)
        # `ensure_ascii=True`（缺省）：落定行是**协议**，非 ASCII 一律转义，管道里只走纯 ASCII。
        print(OUTCOME_PREFIX + json.dumps(rec), flush=True)
    _log("stdin EOF（父进程收线）——回传腿退出 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
