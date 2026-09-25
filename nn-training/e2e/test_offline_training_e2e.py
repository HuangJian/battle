"""e2e/test_offline_training_e2e.py —— 离线训练模式端到端（2026-09-19 用户口径）。

「启动课程训练时，需指定 在线/离线 模式，缺省在线。离线模式下，支持下载任务包…也支持
带特别标识的云端 worker 在线领取。」

本文件用**真 hub 进程 + 真 worker 领活函数 + 真补传器**走通那条「在线领取」的路：

    离线课（hub 侧 mode=offline）
        ↓  训练侧发一份**真 kind="run" 整段 job**（plan + rollout spec + ts_code 随 payload）
    hub 的待领池（普通 worker 领不到；带 `X-Battle-Offline` 的**带标** worker 领得到）
        ↓  云机领走（真 HTTP peek + claim）
    云机自己跑完整段（这里用 ArtifactStore 逐轮落产物代替真 rollout/PPO）
        ↓  每轮 best-effort 补传（真 OfflineDeliverer → POST /offline/artifact）
    hub 落 `<traj>/<课>/remote-jobs/offline/<run>/it-NNN/`
        ↓  GET /admin/offline
    控制台读面（dashboard 的 `parseOfflineProgress` 就吃这个形状）

守住的四件事（单进程单测各自绿、接起来却会断的那种）：
  ① **能力闸**：不带标的 poller 领不到离线课（它不是「谁都领得到的池子」），带标的领得到；
     带标仍可领在线课（能力声明 ≠ 课程绑定）；
  ② **归位**：补传靠取活面下发的课程键落进**本课**目录（多课程 hub 里没有它
     每条补传都会 400「无法归属课程」）；
  ③ **读面**：`/admin/offline` 的 `{its, count, last_mtime}` 就是控制台段内进度的唯一来源；
  ④ **取包**：`GET /offline/task-pack?course=` 把控制台导出的那份 `task-<课>.zip` 递上云。

纪律：不 spawn bun/node、不加载 torch、不跑真 rollout / PPO / eval；HTTP 全在本机临时端口。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent  # nn-training/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import hashlib
import zipfile

import pytest

from remote import net_http, offline_boot
from remote.artifacts import ArtifactStore
from remote.hub_client import publish_job
from remote.offline_deliver import OfflineDeliverer
from remote.protocol import (
    COURSE_ENABLE_MARKER,
    INIT_WEIGHTS_NAME,
    TS_CODE_NAME,
    encode_weights_json,
)
from rl.iter_job import build_iter_spec
from rl.plan import build_plan, dump_plan
from tests.helpers.hub_poll import hub_poll
from tests.subproc_util import spawn_bound_port


@pytest.fixture(autouse=True)
def _isolate_weights_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """把**权重归档根**指到 tmp（2026-09-23）。

    本文件拉的是**真 hub 子进程**（`remote.hub_server`），子进程继承本测试的环境 ⇒ 用 env
    而不是 patch 模块常量。不隔离的话回传轮会往真 `nn-training/weights/` 写归档，而控制台
    的 evalA 权重选择器会把它们当成真训练轮次列出来。
    """
    monkeypatch.setenv("BCITY_WEIGHTS_ARCHIVE_ROOT", str(tmp_path / "weights-archive"))


TOKEN = "e2e-offline-sekret"
C_OFF = "e2e-off"
C_ON = "e2e-on"


def _quiet(_msg: str) -> None:
    return None


def _http(
    base: str, path: str, *, method: str = "GET", body: dict | None = None, token: str = TOKEN
) -> tuple[int, dict]:
    """hub 管理端点小工具（真 HTTP；4xx/5xx 也算「有答」，返回状态码与体）。"""
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


def _http_bytes(base: str, path: str, *, token: str = TOKEN) -> tuple[int, bytes]:
    """取二进制端点（任务包是 zip，json.loads 会炸）。"""
    req = urllib.request.Request(
        base + path, headers={"Authorization": f"Bearer {token}"}, method="GET"
    )
    try:
        with net_http.urlopen(req, timeout=20.0) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


# ────────────────────────── 盘上形状 ──────────────────────────


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


def _plan_args() -> SimpleNamespace:
    """`build_plan` / `build_iter_spec` 需要的最小 args（与 tests/test_plan.py 同源）。"""
    return SimpleNamespace(
        curriculum_stages="",
        curriculum_start=4,
        curriculum_every=8,
        curriculum_grow=4,
        seeds_per_stage=2,
        rotate_stages=2,
        stages="0-3",
        seed_rotate=0,
        seeds="1-2",
        total_stages=4,
        max_ticks=700,
        difficulty="hard",
        goal_rollout=False,
        intent_rollout=False,
        dodge="",
        course_obj=None,
        course_frozen_bytes=None,
        course_path="",
        run_iters=0,
    )


def _publish_run_job(
    course: str, job_root: Path, jsonl: Path, tmp_path: Path, *, it: int = 1, n: int = 3
) -> dict:
    """真发布一份 **kind="run"** 的 manifest（计划随 payload、shard 由节点现产）。

    ★ 2026-09-25：生产端**不再**发这种队列项（离线课走任务包，plan §7）——本助手造的是
    「盘上遗留的离线项」，专门用来钉 hub 侧的归属闸与补传读面在**遗留项**上仍然正确
    （混部期旧 hub / 旧盘上确实会有这种 job；派错盘正是 2026-09-25 事故的形态）。
    """
    args = _plan_args()
    plan = build_plan(
        args, it=it, iters_total=it + n, rotate_seed=99, max_iters=n - 1, log=_quiet
    )
    plan_bytes = dump_plan(plan)
    w = tmp_path / "init_weights.json"
    w.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
    ts = tmp_path / TS_CODE_NAME
    ts.write_bytes(b"PK\x03\x04" + b"ts-bytes" * 10)
    spec = build_iter_spec(args, [(0, 1)], wver="w" * 64, node_label="run")
    return publish_job(
        job_root=job_root,
        jsonl_path=jsonl,
        run_id=f"seg-{course}",
        it=it,
        traj_dir=tmp_path / "traj-seg",
        shard_dirs=[],
        commit="c" * 40,
        code_sha256="z" * 64,
        course='// course jsonc\n{"reward": {"formula": "score"}}',
        course_name=course,
        course_fp="f" * 64,
        init_weights_path=str(w),
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=512,
        lr=3e-4,
        kind="run",
        rollout_spec=spec,
        ts_code_sha256="t" * 64,
        ts_code_zip_path=ts,
        plan_bytes=plan_bytes,
        log=_quiet,
    )


def _publish_plain_job(course: str, job_root: Path, jsonl: Path, tmp_path: Path) -> dict:
    """一份普通逐轮 job（在线课的对照组；不真跑，只看它能不能被领到）。"""
    w = tmp_path / "init_weights.json"
    if not w.exists():
        w.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
    return publish_job(
        job_root=job_root,
        jsonl_path=jsonl,
        run_id=f"run-{course}",
        it=1,
        traj_dir=tmp_path / "traj-on",
        shard_dirs=[],
        commit="c" * 40,
        code_sha256="z" * 64,
        course='// course jsonc\n{"reward": {"formula": "score"}}',
        course_name=course,
        course_fp="f" * 64,
        init_weights_path=str(w),
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=512,
        lr=3e-4,
        log=_quiet,
    )


# ────────────────────────── 真 hub 进程 ──────────────────────────


class _Hub:
    """真 `remote.hub_server` 子进程（控制台实际启动的那条 argv：`--traj-root --discover`）。"""

    def __init__(self, traj_root: Path) -> None:
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
            ]

        # 端口竞态由 helper 消化（裸的「探一个端口 → 交给子进程 bind」在 xdist 并行下会撞
        # 「禁止双监听」当场退出 ⇒ 假红；见 tests/subproc_util.py::spawn_bound_port）
        srv = spawn_bound_port(
            _argv, cwd=str(ROOT), env={**os.environ, "PYTHONPATH": str(ROOT)}
        )
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
            st, body = _http(self.base, "/admin/queue", token=TOKEN)
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

    def set_mode(self, course: str, mode: str) -> None:
        st, body = _http(
            self.base, f"/admin/courses?course={course}&mode={mode}", method="POST"
        )
        assert st == 200, f"/admin/courses 热切失败：{st} {body}"

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)


# ───────────── ① 队列里**遗留**的离线项：谁能领、谁不能（队列腿已退役，闸仍要对） ─────────────


def test_offline_segment_is_claimable_only_by_a_marked_worker(tmp_path: Path) -> None:
    """**遗留**的离线项：普通 poller 领不到；带 `X-Battle-Offline` 的领得到。

    2026-09-25：头的语义从「能力」升为**归属**（一个盘一种任务）——旧口径「带标仍可领
    在线课」已作废，对应的断言不再存在（归属闸会当场拒，见
    `tests/test_role_routing.py`）。本文件保留的是跨进程那条真链路。
    """
    traj = tmp_path / "traj"
    off_job_root, off_jsonl = _course_dirs(traj, C_OFF)
    on_job_root, on_jsonl = _course_dirs(traj, C_ON)
    man_off = _publish_run_job(C_OFF, off_job_root, off_jsonl, tmp_path)
    man_on = _publish_plain_job(C_ON, on_job_root, on_jsonl, tmp_path)
    hub = _Hub(traj)
    try:
        hub.ready(expect=[C_OFF, C_ON])
        hub.set_mode(C_OFF, "offline")

        # 普通 poller：离线课被跳过，先拿到在线课那份（能力闸不是「全都不给」）
        plain = hub_poll(hub.base, TOKEN, worker_id="plain-1")
        assert plain is not None and plain["job_id"] == man_on["job_id"], plain
        assert plain["course"] == C_ON

        # 在线课那份已被领走（活租约）⇒ 普通 poller 再轮到离线课仍然是「没有可领的活」
        assert hub_poll(hub.base, TOKEN, worker_id="plain-1") is None
        q = hub.queue()
        assert q["courses"][C_OFF]["pending_n"] == 1, "离线课的 job 不该消失"
        assert q["courses"][C_OFF]["inflight"] == [], "离线课不该派给普通 worker"

        # 带标 poller：整段 job 立刻到手，且响应自报归属课程（补传的归位键）
        marked = hub_poll(hub.base, TOKEN, worker_id="marked-1", role="offline")
        assert marked is not None, f"带标 worker 领不到离线课；输出：{hub.output()}"
        assert marked["job_id"] == man_off["job_id"]
        assert marked["course"] == C_OFF
        assert marked["manifest"]["kind"] == "run"
        assert marked["manifest"]["plan_sha256"], "整段 job 必须随计划（节点靠它自主跑完）"
        # 观测：hub 日志里有一行「整段交领」（现场排障的第一只手电）。
        # 文案 2026-09-25 改过：判据从「课程当前 mode」换成 **job 自己的 role**，
        # 所以行里报的是「请求方自称的角色」而不是「这是离线课」。
        assert any("整段交领" in ln and "marked-1" in ln for ln in hub.lines), hub.output()
    finally:
        hub.close()


# ────────────────────────── ② 逐轮补传 + 控制台读面 ──────────────────────────


def test_segment_rounds_backfeed_into_the_right_course_and_show_up_on_the_read_face(
    tmp_path: Path,
) -> None:
    """云机逐轮补传 → 落**本课**目录 → `/admin/offline` 报出段内进度（控制台读面）。

    取包腿（在跑的云机）就这么补传：每跑完一轮推一次，`course` 是 hub 下发的归位键。
    """
    traj = tmp_path / "traj"
    off_job_root, off_jsonl = _course_dirs(traj, C_OFF)
    on_job_root, on_jsonl = _course_dirs(traj, C_ON)
    man_off = _publish_run_job(C_OFF, off_job_root, off_jsonl, tmp_path)
    _publish_plain_job(C_ON, on_job_root, on_jsonl, tmp_path)
    hub = _Hub(traj)
    try:
        hub.ready(expect=[C_OFF, C_ON])
        hub.set_mode(C_OFF, "offline")
        got = hub_poll(hub.base, TOKEN, worker_id="marked-1", role="offline")
        assert got is not None and got["job_id"] == man_off["job_id"], got
        course = got["course"]

        # ── 云机侧：产物目录（真 ArtifactStore；这里用「落几轮」代替真 rollout/PPO）──
        run_id = "seg-20260919"
        art = tmp_path / "artifacts"
        plan = {"start_it": 1, "end_it": 4}  # 这里用「落几轮」代替真 rollout/PPO
        store = ArtifactStore(art, run_id=run_id, log=_quiet)
        store.start(plan, got["manifest"], plan_sha256=got["manifest"]["plan_sha256"])
        for it in (2, 3, 4):
            store.checkpoint(
                it,
                weights_json=json.dumps({"it": it, "w": it * 1.5}).encode("utf-8"),
                opt_tar=b"opt-%d" % it,
                row={"it": it, "wall_sec": 1.0, "agg": {"kl": 0.01}},
            )

        # ── 补传（真 OfflineDeliverer；归位键就是 hub 下发的那门课）──
        d = OfflineDeliverer(
            base_url=hub.base, token=TOKEN, run_id=run_id, artifacts_dir=art, course=course
        )
        assert d.sync() == 3, f"逐轮补传没上去：{hub.output()}"

        # ── hub 落位：本课目录（串课 = 曲线画在另一门课上）──
        run_dir = off_job_root / "offline" / run_id
        assert sorted(p.name for p in run_dir.glob("it-*")) == ["it-002", "it-003", "it-004"]
        assert not (on_job_root / "offline").exists(), "补传串到了另一门课"

        # ── 控制台读面（dashboard `parseOfflineProgress` 吃这个形状）──
        st, body = _http(hub.base, "/admin/offline")
        assert st == 200, body
        progress = body["progress"]
        assert list(progress) == [course], progress
        seg = progress[course][run_id]
        assert seg["its"] == [2, 3, 4] and seg["count"] == 3
        assert seg["last_mtime"] > 0, "最近一轮的时间戳 = 「在跑还是在挂」的唯一判据"

        # 段末摘要（另一条腿，同一归位键）
        assert d.deliver_result(it_end=4, state="complete", summary={"rounds": 3})
        assert (run_dir / "result.json").exists()

        # 幂等：重传同一段不产生重复轮次（补传天然会重传：重连 / 重启续投）
        d2 = OfflineDeliverer(
            base_url=hub.base, token=TOKEN, run_id=run_id, artifacts_dir=art, course=course
        )
        assert d2.sync() == 0
        st, body = _http(hub.base, "/admin/offline")
        assert body["progress"][course][run_id]["its"] == [2, 3, 4]
    finally:
        hub.close()


# ────────────────────── ④ 云机侧：**首次跑**（产物目录还不存在） ──────────────────────


def _real_pack(
    tmp_path: Path, course: str, monkeypatch: pytest.MonkeyPatch
) -> Path:
    """用**真导出器**写一个任务包（控制台 `--export-bundle` 走的就是它）。

    `normalize_manifest` 被替换成恒等（最小 manifest 缺时间戳/字段时会拒），与
    `tests/test_offline_task_pack.py::_export_real_pack` 同一口径。
    """
    from remote import bundle as bundle_mod

    monkeypatch.setattr(bundle_mod, "normalize_manifest", lambda m: m)
    src = tmp_path / "_src"
    src.mkdir(parents=True, exist_ok=True)
    (src / INIT_WEIGHTS_NAME).write_bytes(b'{"format":"nn-weights-json","params":{"w":1}}')
    code_zip = src / bundle_mod.CODE_NAME
    with zipfile.ZipFile(code_zip, "w") as z:
        z.writestr("remote/_marker.py", "VALUE = 'e2e'\n")
    with zipfile.ZipFile(src / TS_CODE_NAME, "w") as z:
        z.writestr("tools/sim/export-eval-game.ts", "// e2e\n")
    plan = json.dumps({"start_it": 1, "end_it": 5}).encode("utf-8")
    out = tmp_path / "packs" / f"task-{course}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    bundle_mod.export_bundle(
        out,
        manifest={
            "kind": "run",
            "runId": "run-e2e",
            "it": 1,
            "plan_sha256": hashlib.sha256(plan).hexdigest(),
            "commit": "c" * 40,
        },
        plan_bytes=plan,
        init_weights_path=src / INIT_WEIGHTS_NAME,
        code_zip_path=code_zip,
        ts_code_zip_path=src / TS_CODE_NAME,
    )
    return out


def test_a_fresh_run_lays_down_code_and_ts_tree_before_the_loop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 现场回归（2026-09-25 云机实测）：**首次跑**从**真包**起跑，走到 `run_loop` 之前不能死。

    报障原文：`代码就位: /tmp/worker-code（…来源 任务包 …）` 之后当场
    `未捕获异常 FileNotFoundError: .../battle-offline/x20-dodge-l1/run/ts_code.zip` ——
    补 TS 运行时那一步往一个**还不存在**的产物目录里写 zip，整个 cell 死在 rollout 之前。

    为什么本文件此前没拦住（而这正是补这条用例的理由）：本文件只覆盖 hub 侧，云机侧只有单测，
    而单测的产物目录都是**预建好**的 —— 全新一跑（`<work>/run` 不存在）从来没人走过。

    判据：不抛；`run_loop` 被调起时产物目录已存在、`ts_code/` + `ts_code.zip` 就位；
    首次跑走**老规矩**（argv 带 `--bundle`，铺产物目录交给 `import_bundle`）。
    """
    pack = _real_pack(tmp_path, C_OFF, monkeypatch)
    work = tmp_path / "work"
    argv_seen: list[list[str]] = []
    dest_existed: list[bool] = []

    def fake_loop(argv: list[str]) -> int:
        argv_seen.append(list(argv))
        dest_existed.append((work / "run").is_dir())
        return 0

    # 代码解包到临时目录（生产缺省是 `/tmp/worker-code`：本用例不该往机器上真写那个路径）。
    real_ensure_code = offline_boot.ensure_code
    monkeypatch.setattr(
        offline_boot,
        "ensure_code",
        lambda *a, **kw: real_ensure_code(*a, **{**kw, "code_dir": str(tmp_path / "worker-code")}),
    )
    cfg = {
        "course": C_OFF,
        "work_dir": str(work),
        "download_dir": str(tmp_path / "out"),
        "device": "cpu",
        "live_backfeed": False,
        "task_zip": str(pack),  # = 手动送包那条路：拿包就走，不经 hub
    }
    logs: list[str] = []
    rc = offline_boot.run_one_course(
        cfg, {}, logs.append, None, course=C_OFF, multi=False, run_loop_main=fake_loop
    )

    assert rc == 0, logs
    assert dest_existed == [True], f"run_loop 起来时产物目录必须已存在；日志：{logs}"
    argv = argv_seen[0]
    dest = work / "run"
    assert (dest / TS_CODE_NAME).is_file(), logs
    assert (dest / offline_boot.TS_TREE_NAME / "tools" / "sim" / "export-eval-game.ts").is_file()
    assert "--bundle" in argv and str(pack) in argv, argv


