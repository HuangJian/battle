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
  GET  /jobs/next               云 worker 轮询领取（P3b 独占加超时：首个 open job
                                设租约 + 下发 lease_token；超时前不重发）
  GET  /jobs/{id}/payload       下载 payload zip
  POST /jobs/{id}/heartbeat     心跳续租（60s）
  POST /jobs/{id}/result        worker 回传结果（weights_json + opt_tar + agg）
  GET  /jobs/{id}/status        训练主循环轮询 job 状态（pending/leased/done）
  POST /jobs/{id}/release       worker 瞬时失败主动还租约（job 立即回池，2026-09-05）
  GET  /jobs/{id}/result        训练主循环取回已落盘结果做三重校验

鉴权（D9）：`Authorization: Bearer <token>`。**先验 token，封禁只拒无效鉴权尝试**
（2026-09-17 改序）：同一来源 IP 连续 5 次**无效**鉴权 → 该 IP 的无效尝试 1 小时内
一律 403，但**合法 token 永远放行**（封禁不连坐）。旧序先查封禁 ⇒ 一次误封（本机组件
用陈旧 token 连打 5 次）会把该来源 IP 的**全部**流量（含 console 健康检查、训练循环、
worker 拉活）拒之门外一小时，而封禁只住进程内存、只能靠重启清除——重启又正好被
自己占着的端口挡住（2026-09-17 hub-server 重启死锁事故）。
另：**回环来源（`127.0.0.0/8` / `::1` / `::ffff:127.0.0.1`）永不计数、永不封禁**
（用户口径：「本地 127.0.0.1 鉴权失败不要锁地址」）——回环就是本机自己的组件，而
cloudflared 回源会把隧道流量也全归成 127.0.0.1，对它封禁 = 把本机服务面整体连坐。
token 永不落日志。

启动：
  python -m remote.hub_server --port 8787 --token <token> \
      --job-root <traj_root>/remote-jobs --jsonl <traj_root>/training_log.jsonl

