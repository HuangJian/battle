"""remote/worker_server.py — push 模式 GPU 侧服务端（2026-09-05，DECISIONS §340）。

HUB 推架构（方向翻转）：cloudflared 跑在 GPU 机器上，HUB 作为出站客户端把 PPO
job（manifest + payload.zip + code.zip）POST 到本服务端；本机后台跑 run_job 全套
（sha 校验 → 解包 → D14 → PPO → 结果），HUB 轮询取结果。弱链路（edge↔cloudflared）
落在 Kaggle 网络，HUB 只做出站 HTTPS——不再依赖 HUB 侧隧道。

端点（全部 Bearer token 鉴权）：
  GET  /ping                 存活 + 忙闲 + 排队深度 queued
  GET  /code-sha?sha=X       本机 code 缓存是否已有 sha=X 的代码（HUB 决定是否随 job 上传）
  POST /job                  {manifest, payload_b64, code_b64?} → 202 受理（后台 PPO）
                              202 queued（在跑：入队，position 告知排位）
                              202 幂等（同 jid 重发：不重复 spawn）
                              409 busy（仅队满 WORKER_QUEUE_MAX=8）| 428 code-missing | 400 校验失败
  GET  /job/{id}/status      {"state": "running" | "done" | "failed" | "unknown"}
  GET  /job/{id}/result      200 结果 JSON（幂等可重复读）| 202 在跑 | 500 失败 | 404 未知

运行（GPU 机器，venv 含 torch）：
  python -m remote_worker_serve --port 8790 --token <token>
  cloudflared tunnel --url http://localhost:8790
"""

from __future__ import annotations

import atexit
import base64
import hashlib
import json
import os
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from remote._instance_lock import (
    acquire_instance_lock,
    default_instance_lock_path,
    release_instance_lock,
)
from remote._port_guard import ensure_port_free
from remote.protocol import (
    WIRE_JOB_MAGIC,
    CodeChangedError,
    ProtocolError,
    normalize_manifest,
    unpack_job_v2,
)
from remote.worker import _wire_flush as worker_wire_flush
from remote.worker import _wire_start as worker_wire_start
from remote.worker import run_job

AUTH_HEADER = "Authorization"

#: submit/kick 起后台线程的回调（state 构造后由 make 侧注入）。
Starter = Callable[[str, dict], None]

#: 共享 worker 有界 FIFO 上界（plan P3b §3.8）。单 GPU 一次只跑一个，多的排队；
#: 队满才 409（常态队深 ≤2；频发 409 → 加 worker/降课数，不放大队列）。
WORKER_QUEUE_MAX = 8