# ────────────────────────── ③ 取任务包（hub → 云机） ──────────────────────────


def test_task_pack_endpoint_hands_over_the_console_export(tmp_path: Path) -> None:
    """`GET /offline/task-pack?course=` 递的就是控制台导出的那份 zip（404/401/越界各有话说）。

    ⚠ 取包要**先切离线**（2026-09-25 的 mode 闸，plan/online-offline-role-routing §2.4）：
    包在盘上 ≠ 该发给你——切离线时控制台会自动导出且「已有包不动」⇒ 切回在线后包还在，
    不查 mode 就等于在线课也能被离线盘取走跑整段（L6）。所以本用例先钉 409、切离线后 200。
    """
    traj = tmp_path / "traj"
    _course_dirs(traj, C_OFF)
    _course_dirs(traj, C_ON)
    pack_bytes = b"PK\x03\x04" + b"console-export" * 20
    (traj / C_OFF / f"task-{C_OFF}.zip").write_bytes(pack_bytes)
    hub = _Hub(traj)
    try:
        hub.ready(expect=[C_OFF, C_ON])

        # 课还是在线 ⇒ 409（包在、没丢：正文要说清下一步，不是 404 把人引向「再导一次」）
        st, raw = _http_bytes(hub.base, f"/offline/task-pack?course={C_OFF}")
        assert st == 409 and raw != pack_bytes, (st, raw[:200])
        assert "online" in raw.decode("utf-8"), raw[:200]

        hub.set_mode(C_OFF, "offline")
        st, raw = _http_bytes(hub.base, f"/offline/task-pack?course={C_OFF}")
        assert st == 200 and raw == pack_bytes, (st, raw[:40])
        assert hashlib.sha256(raw).hexdigest() == hashlib.sha256(pack_bytes).hexdigest()

        # 没有这门课的包 ⇒ 404 + 人读下一步（「先去控制台导出」），而不是空体
        st, raw = _http_bytes(hub.base, f"/offline/task-pack?course={C_ON}")
        # 文案 2026-09-25 改过（plan/offline-switch-auto-bundle §3.2/评审 F3）：「导出要求训练已停」是过期
        # 口径（`exportGuard` 早就不以「训练在跑」拒导）⇒ 现在写「随时可导，不必停训」。
        assert st == 404 and "导出任务包" in raw.decode("utf-8"), (st, raw[:200])

        # 未鉴权 ⇒ 401（token 是唯一入口；任务包里有课程全文与权重）
        st, _raw = _http_bytes(hub.base, f"/offline/task-pack?course={C_OFF}", token="")
        assert st == 401

        # 路径越界 ⇒ 400（课程名进的是磁盘路径）
        st, raw = _http_bytes(
            hub.base, "/offline/task-pack?course=" + urllib.parse.quote("../../etc/passwd")
        )
        assert st == 400 and "课程名非法" in raw.decode("utf-8"), (st, raw[:200])
    finally:
        hub.close()
