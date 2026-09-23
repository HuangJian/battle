"""test_local_worker_multi_course —— 一个本机 worker 领**所有**课程的活（2026-09-19 用户指令）。

用户口径：「localWorker 也不应绑定课程，它和云端 worker 一样，只与 hub 通信（pull/push），
领到任务后直接执行，完成后回传结果。」

之所以是**事实**而不是需求：取活面（peek + claim）从来不看课程 —— 挑活的是 hub 的队列
（每课程一条 FIFO + **跨课程轮转**），响应里自带 `course`；job 的 manifest 又自带整份课程
快照（`course` 字段是课程文件正文）。worker 侧因此没有任何课程参数可配：`acquire_job` 的
形参只有 URL / token / worker 身份。

本文件用**进程内真 hub（HTTP）+ 真 worker 领活函数**证明两件事（不跑任何真运算、不起 GPU）：

  ① 同一个 worker 身份连续轮询，先后领到**两门不同课**的活（一门领完即完，下一门才轮得到）；
  ② worker 的领活链路里不存在「课程」这个输入（有的话①就证伪了；这里再钉一道形参围栏）。

控制台侧的等价结论（槽归一 / 旧每课条目换代接管 / 离开 local 不连坐其它课）在
`dashboard/tests/local-worker.test.ts`；hub 侧的跨课程轮转另见 `tests/test_multi_course_hub.py`
（那一条用的是**两个** worker 身份，与本文的「一个进程服务所有课程」互补）。
"""

from __future__ import annotations

import inspect
import sys
import threading
from pathlib import Path

from common.protocol import COURSE_ENABLE_MARKER, COURSE_MODE_ONLINE, normalize_manifest
from remote.hub_server import _HubQueue, _JobStore, make_server
from remote.worker import acquire_job

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

TOKEN = "sekret"
#: 一个身份 = 一份进程。**全程复用**（本题的命题就是「同一个 worker 服务所有课程」）。
WORKER_ID = "local-worker"


# ------------------------------------------------------------------ 夹具


def _manifest(jid: str) -> dict:
    """一份合法的最小 PPO manifest（形状与 `_HubQueue` 的 schema 校验一致）。"""
    return normalize_manifest(
        {
            "proto": 1,
            "runId": "run",
            "it": 1,
            "job_id": jid,
            "commit": "c" * 40,
            "code_sha256": "z" * 64,
            "course": '// course jsonc\n{"reward": {"formula": "score"}}',
            "course_fp": "f" * 64,
            "reward_formula": "score",
            "formula_hash": "h" * 40,
            "metrics_version": 1,
            "gamma": 0.995,
            "lam": 0.95,
            "mode": "per-tick",
            "seed": "s" * 64,
            "epochs": 2,
            "mb": 512,
            "lr": 3e-4,
            "init_weights_fp": "w" * 64,
            "data_fp": "d" * 64,
            "payload_sha256": "p" * 64,
        }
    )


