"""e2e/test_hold_e2e.py —— 接管（hold）全循环 + BC 独占：**端到端**（plan §4 e2e 的落地件）。

真 `hub.server` 子进程 + 真 HTTP + 真盘上形状（同 `test_auto_handoff_e2e.py` 的纪律：不 spawn
bun/node、不加载 torch、不跑真 rollout/PPO）。窗**全部调秒级**（`BCITY_HOLD_PROGRESS_STALE_SEC`
/ `BCITY_BC_PROGRESS_STALE_SEC`），所以 plan 里那句「900s 无进度 ⇒ 掉线 ⇒ 自动恢复」在本层是
**真的**跑出来的（不是模拟时钟、不是打桩）：

  ① 全循环（DoD e2e 前半）：开课（无模式）→ 云机 claim（包在盘 ⇒ hold）→ **协作被压**（真 HTTP
     claim 被拒，理由 `held:<worker>`）→ 进度静默超窗 ⇒ 掉线 ⇒ 协作**自动恢复** → 轮内一次进度
     事件把 hold **刷回 live**（守门①的后半：有完成事件就保活）→ 新盘 stale 接管 → 人工
     `release_hold=1` 强制解除。
  ② BC 独占链（Q5 / 需求 7 / DoD e2e 后半）：带 hold 的盘领 BC ⇒ 拒 `holding:<课>`；别的盘
     （自主型）领到 BC ⇒ 该盘接课程 ⇒ `busy`；BC 进度静默超窗 ⇒ **让出**（观测面不再算在飞 +
     下一个认领者兼现回收，账本 `lease-orphan-reaped`）⇒ 课程接管自动恢复。

先红后绿（本刀 A/B）：把 `_drain_blocked` 与 `_busy_locked` 的 BC 腿分别关掉 ⇒ ② 的对应断言
当场红（`holding:*` 与 `busy:*` 两条都不是装饰）；恢复后 2 passed（≈6s，窗是真的秒级）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # nn-training/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from common import net_http
from common.protocol import (
    COURSE_ENABLE_MARKER,
    OFFLINE_CLAIM_PATH,
    OFFLINE_HOLD_PATH,
    OFFLINE_PROGRESS_PATH,
    ROLE_HEADER,
)
from hub.store import _JobStore
from tests.subproc_util import spawn_bound_port

TOKEN = "e2e-holex-sekret"
C_A = "e2e-hold-a"
C_B = "e2e-hold-b"
C_C = "e2e-hold-c"
#: 秒级窗：hold 2.5s、BC 1.5s（见模块头：不是模拟时钟，是真等）。对着窗做断言的
#: 两步之间**先补一个进度锚**（真云机轮内也在这么打点）——否则满载的 xdist 下
#: 测试进程被换出去几百毫秒就可能把窗放过，把「被压」写成假红。
HOLD_STALE = 2.0
BC_STALE = 1.5
PACK = b"PK\x03\x04e2e-hold-pack"


# ────────────────────────── HTTP 小工具 ──────────────────────────


def _http(
    base: str, path: str, *, method: str = "GET", body: dict | None = None, role: str = ""
) -> tuple[int, dict]:
    """hub 端点（真 HTTP；4xx/5xx 也算「有答」）。`role` 非空 ⇒ 带归属头（`X-Battle-Role`）。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}
    if role:
        headers[ROLE_HEADER] = role
    req = urllib.request.Request(base + path, data=data, headers=headers, method=method)

    def _as_dict(raw: bytes) -> dict:
        try:
            loaded = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    try:
        with net_http.urlopen(req, timeout=10.0) as resp:
            return resp.status, _as_dict(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _as_dict(e.read())
    except Exception:
        return 0, {}


def _wait_for(pred, timeout: float, step: float = 0.05) -> bool:
    """轮询谓词（`timeout` 是挂起兜底，不是同步手段）。"""
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        # sleep-ok: 轮询步长（等的是谓词/状态）
        time.sleep(step)
    return bool(pred())


def _course_dirs(traj_root: Path, course: str) -> tuple[Path, Path]:
    """课程的盘上形状：`<traj>/<课>/{remote-jobs,training_log.jsonl,training-enabled.txt}`。"""
    d = traj_root / course
    job_root = d / "remote-jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    jsonl = d / "training_log.jsonl"
    jsonl.touch()
    (d / COURSE_ENABLE_MARKER).touch()
    (d / f"task-{course}.zip").write_bytes(PACK)  # 包在盘 ⇒ claim 成功即建 hold（Q1）
    return job_root, jsonl


def _seed_jobs(job_root: Path, jsonl: Path, jids: dict[str, dict]) -> None:
    """直写盘上形状（同 `test_auto_handoff_e2e.py::_seed_jobs`：不借训练侧的发布链）。"""
    st = _JobStore(job_root, jsonl)
    for jid, extra in jids.items():
        st.publish(jid, {"job_id": jid, "it": 1, "role": "online", **extra}, b"PK\x03\x04fake")


def _offline_claim(hub: _Hub, course: str, worker: str) -> tuple[int, dict]:
    """真云机形状的 claim（带 `?proto=2`；缺它 = 旧端，hub 会 409 拒收）。"""
    return _http(
        hub.base, f"{OFFLINE_CLAIM_PATH}?proto=2&course={course}&worker={worker}", method="POST"
    )


def _claim_job(hub: _Hub, jid: str, *, worker: str, role: str = "") -> tuple[int, dict]:
    return _http(
        hub.base,
        f"/jobs/{jid}/claim",
        method="POST",
        body={"mode": "exclusive", "worker_id": worker},
        role=role,
    )


def _hold(hub: _Hub, course: str) -> dict:
    st, body = _http(hub.base, f"{OFFLINE_HOLD_PATH}?course={course}")
    assert st == 200, body
    return body


def _inflight(hub: _Hub, course: str) -> list[dict]:
    st, body = _http(hub.base, "/admin/queue")
    assert st == 200, body
    return list(((body.get("courses") or {}).get(course) or {}).get("inflight") or [])


def _progress(hub: _Hub, course: str, lease: str) -> tuple[int, dict]:
    return _http(
        hub.base, f"{OFFLINE_PROGRESS_PATH}?course={course}&lease={lease}", method="POST"
    )


def _ledger_events(traj_root: Path, course: str) -> list[str]:
    p = traj_root / course / "training_log.jsonl"
    return [json.loads(ln).get("event") for ln in p.read_text(encoding="utf-8").splitlines() if ln]


# ────────────────────────── 真 hub 进程 ──────────────────────────


class _Hub:
    """真 `hub.server` 子进程（控制台那条 argv）；窗与环境显式隔离。"""

    def __init__(
        self, traj_root: Path, *, hold_stale_sec: float = 0.0, bc_stale_sec: float = 0.0
    ) -> None:
        def _argv(port: int) -> list[str]:
            return [
                sys.executable,
                "-u",
                "-m",
                "hub.server",
                "--port",
                str(port),
                "--host",
                "127.0.0.1",
                "--token",
                TOKEN,
                "--traj-root",
                str(traj_root),
                "--discover",
                "--discover-sec",
                "0.2",
                "--lock-file",
                str(traj_root / "hub.lock"),
            ]

        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            # 隔离四件：权重归档根（否则回传轮写真 weights/）· 两个秒级窗 ·
            # **控制台地址钉死到不可达端口**（2026-10-06 事故：hub 子进程没传它就回落
            # `127.0.0.1:8900` = 开发机上正在跑的 dashboard，而那个动作会写本机配置；
            # 这里 claim 会触发一次「请控制台核对包新鲜度」，端口 1 必拒、连不出去）。
            "BCITY_WEIGHTS_ARCHIVE_ROOT": str(traj_root / "weights-archive"),
            "BCITY_CONSOLE_URL": "http://127.0.0.1:1/",
        }
        if hold_stale_sec:
            env["BCITY_HOLD_PROGRESS_STALE_SEC"] = str(hold_stale_sec)
        if bc_stale_sec:
            env["BCITY_BC_PROGRESS_STALE_SEC"] = str(bc_stale_sec)
        srv = spawn_bound_port(_argv, cwd=str(ROOT), env=env)
        self.proc = srv.proc
        self.lines = srv.lines
        self.base = f"http://127.0.0.1:{srv.port}"

    def output(self) -> str:
        return "\n".join(self.lines)[-1200:]

    def ready(self, *, expect: list[str], timeout: float = 40.0) -> None:
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                break
            st, body = _http(self.base, "/admin/queue")
            if st == 200 and sorted(body.get("courses") or {}) == sorted(expect):
                return
            # sleep-ok: 轮询步长（等的是「hub 已就绪且课程表已登记」）
            time.sleep(0.1)
        raise AssertionError(
            f"hub-server 未就绪或课程表不对（rc={self.proc.poll()}）；输出：{self.output()}"
        )

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


