"""remote/offline_push.py — 离线 push 的 **supervisor**（2026-10-23，W4）。

一句话：这个进程把「hub 能来拉的一切」摆好，训练本身跑在子进程里。

    notebook（`battle.offline` mode=push）
      └─ run_push(CFG, log)
           ├─ PushFaceState + HTTP 服务（`remote/offline_push_face`，8 端点）
           ├─ cloudflared quick tunnel（`remote/push_tunnel`）→ 稳定 URL
           ├─ Registry（`remote/push_register`，kind=offline_push）→ hub 按能力键派发
           └─ 收到任务 → 子进程 `remote.offline_push_task`
                          └─ offline_boot.run_one_course(push_mode=True)
                               └─ run_loop --outbox <dir> --no-deliver --stop-file <f>
                                    └─ LocalOutboxDeliverer 写 rounds/metrics/final

为什么不把训练跑在本进程：服务端要**一直答得上话**（`/state` 是 hub 的版本门与 busy 判据），
而训练会吃满 CPU、注册进度钩子、解 `code.zip`；一次 OOM 或 `SystemExit` 就把状态面一起带走。
子进程还让「取消」有一个干净落点（写 stop 文件 → 轮边界停）。

失败面（都只影响「这台机器还能不能接活」，不影响已经跑完的产物）：

  * cloudflared 找不到/装不上 ⇒ 返回 -2（与 GPU push 同一个返回码语义）；
  * 隧道 URL 拿不到 ⇒ 继续跑本机服务并打**可粘贴**的手工登记片段（URL 到手后会自动注册）；
  * hub 不认 `kind` / 没有 `capabilities` ⇒ `Registry` 的既有 WARN（不假装成功）；
  * 子进程异常退出 ⇒ `/offline/push/state` 报 `failed` + run.log 尾巴，hub 拉走已完成的轮次。
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common.env_probe import probe_cloud_env
from common.logutil import tag_logger
from common.proc import POPEN_NO_WINDOW
from remote.offline_push_face import PushFaceState, make_face_server
from remote.push_register import (
    Registry,
    default_node_id,
    dispatch_mount_note,
    probe_offline_dispatch,
    register_or_warn,
)
from remote.push_tunnel import find_cloudflared, start_quick_tunnel, stop_proc, tunnel_url_of

#: 每拍（秒）：刷新隧道 URL / 重注册 / 打一行状态。hub 的派发拍是 10s —— 比它快没必要
#: （注册是幂等的），比它慢会让「隧道换名」后多等一拍。
TICK_SEC = 15.0
#: 取消时给子进程的收线时间（轮边界停：当前轮可能还在跑，太短会把它砍成半截）。
CANCEL_GRACE_SEC = 120.0
#: 不写进 `cfg.json` 的键（子进程不连 hub，凭据不该出现在它的环境/盘上）。
_SECRET_KEYS = ("hub_token", "push_token", "ts_authkey", "ts_auth_key", "auth_key")


#: 缺省日志（`tag_logger` + 本模块自己的 `time`；与 `remote/*` 其余模块不重名）。
_log_push = tag_logger("offline-push", lambda: time)


def redacted_cfg(cfg: dict) -> dict:
    """`cfg.json` 的内容：去掉凭据，其余原样（device/线程/评估/目录）。

    为什么把 CFG 传给子进程而不是在子进程里重算：device 的解析（`auto` → cuda/tpu/cpu）、
    `max_iters`、`eval_on_cloud` 都是**人在这台机器上**的选择；子进程重算会让「服务端认为的
    参数」与「训练真正用的参数」分成两份（plan §6.4 要求同一条链）。
    """
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        if str(k).lower() in _SECRET_KEYS:
            continue
        if isinstance(v, (str, int, float, bool)) or v is None or isinstance(v, (list, tuple, dict)):
            out[str(k)] = v
        # 其余（callable / 对象）不落盘：子进程拿到也没用，反而可能不是 JSON
    return out


class _Child:
    """当前训练子进程的句柄（一个任务一个；退出后保留 rc 供 `/state` 读）。"""

    def __init__(self) -> None:
        self.proc: subprocess.Popen[bytes] | None = None
        self.task_id = ""
        self._lock = threading.Lock()

    def running(self) -> bool:
        with self._lock:
            return self.proc is not None and self.proc.poll() is None

    def spawn(
        self,
        argv: list[str],
        *,
        log_path: Path,
        env: dict[str, str],
        on_exit: Callable[[int], None],
        task_id: str,
        log: Callable[[str], None],
    ) -> None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_f = open(log_path, "a", encoding="utf-8")
        try:
            proc = subprocess.Popen(
                argv, stdout=log_f, stderr=subprocess.STDOUT, env=env, cwd=str(log_path.parent),
                **POPEN_NO_WINDOW,
            )
        finally:
            log_f.close()
        with self._lock:
            self.proc = proc
            self.task_id = task_id
        log(f"训练子进程已启动（pid={proc.pid}，task={task_id}，日志 {log_path}）")

        def _wait() -> None:
            rc = int(proc.wait())
            with self._lock:
                self.proc = None
            on_exit(rc)

        threading.Thread(target=_wait, name=f"push-child-{task_id}", daemon=True).start()

    def request_stop(self, *, stop_file: Path, grace: float = CANCEL_GRACE_SEC) -> None:
        """写 stop 文件 → 有界等 → 还在就 terminate（产物在轮边界已是自洽的）。

        没有在跑的子进程 ⇒ **一个字节都不写**：`tasks/<id>/` 是跨重启活着的，留一个 `stop`
        标记会让 hub 重新派来的同一单在第一个轮边界自己取消（收摊时最容易踩）。
        """
        if not self.running():
            return
        try:
            stop_file.parent.mkdir(parents=True, exist_ok=True)
            stop_file.write_text("shutdown\n", encoding="utf-8")
        except OSError:
            pass
        deadline = time.time() + max(0.0, float(grace))
        while time.time() < deadline:
            with self._lock:
                proc = self.proc
            if proc is None or proc.poll() is not None:
                return
            time.sleep(min(2.0, max(0.1, deadline - time.time())))
        stop_proc(self.proc)


def _build_child_env() -> dict[str, str]:
    env = dict(os.environ)
    # 代码目录：与当前进程同一份（notebook 已把 `/tmp/worker-code` 铺进 sys.path）。
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def run_push(
    cfg: dict,
    log: Callable[[str], None] = _log_push,
    *,
    keepalive_stop: Any = None,
    face_state: PushFaceState | None = None,
    registry: Registry | None = None,
    tick_sec: float = TICK_SEC,
    session_budget_sec: float | None = None,
) -> int:
    """跑离线 push 服务（阻塞直到会话到顶/中断）。返回 0 = 干净收摊。

    `face_state` / `registry` 是测试注入点（生产 = 本函数自己装配）。
    """
    work = Path(str(cfg.get("work_dir") or "/tmp/offline-push")).expanduser()
    work.mkdir(parents=True, exist_ok=True)
    # `0` 是**合法的**（内核挑一个空闲端口；用例靠它并行跑）：只有缺省/空串才回 8791。
    _raw_port = cfg.get("push_port")
    port = 8791 if _raw_port is None or str(_raw_port).strip() == "" else int(_raw_port)
    push_token = str(cfg.get("push_token") or "").strip()
    if not push_token or push_token.startswith("YOUR_"):
        # 与 GPU push 同口径：没填就现生成（hub 的手工登记片段需要它）。
        push_token = secrets.token_hex(16)
        cfg = {**cfg, "push_token": push_token}
        log(f"push_token 未配置 —— 本次会话生成：{push_token}（hub 登记时要用它）")

    state = face_state or PushFaceState(work_dir=work / "face", log=log)
    hub_base = str(cfg.get("registry_url") or cfg.get("hub_url") or "").strip()
    reg = registry or Registry(
        hub=hub_base,
        hub_token=str(cfg.get("hub_token") or "").strip(),
        node_id=str(cfg.get("node_id") or "").strip()
        or default_node_id("offline_push", probe_cloud_env()),
        kind="offline_push",
        label=str(cfg.get("label") or "offline-push"),
        log=log,
    )
    log(f"节点身份：id={reg.node_id} kind=offline_push（hub 按能力键派发）")

    server = make_face_server(state, port, push_token, log=log)
    # 隧道要指向**实际绑上的**端口（`push_port=0` 时与入参不同）。
    port = int(server.server_address[1])
    threading.Thread(target=server.serve_forever, name="offline-push-face", daemon=True).start()
    log(f"worker 服务已监听 :{port}（端点 /offline/push/*，Bearer 鉴权）")

    child = _Child()

    def _starter(task: dict, task_dir: Path, outbox: Path) -> None:
        """任务受理后：写 cfg.json、起训练子进程。"""
        cfg_path = task_dir / "cfg.json"
        try:
            cfg_path.write_text(
                json.dumps(redacted_cfg(cfg), ensure_ascii=False), encoding="utf-8"
            )
        except OSError as e:
            log(f"WARN 写 {cfg_path} 失败（{type(e).__name__}: {e}）——子进程会缺参数")
        argv = [
            sys.executable,
            "-u",
            "-m",
            "remote.offline_push_task",
            "--task-dir",
            str(task_dir),
            "--outbox",
            str(outbox),
            "--stop-file",
            str(task_dir / "stop"),
            "--cfg",
            str(cfg_path),
        ]
        try:
            child.spawn(
                argv,
                log_path=task_dir / "run.log",
                env=_build_child_env(),
                on_exit=state.note_child_exit,
                task_id=str(task.get("task_id") or ""),
                log=log,
            )
        except OSError as e:
            log(f"训练子进程起不来（{type(e).__name__}: {e}）")
            state.note_child_exit(-1)

    state.set_starter(_starter)

    # ── 隧道 + 注册 ──
    cf_log = work / "cloudflared.log"
    cf_bin = find_cloudflared(str(cfg.get("cloudflared_path") or ""), log=log)
    if not cf_bin:
        server.shutdown()
        server.server_close()
        return -2
    cf_proc, cf_url = start_quick_tunnel(cf_bin, port, cf_log, log=log)
    if cf_url:
        log(f"★ 隧道 URL: {cf_url}")
        if _register_or_warn(reg, cf_url, push_token, log):
            # 注册成功 ≠ 会派单：hub 没挂 `--offline-push` 时它会收下节点、写进 nodes[]，
            # 然后一直不派（2026-10-24 真机）。这一句把「我在等活」与「没人能派活」分开。
            _declare_dispatch_mount(reg, log)
    else:
        log(
            f"⚠ 隧道 URL 未取得（超时/出错）—— hub 推送将不可达；日志见 {cf_log}。"
            "本机服务仍在跑，URL 到手后会自动注册"
        )

    budget = (
        float(session_budget_sec)
        if session_budget_sec is not None
        else float(cfg.get("session_budget_sec") or 0.0)
    )
    deadline = time.time() + budget if budget > 0 else 0.0
    t0 = time.time()
    last_tick = 0.0
    last_seq = -1
    last_phase = ""
    try:
        while True:
            if keepalive_stop is not None and bool(keepalive_stop.is_set()):
                log("会话停机哨兵已命中 —— 干净收摊")
                break
            if deadline and time.time() > deadline:
                log(f"会话到顶（session_budget_sec={budget:.0f}s）——干净收摊")
                break
            now = time.time()
            if now - last_tick >= float(tick_sec):
                # 隧道换名（quick tunnel 重连）⇒ 重注册；未换 ⇒ 只在未注册时重试（Registry 自己退避）。
                url = tunnel_url_of(cf_log) or cf_url
                if url and url != cf_url:
                    log(f"★ 隧道换名：{cf_url or '?'} → {url} —— 重注册")
                    cf_url = url
                if cf_url:
                    reg.ensure(cf_url, push_token)
                snap = state.snapshot()
                phase = str(snap.get("phase") or "")
                prog = state.progress(last_seq)
                seq = int(prog.get("seq", 0) or 0)
                if seq > last_seq and not prog.get("unchanged"):
                    last_seq = seq
                    log(
                        f"进度 seq={seq} {prog.get('kind') or ''} it{prog.get('it', 0)}"
                        f" {prog.get('done', 0)}/{prog.get('total', 0)}"
                    )
                if phase != last_phase or phase in ("running", "cancelling"):
                    task = snap.get("task") or {}
                    log(
                        f"任务状态: phase={phase} course={task.get('course') or '-'}"
                        f" busy={snap.get('busy')} wait={int(now - t0)}s"
                        + (f" rc={snap.get('rc')}" if snap.get("rc") is not None else "")
                    )
                    last_phase = phase
                elif int(now - t0) % 60 < int(tick_sec):
                    log(f"守候中… phase={phase} wait={int(now - t0)}s")
                last_tick = now
            time.sleep(1.0)
    except KeyboardInterrupt:
        log("收到中断")
    finally:
        reg.unregister()
        task_dir = state.task_dir
        if task_dir is not None:
            child.request_stop(stop_file=task_dir / "stop")
        stop_proc(cf_proc)
        server.shutdown()
        server.server_close()
        log("offline push 服务已收摊")
    return 0


def _register_or_warn(reg: Registry, url: str, push_token: str, log: Callable[[str], None]) -> bool:
    """首拍注册 + 人话横幅；失败给**可粘贴**的手工片段（与 GPU push 同一口径）。

    实现住在 `remote.push_register.register_or_warn`（**唯一一份**）：两条腿的差别只有 kind，
    而「注册失败该等多久 / 422 该怎么说」这种话术最容易被抄错一次就永远不同步。
    """
    return register_or_warn(reg, url, push_token, log)


def _declare_dispatch_mount(reg: Registry, log: Callable[[str], None]) -> None:
    """注册成功之后**再问一句**：hub 到底有没有派发器（没挂 = 本会话永远收不到派单）。

    2026-10-24 真机：worker 侧一切正常（`★ 已自动注册` + `phase=idle`），而 hub 的 argv 里
    没有 `--offline-push` ⇒ 派发器恒为 None ⇒ 云机上只剩「守候中…」。这条链上**没有任何
    东西**会从 worker 的日志里看出「不是我在等活，是没人能派活」，所以这一句是必需的。
    """
    mounted = probe_offline_dispatch(
        str(getattr(reg, "hub", "") or ""), str(getattr(reg, "hub_token", "") or "")
    )
    note = dispatch_mount_note(mounted)
    if note:
        log(note)
    elif mounted:
        log("★ hub 的离线 push 派发器在（/admin/offline-push-workers 200）——等它按能力键派单")


__all__ = ["redacted_cfg", "run_push"]
