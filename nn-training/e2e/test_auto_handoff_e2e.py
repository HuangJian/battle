"""e2e/test_auto_handoff_e2e.py —— 自动离线交接的**端到端**（plan/auto-offline-handoff，2026-10-03）。

真 `hub.server` 子进程 + 真 HTTP + 真云机 `claim_course` + **假控制台**（只收 POST 并记录）。
层 = `e2e/`（hermetic：不 spawn bun/node、不加载 torch、不跑真 rollout/PPO/eval）。

覆盖的链路（一条主线 + 四条专项）：

  ① **全链**（`test_full_flow_online_course_to_offline_completion`）：在训、online、**无包**的课
     → 清单给 `claimable + auto_handoff`（P0-1 的唯一入口）
     → 云机 claim 无包 ⇒ **409 `pending_export`**（不是 404）+ **真触发控制台**（假控制台收到）
       + mode 翻 offline + `offline-dispatch.json` 落盘（§3.8 数据损坏防线）
     → 控制台导包完成（测试直接放一份包到盘上）
     → 云机重 claim ⇒ 200 + 租约
     → 云机报段末摘要 `end_it_reached`
     → `/admin/offline.results` 按 run_id 带出（T6 读面）∧ 清单 `completed` + `claimable=false`
     → 换人再 claim ⇒ 409 `completed`（P1-1：不可再领）
     → 重导包（sha 变）⇒ 自动解封（同一份租约下不再报 completed）
  ② **U2 一拖一**（hub 侧不变量，不是云机自觉）：第一门在交接窗口 ⇒ 第二门 claim 得 409 `busy`
     且**第二门 mode 仍是 online**（U2 的题眼：第一门 drain 时其余照常在线推进）。
  ③ **T8 停摆告警**：已翻 offline、无租约、无进度 ⇒ `/admin/offline.stalled` 点名该课
     （文案由控制台渲染；hub 只出判据）。
  ④ **T0 撤单**：`&drop_jobs=1` 把该课**未认领**的 job 作物（`/admin/queue.pending_n` 归零）；
     不带 `drop_jobs` 的切模式**一个 job 都不动**（停课「队列一字不动」契约）。
  ⑤ **T2 云机腿**：`offline_boot.claim_course` 对中间态返回 `reason` 码而非裸 token ——
     调用方据此**本拍不跑**（旧口径「一律照旧跑」会无租约干等 30 分钟再 SystemExit）。
  ⑥ **离线优先**（2026-10-03 用户裁决）：就绪离线课 + pin online 的在训课 ⇒ 真
     `resolve_courses` 只回离线那门（在线那门带 `seize=True`，可抢但不抢）。
  ⑦ **抢占全循环**（用户裁决「领到的课训练完成后再次开启接活循环」）：没有离线课 ⇒ 真
     `_run_auto` 循环按 `open_time` 逐门抢占在训在线课（hub 逐门翻 offline；假训练报
     `end_it_reached` ⇒ 换下一门），全部跑完才收工。

纪律：不 spawn bun/node、不加载 torch；HTTP 全在本机临时端口；hub 子进程的 env 显式隔离
（权重归档根 + 假控制台 URL + 停摆阈值）。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # nn-training/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from common import net_http
from common.protocol import (
    COURSE_ENABLE_MARKER,
    OFFLINE_CLAIM_PATH,
    OFFLINE_RESULT_PATH,
    OFFLINE_TASKS_PATH,
)
from hub.store import _JobStore
from tests.subproc_util import spawn_bound_port

TOKEN = "e2e-autohandoff-sekret"
C_AUTO = "e2e-auto-a"
C_OTHER = "e2e-auto-b"

#: 自动交接控制台的端点（与生产同路径；`hub/task_pack.py::AUTO_HANDOFF_CONSOLE_PATH`）。
CONSOLE_HANDOFF_PATH = "/api/autoOfflineHandoff"


# ────────────────────────── HTTP 小工具 ──────────────────────────


def _http(
    base: str, path: str, *, method: str = "GET", body: dict | None = None, token: str = TOKEN
) -> tuple[int, dict]:
    """hub 管理端点（真 HTTP；4xx/5xx 也算「有答」，返回状态码与体）。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method=method,
    )

    def _as_dict(raw: bytes) -> dict:
        try:
            loaded = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    try:
        # 回环绕开环境代理（本机若有 HTTP_PROXY，裸 urllib 会把 127.0.0.1 也送出去）
        with net_http.urlopen(req, timeout=10.0) as resp:
            return resp.status, _as_dict(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _as_dict(e.read())
    except Exception:
        return 0, {}


def _wait_for(pred, timeout: float = 10.0, step: float = 0.05) -> bool:
    """轮询一个谓词直到为真（`timeout` 只是挂起兜底，不是同步手段）。"""
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        # sleep-ok: 轮询步长（等的是谓词，超时只当挂起兜底）
        time.sleep(step)
    return bool(pred())


# ────────────────────────── 盘上形状 ──────────────────────────


def _course_dirs(traj_root: Path, course: str) -> tuple[Path, Path]:
    """课程的盘上形状（与生产逐字节同构）：`<traj>/<课>/{remote-jobs,training_log.jsonl,
    training-enabled.txt}` —— 开课标记是 hub 认课的那道显式闸。"""
    d = traj_root / course
    job_root = d / "remote-jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    jsonl = d / "training_log.jsonl"
    jsonl.touch()
    (d / COURSE_ENABLE_MARKER).touch()
    return job_root, jsonl


def _write_pack(traj_root: Path, course: str, payload: bytes) -> Path:
    """放一份任务包（模拟控制台导出完成；sha 变即解封 completed）。"""
    d = traj_root / course
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"task-{course}.zip"
    p.write_bytes(payload)
    return p


def _seed_jobs(job_root: Path, jsonl: Path, jids: list[str]) -> None:
    """直接往盘上塞若干**未认领**的 job（payload + manifest + 账本 `job_pending`）。

    为什么不走训练侧的 `remote.hub_client.publish_job`：它每次发布都会顺手做 §381 的
    悬空清理（`cancel_stale_jobs`：作废所有 `it <= 本次` 且非本次的 pending）⇒ **造不出
    两个并存的 pending**（实测账本：`job_pending j1` → `job_cancelled j1` → `job_pending j2`）。
    本层要的是 hub 侧看到的**盘面形状**，直写才是同一件事的最小构造（`tests/hub` 同款）。
    """
    st = _JobStore(job_root, jsonl)
    for jid in jids:
        st.publish(jid, {"job_id": jid, "it": 1, "role": "online"}, b"PK\x03\x04fake")


def _task_row(tasks_body: dict, course: str) -> dict:
    for row in tasks_body.get("tasks") or []:
        if isinstance(row, dict) and row.get("course") == course:
            return dict(row)
    raise AssertionError(f"清单里没有 {course}：{tasks_body}")


def _mode_of(hub: _Hub, course: str) -> str:
    st, body = _http(hub.base, "/admin/courses")
    assert st == 200, body
    for row in body.get("courses") or []:
        if row.get("course") == course:
            return str(row.get("mode") or "")
    raise AssertionError(f"/admin/courses 里没有 {course}：{body}")


def _queue_course(hub: _Hub, course: str) -> dict:
    st, body = _http(hub.base, "/admin/queue")
    assert st == 200, body
    return (body.get("courses") or {}).get(course) or {}


# ────────────────────────── 假控制台 ──────────────────────────


class _FakeConsole:
    """假控制台：只处理 `POST /api/autoOfflineHandoff`，记录 body。

    真控制台收到它之后会「写 `rollout_src=run`（本机停采）+ 导包」——那两件是 TS 侧的职责
    （`dashboard/tests/course-mode.test.ts` 钉住）；本层验证的是 **hub 真的发出了这个请求**。
    """

    def __init__(self) -> None:
        calls: list[dict] = []

        class _Handler(BaseHTTPRequestHandler):
            # `do_POST` 是 BaseHTTPRequestHandler 的约定方法名（不是本文件的命名风格选择）
            def do_POST(self) -> None:
                raw_len = int(self.headers.get("Content-Length", "0") or 0)
                raw = self.rfile.read(raw_len) if raw_len else b""
                try:
                    payload = json.loads(raw.decode("utf-8")) if raw else {}
                except ValueError:
                    payload = {}
                calls.append({"path": self.path, "body": payload})
                out = json.dumps({"ok": True}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

            def log_message(self, *args: object) -> None:  # 静音（测试不想被 access log 刷屏）
                return None

        self.calls = calls
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def handoff_courses(self) -> list[str]:
        """被请求过的 `autoOfflineHandoff` 课程名（顺序即请求顺序）。"""
        return [
            str(c["body"].get("course") or "")
            for c in self.calls
            if str(c["path"]).startswith(CONSOLE_HANDOFF_PATH)
        ]

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


# ────────────────────────── 真 hub 进程 ──────────────────────────


class _Hub:
    """真 `hub.server` 子进程（控制台实际启动的那条 argv：`--traj-root --discover`）。

    env 显式隔离三件：权重归档根（否则回传轮往真 `nn-training/weights/` 写）、假控制台 URL、
    停摆阈值（用例想秒级触发告警而不是等 1800s 默认值）。
    """

    def __init__(
        self,
        traj_root: Path,
        *,
        console_url: str = "",
        stall_sec: float = 0.0,
        lease_stale_sec: float = 0.0,
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
                # 锁文件落临时目录：缺省住 nn-training/（会向仓库目录撒 .hub_server.<port>.lock）
                "--lock-file",
                str(traj_root / "hub.lock"),
            ]

        env = {
            **os.environ,
            "PYTHONPATH": str(ROOT),
            "BCITY_WEIGHTS_ARCHIVE_ROOT": str(traj_root / "weights-archive"),
        }
        if console_url:
            env["BCITY_CONSOLE_URL"] = console_url
        if stall_sec:
            env["BCITY_OFFLINE_STALL_SEC"] = str(stall_sec)
        if lease_stale_sec:
            # ★六轮 §3.3：租约「连续静默」阈值（e2e 不想真等 180s；hub/task_pack 调用时读 env）
            env["BCITY_OFFLINE_LEASE_STALE_SEC"] = str(lease_stale_sec)
        srv = spawn_bound_port(_argv, cwd=str(ROOT), env=env)
        self.port = srv.port
        self.proc = srv.proc
        self.lines = srv.lines
        self.base = f"http://127.0.0.1:{self.port}"

    def output(self) -> str:
        return "\n".join(self.lines)[-1200:]

    def ready(self, *, expect: list[str], timeout: float = 40.0) -> None:
        """`/admin/queue` 能答 **且**课程表已就位（课程表是后台扫描登记进来的）。"""
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                break
            st, body = _http(self.base, "/admin/queue")
            if st == 200 and sorted(body.get("courses") or {}) == sorted(expect):
                return
            # sleep-ok: 轮询步长（等的是「hub 已就绪且课程表已登记」这个状态）
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


# ─────────────── ① 全链：在训在线课 → claim 接管 → 跑满 → 不可再领 → 重导解封 ───────────────


def test_full_flow_online_course_to_offline_completion(tmp_path: Path) -> None:
    """主线端到端（U1/U3/U6 + P0-1 + T6）：一条链走完，每步都断言在**真 HTTP**上。"""
    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)  # 在训、无包
    console = _FakeConsole()
    hub = _Hub(traj, console_url=console.url)
    try:
        hub.ready(expect=[C_AUTO])

        # ---- ① 清单面：未 pin 的在训课即便**无包**也可领（P0-1 的唯一入口）----
        st, tasks = _http(hub.base, OFFLINE_TASKS_PATH)
        assert st == 200, tasks
        row = _task_row(tasks, C_AUTO)
        assert row["claimable"] is True, row
        assert row["auto_handoff"] is True, row
        assert row["state"] == "no_pack", row
        assert row["pack"] is None, row

        # ---- ② claim 无包 ⇒ 409 指路（不是 404）+ mode 翻 + 触发控制台 ----
        st2, body = _http(hub.base, f"{OFFLINE_CLAIM_PATH}?course={C_AUTO}&worker=w-tpu", method="POST")
        assert st2 == 409, body
        assert body["auto_handoff"] is True and body["pending_export"] is True, body
        assert body["triggered"] is True and body["give_up"] is False, body
        assert "已翻成离线" in body["error"], body
        # 真触发到了假控制台（跨进程：hub 子进程 → 本测试进程的 HTTP server）
        assert _wait_for(lambda: console.handoff_courses() == [C_AUTO]), (
            f"控制台没被触发；hub 输出：{hub.output()}"
        )
        # mode 已翻 + 派发状态落盘（§3.8：重启后不能退回 online）
        assert _mode_of(hub, C_AUTO) == "offline"
        assert (traj / C_AUTO / "offline-dispatch.json").is_file()

        # ---- ③ 控制台导包完成（本层直接放包到盘上）⇒ 云机重 claim 拿租约 ----
        _write_pack(traj, C_AUTO, b"PK\x03\x04pack-v1")
        st3, body3 = _http(
            hub.base, f"{OFFLINE_CLAIM_PATH}?course={C_AUTO}&worker=w-tpu", method="POST"
        )
        assert st3 == 200 and body3.get("lease"), body3

        # ---- ④ 云机报段末摘要（跑满）----
        st4, res4 = _http(
            hub.base,
            OFFLINE_RESULT_PATH,
            method="POST",
            body={
                "course": C_AUTO,
                "run_id": "seg-1",
                "it_end": 8,
                "state": "complete",
                "end_it_reached": True,
            },
        )
        assert st4 == 200 and res4.get("end_it_reached") is True, res4
        # T6 读面：控制台按 run_id 对齐它，把 end_it_reached 转交 python 落 run_complete
        st5, admin = _http(hub.base, "/admin/offline")
        assert st5 == 200, admin
        assert admin["results"][C_AUTO]["seg-1"]["end_it_reached"] is True, admin["results"]

        # ---- ⑤ completed ⇒ 不可再领（P1-1）；换人（有活租约）也拿不到 ----
        st6, tasks6 = _http(hub.base, OFFLINE_TASKS_PATH)
        row6 = _task_row(tasks6, C_AUTO)
        assert row6["state"] == "completed", row6
        assert row6["claimable"] is False, row6
        st7, body7 = _http(
            hub.base, f"{OFFLINE_CLAIM_PATH}?course={C_AUTO}&worker=w-other", method="POST"
        )
        assert st7 == 409, body7
        assert body7.get("completed") is True, body7

        # ---- ⑥ 重导包（sha 变）⇒ 自动解封：不再报 completed（此时报的是 held：租约还在）----
        _write_pack(traj, C_AUTO, b"PK\x03\x04pack-v2-DIFFERENT")
        st8, body8 = _http(
            hub.base, f"{OFFLINE_CLAIM_PATH}?course={C_AUTO}&worker=w-other", method="POST"
        )
        assert st8 == 409, body8
        assert body8.get("completed") is not True, f"新包 sha 变必须解封 completed：{body8}"
        # 拿租约的那个人（同 worker 续领）仍然正常 —— `lease_verdict` 的 `mine` 档。
        # 注意：token 每次 claim 都会重发（`secrets.token_hex`），所以只判「又拿到了租约」。
        st9, body9 = _http(
            hub.base, f"{OFFLINE_CLAIM_PATH}?course={C_AUTO}&worker=w-tpu", method="POST"
        )
        assert st9 == 200 and body9.get("lease"), body9
    finally:
        hub.close()
        console.close()


# ─────────────── ② U2 一拖一：第一门在交接窗口 ⇒ 第二门 busy 且**不被翻 offline** ───────────────


def test_second_course_stays_online_while_first_drains(tmp_path: Path) -> None:
    """U2 是 **hub 侧不变量**（云机不可信）：第一门占着交接窗口，第二门领不到、且 mode 不动。"""
    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)
    _course_dirs(traj, C_OTHER)
    console = _FakeConsole()
    hub = _Hub(traj, console_url=console.url)
    try:
        hub.ready(expect=[C_AUTO, C_OTHER])

        # 第一门：claim 无包 ⇒ 翻 offline + 进交接窗口（占闸）
        st1, body1 = _http(
            hub.base, f"{OFFLINE_CLAIM_PATH}?course={C_AUTO}&worker=w-tpu", method="POST"
        )
        assert st1 == 409 and body1["pending_export"] is True, body1
        assert _mode_of(hub, C_AUTO) == "offline"

        # 第二门：不是「也在导包」，而是明确的 busy（闸在 hub 侧）
        st2, body2 = _http(
            hub.base, f"{OFFLINE_CLAIM_PATH}?course={C_OTHER}&worker=w-tpu", method="POST"
        )
        assert st2 == 409, body2
        assert body2.get("busy") is True, body2
        assert body2.get("pending_export") is not True, body2
        # ★ 题眼：第二门**没被翻 offline**（其余课程照常在线推进）
        assert _mode_of(hub, C_OTHER) == "online"
        # 清单面同源：第二门 claimable=false，且拒因点名第一门
        st3, tasks3 = _http(hub.base, OFFLINE_TASKS_PATH)
        row3 = _task_row(tasks3, C_OTHER)
        assert row3["claimable"] is False and "busy" in str(row3["reason"]), row3
        assert C_AUTO in str(row3["reason"]), row3
    finally:
        hub.close()
        console.close()