class WorkerServerState:
    """job 状态表（单 GPU 串行：同一时刻至多一个在跑，多出的进有界 FIFO）。

    多课程共享（plan P3b §3.8）：N:1 共享时 A 课跑、B 课排队——排队代替 409，
    worker 零空闲（流水线 ≈ R + 2P）。队满才 409；同 jid 重发幂等（超时重试
    不重复 spawn，顺带修旧 busy-409 下的重传风暴）。
    """

    def __init__(self, work_dir: Path) -> None:
        self.work_dir = work_dir
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.jobs: dict[str, dict] = {}  # jid -> {"state", "result"?, "error"?}
        self.done_total = 0
        self._queue: deque[str] = deque()  # 等待执行的 jid（FIFO）
        self._pending: dict[str, dict] = {}  # jid -> {manifest, payload_zip, code_zip, echo}
        self._starter: Starter | None = None  # make 侧注入
        #: jid -> 取消 Event（push 腿 landed 取消的**节点侧落点**，R2-4b）：hub 推一帧
        #: `POST /job/{id}/cancel` 置位，run_job 在 epoch 边界查到就抛 JobCancelledError。
        self._cancel: dict[str, threading.Event] = {}

    def code_cached(self, code_sha256: str) -> bool:
        return (self.work_dir / "code_cache" / code_sha256).exists()

    def blob_cached(self, sha: str) -> bool:
        """M2 B3：内容寻址 blob（opt/ref raw）是否已在本地缓存。

        与 worker.run_job 的 blob_root 同根（code_cache 的兄弟目录）——命中即免上传。
        """
        return bool(sha) and (self.work_dir / "blob_cache" / sha).exists()

    def ts_code_cached(self, ts_sha256: str) -> bool:
        """M3：TS 运行时 zip 是否已在本地缓存（与 worker.run_job 的 ts_code_cache 同根）。

        命中即免上传——TS 代码在训练中极少变，sha 不变则整段腿只传一次。
        """
        return bool(ts_sha256) and (self.work_dir / "ts_code_cache" / ts_sha256).exists()

    def set_state(self, jid: str, state: str) -> None:
        with self._lock:
            self.jobs[jid] = {"state": state}

    def set_result(self, jid: str, result: dict) -> None:
        with self._lock:
            self.jobs[jid] = {"state": "done", "result": result}
            self.done_total += 1

    def set_error(self, jid: str, error: str, kind: str = "") -> None:
        """标为 failed（终局）。kind = 异常类名，供 `/job/{id}/result` 的 410 体带上
        （2026-09-17：训练侧据此区分「确定性能力缺失」与「网络/排队」）。"""
        with self._lock:
            prev = self.jobs.get(jid, {})
            self.jobs[jid] = {
                "state": "failed",
                "error": error,
                "kind": kind,
                **{k: v for k, v in prev.items() if k == "result"},
            }

    def cancel_event(self, jid: str) -> threading.Event:
        """该 job 的取消 Event（幂等创建）——`_execute_job` 与 `/job/{id}/cancel` 共用一份。"""
        with self._lock:
            ev = self._cancel.get(jid)
            if ev is None:
                ev = threading.Event()
                self._cancel[jid] = ev
            return ev

    def request_cancel(self, jid: str) -> bool:
        """置该 job 的取消 Event（幂等）。返回是否**已知**该 jid（未知也置位）。"""
        with self._lock:
            known = jid in self.jobs or jid in self._pending
        self.cancel_event(jid).set()
        return known

    def is_cancelled(self, jid: str) -> bool:
        with self._lock:
            ev = self._cancel.get(jid)
        return bool(ev is not None and ev.is_set())

    def get(self, jid: str) -> dict | None:
        with self._lock:
            return self.jobs.get(jid)

    def busy(self) -> bool:
        with self._lock:
            return any(j["state"] == "running" for j in self.jobs.values())

    def queued(self) -> int:
        """排队深度（/ping 可观测；console 冒烟沿用）。"""
        with self._lock:
            return len(self._queue)

    def set_starter(self, starter) -> None:
        self._starter = starter

    def submit(self, jid: str, item: dict) -> dict:
        """提交 job：已知 jid → 幂等（不重复 spawn）；空闲 → 立即起跑；
        忙 → 入队（返回 position）；队满 → 满（调用方回 409）。

        返回 {"action": "run"|"queue"|"duplicate"|"full", "position"?: int}。
        """
        with self._lock:
            if jid in self.jobs or jid in self._pending:
                return {"action": "duplicate"}
            if self._queue or any(j["state"] == "running" for j in self.jobs.values()):
                if len(self._queue) >= WORKER_QUEUE_MAX:
                    return {"action": "full"}
                self._pending[jid] = item
                self._queue.append(jid)
                return {"action": "queue", "position": len(self._queue)}
            self.jobs[jid] = {"state": "running"}
            starter, pending_item = self._starter, item
        assert starter is not None, "starter 未注入（make_worker_server 负责）"
        # 锁外起线程：spawn 不阻塞，但启动路径不持锁最干净。
        starter(jid, pending_item)
        return {"action": "run"}

    def kick(self) -> None:
        """一个 job 终结（成功/失败都调——失败不堵队）后拉起队首。"""
        with self._lock:
            if any(j["state"] == "running" for j in self.jobs.values()):
                return
            while self._queue:
                jid = self._queue.popleft()
                item = self._pending.pop(jid, None)
                if item is None:
                    continue
                self.jobs[jid] = {"state": "running"}
                starter = self._starter
                break
            else:
                return
        assert starter is not None, "starter 未注入（make_worker_server 负责）"
        starter(jid, item)