监听地址（2026-09-16）：默认 `0.0.0.0`。原因是 Tailscale 内网直连场景下，云 worker
是从 tailnet 侧**入站**访问本 hub（以前 cloudflared 是本机主动外连，绑 127.0.0.1 就够）；
绑回环时 tailnet 根本连不上，表现为对端一直超时。只想听 tailnet 就显式
`--host <本机 Tailscale IP>`；单测/冒烟仍然各自显式传 `host="127.0.0.1"`。
"""

from __future__ import annotations

import argparse
import atexit
import json
import os
import sys
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from remote._instance_lock import (
    acquire_instance_lock,
    default_instance_lock_path,
    release_instance_lock,
)
from remote._port_guard import ensure_port_free
from remote.protocol import (
    AUTH_HEADER,
    CLAIM_TTL_SEC,
    PAYLOAD_NAME,
    WIRE_V2_MAGIC,
    ProtocolError,
    find_payload,
    normalize_manifest,
    unpack_result_v2,
)

# ------------------------------------------------------------------ 来源判定


def _is_loopback(ip: str) -> bool:
    """回环来源（`127.0.0.0/8` / `::1` / IPv4-mapped `::ffff:127.0.0.1`）。

    **闭锁永不作用于回环**（2026-09-17 用户口径：「本地 127.0.0.1 鉴权失败不要锁地址」）：
    回环来源就是本机自己的组件（console 健康检查、训练循环、worker 拉活），而 cloudflared
    回源还会把**隧道流量一并归成 127.0.0.1** —— 对它封禁等于把整台机器的服务面连坐，且
    封禁只住进程内存、只能靠重启清除（2026-09-17 hub-server 重启死锁事故的根因）。
    回环上的**无效**鉴权照常 401（鉴权边界与审计行不变），只是**不计数、不封禁**。
    """
    s = (ip or "").strip().lower()
    if s.startswith("::ffff:"):  # IPv4-mapped IPv6
        s = s[len("::ffff:") :]
    return s == "::1" or s == "localhost" or s.startswith("127.")


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
        #: job_id -> 最近一次心跳（领取算一次）墙钟（P3b 可观测；/jobs/status 暴露）
        self._last_heartbeat: dict[str, float] = {}
        self._now = now_fn or time.time
        #: job_id -> 401 失败计数（闭锁用，D9）
        self._auth_fail: dict[str, int] = {}
        self._auth_blocked_until: dict[str, float] = {}
        #: 账本增量读缓存（H6）：文件 size -> 已解析事件列表
        self._ledger_cache: tuple[int, list[dict]] = (0, [])
        #: 云端停机标志（§386：停机命令随任务同发；云机先试停机、停不掉照常干活）。
        #: 置位后 /jobs/next 响应带 halt:true；由 console 经 /admin/workers/{halt,resume}
        #: 控制；hub 重启即复位（volatile）。停机**不拦任务分发**。
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

        P3b 独占加超时（supersede §343）：持有**未过期租约**的 job 不在池中——
        worker 领到 PPO 任务后超时前不被别 worker 重领。过期租约自动回池
        （死 worker 回收只管这一条，不管调大 TTL——it24 倒车禁令）。
        已有结果未验收的 job 从池中剔除——首写锁定兜底（hub 重启丢租约时用）。
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
        now = self._now()
        out = []
        for jid, _e in sorted(pending.items(), key=lambda kv: kv[1].get("ts", 0)):
            if self._leases.get(jid, 0) > now:
                continue  # 活租约：已被某 worker 独占，超时前不重发
            jd = self._job_dir(jid)
            if not jd.exists() or find_payload(jd) is None:
                continue  # 目录不存在或 payload 未落盘——不可领取
            if (jd / "result").exists():
                continue  # 结果已落盘待验收——首写已分胜负，不再领取
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

    # ---- 租约（P3b 独占加超时：领取即设租约，心跳续租，过期回池） ----
    def claim(self, job_id: str, ttl: float = CLAIM_TTL_SEC) -> str | None:
        """领取（设租约 + owner + last_heartbeat 三件套**同时置**）。

        B3 必杀细节：只写 `_leases` 不写 `_lease_owners` 会导致 heartbeat 恒 False，
        300s 后长 job 被重广播——故领取必须走本函数，不许手写 `_leases[jid] = ...`。
        活租约在持 → 返回 None（调用方跳过本 jid，不是阻塞等）。
        """
        import secrets

        with self._lock:
            now = self._now()
            lease = self._leases.get(job_id)
            if lease is not None and lease > now:
                return None
            token = secrets.token_hex(16)
            self._leases[job_id] = now + ttl
            self._lease_owners[job_id] = token
            self._last_heartbeat[job_id] = now
            return token

    def heartbeat(self, job_id: str, lease_token: str) -> bool:
        """心跳续租（60s 节奏；H2：非原租者拒续）。
        返回 True = 续租成功；False = job 不存在 / lease_token 不符。

        B3 必杀细节：续租必须改用 CLAIM_TTL_SEC（本函数是 claim/heartbeat/
        _get_status 的**唯一** TTL 来源）——否则死 worker 隐身 30min（LEASE_SEC）。
        """
        with self._lock:
            if not (self._job_dir(job_id) / "manifest.json").exists():
                return False
            owner = self._lease_owners.get(job_id)
            if owner is None or owner != lease_token:
                return False
            now = self._now()
            self._leases[job_id] = now + CLAIM_TTL_SEC
            self._last_heartbeat[job_id] = now
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
            self._last_heartbeat.pop(job_id, None)
            return True

    def result_token_ok(self, job_id: str, lease_token: str) -> bool:
        """结果回传鉴权（P3b）：有活租约 → 须持有人 token；无租约（过期/释放/
        从未领取/旧 worker）→ 照收。HTTP 层薄调用本函数。"""
        with self._lock:
            if self._leases.get(job_id, 0) > self._now():
                owner = self._lease_owners.get(job_id)
                return bool(lease_token) and owner == lease_token
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

    # ---- BC 每 epoch 回传（2026-09-13，plan/bc-cloud-integration.plan.md）----
    #: 单文件覆盖存最新 resume（磁盘有界：每 job 恒 1 份权重，~0.5MB）；指标追加 jsonl。
    BC_RESUME_NAME = "bc-resume.json"
    BC_METRICS_NAME = "bc-metrics.jsonl"
    #: epoch POST 体上限（weights ~0.5MB b64 后 ~0.7MB；4MB 已极宽裕）
    BC_EPOCH_BODY_MAX = 4 * 1024 * 1024

    def store_bc_epoch(self, job_id: str, body: dict) -> bool:
        """BC epoch 回传落盘：bc-resume.json（单文件原子覆盖 = 最新 epoch 权重）+
        bc-metrics.jsonl（追加一行指标）。返回 False = 体非法。调用方已验租约。"""
        with self._lock:
            epoch = body.get("epoch")
            if not isinstance(epoch, int) or isinstance(epoch, bool) or epoch < 1:
                return False
            if not isinstance(body.get("weights"), str) or not body["weights"]:
                return False
            jd = self._job_dir(job_id)
            jd.mkdir(parents=True, exist_ok=True)
            # 原子覆盖：tmp + replace——中断的 POST 不留半截 resume
            tmp = jd / (self.BC_RESUME_NAME + ".tmp")
            tmp.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, jd / self.BC_RESUME_NAME)
            metrics = body.get("metrics")
            if isinstance(metrics, dict):
                row = {"epoch": epoch, **metrics, "ts": self._now()}
                with open(jd / self.BC_METRICS_NAME, "a", encoding="utf-8") as f:
                    f.write(json.dumps(row, ensure_ascii=False) + "\n")
            return True

    def get_bc_resume(self, job_id: str) -> dict | None:
        p = self._job_dir(job_id) / self.BC_RESUME_NAME
        if not p.exists():
            return None
        try:
            with open(p, encoding="utf-8") as f:
                loaded = json.load(f)
                return loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError):
            return None

    def get_bc_metrics(self, job_id: str) -> list[dict]:
        p = self._job_dir(job_id) / self.BC_METRICS_NAME
        if not p.exists():
            return []
        out: list[dict] = []
        try:
            with open(p, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except ValueError:
                        continue
                    if isinstance(e, dict):
                        out.append(e)
        except OSError:
            return []
        return out

    def mark_completed(self, job_id: str) -> None:
        """训练主循环验收落位后写 job_completed 账本事件（§3.1）。幂等。"""
        with self._lock:
            self._leases.pop(job_id, None)
            self._lease_owners.pop(job_id, None)
            self._last_heartbeat.pop(job_id, None)
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
    def auth_failure(self, ip: str) -> int:
        """记一次鉴权失败，返回**累计次数**（含本次）；满 5 次封禁 3600s。

        返回值供 handler 打印审计行——2026-09-16 x3-step 事故：401 落在
        `/ping`・`/jobs/next` 等静默路径上，五次失败把 127.0.0.1 封掉后
        **hub 日志一行痕迹都没有**，训练循环被 cloudflared 回源 IP 连坐后
        连续 403 自杀退出，只能靠猜。故次数必须上浮到调用方记录。

        **回环来源永不计数、永不封禁**（返回 0）——口径见 `_is_loopback`。
        """
        if _is_loopback(ip):
            return 0
        with self._lock:
            n = self._auth_fail.get(ip, 0) + 1
            self._auth_fail[ip] = n
            if n >= 5:
                self._auth_blocked_until[ip] = self._now() + 3600
                self._auth_fail.pop(ip, None)
            return n

    def auth_success(self, ip: str) -> None:
        """一次合法鉴权：清零该 ip 的失败计数（**不改封禁状态**）。

        2026-09-17 改序配套：封禁只拒无效尝试后，合法流量必须能把计数打回零——否则
        与合法组件共用同一个来源 IP 的坏客户端（典型：cloudflared 回源把隧道流量与
        所有本机组件都归成 127.0.0.1）仍会**慢性累积**到 5 次，把整个 IP 拖进封禁。
        封禁本身不在此解除：它已只影响无效尝试，到点自愈，无需合法流量代劳。
        """
        with self._lock:
            self._auth_fail.pop(ip, None)

    def is_blocked(self, ip: str) -> bool:
        if _is_loopback(ip):  # 回环永不被封（即便旧内存态里混进过记录）
            return False
        with self._lock:
            until = self._auth_blocked_until.get(ip, 0.0)
            return until > self._now()

    def blocked_remaining(self, ip: str) -> float:
        """该 ip 剩余封禁秒数（未封禁 = 0）——供审计行提示「还要封多久」。"""
        with self._lock:
            return max(0.0, self._auth_blocked_until.get(ip, 0.0) - self._now())


# ------------------------------------------------------------------ HTTP


class HubHandler(BaseHTTPRequestHandler):
    """单例 handler：类属性持共享 store（ThreadingHTTPServer 每请求新建实例）。"""

    store: _JobStore = None  # type: ignore[assignment]  # 由 factory 注入

    #: ip -> 上次打印「封禁拒绝」的墙钟（节流：被封客户端高频轮询时每 ip 每分钟一条）
    _blocked_logged: dict[str, float] = {}

    # ---- 基础 ----
    def log_message(self, fmt: str, *args: object) -> None:  # 只打非常规事件
        # 静默高频只读访问（/ping 健康检查、/jobs/next 拉活、result/payload 轮询含 404）
        # ——这些在多 worker 下每秒可打多行，把 hub-server.out 刷爆。POST result、
        # ERROR、/admin、/code 仍保留。
        # 例外：**401/403 永不静默**（2026-09-16 x3-step 事故）——鉴权失败与封禁
        # 恰恰最爱发生在这些高频路径上，静默等于抹掉唯一的破案线索。
        line = fmt % args
        if (
            '"GET /ping ' in line
            or '"GET /jobs/next ' in line
            or ('"GET /jobs/' in line and ('/result ' in line or '/payload ' in line))
        ) and ' 401 ' not in line and ' 403 ' not in line:
            return
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server {self.client_address[0]}] {line}",
            flush=True,
        )

    def _auth_ok(self) -> bool:
        ip = self.client_address[0]
        auth = self.headers.get(AUTH_HEADER, "")
        token = self.server.token if hasattr(self.server, "token") else ""
        # ① 先验 token（2026-09-17 改序）：**合法 token 永远放行**，封禁只拒无效鉴权尝试。
        # 旧序先查 is_blocked ⇒ 一次误封会把该来源 IP 的全部流量（console 健康检查、训练
        # 循环、worker 拉活）403 一小时，而封禁只住进程内存、只能靠重启清除——重启又被
        # 端口守卫挡死 = 死锁（2026-09-17 hub-server 重启事故）。
        if token and auth == f"Bearer {token}":
            self.store.auth_success(ip)
            return True
        # ② 无效鉴权尝试：已封禁 → 只拒（不重复计数，封禁到点自愈）；未封禁 → 计数，满 5 封禁。
        if self.store.is_blocked(ip):
            self._log_blocked(ip)
            self._json({"error": "ip blocked"}, 403)
            return False
        n = self.store.auth_failure(ip)
        if _is_loopback(ip):
            counter, note = "AUTH FAIL", "（回环来源：不计数、不封禁）"
        else:
            counter = f"AUTH FAIL {n}/5"
            note = " — 已封禁该 IP 3600s（仅拒无效鉴权；合法 token 不受影响）" if n >= 5 else ""
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server {ip}] {counter} "
            f"path={self.path.split('?', 1)[0]}{note}",
            flush=True,
        )
        self._json({"error": "unauthorized"}, 401)
        return False

    def _log_blocked(self, ip: str) -> None:
        """封禁命中审计（每 ip 每 60s 一条）：被封客户端往往仍在高频轮询，
        不节流会把日志刷爆，但完全不打则「谁在被封」永远查不到。"""
        now = time.time()
        if now - HubHandler._blocked_logged.get(ip, 0.0) < 60:
            return
        HubHandler._blocked_logged[ip] = now
        print(
            f"[{time.strftime('%H:%M:%S')}] [hub-server {ip}] BLOCKED — 请求被拒 "
            f"path={self.path.split('?', 1)[0]} 剩余封禁 "
            f"{int(self.store.blocked_remaining(ip))}s（5/5 次**无效**鉴权触发；"
            f"合法 token 照常放行——本次请求的 token 不匹配）",
            flush=True,
        )

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
            elif path.startswith("/jobs/") and path.endswith("/resume"):
                self._get_bc_resume()
            elif path.startswith("/jobs/") and path.endswith("/bc-metrics"):
                self._get_bc_metrics()
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
            elif path.startswith("/jobs/") and path.endswith("/epoch"):
                self._post_bc_epoch()
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
        # §386：停机达令随任务同发——云机取任务时同时拿到"停机命令"，先试停机、
        # 停不掉（Kaggle 无 API）则照常执行任务。停机**不拦任务分发**（否则云机
        # 闲置空烧反而是最大浪费）。空任务时也带 halt 标志，供空闲 worker 感知。
        halt = self.store.halt_workers
        # P3b 独占加超时（supersede §343）：首个 open job 领取即设租约并下发
        # lease_token；活租约 job 已被 claimable_job_ids 排除。worker 零改动
        # （本就读取 lease_token 并走心跳/回传携带链路）。
        jids = self.store.claimable_job_ids()
        for jid in jids:
            lease_token = self.store.claim(jid)
            if lease_token is None:
                continue  # 并发领取竞负：本轮跳过（下次轮询回池见）
            mp = self.store._job_dir(jid) / "manifest.json"
            manifest = json.loads(mp.read_text(encoding="utf-8"))
            # 领取标记：首次向 worker 下发即 touch（console 据此区分「排队等取」与「已在跑」）。
            # 无 worker 轮询 /jobs/next 时不会出现 claimed → 排队超时可告警。
            claim = self.store._job_dir(jid) / "claimed"
            if not claim.exists():
                try:
                    # `_now` 属 _JobStore（handler 无此属性）——漏写 store. 会让
                    # 每个 /jobs/next 在「有可领任务」路径抛 500，云 worker 全取不到 job
                    claim.write_text(str(self.store._now()), encoding="utf-8")
                except OSError:
                    pass
            self._json({"job_id": jid, "manifest": manifest, "halt": halt, "lease_token": lease_token})
            return
        self._json({"job_id": None, "halt": halt})  # 无可领取 job

    # ---- 云端停机 / 恢复（§386：停机=发"停机命令"随任务同发；云机先试停机停不掉照常干活） ----
    # 用法：console 在 TrainingLoop 死亡/设计内停车时 GET /admin/workers/halt 置停机态，
    # 停机条件消失（恢复训练）GET /admin/workers/resume。仅 Bearer 鉴权（同 worker），volatile。
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
        now = self.store._now()
        if (jd / "result" / "result.json").exists():
            state = "done"
        elif self.store._leases.get(jid, 0) > now:
            state = "leased"
        else:
            state = "pending"
        # P3b 可观测：租约剩余秒 + 距上次心跳秒（worker 吞错保持现状，文档化——
        # 心跳 5xx 时 worker 侧只记日志不抛，见 worker._hb_loop）。
        resp: dict = {"job_id": jid, "state": state}
        if state == "leased":
            resp["lease_expires_in"] = round(self.store._leases.get(jid, 0) - now, 1)
            resp["last_heartbeat_ago"] = round(now - self.store._last_heartbeat.get(jid, now), 1)
        self._json(resp)

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
        # P3b 独占加超时：有活租约时验 X-Lease-Token（恢复 H2 检查）——错 token/
        # 缺 token → 403（非持有人不得写回）；无租约照收（兼容旧 worker/重发）；
        # 首写锁定保留（hub 重启丢租约 → 首写胜，结果一致）。
        lease_token = self.headers.get("X-Lease-Token", "") or self.headers.get(
            "lease-token", ""
        )
        if not self.store.result_token_ok(jid, lease_token):
            self._json({"error": "lease mismatch — 非本 job 租约持有人"}, 403)
            return
        if not self.store.store_result(jid, result):
            self._json({"error": "result already stored (duplicate write-back)"}, 409)
            return
        self._json({"job_id": jid, "status": "accepted"})

    # ---- POST /jobs/{id}/epoch（BC 每 epoch 回传：权重 resume + 指标行，2026-09-13）----
    def _post_bc_epoch(self) -> None:
        if not self._auth_ok():
            return
        jid = self._job_id()
        if jid is None or not (self.store._job_dir(jid) / "manifest.json").exists():
            self._json({"error": "not found"}, 404)
            return
        try:
            raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        except Exception as e:
            self._json({"error": f"read body failed: {e}"}, 400)
            return
        if len(raw) > self.store.BC_EPOCH_BODY_MAX:
            self._json({"error": "epoch body too large"}, 400)
            return
        try:
            body = json.loads(raw.decode("utf-8"))
        except ValueError as e:
            self._json({"error": f"bad json: {e}"}, 400)
            return
        # 租约口径与 result 相同：活租约须持有人（防被顶掉的旧 worker 用旧 epoch
        # 覆盖新 resume）；无租约（过期/释放/重启后）照收——resume 是幂等覆盖存最新。
        lease_token = self.headers.get("X-Lease-Token", "") or self.headers.get(
            "lease-token", ""
        )
        if not self.store.result_token_ok(jid, lease_token):
            self._json({"error": "lease mismatch — 非本 job 租约持有人"}, 403)
            return
        if not self.store.store_bc_epoch(jid, body):
            self._json({"error": "invalid epoch body"}, 400)
            return
        self._json({"job_id": jid, "status": "accepted"})

    # ---- GET /jobs/{id}/resume（最新 epoch 权重——worker 重领时接续训练）----
    def _get_bc_resume(self) -> None:
        if not self._auth_ok():
            return
        jid = self._job_id()
        if jid is None:
            self._json({"error": "not found"}, 404)
            return
        r = self.store.get_bc_resume(jid)
        if r is None:
            self._json({"error": "no resume checkpoint"}, 404)
            return
        self._json(r)

    # ---- GET /jobs/{id}/bc-metrics（训练机/run_bc 轮询每 epoch 指标行）----
    def _get_bc_metrics(self) -> None:
        if not self._auth_ok():
            return
        jid = self._job_id()
        if jid is None:
            self._json({"error": "not found"}, 404)
            return
        self._json({"job_id": jid, "rows": self.store.get_bc_metrics(jid)})


def make_server(
    store: _JobStore, port: int, token: str, host: str = "0.0.0.0"
) -> ThreadingHTTPServer:
    """构造 server（handler 注入 store + token）。

    host 默认 0.0.0.0（2026-09-16）：Tailscale 直连时云 worker 从 tailnet 入站访问，
    绑 127.0.0.1 会导致对端超时。单测/冒烟需回环时显式传 host="127.0.0.1"。
    """

    class Server(ThreadingHTTPServer):
        def __init__(self) -> None:
            super().__init__((host, port), HubHandler)
            self.token = token

    HubHandler.store = store
    return Server()


def main() -> None:
    ap = argparse.ArgumentParser(description="hub-server: remote PPO job queue (stdlib)")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--token", default="", help="Bearer token（云 worker 与训练主循环共享）")
    ap.add_argument("--token-file", default="", help="从文件读取 token（避免进程列表泄露，H10）")
    ap.add_argument(
        "--job-root", required=True, help="job 目录根（payload zip / 结果 / ppo_ckpt_remote）"
    )
    ap.add_argument(
        "--jsonl", required=True, help="training_log.jsonl 路径（job_pending/job_completed 账本）"
    )
    ap.add_argument(
        "--lock-file",
        default="",
        help="单实例锁路径（缺省 nn-training/.hub_server.<port>.lock；按端口键控）",
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
    # §单实例锁（2026-09-17，第二道闸）：端口守卫是「探测 → bind」的 TOCTOU —— 两个
    # starter 同时探测会双双通过（Windows 的 SO_REUSEADDR 还允许双绑，后启动者静默
    # 变僵尸）。锁用 O_CREAT|O_EXCL 把启动串行化，且能在**持有者身份可核验**的前提下
    # 自动接管陈旧锁（PID 复用 / 崩溃残留），不再出现「锁在、进程没了、永远启不来」。
    lock_path = args.lock_file or default_instance_lock_path("hub_server", args.port)
    if not acquire_instance_lock(lock_path, marker="hub_server", tag="hub-server"):
        sys.exit(1)
    atexit.register(release_instance_lock, lock_path)
    # §双监听守卫：Windows SO_REUSEADDR 允许双绑同端口（后启动者静默变僵尸）——
    # bind 前探测，端口已有活监听者即拒绝启动（2026-09-09 8787 双实例事故）。
    # 通配地址（0.0.0.0 / :: / ""）没有可连的语义 ⇒ 统一探回环，避免 0.0.0.0 在
    # Windows 上直接 WSAEADDRNOTAVAIL 而让守卫形同虚设。
    _probe_host = args.host if args.host not in ("0.0.0.0", "::", "") else "127.0.0.1"
    try:
        ensure_port_free(_probe_host, args.port)
    except RuntimeError as e:
        print(f"[hub-server] ERROR: {e}", flush=True)
        sys.exit(1)
    store = _JobStore(args.job_root, args.jsonl)
    srv = make_server(store, args.port, token, host=args.host)
    print(
        f"[hub-server] listening on {args.host}:{args.port} "
        f"job_root={args.job_root} jsonl={args.jsonl} claim_ttl={CLAIM_TTL_SEC}s",
        flush=True,
    )
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