# ─────────────── ③ T8 停摆告警：已翻 offline、无租约、无进度 ───────────────


def test_stalled_course_raises_alert(tmp_path: Path) -> None:
    """自动化的固有代价必须显式付：claim 翻完 offline 却没人跑 ⇒ `/admin/offline.stalled` 点名。"""
    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)
    console = _FakeConsole()
    # 阈值调到秒级：默认 1800s 会让用例「等或假钟」，而我们只要判据本身
    hub = _Hub(traj, console_url=console.url, stall_sec=0.5)
    try:
        hub.ready(expect=[C_AUTO])
        # 安静时无告警（别把「一切正常」也画成红的）
        st0, off0 = _http(hub.base, "/admin/offline")
        assert st0 == 200 and off0["stalled"] == [], off0

        # claim 无包 ⇒ 翻 offline、无租约、包未出现 ⇒ 导包窗口超阈 ⇒ pending-export
        st1, body1 = _http(
            hub.base, f"{OFFLINE_CLAIM_PATH}?course={C_AUTO}&worker=w-tpu", method="POST"
        )
        assert st1 == 409 and body1["pending_export"] is True, body1

        def _stalled() -> list[dict]:
            st, off = _http(hub.base, "/admin/offline")
            return (off.get("stalled") or []) if st == 200 else []

        assert _wait_for(lambda: any(r.get("course") == C_AUTO for r in _stalled()), timeout=15.0), (
            f"停摆告警没出现；输出：{hub.output()}"
        )
        hit = next(r for r in _stalled() if r.get("course") == C_AUTO)
        assert hit["why"] == "pending-export", hit
        assert hit["holder"] is None, hit
        assert float(hit["flipped_at"]) > 0.0, hit
    finally:
        hub.close()
        console.close()