def _execute_job(
    state: WorkerServerState,
    manifest: dict,
    payload_zip: bytes,
    code_zip: bytes | None,
    ts_code_zip: bytes | None,
    blobs: dict | None,
    work_dir: Path,
    device: str,
    torch_threads: int,
    echo: bool,
    log=lambda msg: print(f"[{time.strftime('%H:%M:%S')}] [worker-serve] {msg}", flush=True),
) -> None:
    """后台执行：run_job 全套（preloaded push 路径）→ 状态落表。异常进 failed（HUB 可见）。"""
    jid = manifest["job_id"]
    state.set_state(jid, "running")
    worker_wire_start(jid)  # 阶段占比（in/out/ppo/other）的 wall 从开跑起算
    try:
        result = run_job(
            "",
            "",
            {"job_id": jid, "manifest": manifest},
            work_dir=work_dir,
            device=device,
            torch_threads=torch_threads,
            echo=echo,
            preloaded={
                "payload_zip": payload_zip,
                "code_zip": code_zip,
                # M3：kind=iter 的 TS 运行时（其余 job 恒 None → 键缺席，run_job 不读）。
                "ts_code_zip": ts_code_zip,
                # M2 B3：随 body 上传的内容寻址 blob（仅缓存未命中的那些）。
                "blobs": blobs or {},
            },
            log=log,
            # landed 取消（R2-4b）：hub 推的取消帧置 Event → run_job 在 epoch 边界停。
            should_cancel=lambda: state.is_cancelled(jid),
        )
        state.set_result(jid, result)
        log(f"job {jid} done — result ready for pickup")
    except CodeChangedError as e:
        # push 模式是长驻服务进程：execv 自重启会打断在跑的 job —— 只能拒收 + 喊人。
        state.set_error(jid, f"{type(e).__name__}: {e}", kind=type(e).__name__)
        log(f"job {jid} REJECTED: {e}")
        log("  → 代码已变更：本服务进程无法热替换。请**重启 worker_server 进程**"
            "（notebook 重跑 push 单元格 / 本机重跑 remote_worker_serve），再重发本 job。")
    except Exception as e:
        state.set_error(jid, f"{type(e).__name__}: {e}", kind=type(e).__name__)
        log(f"job {jid} FAILED: {e}")
    finally:
        # 每 job 一行传输账（push 模式不跑 pull 循环 ⇒ 没有别的 flush 点）：
        # preloaded 命中 / blob 下载的 (bytes, sec) 都在这里可见。
        worker_wire_flush(jid, log)
        state.kick()  # 失败不堵队：队首立即顶上（流水线无间隙）


