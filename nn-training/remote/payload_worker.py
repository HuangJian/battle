"""remote/payload_worker.py — 下行腿（预取：`peek` + payload 下载 + 解包）的**独立子进程**。

> 设计：`plan/ppo-worker-transfer-isolation.plan.md`。父侧代理 = `remote/payload_proc.py`。

**为什么要有它**：预取的整条下行腿此前住在训练进程的**后台线程**里（`job_round._prefetch_fill`）：
`peek`（控制面）+ `download_payload`（P2 bulk，可被挤走重下）+ `unpack_payload`（单线程 lzma，
实测单份 300MB 级 / ~1s）。它**刻意**与上一轮 PPO 重叠（`prefetch.py` 头部 §0：串行会把 GPU
饿死在传输上）——而重叠的代价是抢同一个 GIL、同一颗核。回传腿 2026-10-20 已经搬进子进程
（`remote/upload_worker.py`），本条把**另一条腿**也搬出去，重叠保持、竞争消失。

**形态**：`python -m remote.payload_worker`（无 argv；token 与 hub URL 都由父侧逐条命令下发）：

  * **stdin**：父侧每行一个 JSON 命令（`init` / `fill` / `stopfill` / `take` / `drop` /
    `release` / `prune` / `p1` / `ping` / `shutdown`）。token **只走这条管道**，不进 argv
    （`/proc/<pid>/cmdline` 全机可读），也不进环境变量（父侧可能同时握多个 hub 的 token）。
  * **stdout**：普通行 = 日志（父侧原样转发）；`@@pf@@ {json}` = **应答行**（带 `id`，
    父侧按 id 兑现）。**`id` = 兑现凭据**：命令**没有** `id` ⇒ fire-and-forget
    （只有 `p1` 广播）⇒ **不应答**（回了也没人等，白醒父侧读者线程）。
  * **stdin EOF = 父进程没了** ⇒ 立即退出 0（不空转、不等人）。

**谁写暂存区**：**只有本进程**（`work_dir/prefetch/<jid>/`）。父侧通过命令间接操作它
（`take` / `drop` / `release` / `prune`），并把 `take` 拿到的 `staged_dir` 用 `rename` 搬进
job 目录 —— 同一台机器、同一块盘（子进程是本机 `Popen`，不是远端）。这条「单写者」是搬进程
的前提：两个进程同时 prune 同一个目录会把刚下好的暂存删掉。

**边界（刻意不做）**：

  * **不重写下载/解包** —— 复用 `job_round._prefetch_fill`（唯一实现）+ `PrefetchStore`（唯一
    暂存实现）。在这里另写一套必然与 omit 协商 / 预取失败不是失败 / staged 条数记账分叉。
  * **不落 `delivered.json` 之类的账** —— 暂存区是**软持有**：父进程死了就整份蒸发，hub 侧
    零残留（`prefetch.py` 头部 §2）。
  * **不做 idle-timeout** —— 生命周期完全由父侧的 stdin 决定（无父即无活）。
  * **不判断 `take` 的业务语义** —— 命中与否、sha 校验、`preloaded` 形状分派都在
    `PrefetchStore.take()` / `job_round` 里（逐字旧行为）。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading
from typing import Any

#: 控制行前缀：与父侧**同源**（L0 常量，两个进程互不 import）。
from remote.prefetch import PREFETCH_LINE_PREFIX, PrefetchStore

#: `stopfill` 等填充线程的自然退出上限（秒）：`_prefetch_fill` 每轮最多一次 payload 下载，
#: 与父侧 `run_one_round` 的 `pf_thread.join(timeout=30.0)` 同预算（不另立一个数）。
STOP_JOIN_SEC = 30.0


def _log(msg: str) -> None:
    print(f"[payload] {msg}", flush=True)


def _reply(rid: Any, payload: dict[str, Any]) -> None:
    """应答行（协议 = 纯 ASCII：`ensure_ascii` 缺省转义，管道里不走未定义编码）。

    **只在命令带 `id` 时才写** —— `id` 是**兑现凭据**：父侧 `_request` 按它派发应答。
    没有 `id` 的命令是 **fire-and-forget**（`p1` 广播：父进程的关键下载里绝不等应答）
    ⇒ **不应答**：回了父侧也只会当成「没人等」丢掉，而每次 P1 进出等待都白醒父侧
    读者线程一次（现场：worker 日志每 job 刷 4~6 条 `WARN 预取应答行没有 id`）。
    """
    if not isinstance(rid, int):
        return
    print(PREFETCH_LINE_PREFIX + json.dumps({"id": rid, **payload}), flush=True)


class _Engine:
    """命令 → `PrefetchStore` / 填充线程的映射（**全部状态住在这里**，模块级零可变状态）。"""

    def __init__(self) -> None:
        self.store: PrefetchStore | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------ 命令

    def init(self, cmd: dict[str, Any]) -> dict[str, Any]:
        if self.store is not None:
            return {"ok": True, "note": "已初始化（幂等）"}
        self.store = PrefetchStore(
            cmd["work_dir"],
            budget_bytes=int(cmd.get("budget_bytes") or 0),
            staged_max=int(cmd.get("staged_max") or 0),
            log=_log,
        )
        # 建 store 时先扫一遍（与 `worker_loop` 的旧调用点同一句）：清上一会话遗留的残壳 /
        # 崩溃 `.tmp`。`prefetch/` 在 `prune_job_dirs` 豁免名单里 ⇒ 只有这里会收它们。
        dropped = self.store.prune()
        return {"ok": True, "pruned": int(dropped)}

    def fill(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """起一轮填充（先停掉上一轮）。**参数逐字转发**给 `_prefetch_fill`。"""
        if self.store is None:
            return {"ok": False, "error": "未初始化"}
        if self._thread is not None and self._thread.is_alive():
            # 上一轮还在（正常情况下父侧先发过 `stopfill`）：给它同预算的机会自然退出，
            # 否则两个填充器会同时 peek 同一份候选 ⇒ 白下一次（幂等，但纯浪费）。
            self._stopf(int(STOP_JOIN_SEC))
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=_fill_once,
            args=(self, cmd, self._stop),
            daemon=True,
            name="payload-fill",
        )
        self._thread.start()
        return {"ok": True}

    def stopfill(self, _cmd: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "joined": self._stopf(int(STOP_JOIN_SEC))}

    def _stopf(self, join_sec: int) -> bool:
        """置停止位并等它退出；返回「真的退干净了」。"""
        with self._lock:
            self._stop.set()
            th = self._thread
        if th is None:
            return True
        th.join(timeout=max(0, join_sec))
        return not th.is_alive()

    def take(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """取一份命中项。staged ⇒ 交路径；字节 ⇒ 交 base64（管道里没有更好的字节通道）。"""
        if self.store is None:
            return {"ok": False, "error": "未初始化"}
        rec = self.store.take(str(cmd.get("jid") or ""))
        if rec is None:
            return {"ok": True, "rec": None}
        out = {k: v for k, v in rec.items() if k != "payload_zip"}
        payload = rec.get("payload_zip")
        if payload is not None:
            out["payload_b64"] = base64.b64encode(payload).decode("ascii")
        return {"ok": True, "rec": out}

    def drop(self, cmd: dict[str, Any]) -> dict[str, Any]:
        if self.store is not None:
            self.store.drop(str(cmd.get("jid") or ""))
        return {"ok": True}

    def release(self, cmd: dict[str, Any]) -> dict[str, Any]:
        if self.store is not None:
            jid = str(cmd.get("jid") or "")
            self.store.release_handoff(jid or None)
        return {"ok": True}

    def prune(self, _cmd: dict[str, Any]) -> dict[str, Any]:
        if self.store is None:
            return {"ok": False, "error": "未初始化"}
        return {"ok": True, "dropped": int(self.store.prune())}

    def p1(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """父进程的 P1（关键下载/回传）进出等待 ⇒ 按本进程 P1 的口径挡住/打断 P2。"""
        from remote.wire import _BULK  # 调度器单例住 `wire`（本进程与父进程各一份）

        _BULK.foreign_p1(bool(cmd.get("waiting")))
        return {"ok": True}

    def shutdown(self, _cmd: dict[str, Any]) -> dict[str, Any]:
        self._stopf(int(STOP_JOIN_SEC))
        return {"ok": True}

    def close(self) -> None:
        self._stopf(int(STOP_JOIN_SEC))


def _fill_once(engine: _Engine, cmd: dict[str, Any], stop: threading.Event) -> None:
    """跑一轮 `_prefetch_fill`（**唯一实现**在 `job_round`；本函数只做参数搬运与兜底）。"""
    from remote.job_round import _prefetch_fill

    store = engine.store
    if store is None:  # pragma: no cover - `fill` 已挡在前面
        return
    try:
        _prefetch_fill(
            str(cmd.get("base_url") or ""),
            str(cmd.get("token") or ""),
            store,
            stop,
            worker_id=str(cmd.get("worker_id") or ""),
            role=str(cmd.get("role") or "online"),
            depth=int(cmd.get("depth") or 0),
            unpack_n=int(cmd.get("unpack_n") or 0),
            skip={str(x) for x in (cmd.get("skip") or [])},
            log=_log,
        )
    except BaseException as e:  # 填充腿是提前量：崩了也必须把原因说出来，而不是静默消失
        _log(f"★ 填充腿异常退出（{type(e).__name__}: {e}）——下一轮 fill 会重新拉起")


_COMMANDS = {
    "init": _Engine.init,
    "fill": _Engine.fill,
    "stopfill": _Engine.stopfill,
    "take": _Engine.take,
    "drop": _Engine.drop,
    "release": _Engine.release,
    "prune": _Engine.prune,
    "p1": _Engine.p1,
    "shutdown": _Engine.shutdown,
}


def main() -> int:
    """读 stdin 的命令行 → 执行 → 写应答行。EOF（父进程收线/消失）即返回 0。"""
    # 管道编码钉死 utf-8（Windows 控制台是 gb2312；父侧按 utf-8 解码，AGENTS §17.6）。
    for stream in (sys.stdout, sys.stderr, sys.stdin):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    _log(f"下行腿已就绪（pid={os.getpid()}，hub 由父侧逐轮下发）")
    engine = _Engine()
    try:
        for line in sys.stdin:
            s = line.strip()
            if not s:
                continue
            try:
                cmd = json.loads(s)
            except ValueError as e:
                _log(f"WARN 命令行解析失败（忽略）：{type(e).__name__}: {e}")
                continue
            if not isinstance(cmd, dict):
                _log("WARN 命令行不是对象（忽略）")
                continue
            op = str(cmd.get("op") or "")
            fn = _COMMANDS.get(op)
            rid = cmd.get("id")
            if fn is None:
                _reply(rid, {"ok": False, "error": f"未知命令 {op!r}"})
                continue
            try:
                out = fn(engine, cmd)
            except BaseException as e:  # 命令失败不许把整条腿带走（父侧按应答缺省处理）
                _log(f"命令 {op} 失败（{type(e).__name__}: {e}）")
                out = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            _reply(rid, out)
            if op == "shutdown":
                break
    finally:
        engine.close()
    _log("stdin EOF（父进程收线）——下行腿退出 0")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
