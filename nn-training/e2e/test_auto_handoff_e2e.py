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
    """用户口径：「优先取当时就绪的离线课程；没有离线才抢第一个在训在线课」——真 HTTP 上验。"""
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
            hub.base, "/admin/courses?course=e2e-on-live&mode=online&pin=1", method="POST"
        )
        assert st == 200, body

        st, tasks = _http(hub.base, OFFLINE_TASKS_PATH)
        assert st == 200, tasks
        row_off = _task_row(tasks, "e2e-off-ready")
        row_on = _task_row(tasks, "e2e-on-live")
        assert row_off["seize"] is False and row_off["claimable"] is True, row_off
        # 旧口径里 pin online 的课根本不在清单/领不到；现在它带 seize=True（可抢，但本拍不抢）
        assert row_on["seize"] is True and row_on["claimable"] is True, row_on

        from remote.offline_boot import resolve_courses

        got = resolve_courses({"hub_url": hub.base}, {"HUB_TOKEN": TOKEN}, lambda _m: None)
        assert [t["course"] for t in got] == ["e2e-off-ready"], got
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


if __name__ == "__main__":  # 手工跑单条：python -m pytest e2e/test_auto_handoff_e2e.py -q
    raise SystemExit(pytest.main([__file__, "-q"]))