# ─────────────── ④ T0 撤单：`&drop_jobs=1` 只撤未认领的 ───────────────


def test_drop_jobs_cancels_unclaimed_online_jobs(tmp_path: Path) -> None:
    """切模式撤单（plan/switch-mode-drops-jobs）：撤**没被领的**；不带参数则一个都不动。"""
    traj = tmp_path / "traj"
    job_root, jsonl = _course_dirs(traj, C_AUTO)
    _seed_jobs(job_root, jsonl, ["job-drop-1", "job-drop-2"])  # 同一门课的两个未认领 job
    hub = _Hub(traj)
    try:
        hub.ready(expect=[C_AUTO])
        assert _queue_course(hub, C_AUTO)["pending_n"] == 2, _queue_course(hub, C_AUTO)

        # 不带 drop_jobs：切模式但**队列一字不动**（停课/回灌走的就是这条）
        st1, body1 = _http(hub.base, f"/admin/courses?course={C_AUTO}&mode=offline&pin=1", method="POST")
        assert st1 == 200, body1
        assert _queue_course(hub, C_AUTO)["pending_n"] == 2, "不带 drop_jobs 不许动队列"

        # 带 drop_jobs=1：未认领的 job 全作废
        st2, body2 = _http(
            hub.base,
            f"/admin/courses?course={C_AUTO}&mode=offline&pin=1&drop_jobs=1",
            method="POST",
        )
        assert st2 == 200, body2
        assert _queue_course(hub, C_AUTO)["pending_n"] == 0, _queue_course(hub, C_AUTO)
        # 幂等：再撤一次不炸也不变
        st3, body3 = _http(
            hub.base,
            f"/admin/courses?course={C_AUTO}&mode=offline&pin=1&drop_jobs=1",
            method="POST",
        )
        assert st3 == 200, body3
        assert _queue_course(hub, C_AUTO)["pending_n"] == 0
    finally:
        hub.close()


