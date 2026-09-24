"""e2e/test_multi_course_single_hub_e2e.py —— 多课程单 hub 端到端（P1 余下 ②）。

**一个 hub 进程**（`--discover --push`，命令行**不点课程名**）+ **一堂真 worker_server**
（starter 换成写结果的假执行器 ⇒ 不跑 rollout / PPO / torch），两门课的 hubpush job 各自走通：

    训练侧 publish_job(dispatch="push")        ← 真磁盘 IPC：<traj>/<课>/remote-jobs/<jid>/
        ↓
    hub（真进程；课程表从盘上发现）              ← discover：训练侧发布即「这门课在跑」
        ↓  push_client.submit_job（真线协议，code.zip 随体上传）
    worker_server（真 HTTP 契约 /ping /job /job/{id}/result）
        ↓
    hub accept_result（对账 → 租约 → 首写锁定）→ 本课目录 result.json
        ↑
    训练侧 wait_job(base, token, jid)（真轮询路径，控制台读的是同一份账本）

覆盖三条只有**跨进程**才暴露的接线（单进程单测各自绿、接起来却断的那种）：
  ① 课程表发现 → 轮转派发 → 结果路由回**本课**目录（串课 = 权重写错课程）；
  ② 离线课（`POST /admin/courses?mode=offline` 真热切）不实时派发、且切回在线后同一份
     job 仍在队首被推走（用户口径「离线课不实时派发、已分派任务回落队首等 worker」）；
  ③ 训练侧的 job 身份（幂等键/账本行）与 hub 的认领判据（`manifest.dispatch`）用同一个字面量。

纪律：不 spawn bun/node、不加载 torch（假 PPO）；HTTP 全在本机临时端口。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # nn-training/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote import net_http
from remote.hub_client import mark_job_completed, publish_job, wait_job
from remote.protocol import COURSE_ENABLE_MARKER
from remote.worker_server import WorkerServerState, make_worker_server
from tests.subproc_util import spawn_bound_port

#: hub 与 worker 共用的 Bearer（推模式下 worker 的 authKey 就是它）。
TOKEN = "e2e-sekret"

#: 两门课**刻意共用**的 runId（2026-09-24 job 身份事故的现场条件：单进程多课程下
#: runId 是进程级共享的）。与「同 warm-start + 同 shard 集」一起，构成「四分量全同」。
SHARED_RUN_ID = "e2e-shared-run"


def _course_fp(course: str) -> str:
    """课程身份（真链路上是课程 jsonc 的 sha256）——两门课必须不同，否则它们就是同一门课。"""
    return hashlib.sha256(course.encode("utf-8")).hexdigest()


def _weights_json_for(manifest: dict) -> str:
    """假 PPO 的「训练结果」形状（`validate_result` 契约：agg 五键 + weights_json 非空）。

    ★ 把本 job 的 `course_fp` **烙进权重**：两门课若拿到同一份结果（= 事故），落盘字节
    就完全一样；分开之后各自的权重必须不同 —— 没有这一手，「串课」这条回归判不出来。
    """
    raw = json.dumps(
        {"format": "nn-weights-json", "params": {"course_fp": manifest["course_fp"]}}
    ).encode("utf-8")
    return base64.b64encode(raw).decode("ascii")


def _quiet(_msg: str) -> None:
    return None


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _http(
    base: str, path: str, *, method: str = "GET", body: dict | None = None, timeout: float = 10.0
) -> tuple[int, dict]:
    """hub 管理端点小工具（真 HTTP；4xx/5xx 也算「有答」，返回状态码与体）。"""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        base + path,
        data=data,
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
        method=method,
    )
    def _as_dict(raw: bytes) -> dict:
        """体解析：非对象/坏 JSON 一律退化为 `{}`（观测面不该把用例带崩）。"""
        try:
            loaded = json.loads(raw.decode("utf-8")) if raw else None
        except ValueError:
            return {}
        return loaded if isinstance(loaded, dict) else {}

    try:
        # 回环绕开环境代理：本机若有 HTTP_PROXY，裸 urllib 会把 127.0.0.1 也送出去
        # （`no_proxy` 的 `127.*` 通配 Python 不认，见 remote/net_http.py）。
        with net_http.urlopen(req, timeout=timeout) as resp:
            return resp.status, _as_dict(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _as_dict(e.read())
    except Exception:
        return 0, {}


def _wait_until(pred, *, timeout: float = 30.0, step: float = 0.1) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        # sleep-ok: 轮询步长（等的是谓词/状态，超时只当挂起兜底）
        time.sleep(step)
    return bool(pred())


# ────────────────────────── 假 GPU worker（真 worker_server 契约） ──────────────────────────


class _LiveWorker:
    """真 `worker_server`（真 HTTP 路由/鉴权/幂等），starter 换成写结果的假执行器。

    结果字段照 `validate_result` 逐条从 manifest 取（job_id/data_fp/init_weights_fp/
    commit_echo）——**假 PPO 也必须过 hub 的对账闸**，否则测的就不是真链路。
    """

    def __init__(self, work_dir: Path) -> None:
        self.state = WorkerServerState(work_dir)
        self.srv = make_worker_server(self.state, 0, TOKEN)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.started: list[str] = []
        self._thread = threading.Thread(target=self.srv.serve_forever, daemon=True)
        self._thread.start()
        self.state.set_starter(self._fake_starter)

    def _fake_starter(self, jid: str, item: dict) -> None:
        man = item["manifest"]
        self.started.append(jid)
        self.state.set_result(
            jid,
            {
                "job_id": jid,
                "data_fp": man["data_fp"],
                "init_weights_fp": man["init_weights_fp"],
                "commit_echo": man["commit"],
                "weights_json": _weights_json_for(man),
                "agg": {"policy": 0.1, "value": 0.2, "entropy": 3.0, "kl": 0.01, "mean_ret": 0.5},
                "wire": {"payload_bytes": len(item.get("payload_zip") or b"")},
            },
        )

    def close(self) -> None:
        self.srv.shutdown()
        self.srv.server_close()


# ────────────────────────── 真 hub 进程 ──────────────────────────


class _Hub:
    """真 `remote.hub_server` 子进程（控制台实际启动的那条 argv）。"""

    def __init__(self, traj_root: Path, push_config: Path, *, extra: list[str] | None = None) -> None:
        def _argv(port: int) -> list[str]:
            return [
                sys.executable,
                "-u",
                "-m",
                "remote.hub_server",
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
                "--push",
                "--push-config",
                str(push_config),
                "--push-poll-sec",
                "0.05",
                *(extra or []),
            ]

        # 端口竞态由 helper 消化（裸的「探一个端口 → 交给子进程 bind」在 xdist 并行下
        # 会撞「禁止双监听」当场退出 ⇒ 假红；见 tests/subproc_util.py::spawn_bound_port）
        srv = spawn_bound_port(
            _argv, cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)}
        )
        self.port = srv.port
        self.proc = srv.proc
        self.lines = srv.lines
        self.base = f"http://127.0.0.1:{self.port}"

    def output(self) -> str:
        """已捕获的子进程输出尾部（诊断用；进程活着也能安全取）。"""
        return "\n".join(self.lines)[-800:]

    def ready(self, *, expect: list[str], timeout: float = 40.0) -> None:
        """/admin/queue 能答 **且**课程表已就位。

        就绪不能只看端口：课程表是**后台扫描**（`DISCOVER_SCAN_MIN_SEC` 最少隔 2s）
        登记进来的，端口答 200 时它可能还是空的——早一拍断言 = 假红。
        """
        end = time.time() + timeout
        while time.time() < end:
            if self.proc.poll() is not None:
                break
            st, body = _http(self.base, "/admin/queue", timeout=2.0)
            if st == 200 and sorted(body.get("courses") or {}) == sorted(expect):
                return
            # sleep-ok: 轮询步长（等的是「hub 已就绪且课程表已登记」这个状态）
            time.sleep(0.1)
        raise AssertionError(
            f"hub-server 未就绪或课程表不对（rc={self.proc.poll()}）；输出：{self.output()}"
        )

    def queue(self) -> dict:
        st, body = _http(self.base, "/admin/queue")
        assert st == 200, body
        return body

    def push_state(self) -> dict:
        st, body = _http(self.base, "/admin/push-workers")
        assert st == 200, body
        disp = body.get("dispatcher")
        assert isinstance(disp, dict), body
        return disp

    def set_mode(self, course: str, mode: str) -> dict:
        st, body = _http(
            self.base, f"/admin/courses?course={course}&mode={mode}", method="POST"
        )
        assert st == 200, f"/admin/courses 热切失败：{st} {body}"
        return body

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


# ────────────────────────── 训练侧发布 ──────────────────────────


def _course_dirs(traj_root: Path, course: str) -> tuple[Path, Path]:
    """课程的盘上形状（与生产逐字节同构）：
    `<traj>/<课>/{remote-jobs,training_log.jsonl,training-enabled.txt}`。

    ★ 开课标记（`training-enabled.txt`）是 hub 认课的那道显式闸（2026-09-20）：课程表 =
    账本 ∧ 标记——否则 tmp/ 下的历史课（同样有 `remote-jobs/` 残影）会把残留 job 继续派给
    真 GPU worker。生产里由控制台「开课」写；这里由一个已开课的课程目录代人按下那一下。
    """
    d = traj_root / course
    job_root = d / "remote-jobs"
    job_root.mkdir(parents=True, exist_ok=True)
    jsonl = d / "training_log.jsonl"
    jsonl.touch()
    (d / COURSE_ENABLE_MARKER).touch()
    return job_root, jsonl


def _make_code_zip(tmp_path: Path, name: str) -> Path:
    """一份最小合法 code.zip（worker 侧按内容寻址缓存，内容本身无人执行）。"""
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("remote/__init__.py", "")
        z.writestr("remote_worker.py", "# fake code snapshot\n")
    return p


def _publish(course: str, job_root: Path, jsonl: Path, traj_root: Path, code_zip: Path) -> dict:
    """训练侧真发布（hubpush）：写 payload + manifest（`dispatch="push"`）+ 账本 job_pending。

    ★ 两门课刻意共用 `runId` / `it` / `init_weights_fp`（同一份假 warm-start）/ `data_fp`
    （同一份假 shard 集 = 空），**只有 `course_fp` 不同** —— 这正是 2026-09-24 事故的配置，
    也是本夹具原来**没有**压到的那个角落（原来 run_id 带课程名 ⇒ id 天生不同）。
    """
    wfile = traj_root / "init_weights.json"
    if not wfile.exists():
        wfile.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
    man = publish_job(
        job_root=job_root,
        jsonl_path=jsonl,
        run_id=SHARED_RUN_ID,
        it=1,
        traj_dir=traj_root / course / "it1",
        shard_dirs=[],
        commit="c" * 40,
        code_sha256=_sha(code_zip.read_bytes()),
        code_zip_path=code_zip,
        course='// course jsonc\n{"reward": {"formula": "score"}}',
        course_name=course,
        course_fp=_course_fp(course),
        init_weights_path=str(wfile),
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=512,
        lr=3e-4,
        dispatch="push",
        log=_quiet,
    )
    return man


def _ledger_rows(jsonl: Path) -> list[dict]:
    out: list[dict] = []
    for line in jsonl.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ────────────────────────── ① 一个 hub 服务两门课 ──────────────────────────


def test_single_hub_dispatches_two_courses_to_one_worker(tmp_path: Path) -> None:
    """两门课的 hubpush job 由**同一个 hub 进程**推给同一台 worker，各回各家。"""
    traj = tmp_path / "traj"
    c_a, c_b = "e2e-c1", "e2e-c2"
    dirs = {c: _course_dirs(traj, c) for c in (c_a, c_b)}
    code_zip = _make_code_zip(tmp_path, "code.zip")
    worker = _LiveWorker(tmp_path / "worker")
    push_cfg = tmp_path / "rl-config.json"
    push_cfg.write_text(
        json.dumps(
            {
                "nodes": [
                    {"id": "g1", "url": worker.url, "authKey": TOKEN, "gpu_push": True},
                ]
            }
        ),
        encoding="utf-8",
    )
    hub = _Hub(traj, push_cfg)
    try:
        hub.ready(expect=[c_a, c_b])
        # 课程表来自磁盘（命令行没点过任何课程名）：两门课都在，且都还没活
        q = hub.queue()
        assert all(q["courses"][c]["pending_n"] == 0 for c in (c_a, c_b))
        assert hub.push_state()["pushed"] == 0

        # 训练侧各自发布（同一 it、不同课程）——两门课并行
        mans = {c: _publish(c, dirs[c][0], dirs[c][1], traj, code_zip) for c in (c_a, c_b)}
        jids = {c: mans[c]["job_id"] for c in (c_a, c_b)}
        # 先钉住前提：**幂等键的四分量确实全同**（runId/it/init_weights_fp/data_fp）——
        # 否则这条用例就退回「夹具天生不同 id」那种假绿（正是改造前的状态）。
        for k in ("runId", "it", "init_weights_fp", "data_fp"):
            assert mans[c_a][k] == mans[c_b][k], f"夹具前提被破坏：{k} 两课不同 ⇒ 没压到碰撞"
        assert mans[c_a]["course_fp"] != mans[c_b]["course_fp"], "两门课的课程身份必须不同"
        assert jids[c_a] != jids[c_b], "不同课程的同四分量活必须是两个 job"
        for c in (c_a, c_b):
            assert mans[c]["dispatch"] == "push" and mans[c]["course_name"] == c

        # 训练侧真等（HTTP 轮询：hub 按 job_id 路由到课程）
        results = {
            c: wait_job(hub.base, TOKEN, jids[c], timeout_sec=60, poll_sec=0.1, poll_max_sec=0.5, log=_quiet)
            for c in (c_a, c_b)
        }
        for c in (c_a, c_b):
            r = results[c]
            assert r["job_id"] == jids[c]
            assert r["data_fp"] == mans[c]["data_fp"]
            assert r["agg"]["mean_ret"] == 0.5

        # ★ 污染的回归判据（事故：两个 trainer 读到**同一份** result ⇒ 权重互串）：
        # 两课拿到的权重必须各自带自己的课程身份，且**两两不同**。
        # 注意 `data_fp` 在这一对里本来就相同（夹具刻意同 shard 集）⇒ 它**不能**再当判据，
        # 真正能判「串没串」的只有这份烙了 course_fp 的权重体。
        assert results[c_a]["weights_json"] != results[c_b]["weights_json"], (
            "两门课拿到了同一份结果——串课又回来了"
        )
        for c in (c_a, c_b):
            body = json.loads(base64.b64decode(results[c]["weights_json"]).decode("utf-8"))
            assert body["params"]["course_fp"] == mans[c]["course_fp"], f"{c} 的权重是别人的"

        # 结果落**本课**目录（串课 = 权重写进另一门课）；两课的 job 目录集合**不相交**
        for c, other in ((c_a, c_b), (c_b, c_a)):
            jd = dirs[c][0] / jids[c]
            assert (jd / "result" / "result.json").exists(), f"{c} 的结果没落位"
            assert not (dirs[c][0] / jids[other]).exists(), f"{c} 的目录里出现了 {other} 的 job"
            rows = _ledger_rows(dirs[c][1])
            assert [x["job_id"] for x in rows if x.get("event") == "job_pending"] == [jids[c]], (
                f"{c} 的账本混进了别的课的 job：{rows}"
            )
            assert not [x for x in rows if x.get("event") == "job_completed"]

        def _job_dirs(c: str) -> set[str]:
            return {
                p.name
                for p in dirs[c][0].iterdir()
                if p.is_dir() and not p.name.startswith(".")
            }

        assert not (_job_dirs(c_a) & _job_dirs(c_b)), (
            f"两课的 job 目录集合相交了：{_job_dirs(c_a) & _job_dirs(c_b)}"
        )
        assert hub.queue()["ambiguous_jids"] == {}, "不该出现 job 身份歧义"

        # 训练侧验收落位后写 job_completed（只动本课账本——两课共用一份 hub 也不能串）
        mark_job_completed(dirs[c_a][1], jids[c_a])
        done_a = [x["job_id"] for x in _ledger_rows(dirs[c_a][1]) if x.get("event") == "job_completed"]
        assert done_a == [jids[c_a]]
        assert not [x for x in _ledger_rows(dirs[c_b][1]) if x.get("event") == "job_completed"]

        # 一台 worker 收下两份、各一次（重复受理 = 同一轮跑两遍）
        assert sorted(worker.started) == sorted(jids.values()), worker.started
        # 队列清空 + 派发器口径：推了 2 份、零回落（这条腿没打过架）
        # 注：租约不会因「结果已入账」立刻消失（等 TTL 过期/回收）——它不在可领池里的
        # 依据是**结果已落盘**（`claimable_job_ids` 看盘，不看租约），所以这里断言的
        # 是「无可领的活」+「派发器自己手上没有在飞」两件事，而不是 inflight 空。
        q = hub.queue()
        for c in (c_a, c_b):
            assert q["courses"][c]["pending_n"] == 0
            assert q["courses"][c]["next_job"] is None
        st = hub.push_state()
        assert st["pushed"] == 2 and st["requeued"] == 0 and st["inflight"] == [], st
    finally:
        hub.close()
        worker.close()


# ────────────────────────── ② 离线课不实时派发 ──────────────────────────


def test_offline_course_is_parked_and_resumes_on_going_online(tmp_path: Path) -> None:
    """离线课：hub 不派它的活（job 留在队首）；切回在线后同一份活立刻被推走。"""
    traj = tmp_path / "traj"
    c_live, c_off = "e2e-online", "e2e-offline"
    dirs = {c: _course_dirs(traj, c) for c in (c_live, c_off)}
    code_zip = _make_code_zip(tmp_path, "code.zip")
    worker = _LiveWorker(tmp_path / "worker")
    push_cfg = tmp_path / "rl-config.json"
    push_cfg.write_text(
        json.dumps({"nodes": [{"id": "g1", "url": worker.url, "authKey": TOKEN, "gpu_push": True}]}),
        encoding="utf-8",
    )
    hub = _Hub(traj, push_cfg)
    try:
        hub.ready(expect=[c_live, c_off])

        # 先热切成离线（真端点；发现登记缺省是 online）
        hub.set_mode(c_off, "offline")
        st, body = _http(hub.base, "/admin/courses")
        assert st == 200
        modes = {row["course"]: row["mode"] for row in body["courses"]}
        assert modes.get(c_off) == "offline" and modes.get(c_live) == "online", modes

        m_off = _publish(c_off, dirs[c_off][0], dirs[c_off][1], traj, code_zip)
        jid_off = m_off["job_id"]
        # 离线课的活躺在队首、没人碰：等**派发器真的转过 10 拍**（`ticks` 计数 = 事件）
        # 再断言它仍 pending —— 原来 `sleep(1.5)` 是拿时长猜「拍数应该够了」
        # （--push-poll-sec 0.05 ⇒ 1.5s≈30 拍），满载时会睡多/睡少（2026-09-24）。
        t0 = hub.push_state()["ticks"]
        assert _wait_until(lambda: hub.push_state()["ticks"] >= t0 + 10, timeout=30.0), (
            "派发器 30s 内没转过 10 拍（线程死了？）——负向断言失去前提"
        )
        q = hub.queue()
        assert q["courses"][c_off]["pending_n"] == 1, "离线课的 job 不该消失"
        assert q["courses"][c_off]["inflight"] == [], "离线课不该被派发"
        assert jid_off not in worker.started
        assert hub.push_state()["pushed"] == 0

        # 同 hub、同一台 worker：在线课的活照常走（离线只停自己那一门）
        m_live = _publish(c_live, dirs[c_live][0], dirs[c_live][1], traj, code_zip)
        r_live = wait_job(
            hub.base, TOKEN, m_live["job_id"], timeout_sec=60, poll_sec=0.1, poll_max_sec=0.5, log=_quiet
        )
        assert r_live["agg"]["policy"] == 0.1
        assert worker.started == [m_live["job_id"]]

        # 切回在线 ⇒ 「已分派任务回落队首等 worker」的那份立刻被推走（无需重发）
        hub.set_mode(c_off, "online")
        r_off = wait_job(hub.base, TOKEN, jid_off, timeout_sec=60, poll_sec=0.1, poll_max_sec=0.5, log=_quiet)
        assert r_off["job_id"] == jid_off
        assert sorted(worker.started) == sorted([m_live["job_id"], jid_off])
        q = hub.queue()
        assert q["courses"][c_off]["pending_n"] == 0 and q["courses"][c_off]["next_job"] is None
    finally:
        hub.close()
        worker.close()