# ──────────────────────── ① hold 全循环 ────────────────────────


def test_full_hold_cycle_recovery_then_takeover_then_revoke(tmp_path: Path) -> None:
    """开课无模式 → claim 建 hold → 协作被压 → 掉线自动恢复 → 打点保活 → 新盘接管 → 强制解除。"""
    traj = tmp_path / "traj"
    job_root, jsonl = _course_dirs(traj, C_A)
    _seed_jobs(job_root, jsonl, {"h" * 16: {}})  # 一份协作盘的 PPO 作业
    jid = "h" * 16
    hub = _Hub(traj, hold_stale_sec=HOLD_STALE)
    try:
        hub.ready(expect=[C_A])
        # ① 开课（无模式）+ 包在盘 ⇒ claim 成功即建 hold（Q1：无包不建 hold）
        st, body = _offline_claim(hub, C_A, "w-cloud")
        # 领到 = 200 + 嵌在 `lease` 里的 token（响应形状 = 云机侧 `claim_course` 读的那份）
        assert st == 200 and (body.get("lease") or {}).get("token"), body
        lease = str(body["lease"]["token"])
        hold = _hold(hub, C_A)
        assert hold["held"] is True and hold["state"] == "live", hold
        assert hold["worker_id"] == "w-cloud", hold
        # ② 协作被压：先补一个进度锚（模拟真实云机的轮内完成事件），再断言真 HTTP 被拒，
        #    理由具体（`held:<worker>`，P2 的文案口径）
        assert _progress(hub, C_A, lease)[0] == 200
        st, ref = _claim_job(hub, jid, worker="w-coop")
        assert st == 409 and "held:w-cloud" in str(ref.get("error")), ref
        # ③ 进度静默超窗 ⇒ 掉线（惰性判据）⇒ 协作**自动恢复**
        assert _wait_for(lambda: _hold(hub, C_A)["held"] is False, timeout=HOLD_STALE + 8.0), _hold(
            hub, C_A
        )
        assert _hold(hub, C_A)["state"] == "stale", _hold(hub, C_A)
        st, ok = _claim_job(hub, jid, worker="w-coop")
        assert st == 200 and ok.get("lease_token"), ok
        # ④ 轮内一次进度事件把 hold **刷回 live**（守门①：有完成事件 ⇒ 保活；心跳不作活性）
        st_p, ok = _progress(hub, C_A, lease)
        assert st_p == 200 and ok.get("ok") is True, (st_p, ok)
        assert _hold(hub, C_A)["state"] == "live", _hold(hub, C_A)
        # ⑤ 再静默超窗 ⇒ 新盘 **stale 自动接管**（不需要 takeover=1）
        assert _wait_for(lambda: _hold(hub, C_A)["state"] == "stale", timeout=HOLD_STALE + 8.0)
        st, body2 = _offline_claim(hub, C_A, "w-cloud-2")
        assert st == 200 and (body2.get("lease") or {}).get("token"), body2
        assert _hold(hub, C_A)["worker_id"] == "w-cloud-2", _hold(hub, C_A)
        # ⑥ 人工强制解除（控制台那颗唯一解除钮）= revoke
        st, rel = _http(hub.base, f"/admin/courses?course={C_A}&release_hold=1", method="POST")
        assert st == 200 and rel.get("released") is True, rel
        assert _hold(hub, C_A)["held"] is False, _hold(hub, C_A)
    finally:
        hub.close()