# ─────────────── ⑤ T2 云机腿：中间态必须回 reason 码（调用方据此**不跑**） ───────────────


def test_cloud_claim_course_middle_states_do_not_run(tmp_path: Path) -> None:
    """`offline_boot.claim_course` 对无包 claim 回 `pending_export`（不是「被别人持有」）。

    这是二轮 P0-1 的负向锚：旧口径「领不到也照跑」会让云机无租约进入 run、干等 30 分钟再
    `SystemExit`，而 hub 的 busy 闸 / stalled 判据（都以活租约为据）**全部观测不到**。
    """
    from remote.offline_boot import claim_course

    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)
    console = _FakeConsole()
    hub = _Hub(traj, console_url=console.url)
    try:
        hub.ready(expect=[C_AUTO])
        logs: list[str] = []
        lease, why = claim_course(hub.base, TOKEN, C_AUTO, "w-cloud", logs.append)
        assert lease == "" and why == "pending_export", (lease, why)
        # 中间态名单（`_run_batch` 的判据）：本拍不跑、不吃 idle 预算、到上界才放弃
        assert why in ("busy", "pending_export", "completed", "not_offline")
        assert any("本拍不跑" in ln for ln in logs), logs
    finally:
        hub.close()
        console.close()


# ─────────── ⑥ 离线优先：有就绪的离线课就不抢在训在线课（2026-10-03 用户裁决） ───────────


def test_resolve_prefers_offline_course_over_seizing_a_live_online_course(tmp_path: Path) -> None:
    """用户口径：「优先取当时就绪的离线课程；没有离线才抢第一个在训在线课」——真 HTTP 上验。

    ★六轮 §4.2 半回摆：可抢的只能是在训的 **auto** 课（开课未选模式 / 交还自动）；
    pin online 重新获得阻止力⇒ 那门课不再带 `seize`（其专属用例 = 本文件 T2）。
    """
    traj = tmp_path / "traj"
    _course_dirs(traj, "e2e-off-ready")
    _course_dirs(traj, "e2e-on-live")
    _write_pack(traj, "e2e-off-ready", b"PK\x03\x04off")
    _write_pack(traj, "e2e-on-live", b"PK\x03\x04on")
    hub = _Hub(traj)
    try:
        hub.ready(expect=["e2e-off-ready", "e2e-on-live"])
        st, body = _http(
            hub.base, "/admin/courses?course=e2e-off-ready&mode=offline&pin=1", method="POST"
        )
        assert st == 200, body
        st, body = _http(
            hub.base, "/admin/courses?course=e2e-on-live&mode=online&pin=0", method="POST"
        )
        assert st == 200, body

        st, tasks = _http(hub.base, OFFLINE_TASKS_PATH)
        assert st == 200, tasks
        row_off = _task_row(tasks, "e2e-off-ready")
        row_on = _task_row(tasks, "e2e-on-live")
        assert row_off["seize"] is False and row_off["claimable"] is True, row_off
        # 未 pin 的在训在线课（auto 档）带 seize=True（可抢，但本拍不抢——离线课优先）
        assert row_on["authority"] == "auto", row_on
        assert row_on["seize"] is True and row_on["claimable"] is True, row_on

        from remote.offline_boot import resolve_courses

        got, blocked = resolve_courses(
            {"hub_url": hub.base}, {"HUB_TOKEN": TOKEN}, lambda _m: None
        )
        assert [t["course"] for t in got] == ["e2e-off-ready"], got
        assert blocked == [], blocked
    finally:
        hub.close()


# ─────────── ⑦ 抢占全循环：没有离线课 ⇒ 逐门抢在训在线课（open_time 升序） ───────────


