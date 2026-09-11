"""remote/hub_server.py — 旁路 hub-server 进程（stdlib http.server，零新依赖）。

进程拓扑（D11/§3.1，plan/remote-ppo-architecture.md）：训练主循环只负责
「打包 → 发布 → 轮询/等待 → 校验落位」；job 队列/租约/鉴权/jsonl 账单全部
归本进程。两端通过**磁盘 IPC** 解耦：
  * job 目录（`<job_root>/<job_id>/`）：`payload.zip`（hub 发布时写入）、
    `manifest.json`（同一份）、`result/`（worker POST 结果落盘）；
  * jsonl 账本（`training_log.jsonl`）：`job_pending`（发布时写）→
    `job_completed`（验收落位后写）双态事件——重启后可领取池由 jsonl 纯重读
    重建（D8），不依赖进程内状态。

端点（附录 C）：
  GET  /jobs/next               云 worker 轮询领取（§343 竞速：广播同一 open job，无租约）
  GET  /jobs/{id}/payload       下载 payload zip
  POST /jobs/{id}/heartbeat     心跳续租（60s）
  POST /jobs/{id}/result        worker 回传结果（weights_json + opt_tar + agg）
  GET  /jobs/{id}/status        训练主循环轮询 job 状态（pending/leased/done）
  POST /jobs/{id}/release       worker 瞬时失败主动还租约（job 立即回池，2026-09-05）
  GET  /jobs/{id}/result        训练主循环取回已落盘结果做三重校验

鉴权（D9）：`Authorization: Bearer <token>`；同一来源 IP 5 次 401 → 封 1 小时
（失败闭锁）。token 永不落日志。

启动：
  python -m remote.hub_server --port 8787 --token <token> \
      --job-root <traj_root>/remote-jobs --jsonl <traj_root>/training_log.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from remote._port_guard import ensure_port_free
from remote.protocol import (
    AUTH_HEADER,
    LEASE_SEC,
    PAYLOAD_NAME,
    WIRE_V2_MAGIC,
    ProtocolError,
    find_payload,
    normalize_manifest,
    unpack_result_v2,
)

# ------------------------------------------------------------------ state


class _JobStore:
    """磁盘 job 存储 + 内存租约状态。

    事实来源 = 磁盘（jsonl 账本 + job 目录）；内存只存租约（重启即丢，符合
    D8「重启后 job_pending 未完成的重发、job_completed 跳过」）。

    H2（review-hy）：领取时下发 `lease_token`（随机串），心跳/结果回传必须携带——
    hub 校验后才续租/收结果，杜绝「任何持 token 者都能续租/抢租约」的多 worker 竞态。
    H6（review-hy）：jsonl 增量读——记住上次文件 size，只解析新增行（长跑轮询
    不随账本线性变慢）。"""

    def __init__(self, job_root: str | Path, jsonl_path: str | Path, now_fn=None) -> None:
        self.job_root = Path(job_root)
        self.job_root.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = Path(jsonl_path)
        self._lock = Lock()
        #: job_id -> lease 到期时间戳（monotonic 无关；用墙钟，重启即空）
        self._leases: dict[str, float] = {}
        #: job_id -> 租约持有人 lease_token（H2；重启即丢，随租约重建）
        self._lease_owners: dict[str, str] = {}
        self._now = now_fn or time.time
        #: job_id -> 401 失败计数（闭锁用，D9）
        self._auth_fail: dict[str, int] = {}
        self._auth_blocked_until: dict[str, float] = {}
        #: 账本增量读缓存（H6）：文件 size -> 已解析事件列表
        self._ledger_cache: tuple[int, list[dict]] = (0, [])
        #: 云端停机标志（§385 复审：停云端省 GPU 配额，本地进程不动）。
        #: 置位后 /jobs/next 下发 halt，worker 收到即退出（keepalive 停、cell 走完）。
        #: 由 console 经 /admin/workers/{halt,resume} 控制；hub 重启即复位（volatile）。
        self.halt_workers = False

    # ---- jsonl 账本（job_pending / job_completed 双态，§3.1/D8） ----
    def _read_ledger(self) -> list[dict]:
        if not self.jsonl_path.exists():
            self._ledger_cache = (0, [])
            return []
        size = self.jsonl_path.stat().st_size
        cached_size, cached = self._ledger_cache
        if cached_size == size:
            return list(cached)  # 未变化：零 IO 复用
        out: list[dict] = []
        try:
            with open(self.jsonl_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if e.get("event") in ("job_pending", "job_completed", "job_cancelled"):
                        out.append(e)
        except OSError:
            return list(cached)
        self._ledger_cache = (size, out)
        return list(out)

    def _append_ledger(self, event: dict) -> None:
        self.jsonl_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    # ---- 可领取池（jsonl + 结果落盘重算，D8） ----
    def claimable_job_ids(self) -> list[str]:
        """job_pending 且未 job_completed 且 payload 在盘且**结果未落盘**的 job_id，按发布序。

        §343 竞速语义（2026-09-06 用户指令）：不再有租约独占——同一 open job 对
        所有轮询 worker 广播，先回传结果者胜（store_result 首写锁定，迟到写回 409
        丢弃）。实际部署单 worker，竞速只发生在 session 断开重连：重连后立即重领
        同一 job 重算，不再付 LEASE_SEC 孤儿租约等待（it24 实测白等 30min）。
        已有结果未验收的 job 从池中剔除——防落后 worker 死循环重算同一 job。
        """
        pending: dict[str, dict] = {}
        for e in self._read_ledger():
            jid = e.get("job_id")
            if not isinstance(jid, str):
                continue
            if e["event"] == "job_pending":
                pending[jid] = e
            elif e["event"] in ("job_completed", "job_cancelled"):
                pending.pop(jid, None)
        out = []
        for jid, _e in sorted(pending.items(), key=lambda kv: kv[1].get("ts", 0)):
            jd = self._job_dir(jid)
            if not jd.exists() or find_payload(jd) is None:
                continue  # 目录不存在或 payload 未落盘——不可领取
            if (jd / "result").exists():
                continue  # 结果已落盘待验收——竞速已分胜负，落后者不再领取（§343）
            out.append(jid)
        return out

    def _job_dir(self, job_id: str) -> Path:
        return self.job_root / job_id

    # ---- 发布（训练主循环调用：写磁盘 + 账本） ----
    def publish(self, job_id: str, manifest: dict, payload_zip: bytes) -> None:
        """hub 发布 job：落盘 payload.zip + manifest.json + 账本 job_pending。

        幂等：同 job_id 已发布 → 覆盖 payload 但**不重复**追加 job_pending
        （账本按 job_id 去重——重启后重发布不产生双 pending）。
        """
        with self._lock:
            jd = self._job_dir(job_id)
            jd.mkdir(parents=True, exist_ok=True)
            (jd / PAYLOAD_NAME).write_bytes(payload_zip)
            (jd / "manifest.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            pending_ids = {
                e.get("job_id") for e in self._read_ledger() if e.get("event") == "job_pending"
            }
            completed_ids = {
                e.get("job_id") for e in self._read_ledger() if e.get("event") == "job_completed"
            }
            if job_id not in pending_ids and job_id not in completed_ids:
                self._append_ledger(
                    {
                        "event": "job_pending",
                        "job_id": job_id,
                        "runId": manifest.get("runId"),
                        "it": manifest.get("it"),
                        "ts": self._now(),
                    }
                )

    # ---- 租约（H2 旧路径，§343 起仅兼容保留——调度不再消费租约） ----
    def claim(self, job_id: str) -> str | None:
        """（兼容）领取（设租约）。§343 竞速模型下 _get_next 不再调用。"""
        import secrets

        with self._lock:
            now = self._now()
            lease = self._leases.get(job_id)
            if lease is not None and lease > now:
                return None
            token = secrets.token_hex(16)
            self._leases[job_id] = now + LEASE_SEC
            self._lease_owners[job_id] = token
            return token

    def heartbeat(self, job_id: str, lease_token: str) -> bool:
        """心跳续租（60s 节奏；H2：非原租者拒续）。
        返回 True = 续租成功；False = job 不存在 / lease_token 不符。"""
        with self._lock:
            if not (self._job_dir(job_id) / "manifest.json").exists():
                return False
            owner = self._lease_owners.get(job_id)
            if owner is None or owner != lease_token:
                return False
            self._leases[job_id] = self._now() + LEASE_SEC
            return True

    def release(self, job_id: str, lease_token: str) -> bool:
        """worker 瞬时失败主动还租约（2026-09-05）：job 立即回池可重领，
        不再干等 LEASE_SEC 过期。H2：仅租约持有人可释放。返回 False = 无租约/非持有人。"""
        with self._lock:
            owner = self._lease_owners.get(job_id)
            if not lease_token or owner != lease_token:
                return False
            self._leases.pop(job_id, None)
            self._lease_owners.pop(job_id, None)
            return True

    # ---- 结果 ----
    def store_result(self, job_id: str, result: dict) -> bool:
        """落盘 worker 回传结果（result/ 目录）。返回 False = 该 job 已有结果（防重复写回）。"""
        with self._lock:
            rdir = self._job_dir(job_id) / "result"
            if rdir.exists():
                return False
            rdir.mkdir(parents=True, exist_ok=True)
            (rdir / "result.json").write_text(
                json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            # weights_json / opt_tar 以 base64 存于 result.json（< 数 MB，可接受）
            return True

    def mark_completed(self, job_id: str) -> None:
        """训练主循环验收落位后写 job_completed 账本事件（§3.1）。幂等。"""
        with self._lock:
            self._leases.pop(job_id, None)
            completed_ids = {
                e.get("job_id") for e in self._read_ledger() if e.get("event") == "job_completed"
            }
            if job_id not in completed_ids:
                self._append_ledger({"event": "job_completed", "job_id": job_id, "ts": self._now()})

    def get_result(self, job_id: str) -> dict | None:
        p = self._job_dir(job_id) / "result" / "result.json"
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                loaded = json.load(f)
                return loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            return None

    # ---- 闭锁（D9） ----
    def auth_failure(self, ip: str) -> None:
        with self._lock:
            n = self._auth_fail.get(ip, 0) + 1
            self._auth_fail[ip] = n
            if n >= 5:
                self._auth_blocked_until[ip] = self._now() + 3600
                self._auth_fail.pop(ip, None)

    def is_blocked(self, ip: str) -> bool:
        with self._lock:
            until = self._auth_blocked_until.get(ip, 0.0)
            return until > self._now()


# ------------------------------------------------------------------ HTTP


class HubHandler(BaseHTTPRequestHandler):
    """单例 handler：类属性持共享 store（ThreadingHTTPServer 每请求新建实例）。"""

    store: _JobStore = None  # type: ignore[assignment]  # 由 factory 注入

    # ---- 基础 ----
    def log_message(self, fmt: str, *args: object) -> None:  # 简洁日志（含时间戳）
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server {self.client_address[0]}] {fmt % args}",
            flush=True,
        )

    def _auth_ok(self) -> bool:
        ip = self.client_address[0]
        if self.store.is_blocked(ip):
            self._json({"error": "ip blocked"}, 403)
            return False
        auth = self.headers.get(AUTH_HEADER, "")
        token = self.server.token if hasattr(self.server, "token") else ""
        if auth != f"Bearer {token}" or not token:
            self.store.auth_failure(ip)
            self._json({"error": "unauthorized"}, 401)
            return False
        return True

    def _json(self, obj: object, status: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _bytes(
        self, data: bytes, status: int = 200, ctype: str = "application/octet-stream"
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _job_id(self) -> str | None:
        """从路径 /jobs/{id}/... 取 job_id；非法 404。"""
        parts = self.path.split("?", 1)[0].strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "jobs" and len(parts[1]) > 0:
            return urllib.parse.unquote(parts[1])
        return None

    # ---- 路由 ----
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path == "/ping" or path == "/":
                if not self._auth_ok():
                    return
                self._json({"status": "ok"}, 200)
            elif path == "/code":
                self._get_shared_code()
            elif path == "/jobs/next":
                self._get_next()
            elif path == "/admin/workers/halt":
                self._admin_halt(True)
            elif path == "/admin/workers/resume":
                self._admin_halt(False)
            elif path == "/admin/workers/status":
                self._admin_status()
            elif path.startswith("/jobs/") and path.endswith("/payload"):
                self._get_payload()
            elif path.startswith("/jobs/") and path.endswith("/code"):
                self._get_code()
            elif path.startswith("/jobs/") and path.endswith("/status"):
                self._get_status()
            elif path.startswith("/jobs/") and path.endswith("/result"):
                self._get_result()
            else:
                self._json({"error": "not found"}, 404)
        except (ProtocolError, ValueError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:  # 服务器不因单请求崩溃
            self.log_message("ERROR %s: %s", path, e)
            self._json({"error": "internal"}, 500)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        try:
            if path.startswith("/jobs/") and path.endswith("/heartbeat"):
                self._post_heartbeat()
            elif path.startswith("/jobs/") and path.endswith("/release"):
                self._post_release()
            elif path.startswith("/jobs/") and path.endswith("/result"):
                self._post_result()
            else:
                self._json({"error": "not found"}, 404)
        except (ProtocolError, ValueError) as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            self.log_message("ERROR %s: %s", path, e)
            self._json({"error": "internal"}, 500)

    # ---- GET /jobs/next ----
    def _get_next(self) -> None:
        if not self._auth_ok():
            return
        if self.store.halt_workers:  # 云端停机：下发达令，worker 收到即退出
            self._json({"halt": True, "job_id": None}, 200)
            return
        # §343 竞速：不设租约、不独占——同一 open job 对所有轮询者广播，
        # 先回传结果者胜（store_result 首写锁定），落后者 409 丢弃。
        jids = self.store.claimable_job_ids()
        for jid in jids:
            mp = self.store._job_dir(jid) / "manifest.json"
            manifest = json.loads(mp.read_text(encoding="utf-8"))
            self._json({"job_id": jid, "manifest": manifest})
            return
        self._json({"job_id": None}, 200)  # 无可领取 job

    # ---- 云端停机 / 恢复（§385 复审：停云端省 GPU 配额，本地进程不动） ----
    # 用法：console 在 TrainingLoop 死亡/设计内停车时 GET /admin/workers/halt，
    # 恢复训练 GET /admin/workers/resume。仅 Bearer 鉴权（同 worker），volatile。
    def _admin_halt(self, halt: bool) -> None:
        if not self._auth_ok():
            return
        self.store.halt_workers = halt
        self._json({"halt": halt}, 200)

    def _admin_status(self) -> None:
        if not self._auth_ok():
            return
        self._json({"halt": self.store.halt_workers}, 200)

    # ---- GET /jobs/{id}/payload ----
    def _get_payload(self) -> None:
        if not self._auth_ok():
            return
        jid = self._job_id()
        if jid is None:
            self._json({"error": "not found"}, 404)
            return
        p = find_payload(self.store._job_dir(jid))
        if p is None:
            self._json({"error": "no payload"}, 404)
            return
        self._bytes(p.read_bytes())

    # ---- GET /jobs/{id}/code ----
    def _get_code(self) -> None:
        if not self._auth_ok():
            return
        jid = self._job_id()
        if jid is None:
            self._json({"error": "not found"}, 404)
            return
        p = self.store._job_dir(jid) / "code.zip"
        if not p.exists():
            self._json({"error": "no code zip"}, 404)
            return
        self._bytes(p.read_bytes())

    # ---- GET /code（共享 code.zip，colab bootstrap 用） ----
    def _get_shared_code(self) -> None:
        if not self._auth_ok():
            return
        p = self.store.job_root / "code.zip"
        if not p.exists():
            self._json({"error": "no shared code zip — training loop 尚未启动"}, 404)
            return
        self._bytes(p.read_bytes())

    # ---- GET /jobs/{id}/status ----
    def _get_status(self) -> None:
        if not self._auth_ok():
            return
        jid = self._job_id()
        if jid is None:
            self._json({"error": "not found"}, 404)
            return
        jd = self.store._job_dir(jid)
        if not (jd / "manifest.json").exists():
            self._json({"error": "unknown job"}, 404)
            return
        if (jd / "result" / "result.json").exists():
            state = "done"
        elif self.store._leases.get(jid, 0) > self.store._now():
            state = "leased"
        else:
            state = "pending"
        self._json({"job_id": jid, "state": state})

    # ---- GET /jobs/{id}/result ----
    def _get_result(self) -> None:
        if not self._auth_ok():
            return
        jid = self._job_id()
        if jid is None:
            self._json({"error": "not found"}, 404)
            return
        r = self.store.get_result(jid)
        if r is None:
            self._json({"error": "not done"}, 404)
            return
        self._json(r)

    # ---- POST /jobs/{id}/heartbeat ----
    def _post_heartbeat(self) -> None:
        if not self._auth_ok():
            return
        jid = self._job_id()
        if jid is None:
            self._json({"error": "not found"}, 404)
            return
        # H2：lease_token 必填且须与原租者一致（否则拒续）
        lease_token = self.headers.get("X-Lease-Token", "") or self.headers.get("lease-token", "")
        ok = self.store.heartbeat(jid, lease_token)
        self._json({"job_id": jid, "ok": ok}, 200 if ok else 404)

    # ---- POST /jobs/{id}/release ----
    def _post_release(self) -> None:
        """worker 瞬时失败主动还租约（2026-09-05）：仅租约持有人可释放（H2）。"""
        if not self._auth_ok():
            return
        jid = self._job_id()
        if jid is None or not (self.store._job_dir(jid) / "manifest.json").exists():
            self._json({"error": "not found"}, 404)
            return
        lease_token = self.headers.get("X-Lease-Token", "") or self.headers.get("lease-token", "")
        if self.store.release(jid, lease_token):
            self._json({"job_id": jid, "status": "released"})
        else:
            self._json({"error": "lease mismatch or absent — 非本 job 租约持有人"}, 403)

    # ---- POST /jobs/{id}/result ----
    def _post_result(self) -> None:
        if not self._auth_ok():
            return
        jid = self._job_id()
        if jid is None:
            self._json({"error": "not found"}, 404)
            return
        jd = self.store._job_dir(jid)
        if not (jd / "manifest.json").exists():
            self._json({"error": "unknown job"}, 404)
            return
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        except Exception as e:
            self._json({"error": f"read body failed: {e}"}, 400)
            return
        try:
            # 方案B（2026-09-10）：v2 体（gzip 裸二进制段）**按魔数自动识别** —— 不依赖
            # Content-Type，故旧 worker（纯 JSON）与新 worker（v2）都能收。还原出的 dict
            # 与方案A 逐字段一致（二进制字段被重新 base64）⇒ 下游零改动。
            if raw.startswith(WIRE_V2_MAGIC):
                result = unpack_result_v2(raw)
            else:
                result = json.loads(raw.decode("utf-8"))
            # 与 manifest 对账（job_id/data_fp/init_weights_fp/commit_echo）
            manifest = json.loads((jd / "manifest.json").read_text(encoding="utf-8"))
            normalize_manifest(manifest)
            from remote.protocol import validate_result

            validate_result(result, manifest, commit_echo_must_match=True)
        except (ValueError, ProtocolError) as e:
            self._json({"error": f"result rejected: {e}"}, 400)
            return
        # §343 竞速：不再校验 lease_token（H2 租约已废）——鉴权边界 = Bearer token，
        # 结果内容 = validate_result 对账（job_id/data_fp/init_weights_fp/commit_echo）；
        # 先回传者 store_result 首写锁定，迟到写回 409 丢弃（防重复写回测试覆盖）。
        if not self.store.store_result(jid, result):
            self._json({"error": "result already stored (duplicate write-back)"}, 409)
            return
        self._json({"job_id": jid, "status": "accepted"})


def make_server(
    store: _JobStore, port: int, token: str, host: str = "127.0.0.1"
) -> ThreadingHTTPServer:
    """构造 server（handler 注入 store + token）。"""

    class Server(ThreadingHTTPServer):
        def __init__(self) -> None:
            super().__init__((host, port), HubHandler)
            self.token = token

    HubHandler.store = store
    return Server()


def main() -> None:
    ap = argparse.ArgumentParser(description="hub-server: remote PPO job queue (stdlib)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--token", default="", help="Bearer token（云 worker 与训练主循环共享）")
    ap.add_argument("--token-file", default="", help="从文件读取 token（避免进程列表泄露，H10）")
    ap.add_argument(
        "--job-root", required=True, help="job 目录根（payload zip / 结果 / ppo_ckpt_remote）"
    )
    ap.add_argument(
        "--jsonl", required=True, help="training_log.jsonl 路径（job_pending/job_completed 账本）"
    )
    args = ap.parse_args()
    token = args.token
    if args.token_file:
        try:
            token = Path(args.token_file).read_text(encoding="utf-8").strip()
        except OSError as e:
            print(f"[hub-server] ERROR: 读 --token-file 失败: {e}", flush=True)
            sys.exit(1)
    if not token:
        print("[hub-server] ERROR: 需要 --token 或 --token-file", flush=True)
        sys.exit(1)
    # §双监听守卫：Windows SO_REUSEADDR 允许双绑同端口（后启动者静默变僵尸）——
    # bind 前探测，端口已有活监听者即拒绝启动（2026-09-09 8787 双实例事故）。
    try:
        ensure_port_free(args.host, args.port)
    except RuntimeError as e:
        print(f"[hub-server] ERROR: {e}", flush=True)
        sys.exit(1)
    store = _JobStore(args.job_root, args.jsonl)
    srv = make_server(store, args.port, token, host=args.host)
    print(
        f"[hub-server] listening on {args.host}:{args.port} "
        f"job_root={args.job_root} jsonl={args.jsonl} lease={LEASE_SEC}s",
        flush=True,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