def _hub(tmp_path: Path, courses: tuple[str, ...]) -> _HubQueue:
    """每课程一棵 `remote-jobs/` + 一条 `training_log.jsonl`（磁盘布局与控制台一致）。

    `discover_root` = 真实的 `--traj-root` 形态：hub 据此目录结构发现新课（`add_course`），
    所以「先起 hub/worker、后加课」在本夹具里与生产同构。
    """
    stores = {
        c: _JobStore(tmp_path / c / "remote-jobs", tmp_path / c / "training_log.jsonl")
        for c in courses
    }
    # 开课标记 = 代操作员按一下控制台的「训练」（`<课>/training-enabled.txt`）：发现模式下
    # **派发闸**要求它存在（2026-09-20：没有它，未开课/历史课程的陈旧 pending job 会被继续
    # 派给真 GPU worker）。夹具造的是「课已开、正在跑」的盘上形状，故两门课都写。
    for c in courses:
        (tmp_path / c / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
    return _HubQueue(
        stores,
        order=list(courses),
        modes={c: COURSE_MODE_ONLINE for c in courses},
        discover_root=tmp_path,
    )


def _publish(hub: _HubQueue, course: str, jid: str) -> None:
    hub._stores[course].publish(jid, _manifest(jid), b"PK\x03\x04fake")


def _boot(tmp_path: Path, hub: _HubQueue):
    srv = make_server(hub, 0, TOKEN, host="127.0.0.1")
    port = int(srv.server_address[1])
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return f"http://127.0.0.1:{port}", srv, th


# ------------------------------------------------------------------ ① 一个进程服务所有课程


def test_one_worker_takes_jobs_from_every_course(tmp_path: Path) -> None:
    """同一个 worker 身份依次领到两门课的活（跨课程轮转 + 响应自报 course）。

    单 hub、单 worker 身份：独占租约（这正是本机单 worker 的真实形态）。
    """
    courses = ("c5-gae", "c6-chip")
    hub = _hub(tmp_path, courses)
    for i, c in enumerate(courses):
        _publish(hub, c, f"{i}" * 16)
    base, srv, th = _boot(tmp_path, hub)
    try:
        seen: list[tuple[str, str]] = []
        for _ in courses:
            got = acquire_job(base, TOKEN, worker_id=WORKER_ID)
            assert got is not None, "应当有可领的 job（没有就是轮转/派发断了）"
            assert got.get("job_id"), f"空轮询：{got}"
            seen.append((str(got.get("course")), str(got["job_id"])))
            # worker 干完活 → 记账（真实回传走 `POST /jobs/{id}/result`；本用例只驱动**领活**
            # 这一侧，故直接落 `job_completed` —— 与回传处理器落地的是同一类事件）。
            hub._stores[str(got["course"])].mark_completed(str(got["job_id"]))
        assert [c for c, _ in seen] == list(courses), f"单 worker 未跨课领活：{seen}"
        # 两门课都领完 ⇒ 再轮询是空（不是把某一门重复领一遍）
        assert acquire_job(base, TOKEN, worker_id=WORKER_ID) is None
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


def test_one_worker_serves_a_late_third_course(tmp_path: Path) -> None:
    """「先起 worker、后加课」：worker 常驻不动，新课程发布后同一进程立刻能领到。

    这条是共享 worker 的真正常态（控制台不重启、不重新配置）：课程只出现在 hub 的队列里。
    """
    hub = _hub(tmp_path, ("c5-gae",))
    _publish(hub, "c5-gae", "a" * 16)
    base, srv, th = _boot(tmp_path, hub)
    try:
        first = acquire_job(base, TOKEN, worker_id=WORKER_ID)
        assert first is not None and first["course"] == "c5-gae"
        hub._stores["c5-gae"].mark_completed(str(first["job_id"]))

        # 后加一门课：只往队列里发布（worker 与 hub 都不重启）
        hub.add_course("c7-new")
        # 「后加课」在生产 = 控制台开课（写 `training-enabled.txt`）+ 发布 job——两道事实
        # 都要有（派发闸在发现模式下要求标记，见 `_hub` 注释）。
        (tmp_path / "c7-new").mkdir(parents=True, exist_ok=True)
        (tmp_path / "c7-new" / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
        _publish(hub, "c7-new", "b" * 16)
        nxt = acquire_job(base, TOKEN, worker_id=WORKER_ID)
        assert nxt is not None, "新课的 job 必须被同一份 worker 领走"
        assert nxt["course"] == "c7-new"
    finally:
        srv.shutdown()
        srv.server_close()
        th.join(timeout=5)


# ------------------------------------------------------------------ ② 形参围栏


def test_poll_path_has_no_course_input() -> None:
    """领活链路的形参里不存在「课程」——课程只能是**响应**给的事实，不能是**请求**的参数。

    哪天真有人给 worker 加上 `--course`（把进程按课绑回去），这条会红：那一刻「一个进程服务
    所有课程」就不成立了，而它不会以崩溃的形式暴露，只会表现为「另一门课的 job 永远没人领」。
    """
    params = set(inspect.signature(acquire_job).parameters)
    assert not any("course" in p.lower() for p in params), params
    # 真实 HTTP 路径也钉一道（URL 里带课程 = 把归属塞回请求侧）
    # 取活必须经 peek + claim 两个面（任一面被换成「带课程参数」的单条腿，这里就红）
    src = inspect.getsource(acquire_job)
    assert "peek_jobs" in src and "claim_job" in src, "取活链路少了 peek/claim 之一"
    assert "course=" not in src, "acquire_job 的请求里出现了课程参数"
    # 三个 HTTP 面的形参也钉一道（响应侧当然会有 course —— 那是 hub 告诉我们的归属事实）
    import remote.worker as W

    for fn in (W.peek_jobs, W.request_priority, W.claim_job):
        assert not any("course" in p.lower() for p in inspect.signature(fn).parameters), fn