def make_worker_server(
    state: WorkerServerState,
    port: int,
    token: str,
    *,
    device: str = "cpu",
    torch_threads: int = 0,
    host: str = "0.0.0.0",
) -> ThreadingHTTPServer:
    def log(msg: str) -> None:
        print(f"[{time.strftime('%H:%M:%S')}] [worker-serve] {msg}", flush=True)

    class Handler(BaseHTTPRequestHandler):
        def _auth_ok(self) -> bool:
            auth = self.headers.get(AUTH_HEADER, "")
            return secrets.compare_digest(auth, f"Bearer {token}")

        def _json(self, obj: dict, status: int = 200) -> None:
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, fmt: str, *args) -> None:  # 静默默认访问日志
            pass

        def do_GET(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                if not self._auth_ok():
                    self._json({"error": "unauthorized"}, 401)
                    return
                if path == "/ping":
                    self._json(
                        {
                            "ok": True,
                            "pid": os.getpid(),
                            "busy": state.busy(),
                            "queued": state.queued(),
                            "done": state.done_total,
                        }
                    )
                elif path == "/code-sha":
                    qs = parse_qs(urlparse(self.path).query)
                    sha = (qs.get("sha") or [""])[0]
                    self._json({"sha": sha, "cached": state.code_cached(sha)})
                elif path == "/ts-code-sha":
                    # M3：与 /code-sha 同规 —— hub 侧据此判要不要随 body 传 TS 运行时。
                    qs = parse_qs(urlparse(self.path).query)
                    sha = (qs.get("sha") or [""])[0]
                    self._json({"sha": sha, "cached": state.ts_code_cached(sha)})
                elif path == "/blob-sha":
                    # M2 B3：opt/ref 内容寻址 blob 是否已缓存（HUB 决定是否随 job 上传）。
                    qs = parse_qs(urlparse(self.path).query)
                    sha = (qs.get("sha") or [""])[0]
                    self._json({"sha": sha, "cached": state.blob_cached(sha)})
                elif path.startswith("/job/") and path.endswith("/status"):
                    jid = path[len("/job/") : -len("/status")]
                    rec = state.get(jid)
                    self._json({"state": rec["state"] if rec else "unknown"})
                elif path.startswith("/job/") and path.endswith("/result"):
                    jid = path[len("/job/") : -len("/result")]
                    rec = state.get(jid)
                    if rec is None:
                        self._json({"error": "unknown job"}, 404)
                    elif rec["state"] == "done":
                        self._json(rec["result"])
                    elif rec["state"] == "failed":
                        # 410（= 不会有结果，原因是终局）而非 500：500 在
                        # push_client.wait_result 里被当**瞬时错误**重试到预算耗尽，
                        # 把「bun 装不上」伪装成网络问题（2026-09-17）。
                        self._json(
                            {
                                "failed": True,
                                "error": rec.get("error", "?"),
                                "fail_kind": rec.get("kind", ""),
                            },
                            410,
                        )
                    else:
                        self._json({"status": "running"}, 202)
                else:
                    self._json({"error": "not found"}, 404)
            except Exception as e:  # 单请求不崩服务
                log(f"GET {path} ERROR: {e}")
                self._json({"error": "internal"}, 500)

        def do_POST(self) -> None:
            path = self.path.split("?", 1)[0]
            try:
                if not self._auth_ok():
                    self._json({"error": "unauthorized"}, 401)
                    return
                if path.startswith("/job/") and path.endswith("/cancel"):
                    # landed 取消帧（R2-4b）：幂等置 Event。未知 jid 也 200（先到先得——
                    # 取消帧常常比 job body 早到/晚到，都不该变成错误）。
                    cid = path[len("/job/") : -len("/cancel")]
                    known = state.request_cancel(cid)
                    self._json({"job_id": cid, "cancel": True, "known": known}, 200)
                    return
                if path != "/job":
                    self._json({"error": "not found"}, 404)
                    return
                raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if raw.startswith(WIRE_JOB_MAGIC):
                    # M2 B5：v2 体（payload/code/blob 为裸二进制段）→ 旧 JSON 形状，
                    # 下游字段名不变（payload_b64/code_b64/blobs）。非法体响亮 400。
                    try:
                        body = unpack_job_v2(raw)
                    except ProtocolError as e:
                        self._json({"error": f"job v2 体非法: {e}"}, 400)
                        return
                else:
                    body = json.loads(raw.decode("utf-8"))
                manifest = normalize_manifest(body["manifest"])
                jid = manifest["job_id"]
                payload_zip = base64.b64decode(body["payload_b64"])
                if hashlib.sha256(payload_zip).hexdigest() != manifest["payload_sha256"]:
                    self._json({"error": "payload_sha256 不匹配——上传损坏（重传）"}, 400)
                    return
                code_b64 = body.get("code_b64")
                code_zip = base64.b64decode(code_b64) if code_b64 else None
                if code_zip is None and not state.code_cached(manifest["code_sha256"]):
                    self._json(
                        {"error": "code-missing", "code_sha256": manifest["code_sha256"]}, 428
                    )
                    return
                # M3 kind=iter：TS 运行时 zip 同规（body 未带且缓存未命中 → 428 补传）。
                ts_b64 = body.get("ts_code_b64")
                ts_code_zip = base64.b64decode(ts_b64) if ts_b64 else None
                _ts_sha = str(manifest.get("ts_code_sha256", "") or "")
                if ts_code_zip is None and _ts_sha and not state.ts_code_cached(_ts_sha):
                    self._json({"error": "ts-code-missing", "ts_code_sha256": _ts_sha}, 428)
                    return
                # M2 B3：opt/ref blob —— body 未带且本地缓存未命中 → 428 要求重发。
                blobs_raw = body.get("blobs") or {}
                blobs: dict[str, bytes] = {}
                if isinstance(blobs_raw, dict):
                    for k, v in blobs_raw.items():
                        if isinstance(v, str) and v:
                            blobs[str(k)] = base64.b64decode(v)
                need_blobs = []
                for name, sha in (("opt", manifest.get("opt_sha")), ("ref", manifest.get("ref_sha"))):
                    s = str(sha or "")
                    if s and name not in blobs and not state.blob_cached(s):
                        need_blobs.append(name)
                if need_blobs:
                    self._json({"error": "blob-missing", "need": need_blobs}, 428)
                    return
                echo = self.headers.get("X-Smoke-Echo", "") == "1"
                item = {
                    "manifest": manifest,
                    "payload_zip": payload_zip,
                    "code_zip": code_zip,
                    "ts_code_zip": ts_code_zip,
                    "blobs": blobs,
                    "echo": echo,
                }
                verdict = state.submit(jid, item)
                if verdict["action"] == "duplicate":
                    # 幂等：超时重试/重复 POST 不重复 spawn（修旧 busy-409 下的重传风暴）。
                    rec = state.get(jid) or {"state": "queued"}
                    log(f"job {jid} 重复提交——幂等受理（当前 {rec.get('state')}），不重复执行")
                    self._json({"job_id": jid, "status": rec.get("state", "queued")}, 202)
                elif verdict["action"] == "queue":
                    log(f"job {jid} 入队（position={verdict['position']}）— 在跑 job 结束后即顶上")
                    self._json(
                        {"job_id": jid, "status": "queued", "position": verdict["position"]}, 202
                    )
                elif verdict["action"] == "full":
                    self._json(
                        {"error": f"busy — 队列已满（{WORKER_QUEUE_MAX}），稍后重试"}, 409
                    )
                else:
                    log(f"job {jid} accepted（echo={echo}）— PPO 后台执行")
                    self._json({"job_id": jid, "status": "accepted"}, 202)
            except (ProtocolError, ValueError, KeyError) as e:
                self._json({"error": str(e)}, 400)
            except Exception as e:
                log(f"POST {path} ERROR: {e}")
                self._json({"error": "internal"}, 500)

    def _starter(jid: str, item: dict) -> None:
        threading.Thread(
            target=_execute_job,
            args=(
                state,
                item["manifest"],
                item["payload_zip"],
                item["code_zip"],
                item.get("ts_code_zip"),
                item.get("blobs"),
                state.work_dir,
                device,
                torch_threads,
                item["echo"],
                log,
            ),
            daemon=True,
            name=f"job-{jid[:8]}",
        ).start()

    state.set_starter(_starter)
    return ThreadingHTTPServer((host, port), Handler)


def serve_forever(
    port: int,
    token: str,
    work_dir: Path,
    *,
    device: str = "cpu",
    torch_threads: int = 0,
    lock_file: str = "",
) -> None:
    """阻塞运行（调用方负责进程生命周期）；token 缺失响亮报错。"""
    if not token:
        raise SystemExit("[worker-serve] ERROR: 需要 --token（与 HUB 共享密钥）")
    # §单实例锁（第二道闸，同 hub_server，2026-09-17）：端口守卫是「探测 → bind」的 TOCTOU
    # （两个 starter 同时探测会双双通过），Windows 的 SO_REUSEADDR 又允许双绑（后启动者静默
    # 变僵尸）。锁把启动串行化，并能在**核验身份**后接管陈旧锁（PID 复用 / 崩溃残留）。
    # 指纹给多个：本服务既有 `-m remote_worker_serve` 入口，也可能被以模块名拉起。
    lock_path = lock_file or default_instance_lock_path("worker_server", port)
    if not acquire_instance_lock(
        lock_path, marker=("remote_worker_serve", "worker_server"), tag="worker-serve"
    ):
        raise SystemExit(f"[worker-serve] ERROR: 已有实例在运行（锁 {lock_path}）——拒绝启动")
    atexit.register(release_instance_lock, lock_path)
    # §双监听守卫（同 hub_server）：Windows SO_REUSEADDR 双绑同端口不崩，后启动者
    # 静默变僵尸——bind 前探测 127.0.0.1（worker 绑 0.0.0.0 含回环，任何本地监听都冲突）。
    try:
        ensure_port_free("127.0.0.1", port)
    except RuntimeError as e:
        raise SystemExit(f"[worker-serve] ERROR: {e}") from None
    state = WorkerServerState(work_dir)
    srv = make_worker_server(state, port, token, device=device, torch_threads=torch_threads)
    print(
        f"[{time.strftime('%H:%M:%S')}] [worker-serve] listening on 0.0.0.0:{port} "
        f"work={state.work_dir} pid={os.getpid()}",
        flush=True,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