# ──────────────────────── ② BC 独占链 ────────────────────────


def test_bc_exclusivity_chain_end_to_end(tmp_path: Path) -> None:
    """Q5 / 需求 7：hold × BC 互斥两向；BC 无备份；超窗让出（账本留凭据）后课程自动恢复。"""
    traj = tmp_path / "traj"
    job_a, jsonl_a = _course_dirs(traj, C_A)
    _seed_jobs(job_a, jsonl_a, {"a" * 16: {}})
    job_b, jsonl_b = _course_dirs(traj, C_B)
    _seed_jobs(job_b, jsonl_b, {"b" * 16: {"kind": "bc"}})
    job_c, jsonl_c = _course_dirs(traj, C_C)  # 空课：只用来验「在跑 BC 的盘接不了课程」
    bc_jid = "b" * 16
    hub = _Hub(traj, hold_stale_sec=HOLD_STALE, bc_stale_sec=BC_STALE)
    try:
        hub.ready(expect=[C_A, C_B, C_C])
        # 前置：w-off 在 A 上建了一个 live hold（包在盘 ⇒ claim 成功）
        st, body = _offline_claim(hub, C_A, "w-off")
        assert st == 200 and (body.get("lease") or {}).get("token"), body
        assert _hold(hub, C_A)["held"] is True, _hold(hub, C_A)
        # ① 带 hold 的盘领 BC ⇒ 拒（`holding:<课>`；跨课程腿，判据在 hub 侧）
        #    （先补一个进度锚：下一步的判据是「hold 正 live」）
        assert _progress(hub, C_A, str(body["lease"]["token"]))[0] == 200
        st, ref = _claim_job(hub, bc_jid, worker="w-off", role="offline")
        assert st == 409 and f"holding:{C_A}" in str(ref.get("error")), ref
        # ② 双角色：另一台（自主型）领同一份 BC ⇒ 200（只豁免角色闸）
        st, got = _claim_job(hub, bc_jid, worker="w-bc", role="offline")
        assert st == 200 and got.get("lease_token"), got
        # ③ BC 无备份副本：任何盘拿 backup 来领 ⇒ 当面拒（no_backup）
        st, ref = _http(
            hub.base,
            f"/jobs/{bc_jid}/claim",
            method="POST",
            body={"mode": "backup", "worker_id": "w-x"},
            role="online",
        )
        assert st == 409 and "no_backup" in str(ref.get("error")), ref
        # ④ 在跑 BC 的盘接课程 ⇒ busy（`_busy_locked` 的 BC 腿；文案点名 BC，不是笼统的忙）
        st, ref = _offline_claim(hub, C_C, "w-bc")
        assert st == 409 and ref.get("busy") is True, ref
        assert "BC 作业" in str(ref.get("error")) and C_B in str(ref.get("error")), ref
        # ⑤ 观测面：超窗后这份 BC **不再算在飞**（池里回了它；惰性判据、无清理线程）
        assert _wait_for(lambda: _inflight(hub, C_B) == [], timeout=BC_STALE + 8.0), _inflight(
            hub, C_B
        )
        # ⑥ 让出被下一个认领者兼现：换人重领 ⇒ 成功，且回收点留下账本凭据
        #    （`lease-orphan-reaped` 与过期同路回收；观测面的 `inflight` 只是不再算它）
        st, again = _claim_job(hub, bc_jid, worker="w-bc-2", role="offline")
        assert st == 200 and again.get("lease_token"), again
        assert "lease-orphan-reaped" in _ledger_events(traj, C_B), _ledger_events(traj, C_B)
        # ⑦ 课程接管自动恢复（BC 已让出 ⇒ w-bc 不再算 drain，立刻能接课程）
        st, body = _offline_claim(hub, C_C, "w-bc")
        assert st == 200 and (body.get("lease") or {}).get("token"), body
        # ⑧ A 上的 hold 不受 BC 那条链影响（两件事正交）
        assert _hold(hub, C_A)["worker_id"] == "w-off", _hold(hub, C_A)
    finally:
        hub.close()
