"""e2e/test_autonomous_round_flow_e2e.py —— 自主云机的**整轮编排**（不是 rollout/PPO 内部）。

用户口径（2026-10-29）：
  · **PPO 完成，立即开始下一轮 rollout，再继续 PPO**；
  · **eval 并行藏进 PPO 墙钟里**（不许占轮边界、不许和 rollout 抢 CPU）；
  · **传输进程不要阻塞训练进程**（本文件用「提交时刻」钉住：`submit` 只在 PPO 窗口里发生）。

做法：真的走 `remote.plan_run.run_plan_job`（离线段的唯一入口）+ 真的 `ArtifactStore`
（产物在盘上）+ 真的账本，只把 **rollout/PPO 的执行体** 和 **评估执行体** 换成注入的桩
（`run_job_fn` / `_setup_cloud_eval`）——坏就坏在编排的顺序与时机上，那正是本文件守的东西。

纪律：不 spawn bun/node、不加载 torch、不跑真 rollout/PPO；单用例 < 5s。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent  # nn-training/
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from remote import plan_handoff
from remote.artifacts import ArtifactStore
from remote.plan_run import run_plan_job
from tests.remote.test_run_loop import _iter_shaped_result, _prepare, _quiet

#: 桩里 rollout/PPO 各自的「耗时」（秒）——只用来造一个可辨认的时间窗。
ROLLOUT_SEC = 0.02
PPO_SEC = 0.05


class _FakeRunJob:
    """一轮的 rollout + PPO（桩）：记录两个窗口，并在 PPO **开跑那一刻**回调 `on_ppo_start`。"""

    def __init__(self) -> None:
        self.rounds: list[dict] = []

    def __call__(self, _base, _token, job, *, on_ppo_start=None, **_kw):
        m = job["manifest"]
        roll0 = time.monotonic()
        # sleep-ok: 夹具模拟 rollout 工作量（不是「等对方先跑」）
        time.sleep(ROLLOUT_SEC)
        roll1 = ppo0 = time.monotonic()
        if on_ppo_start is not None:
            on_ppo_start()  # ← 编排的判据：评估只能在这里被提交
        # sleep-ok: 夹具模拟 PPO 工作量（TPU 上的那几十秒）
        time.sleep(PPO_SEC)
        ppo1 = time.monotonic()
        self.rounds.append(
            {"it": int(m["it"]), "roll0": roll0, "roll1": roll1, "ppo0": ppo0, "ppo1": ppo1}
        )
        return _iter_shaped_result(m, weights=b'{"w":1}', opt=b"opt", win=0.5, marker="it")


class _FakeEvalPlan:
    """每轮都到点（`eval_every=1`）；`games` 只喂进度行。"""

    plan = {"games": 8}

    def due(self, it: int) -> bool:
        return True


class _FakeEvalRunner:
    """记录**提交时刻**；`submit` 立即返回（真实现是后台线程跑局）。"""

    def __init__(self) -> None:
        self.submits: list[tuple[int, float]] = []

    def submit(self, it: int) -> bool:
        self.submits.append((int(it), time.monotonic()))
        return True

    def wait_idle(self, _timeout: float) -> bool:
        return True

    def drain(self, _timeout: float | None = None) -> bool:
        return True

    def inflight_it(self):
        return None

    def pending_it(self):
        return None


@pytest.fixture()
def scene(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """造一个交接现场，并把评估执行体换成桩（装配点在 `plan_handoff`）。"""
    plan, m, job_dir, first = _prepare(tmp_path, iters=5)
    fake_job = _FakeRunJob()
    runner = _FakeEvalRunner()

    def _setup(ctx) -> None:  # 替掉真装配（它会去 spawn bun 的评估腿）
        ctx.eval_on_cloud = True
        ctx.eval_plan = _FakeEvalPlan()
        ctx.eval_runner = runner

    monkeypatch.setattr(plan_handoff, "_setup_cloud_eval", _setup)
    return plan, m, job_dir, first, fake_job, runner, tmp_path


def test_eval_is_submitted_inside_a_ppo_window_not_between_rounds(scene) -> None:
    """★ 核心判据：评估提交只发生在**某轮 PPO 的墙钟里**，且评的是**上一轮**。

    旧实现是在轮边界 `_maybe_cloud_eval`（提交 + 就地等），评估正好压在「下一轮 rollout
    之前」——rollout 要 94 个 CPU 槽、评估要 80 个 ⇒ 真重叠会成批踩单局硬顶；
    不重叠就等于白等它跑完再开 rollout。
    """
    plan, m, job_dir, first, fake_job, runner, tmp = scene
    run_plan_job(
        job_id="",
        manifest=m,
        job_dir=job_dir,
        work_dir=tmp / "w",
        artifacts_dir=tmp / "art",
        plan=plan,
        plan_sha256=m["plan_sha256"],
        first_result=first,
        run_job_fn=fake_job,
        deliver=False,
        log=_quiet,
    )
    assert len(fake_job.rounds) >= 2, f"至少要跑两轮才看得出「藏在下一轮 PPO 里」：{fake_job.rounds}"
    assert runner.submits, "一条评估都没提交"
    for eit, t in runner.submits:
        hosts = [r for r in fake_job.rounds if r["ppo0"] <= t <= r["ppo1"]]
        assert hosts, f"it{eit} 的评估提交不在任何 PPO 窗口内：{runner.submits} / {fake_job.rounds}"
        assert hosts[0]["it"] == eit + 1, f"提交的必须是**上一轮**的评估（it{eit} ≠ {hosts[0]['it']}-1）"
        # 半开区间：提交点就在 `roll1 == ppo0` 这个边界上，它属于 PPO（不属于 rollout）
        assert not any(r["roll0"] <= t < r["roll1"] for r in fake_job.rounds), (
            "评估不许落在 rollout 窗口里（那是 94 个 CPU 槽最紧的时候）"
        )


def test_each_round_leaves_a_complete_artifact_pair(scene) -> None:
    """整轮编排的产出面：每轮 `weights.json` + `opt.tar` + 一行账本（缺一不可）。"""
    plan, m, job_dir, first, fake_job, _runner, tmp = scene
    res = run_plan_job(
        job_id="",
        manifest=m,
        job_dir=job_dir,
        work_dir=tmp / "w2",
        artifacts_dir=tmp / "art",
        plan=plan,
        plan_sha256=m["plan_sha256"],
        first_result=first,
        run_job_fn=fake_job,
        deliver=False,
        log=_quiet,
    )
    store = ArtifactStore(tmp / "art", run_id="run-runloop")
    its = [r["it"] for r in fake_job.rounds]
    for it in its:
        assert store.weights_path(it).is_file(), f"it{it} 缺权重"
        assert store.opt_path(it).is_file(), f"it{it} 缺 opt"
    assert [int(r["it"]) for r in res["iters"]][-1] == its[-1], "末轮账本行与实跑轮次要对上"


def test_round_loop_does_not_pause_for_eval(scene) -> None:
    """轮与轮之间的空档 ≈ 0：评估不许变成「轮边界上的等待」。"""
    plan, m, job_dir, first, fake_job, _runner, tmp = scene
    run_plan_job(
        job_id="",
        manifest=m,
        job_dir=job_dir,
        work_dir=tmp / "w3",
        artifacts_dir=tmp / "art",
        plan=plan,
        plan_sha256=m["plan_sha256"],
        first_result=first,
        run_job_fn=fake_job,
        deliver=False,
        log=_quiet,
    )
    rounds = fake_job.rounds
    gaps = [rounds[i + 1]["roll0"] - rounds[i]["ppo1"] for i in range(len(rounds) - 1)]
    worst = max(gaps)
    # timing-ok: 契约上界 —— 桩的每轮固定 0.07s；这里的"不许等"是**没有评估等待**（旧口径下
    # 一次 wait_idle 就是几百 ms 起），所以给 1s 的宽松上界，只抓"轮边界被按住"这种量级。
    assert worst < 1.0, f"轮间空档 {worst:.2f}s —— 评估或传输把轮边界按住了：{gaps}"