def test_auto_loop_seizes_in_training_courses_in_open_time_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户口径的全链：领到的课跑完后**再次开启接活循环**。

    就绪离线课（C_OFF）先跑；跑完没有离线课 ⇒ 按 `open_time` 抢 C_ON1 → C_ON2，
    hub 每抢一门就把它翻成 offline；全部跑完循环才收工。真 hub 子进程 + 真 HTTP +
    真 `_run_auto`/`resolve_courses`/`claim_course`，只把真训练换成假跑。
    """
    traj = tmp_path / "traj"
    _course_dirs(traj, "e2e-off-first")
    for name, when in (("e2e-on-1", 1000.0), ("e2e-on-2", 2000.0)):
        _course_dirs(traj, name)
        os.utime(traj / name / COURSE_ENABLE_MARKER, (when, when))  # 开课时间顺序（SSOT）
    for name in ("e2e-off-first", "e2e-on-1", "e2e-on-2"):
        _write_pack(traj, name, f"PK\x03\x04{name}".encode())
    hub = _Hub(traj)
    try:
        hub.ready(expect=["e2e-off-first", "e2e-on-1", "e2e-on-2"])
        st, body = _http(
            hub.base, "/admin/courses?course=e2e-off-first&mode=offline&pin=1", method="POST"
        )
        assert st == 200, body

        from remote import offline_boot

        seen: list[str] = []

        def fake_run(cfg, creds, log, stop, *, course, multi=False, **kw):
            """假训练：记一笔 + 报段末摘要（`end_it_reached` ⇒ hub 记 completed，循环换下一门）。"""
            seen.append(course)
            st, res = _http(
                cfg["hub_url"],
                OFFLINE_RESULT_PATH,
                method="POST",
                body={
                    "course": course,
                    "run_id": f"seg-{course}",
                    "it_end": 8,
                    "state": "complete",
                    "end_it_reached": True,
                },
            )
            assert st == 200 and res.get("end_it_reached") is True, res
            return 0

        monkeypatch.setattr(offline_boot, "run_one_course", fake_run)
        lines: list[str] = []
        rc = offline_boot._run_auto(
            {
                "hub_url": hub.base,
                "work_dir": str(tmp_path / "work"),
                "queue_mode": "drain",
                "idle_wait_sec": 1.0,
                "queue_poll_sec": 0.2,
            },
            {"HUB_TOKEN": TOKEN},
            lines.append,
            None,
        )
        assert rc == 0
        assert seen == ["e2e-off-first", "e2e-on-1", "e2e-on-2"], (seen, lines[-20:])
        assert any("抢占第一个在训在线课" in ln for ln in lines), lines
        # hub 把两门在训在线课都翻成了 offline（用户口径「hub 将其改为离线」）
        assert _mode_of(hub, "e2e-on-1") == "offline"
        assert _mode_of(hub, "e2e-on-2") == "offline"
    finally:
        hub.close()


# ══════════════ ★六轮评审 T1–T5b/T8（plan/offline-online-status-switch §6.2）══════════════
#
# 层纪律（§6.1）：真 hub 子进程 + 真 HTTP + 真生产写者（`remote.hub_client.publish_job`）；
# 不真等 900s（交接窗口用「盘上旧 `claimed_at` 的记录」构造，烧满触发账本那条由 hub 侧
# 假钟用例 `test_handoff_trigger_budget_resets_on_new_round` 盖）。

from common.protocol import (
    OFFLINE_HEARTBEAT_PATH,
    ROLE_ONLINE,
)
from remote.hub_client import publish_job
from remote.job_lifecycle import claim_job, peek_jobs


def _admin_offline(hub: _Hub) -> dict:
    st, body = _http(hub.base, "/admin/offline")
    assert st == 200, body
    return body


def _prod_publish(traj_root: Path, course: str, *, it: int, run_id: str) -> dict:
    """**生产写者**（`remote/hub_client.publish_job`，磁盘 IPC）发布一份 role=online 的 job。

    为什么要它而不是直写 jsonl：第六轮 F1 的教训——hub 的 `store_ledger.publish` 在生产
    **零调用**，真实重发布路径是这条「无条件追加 `job_pending`」的磁盘 IPC。用例不经过它，
    就会在一条生产不会走的路径上绿着骗人。
    """
    d = traj_root / course
    init = traj_root / "_init_weights.json"
    if not init.exists():
        init.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
    return publish_job(
        job_root=d / "remote-jobs",
        jsonl_path=d / "training_log.jsonl",
        run_id=run_id,
        it=it,
        traj_dir=d / "it-src",
        shard_dirs=[],
        commit="c" * 40,
        code_sha256="z" * 64,
        course='// course jsonc\n{"reward": {"formula": "score"}}',
        course_fp="33" * 32,
        init_weights_path=str(init),
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=2,
        mb=512,
        lr=3e-4,
        log=lambda _m: None,
    )


def _offline_claim(hub: _Hub, course: str, worker: str) -> tuple[int, dict]:
    return _http(
        hub.base, f"{OFFLINE_CLAIM_PATH}?course={course}&worker={worker}", method="POST"
    )


def _peek_ids(hub: _Hub, *, role: str = ROLE_ONLINE) -> list[str]:
    got = peek_jobs(hub.base, TOKEN, worker_id="w-peek", role=role)
    assert got is not None, "peek 不可达"
    cands, _halt = got
    return [str(c.get("job_id") or "") for c in cands]


# ─────────── T1（报障一）：切在线撤租约 + **重发布同一 job_id 可领**（★F1 ③ 生产写者） ───────────


def test_manual_online_switch_revokes_lease_and_republished_job_is_claimable(
    tmp_path: Path,
) -> None:
    """用户报障一「切成在线不稳定」的端到端复现。

    链（每步都在真 HTTP / 真盘面上断言）：
      ① 生产写者发一份 role=online 的 job（同 it/runId ⇒ 幂等键固定）
      ② A 盘 claim（有包）⇒ hub 翻 offline 并**撤掉未认领 job**（`_cancelled` 进 hub 内存）
      ③ 人切 `mode=online&pin=1&drop_jobs=1` ⇒ 租约成**墓碑**
      ④ A 心跳 409 `revoked`；清单 `authority=pinned_online`、`claimable/seize=false`（照发行）
      ⑤ **重发布同一 job_id**（生产写者）⇒ peek 可见 ∧ 在线 role claim 200（旧 bug：永久 cancelled）
    """
    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)
    _write_pack(traj, C_AUTO, b"PK\x03\x04t1-pack")
    hub = _Hub(traj)
    try:
        hub.ready(expect=[C_AUTO])
        # ① 生产写者发布（磁盘 IPC；role 由 kind=ppo 推成 online）
        m1 = _prod_publish(traj, C_AUTO, it=1, run_id="t1-run")
        jid = str(m1["job_id"])
        assert jid
        # ② A claim（有包那条腿）⇒ 翻 offline + 撤单（`_cancelled`）
        st, body = _offline_claim(hub, C_AUTO, "w-a")
        assert st == 200 and body.get("lease"), body
        token = str(body["lease"]["token"])
        assert _mode_of(hub, C_AUTO) == "offline"
        assert _queue_course(hub, C_AUTO)["pending_n"] == 0, _queue_course(hub, C_AUTO)
        # ③ 人切固定在线（带 drop_jobs=1；P0-6 半球：只对 offline 生效）
        st2, body2 = _http(
            hub.base,
            f"/admin/courses?course={C_AUTO}&mode=online&pin=1&drop_jobs=1",
            method="POST",
        )
        assert st2 == 200, body2
        assert _mode_of(hub, C_AUTO) == "online"
        # ④ 墓碑 + 心跳 409 revoked + 清单行照发但锁住
        leases = _admin_offline(hub).get("leases") or {}
        assert leases.get(C_AUTO, {}).get("revoked") is True, leases
        st3, body3 = _http(
            hub.base, f"{OFFLINE_HEARTBEAT_PATH}?course={C_AUTO}&lease={token}", method="POST"
        )
        assert st3 == 409 and body3.get("revoked") is True, body3
        st4, tasks4 = _http(hub.base, OFFLINE_TASKS_PATH)
        row = _task_row(tasks4, C_AUTO)
        assert row["authority"] == "pinned_online", row
        assert row["claimable"] is False and row["seize"] is False, row
        assert str(row["reason"]).startswith("pinned:"), row
        # ⑤ 重发布同一 job_id（生产写者，同 it/runId）⇒ 池可见 ∧ claim 200
        m2 = _prod_publish(traj, C_AUTO, it=1, run_id="t1-run")
        assert m2["job_id"] == jid, "同一幂等键必须给同一 job_id（幂等键 = 课程+it+runId）"
        assert jid in _peek_ids(hub), f"重发布后 peek 里没有 {jid}"
        got = claim_job(hub.base, TOKEN, jid, worker_id="w-us", role=ROLE_ONLINE)
        assert got is not None and got["status"] == "ok", got
    finally:
        hub.close()


# ─────── T2（报障一）：pin online 不被抢——claim 拒 + 真 resolve_courses 不选它 ───────


def test_pinned_online_course_is_not_seized_nor_claimed_by_offline_disk(tmp_path: Path) -> None:
    """pin online = 人固定在线：离线盘 claim 409 `pinned_online`；真 `resolve_courses` 空表；
    「交还自动」后同一门课立刻回到可抢（`seize=true` + claim 200 并翻 offline）。"""
    from remote.offline_boot import resolve_courses

    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)
    _write_pack(traj, C_AUTO, b"PK\x03\x04t2-pack")
    hub = _Hub(traj)
    try:
        hub.ready(expect=[C_AUTO])
        st, body = _http(
            hub.base, f"/admin/courses?course={C_AUTO}&mode=online&pin=1", method="POST"
        )
        assert st == 200, body
        st2, body2 = _offline_claim(hub, C_AUTO, "w-a")
        assert st2 == 409 and body2.get("pinned_online") is True, body2
        assert _mode_of(hub, C_AUTO) == "online"
        # 真云机选课：没有别的课时一行都不给（不抢人固定的课）
        picks, blocked = resolve_courses(
            {"hub_url": hub.base}, {"HUB_TOKEN": TOKEN}, lambda _m: None
        )
        assert picks == [], picks
        # ★P1-9：pin online 的课不算「被别人持有」的等活（它是人固定的，不会自动放出来）
        assert blocked == [], blocked
        # 交还自动 ⇒ 回到自动池：可抢（seize）且 claim 立刻翻 offline
        st3, body3 = _http(
            hub.base, f"/admin/courses?course={C_AUTO}&mode=online&pin=0", method="POST"
        )
        assert st3 == 200, body3
        st4, tasks4 = _http(hub.base, OFFLINE_TASKS_PATH)
        row = _task_row(tasks4, C_AUTO)
        assert row["authority"] == "auto" and row["seize"] is True, row
        st5, body5 = _offline_claim(hub, C_AUTO, "w-a")
        assert st5 == 200 and body5.get("lease"), body5
        assert _mode_of(hub, C_AUTO) == "offline"
    finally:
        hub.close()


# ──────────── T3（报障二）：死持有人静默超阈 ⇒ 新盘自动接管，旧主心跳 409 ────────────


def test_dead_offline_holder_is_reclaimed_after_silence(tmp_path: Path) -> None:
    """A 停跳（超 `BCITY_OFFLINE_LEASE_STALE_SEC`）⇒ B 清单 `claimable + holder.stale`、
    claim 200（**不需要 `takeover=1`**、带 `reclaimed`）；A 心跳 409。"""
    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)
    _write_pack(traj, C_AUTO, b"PK\x03\x04t3-pack")
    hub = _Hub(traj, lease_stale_sec=2.0)
    try:
        hub.ready(expect=[C_AUTO])
        st, body = _offline_claim(hub, C_AUTO, "w-a")
        assert st == 200 and body.get("lease"), body
        tok_a = str(body["lease"]["token"])

        def _row() -> dict:
            _st, t = _http(hub.base, OFFLINE_TASKS_PATH)
            return _task_row(t, C_AUTO) if _st == 200 else {}

        assert _wait_for(
            lambda: bool(_row().get("holder")) and _row()["holder"].get("stale") is True,
            timeout=15.0,
        ), f"静默超阈没生效；输出：{hub.output()}"
        row = _row()
        assert row["claimable"] is True, row
        assert str(row["reason"]).startswith("held-stale"), row
        # B 接管：不需要 takeover=1；回执带 reclaimed_from
        st2, body2 = _offline_claim(hub, C_AUTO, "w-b")
        assert st2 == 200 and body2.get("lease"), body2
        assert body2["lease"].get("reclaimed") is True, body2
        assert body2["lease"].get("reclaimed_from") == "w-a", body2
        # 旧主心跳：409（token 对不上 / 已被接管）
        st3, body3 = _http(
            hub.base, f"{OFFLINE_HEARTBEAT_PATH}?course={C_AUTO}&lease={tok_a}", method="POST"
        )
        assert st3 == 409, body3
    finally:
        hub.close()


# ────── T4（报障二）：死盘不占「一拖一」闸——另一门课能被抢；对照组 409 busy ──────


def test_dead_holder_does_not_block_seizing_another_course(tmp_path: Path) -> None:
    """A 的租约 **活的** ⇒ B 领另一门课 409 `busy`（对照组）；A 静默超阈后 ⇒ 200。

    ⚠ 必须给**自己的**假控制台（2026-10-07）：这一组的 409 判据走「请控制台核对任务包新鲜度」
    那条路，而 `tests/conftest.py::pin_production_env` 把控制台地址钉在**死端口**上（2026-10-06
    事故的隔离面）⇒ 控制台不可达时 hub 会**跳过**一拖一闸、直接 200。旧写法（不传
    `console_url`）实际是在赌「开发机上正跑着 dashboard」，属于把测试打到真面上那一类。
    """
    traj = tmp_path / "traj"
    for c in (C_AUTO, C_OTHER):
        _course_dirs(traj, c)
        _write_pack(traj, c, f"PK\x03\x04{c}".encode())
    console = _FakeConsole()
    hub = _Hub(traj, lease_stale_sec=2.0, console_url=console.url)
    try:
        hub.ready(expect=[C_AUTO, C_OTHER])
        assert _offline_claim(hub, C_AUTO, "w-a")[0] == 200
        st, body = _offline_claim(hub, C_OTHER, "w-b")
        assert st == 409 and body.get("busy") is True, body
        # A 死掉（不再心跳）⇒ 静默超阈后闸自动解除
        def _row() -> dict:
            _st, t = _http(hub.base, OFFLINE_TASKS_PATH)
            return _task_row(t, C_AUTO) if _st == 200 else {}

        assert _wait_for(
            lambda: bool(_row().get("holder")) and _row()["holder"].get("stale") is True,
            timeout=15.0,
        ), f"静默超阈没生效；输出：{hub.output()}"
        st2, body2 = _offline_claim(hub, C_OTHER, "w-b")
        assert st2 == 200 and body2.get("lease"), body2
    finally:
        hub.close()
        console.close()


# ────── T5a（报障二）：导包窗口的**主人死掉且窗口过期** ⇒ 重开（可重触发、不占闸） ──────


def test_export_window_owner_death_reopens_the_course(tmp_path: Path) -> None:
    """无包 claim 翻 offline 后主人死掉、窗口（`AUTO_HANDOFF_PENDING_SEC=900`）过期：

    · 同一门课：B claim ⇒ 409 `pending_export`（可重触发，**不是** busy/give_up）
    · 窗口**不占闸**：B 能同时领另一门课（真 claim 200）

    窗口过期不真等 900s：起 hub **之前**在盘上写一条旧 `claimed_at` 的派发记录（`_dispatch_load`
    读的就是它）——这正是「hub 重启后旧窗口不再冻结全池」的现场形状（§3.8）。
    """
    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)
    _course_dirs(traj, C_OTHER)
    _write_pack(traj, C_OTHER, b"PK\x03\x04t5a-other")
    # 旧窗口记录：已翻 offline、有主、`claimed_at` 远超窗口（900s）、包还没出现
    old = time.time() - 5000.0
    (traj / C_AUTO / "offline-dispatch.json").write_text(
        json.dumps(
            {
                "v": 1,
                "mode": "offline",
                "pinned": False,
                "claimed_offline": True,
                "claimed_by": "w-dead",
                "claimed_at": old,
                "flipped_at": old,
                "updated_at": old,
                "completed_pack_sha": "",
            }
        ),
        encoding="utf-8",
    )
    console = _FakeConsole()
    hub = _Hub(traj, console_url=console.url)
    try:
        hub.ready(expect=[C_AUTO, C_OTHER])
        assert _mode_of(hub, C_AUTO) == "offline"  # 盘上记录优先（T1 数据损坏防线）
        # 顺序要紧：先证「旧窗口不占闸」（另一门课照常可领）——B 领 C_AUTO 会把 `claimed_at`
        # 刷成现在（新一轮交接正式开始），那一刻之后别的课又该被一拖一挡住。
        st0, body0 = _offline_claim(hub, C_OTHER, "w-b")
        assert st0 == 200 and body0.get("lease"), body0
        rel = _http(
            hub.base,
            f"/offline/release?course={C_OTHER}&lease={body0['lease']['token']}",
            method="POST",
        )
        assert rel[0] == 200, rel[1]
        # 同一门课：可重触发（不是 busy，也不是 give_up）
        st, body = _offline_claim(hub, C_AUTO, "w-b")
        assert st == 409, body
        assert body.get("pending_export") is True, body
        assert body.get("busy") is not True, body
        assert body.get("give_up") is not True, body
    finally:
        hub.close()
        console.close()


# ────── T5b（报障二 / F4）：换主 = 新一轮交接 ⇒ 触发账本重置（回 triggered，不是 throttled） ──────


def test_export_window_new_owner_resets_the_trigger_budget(tmp_path: Path) -> None:
    """无包 claim 的**触发账本**按「新一轮」重置：换 `worker_id` ⇒ 重新可触发。

    · A 第一次 claim ⇒ `triggered=true`（真推了假控制台）
    · A 立刻再来 ⇒ `throttled`（节流窗 600s 内，同一轮不重复推）
    · B（**不同 `worker_id`**）来 ⇒ `triggered=true`（★F4：换主 = 新一轮 ⇒ 账本重置）

    为什么不在 e2e 里烧满 3 次上界（`give_up`）：节流窗 600s 无法在 e2e 里跨过（§6.1「不许
    真等 900s」的同一纪律）；「烧满 + 换主/超窗 ⇒ 重置」那条腿由 hub 侧假钟用例
    `test_handoff_trigger_budget_resets_on_new_round` 盖住（判据同一处实现）。
    """
    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)
    console = _FakeConsole()
    hub = _Hub(traj, console_url=console.url)
    try:
        hub.ready(expect=[C_AUTO])
        st1, body1 = _offline_claim(hub, C_AUTO, "w-a")
        assert st1 == 409 and body1.get("triggered") is True, body1
        assert _wait_for(lambda: console.handoff_courses() == [C_AUTO], timeout=10.0), (
            f"控制台没被触发；输出：{hub.output()}"
        )
        st2, body2 = _offline_claim(hub, C_AUTO, "w-a")
        assert st2 == 409 and body2.get("triggered") is False, body2
        assert body2.get("pending_export") is True, body2
        # ★F4：换主 ⇒ 新一轮 ⇒ 重置（同主回来会被节流/上界挡住，这正是修复前的坏形状）
        st3, body3 = _offline_claim(hub, C_AUTO, "w-b")
        assert st3 == 409, body3
        assert body3.get("triggered") is True, body3
        assert len(console.handoff_courses()) == 2, console.handoff_courses()
    finally:
        hub.close()
        console.close()


# ──────── T7（报障一+二）：全循环 —— 在线 → A 接手 → A 死 → B 接管 → 人切在线 → 交还自动 → C 再抢 ────────


def test_full_cycle_offline_then_online_then_offline(tmp_path: Path) -> None:
    """一条课穿过全部归属档，每次转移断言 `mode/authority/lease/holder`；末尾账本无假 `run_complete`。

    链（真 hub 子进程 + 真 HTTP）：
      ① 在线（auto）：清单可抢（seize=true）
      ② A claim ⇒ 200 租约 + mode 翻 offline + holder=w-a
      ③ A 死（停跳）⇒ 静默超阈 ⇒ B 静态接管（`reclaimed_from=w-a`）；A 旧 token 心跳 409
      ④ 人切「固定在线」⇒ B 租约成**墓碑**（心跳 409 revoked）；行锁住（pinned_online、claimable/seize=false）
      ⑤ 交还自动 ⇒ 回 auto 池（seize=true）
      ⑥ C 再抢走 ⇒ 200 + mode 再翻 offline（报障一的「切回切走」全程可重复）
      ⑦ 账本无假 `run_complete`（假收官只在训练侧；hub 永不代替它写）
    """
    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)
    _write_pack(traj, C_AUTO, b"PK\x03\x04t7-pack")
    hub = _Hub(traj, lease_stale_sec=2.0)
    try:
        hub.ready(expect=[C_AUTO])
        # ① 在线（auto）：可抢
        st, tasks = _http(hub.base, OFFLINE_TASKS_PATH)
        assert st == 200, tasks
        row = _task_row(tasks, C_AUTO)
        assert row["authority"] == "auto" and row["seize"] is True, row

        # ② A 接手（claim 翻 offline）
        st1, body1 = _offline_claim(hub, C_AUTO, "w-a")
        assert st1 == 200 and body1.get("lease"), body1
        tok_a = str(body1["lease"]["token"])
        assert _mode_of(hub, C_AUTO) == "offline"
        assert _task_row(_http(hub.base, OFFLINE_TASKS_PATH)[1], C_AUTO)["holder"][
            "worker_id"
        ] == "w-a"

        # ③ A 死 ⇒ 静默超阈 ⇒ B 静态接管（T3 链）
        def _row() -> dict:
            _st, t = _http(hub.base, OFFLINE_TASKS_PATH)
            return _task_row(t, C_AUTO) if _st == 200 else {}

        assert _wait_for(
            lambda: bool(_row().get("holder")) and _row()["holder"].get("stale") is True,
            timeout=15.0,
        ), f"静默超阈没生效；输出：{hub.output()}"
        st2, body2 = _offline_claim(hub, C_AUTO, "w-b")
        assert st2 == 200 and body2.get("lease"), body2
        assert body2["lease"].get("reclaimed_from") == "w-a", body2
        tok_b = str(body2["lease"]["token"])
        st_a, _ = _http(
            hub.base, f"{OFFLINE_HEARTBEAT_PATH}?course={C_AUTO}&lease={tok_a}", method="POST"
        )
        assert st_a == 409

        # ④ 人切「固定在线」⇒ B 租约墓碑；心跳 409 revoked；行锁住
        st3, body3 = _http(
            hub.base, f"/admin/courses?course={C_AUTO}&mode=online&pin=1", method="POST"
        )
        assert st3 == 200, body3
        leases = _admin_offline(hub).get("leases") or {}
        assert leases.get(C_AUTO, {}).get("revoked") is True, leases
        st_b, body_b = _http(
            hub.base, f"{OFFLINE_HEARTBEAT_PATH}?course={C_AUTO}&lease={tok_b}", method="POST"
        )
        assert st_b == 409 and body_b.get("revoked") is True, body_b
        row4 = _task_row(_http(hub.base, OFFLINE_TASKS_PATH)[1], C_AUTO)
        assert row4["authority"] == "pinned_online", row4
        assert row4["claimable"] is False and row4["seize"] is False, row4
        assert str(row4["reason"]).startswith("pinned:"), row4
        assert _mode_of(hub, C_AUTO) == "online"
        # 锁住期间：claim 409 `pinned_online`（离线盘抢不走）
        st_pin, body_pin = _offline_claim(hub, C_AUTO, "w-c")
        assert st_pin == 409 and body_pin.get("pinned_online") is True, body_pin

        # ⑤ 交还自动 ⇒ 回 auto 池（可抢）
        st5, body5 = _http(
            hub.base, f"/admin/courses?course={C_AUTO}&mode=online&pin=0", method="POST"
        )
        assert st5 == 200, body5
        row5 = _task_row(_http(hub.base, OFFLINE_TASKS_PATH)[1], C_AUTO)
        assert row5["authority"] == "auto" and row5["seize"] is True, row5

        # ⑥ C 再抢走：claim 200 + mode 再翻 offline
        st6, body6 = _offline_claim(hub, C_AUTO, "w-c")
        assert st6 == 200 and body6.get("lease"), body6
        assert _mode_of(hub, C_AUTO) == "offline"
        row6 = _task_row(_http(hub.base, OFFLINE_TASKS_PATH)[1], C_AUTO)
        assert row6["holder"]["worker_id"] == "w-c", row6

        # ⑦ 账本无假 `run_complete`（事件名级断言，不是子串巧合）
        ledger = (traj / C_AUTO / "training_log.jsonl").read_text(encoding="utf-8")
        events = [json.loads(ln).get("event") for ln in ledger.splitlines() if ln.strip()]
        assert "run_complete" not in events, events
    finally:
        hub.close()


# ──────── T8（二轮 R3-a）：hub 重启后盘上的 offline 记录仍把课停摆（Job 不被派走） ────────


def test_hub_restart_keeps_discovered_offline_course_parked(tmp_path: Path) -> None:
    """discover-only 起 hub：盘上有 `offline` 记录的课 ⇒ **立即停摆**（`parked`），重启不变。

    题眼：记录是 `dispatch_effective_mode` 的输入（启动参数给的 mode 只是缺省）——没有它，
    离线课重启后被解封队列，在线盘能把残留 job 领走（报告二的土壤）。
    """
    traj = tmp_path / "traj"
    _course_dirs(traj, C_AUTO)
    _write_pack(traj, C_AUTO, b"PK\x03\x04t8-pack")
    rec = {
        "v": 1,
        "mode": "offline",
        "pinned": False,
        "claimed_offline": False,
        "claimed_by": "",
        "claimed_at": 0.0,
        "flipped_at": 0.0,
        "updated_at": 0.0,
        "completed_pack_sha": "",
    }
    (traj / C_AUTO / "offline-dispatch.json").write_text(json.dumps(rec), encoding="utf-8")
    _prod_publish(traj, C_AUTO, it=1, run_id="t8-run")

    def _peek_is_empty(h: _Hub, tag: str) -> None:
        assert _peek_ids(h) == [], f"{tag}：停摆课的 job 不该被派走"

    hub = _Hub(traj)
    try:
        hub.ready(expect=[C_AUTO])
        assert _mode_of(hub, C_AUTO) == "offline"
        _peek_is_empty(hub, "首次启动")
    finally:
        hub.close()
    hub2 = _Hub(traj)
    try:
        hub2.ready(expect=[C_AUTO])
        assert _mode_of(hub2, C_AUTO) == "offline", "重启不得把 offline 记录解封（§3.8）"
        _peek_is_empty(hub2, "重启后")
    finally:
        hub2.close()


if __name__ == "__main__":  # 手工跑单条：python -m pytest e2e/test_auto_handoff_e2e.py -q
    raise SystemExit(pytest.main([__file__, "-q"]))
