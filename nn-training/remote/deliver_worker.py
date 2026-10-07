"""remote/deliver_worker.py —— 产物补传腿的**独立子进程**（2026-10-06 事故的隔离面）。

> 立案与设计：`plan/offline-deliver-isolation.plan.md`（P1）。父侧代理 = `DelivererProcess`。

**为什么要有它**：补传与 PPO 同进程时，这条腿的每一步都压在训练进程的 GIL 上 ——
`_eval_rows_for()` 每投一轮要全量解析 `eval_log.jsonl`（云上实测 33MB）、
`encode_weights_json`/`encode_opt_tar` 要 gzip+base64 ~1.2MB、`json.dumps` 出一个 ~2MB 的体。
搬到独立进程 = 「网络腿抢 GIL/核」这条通道**永久不存在**（这就是本文件存在的全部理由）。

**形态**：`python -m remote.deliver_worker --ctl <…> --status <…> …`（由 `DelivererProcess` 拉起）。
两个输入面，都不是共享内存：

  * **控制通道** = 父侧写的 append-only JSONL（`{"round":it}` 仅唤醒 / `{"repost":it}` /
    `{"final":{…}}` / `{"stop":true,"budget":秒}`）——本进程**增量**读；
  * **产物目录** = 唯一的真相面：`pending()` 的定义就是「磁盘上有、账本里没有」
    （`remote/artifacts.py`），所以重启/换会话都能原地接手。

**纪律**（写在这里免得后来者「顺手」破坏）：

  * 每个 tick 有**预算**（几轮 / 几秒）：隧道一恢复就连发几十个 ~1.9MB POST 会把 2–4 核的云机
    反过来拖住（`common/platform_utils.cpu_worker_slots`：「≤4 核全给」的正是这种机器）；
    **段末 flush 例外**（那时本来就在收线，预算放宽到 `stop.budget`）。
  * **`final` 必须排在 `stop` 之前**尝试一次（对齐线程语义：`if final is not None: …` 在
    `elif stopping: return` 之前）——反过来就会把最该送出去的那份摘要丢掉。
  * **idle-timeout 是纯兜底**：`无新控制行 ≥ idle` **且** `pending()` 空 **且** 无未送达重投才退
    （默认 1800s = 最长轮 700s 的 2.5 倍以上）。它不承担生命周期管理——那件事由 stdin EOF 与
    父侧按需重启负责。
  * **stdin EOF = 父进程没了**（阻塞读线程；**不用 `selectors`**：Windows 的 selectors 只认 socket）。
  * 日志一律 stdout 一行（父侧的转发线程加前缀 + 时间戳）；退出码 0 = 干净，非 0 = 异常
    （父侧负责**响亮记录**）。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

from remote.artifacts import atomic_write_json
from remote.offline_deliver import DRAIN_FLUSH_SEC, DRAIN_TICK_SEC, OfflineDeliverer

#: idle-timeout 缺省（秒）：纯兜底（见模块 docstring）。
IDLE_TIMEOUT_SEC = 1800.0
#: 正常 tick 的预算：一次 tick 最多推这么多轮 / 这么多秒（把「隧道恢复后的追赶」摊到几个 tick）。
TICK_ROUNDS = 8
TICK_SECONDS = 20.0
#: 收线（`stop`）时的 flush 上界（秒）；父侧给的 `budget` 更小就用父侧的。
STOP_SECONDS_MAX = DRAIN_FLUSH_SEC


def _log(msg: str) -> None:
    """子进程的日志 = stdout 一行（父侧的转发线程会给它加 `[deliver]` 前缀与时间戳）。"""
    print(msg, flush=True)


class CtlReader:
    """控制文件（append-only JSONL）的**增量**读取。

    两条纪律（plan §3.5 的 C4/C15）：

      * **残余字节留在缓冲区，绝不丢**：父侧可能正好写在半行上，而 `{"final": …}` 完全可能
        > 4KB（POSIX 单次 `write` 的原子边界只有 ~4KB）——把半行当「坏行」跳过去，剩下那半个
        JSON 就**永远拼不回来**（段末摘要永久丢失）。所以只有见到 `\\n` 才切行，尾巴留到下一 tick。
      * `offset` 可以由父侧在**重启时**传进来（`--ctl-offset`）：否则重启会从 0 重放本会话所有
        `repost`——而每一个都是一次真 POST。
    """

    def __init__(self, path: str | Path, offset: int = 0) -> None:
        self.path = Path(path)
        self.offset = max(0, int(offset))
        self.buf = b""

    def poll(self) -> list[dict]:
        """读到当前文件末尾，返回**解析成功的整行**（永不抛：读不到 = 空列表）。"""
        try:
            with self.path.open("rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except OSError:
            return []
        if not chunk:
            return []
        self.offset += len(chunk)  # 文件位置照常推进；**未成行**的字节留在 buf 里
        head, sep, tail = (self.buf + chunk).rpartition(b"\n")
        if not sep:
            self.buf = self.buf + chunk
            return []
        self.buf = tail
        out: list[dict] = []
        for raw in head.split(b"\n"):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                _log(f"控制行解析失败（忽略这一行，不推进语义）：{raw[:120]!r}")
                continue
            if isinstance(obj, dict):
                out.append(obj)
        return out


def _watch_stdin(parent_gone: threading.Event) -> None:
    """父进程没了 ⇒ 置位（阻塞读 stdin 到 EOF；**不用 `selectors`**，见模块 docstring）。"""
    try:
        stream = getattr(sys.stdin, "buffer", None)
        if stream is None:
            return
        while True:
            try:
                chunk = stream.read(4096)
            except Exception:
                break
            if not chunk:
                break
    except Exception:
        pass
    parent_gone.set()


def _write_status(path: Path, d: OfflineDeliverer, *, ctl: CtlReader, last_ctl_at: float) -> None:
    """子进程 → 父侧的**状态面**（plan §10.2）：父侧 `status()` 只读它，不猜。

    1Hz 全量覆盖（小文件）；写不进去不致命（父侧会看到旧值/缺失，属于「降级但可见」）。
    """
    try:
        st = d.status()
        atomic_write_json(
            path,
            {
                "pid": os.getpid(),
                "updated_at": time.time(),
                "updated_mono": time.monotonic(),
                "ctl_offset": int(ctl.offset),
                "last_ctl_at": float(last_ctl_at),
                "enabled": bool(st.get("enabled")),
                "disabled_reason": str(st.get("disabled_reason", "") or ""),
                "delivered": int(st.get("delivered", 0)),
                "pending": int(st.get("pending", 0)),
                "reposts_unsent": int(st.get("reposts_unsent", 0)),
                "rejected": st.get("rejected") or {},
                "result_done": bool(st.get("result_done")),
                "ticks": int(st.get("ticks", 0)),
                "idle_spins": int(st.get("idle_spins", 0)),
            },
        )
    except Exception as e:  # 状态面坏掉不该拖垮补传本身
        _log(f"状态文件写失败（忽略）：{type(e).__name__}: {e}")


def build_argparser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="remote.deliver_worker",
        description="产物补传腿的独立子进程（由 remote.offline_deliver.DelivererProcess 拉起）",
    )
    ap.add_argument("--ctl", required=True, help="控制文件（append-only JSONL；父侧写）")
    ap.add_argument(
        "--ctl-offset",
        type=int,
        default=0,
        help="从控制文件的第几个字节开始读（父侧重启子进程时传已写字节数，防重放）",
    )
    ap.add_argument("--status", required=True, help="状态文件（本进程写；父侧读）")
    ap.add_argument("--artifacts", required=True, help="产物目录（唯一真相面）")
    ap.add_argument("--hub-url", required=True, help="hub 地址")
    ap.add_argument("--run-id", required=True, help="run_id（hub 侧目录名）")
    ap.add_argument("--course", default="", help="hub 侧课程键（多课程 hub 必需；空 = 单课程）")
    ap.add_argument(
        "--idle-timeout",
        type=float,
        default=IDLE_TIMEOUT_SEC,
        help=f"纯兜底：无新控制行 + 无积压 + 无未送达重投满这么久 ⇒ 退出（缺省 {IDLE_TIMEOUT_SEC:g}s）",
    )
    ap.add_argument("--tick-sec", type=float, default=DRAIN_TICK_SEC, help="tick 间隔（秒）")
    return ap


def main(argv: list[str] | None = None) -> int:
    """每 tick：读控制行 → 按预算推积压 → 落状态。**永不把异常带出去**（退出码说话）。"""
    args = build_argparser().parse_args(argv)
    # token **只从环境变量读**（argv 里会出现 `/proc/<pid>/cmdline` 上，全机可读）。
    token = (os.environ.get("BATTLE_HUB_TOKEN") or "").strip()
    ctl = CtlReader(args.ctl, args.ctl_offset)
    status_path = Path(args.status)
    d = OfflineDeliverer(
        base_url=args.hub_url,
        token=token,
        run_id=args.run_id,
        artifacts_dir=args.artifacts,
        course=args.course,
        # 子进程里没有「训练线程」可并行 —— 它就是那条腿，同步语义即正确语义。
        background=False,
        log=_log,
    )
    tick = max(0.05, float(args.tick_sec))
    idle = max(60.0, float(args.idle_timeout))
    parent_gone = threading.Event()
    threading.Thread(
        target=_watch_stdin, args=(parent_gone,), name="deliver-stdin", daemon=True
    ).start()
    last_ctl_at = time.monotonic()
    stop_deadline: float | None = None
    stop_budget = 0.0
    saw_final = False
    reason = "idle"
    st0 = d.status()
    _log(
        f"补传腿已就绪（pid={os.getpid()}，产物 {d.root}，hub {d.base_url or '(未配置)'}，"
        f"已投递 {st0['delivered']} 轮 / 待投递 {st0['pending']} 轮 / 欠重投 {st0['reposts_unsent']}）"
    )
    # ★ boot 握手：父侧靠这一行判「真的活着」（`Popen` 成功 ≠ 能 import）。
    print(f"boot ok {os.getpid()}", flush=True)
    try:
        while True:
            for cmd in ctl.poll():
                last_ctl_at = time.monotonic()
                # ① `{"round": it}` = **仅唤醒**：本进程每 tick 都会重扫磁盘，不需要别的语义。
                if isinstance(cmd.get("repost"), int):
                    d.mark_repost(int(cmd["repost"]))
                final = cmd.get("final")
                if isinstance(final, dict):
                    saw_final = True
                    summary = final.get("summary")
                    d.deliver_result(
                        it_end=int(final.get("it_end", 0) or 0),
                        state=str(final.get("state", "") or ""),
                        summary=summary if isinstance(summary, dict) else None,
                        end_it_reached=bool(final.get("end_it_reached")),
                    )
                if cmd.get("stop"):
                    stop_budget = max(0.0, min(float(cmd.get("budget") or 0.0), STOP_SECONDS_MAX))
                    stop_deadline = time.monotonic() + stop_budget
                    reason = "stop"
                    break  # 收到的命令按文件顺序生效 ⇒ final 已经在 stop 之前尝试过了
            # ⚠ 收到 `stop` 之后就不再理会 EOF：父侧 `close()` 的收线动作**就是**先写 stop 再关 stdin
            #（顺序反过来会把段末 flush 整个吃掉——2026-10-07 慢层用例当场抓到过：子进程看到
            # 「父进程已消失」就退出，一行都没推）。EOF 的真义是「父进程**意外**没了」。
            if parent_gone.is_set() and stop_deadline is None:
                _log("父进程已消失（stdin EOF）——收线退出（产物照常落本地）")
                reason = "parent-gone"
                break
            if stop_deadline is not None and time.monotonic() >= stop_deadline:
                break
            # 预算：正常 tick 限轮数/秒数；收线时放宽到 `stop.budget` 耗尽为止。
            rounds = 0
            until = time.monotonic() + (
                TICK_SECONDS if stop_deadline is None else max(0.0, stop_deadline - time.monotonic())
            )
            while True:
                n = d.sync(force_probe=stop_deadline is not None)
                if n <= 0:
                    break
                rounds += n
                if stop_deadline is None and rounds >= TICK_ROUNDS:
                    break
                if time.monotonic() >= until:
                    break
            _write_status(status_path, d, ctl=ctl, last_ctl_at=last_ctl_at)
            if (
                stop_deadline is None
                and not parent_gone.is_set()
                and time.monotonic() - last_ctl_at >= idle
                and not d.pending()
                and not d.unsent_reposts()
            ):
                reason = "idle"
                break
            if stop_deadline is not None:
                # 收线：**有活就接着推**（不等 tick）；这一趟什么也推不动（积压空 / 探活不通）就收线
                # 结束——`budget` 是**上界**不是目标（与线程侧的 `_push_backlog` 同语义：
                # 它也是「一趟推不动就返回」）。既不许空转，也不许为等满预算而挂着不退。
                if rounds == 0:
                    break
                continue
            # sleep-ok: 轮询步长（等的是「控制文件里有没有新行」这个状态；超时只当挂起兜底）
            time.sleep(tick)
    except Exception as e:  # 任何意外都走退出码（父侧负责响亮记录）
        _log(f"补传子进程异常退出：{type(e).__name__}: {e}")
        _write_status(status_path, d, ctl=ctl, last_ctl_at=last_ctl_at)
        return 1
    if stop_deadline is not None and not saw_final:
        _log("收到 stop 但本会话没见过 final（段末摘要没提交？）——按「没送出去」处理")
    if reason == "idle":
        _log(f"idle-timeout（{idle:g}s 无新控制行 + 无积压 + 无欠重投）——退出 0（父侧按需会重新拉起）")
    elif reason == "stop":
        _log(f"段末收线完成（预算 {stop_budget:g}s）——退出 0")
    d.close(0.0)  # 段末核对：还有没送达的重投 / 摘要没送到 ⇒ 响亮一行（同步模式只是核对，不等）
    _write_status(status_path, d, ctl=ctl, last_ctl_at=last_ctl_at)
    return 0


if __name__ == "__main__":
    sys.exit(main())
