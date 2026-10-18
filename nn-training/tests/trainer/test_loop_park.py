"""test_loop_park.py — 正常完成停车不断进程（2026-09-12 用户定案）。

c5-ent it80 事故复盘：loop 跑满预设 iters 后直接退出进程 → console 误报意外退出。
新语义：ALL DONE 后进程**停车不断开**——本地停止采集（不再开新 it）、向云机下发
停机指示（省配额）、账本落 run_complete 事件（console 横幅派生源）。

覆盖（纯逻辑 + 注入，不起真 loop、不碰 torch）：
  - write_run_complete：账本事件 schema（event/iter/iters/reason/time）。
  - should_park_on_done：smoke 作废干净退出 / --exit-on-done → 退出（旧行为）；
    其余一律停车。
  - TrainingLoop._park_after_completion：云停机达令（PAUSE）确有下发、在飞预采
    子进程被收敛、账本事件落盘、中断干净返回（不抛、不退出进程）。
"""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from trainer.loop_core import TrainingLoop
from trainer.loop_lifecycle import should_park_on_done
from worker.events import write_run_complete


def _args(**kw) -> types.SimpleNamespace:
    base = {
        "iters": 80,
        "out": "tmp/x/weights.json",
        "traj": "tmp/x",
        "remote_hub_url": "",
        "remote_token": "",
        "exit_on_done": False,
        "mode": "per-tick",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


def _loop(tmp_path: Path, **kw) -> TrainingLoop:
    loop = TrainingLoop(_args(**kw), None, "bun", {})
    loop._jsonl_path = tmp_path / "training_log.jsonl"
    loop._traj_root = tmp_path
    return loop


@pytest.fixture(autouse=True)
def _gate_halt_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """门禁停机模式 2026-10-01 起是**平台文件**：本文件一律把它重定向进 tmp_path。

    不重定向就会读仓根 `tmp/gate-halt.json`（那是控制台的活状态：拨到 notify 时本文件的
    停机断言会变红），回执也会写进本机工作区。
    """
    monkeypatch.setenv("NN_GATE_HALT", str(tmp_path / "gate-halt.json"))
    monkeypatch.setenv("NN_GATE_HALT_APPLIED", str(tmp_path / "gate-halt.applied.json"))
    monkeypatch.setenv("NN_GATE_HALT_LEG", "local")


def test_write_run_complete_schema(tmp_path: Path) -> None:
    p = tmp_path / "training_log.jsonl"
    write_run_complete(p, 80, 80, "正常收官")
    row = json.loads(p.read_text(encoding="utf-8").strip())
    assert row["event"] == "run_complete"
    assert row["iter"] == 80 and row["iters"] == 80
    assert "收官" in row["reason"]
    assert isinstance(row["time"], str) and len(row["time"]) == 19


# ─────────────────── 收工原因（2026-10-15，plan/run-complete-cause-banner 写侧） ───────────────────


def test_write_run_complete_cause_keys_are_optional(tmp_path: Path) -> None:
    """**只在给了才写**：老调用方（BC 腿 / 老测试）的账本行逐字节不变（多键对未知读者透明）。"""
    p = tmp_path / "training_log.jsonl"
    write_run_complete(p, 80, 80, "正常收官")
    row = json.loads(p.read_text(encoding="utf-8").strip())
    # 键集合**相等**（不用 `"cause" not in row` 那种子串形态：`tests/test_source_text_assert_budget.py`
    # 的口径把 `in`/`not in` + 字面量算成文本断言，而这条判据本来就是「键集合」这件事）。
    assert set(row) == {"event", "iter", "iters", "time", "reason"}

    write_run_complete(p, 81, 80, "正常收官", cause="max_hours", detail="墙钟 12h 预算到顶")
    row2 = json.loads(p.read_text(encoding="utf-8").splitlines()[-1])
    assert row2["cause"] == "max_hours" and row2["detail"] == "墙钟 12h 预算到顶"


@pytest.mark.parametrize(
    ("cause", "detail", "iters", "want_cause", "want_reason_head"),
    [
        ("max_hours", "墙钟 12h 预算到顶（从课程首启累计 19.5h）", 500, "max_hours", "正常收官（it239/500）："),
        ("", "", 500, "iters", "正常收官（it239/500）：跑满声明的轮数，"),
        ("", "", 0, "unknown", "正常收官（it239/∞），"),
    ],
    # ⚠ 参数里带中文 ⇒ 必须给 ASCII id：pytest 会把参数拼进 `tmp_path` 的目录名
    #（Windows 上「：」「（」直接是非法路径字符，用例在 setup 阶段就炸）。
    ids=["cause_from_guard", "iters_exhausted", "infinite_course_unknown"],
)
def test_finish_course_puts_the_stop_cause_into_run_complete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cause: str, detail: str, iters: int,
    want_cause: str, want_reason_head: str,
) -> None:
    """收官账本行带 `cause`/`detail`，`reason` 保留旧词头与旧尾串（手工 grep / 老读面不受影响）。

    三格对应三种真实形状：① 判据写了原因（max_hours 现场）；② 没有任何判据 = 正常跑满；
    ③ 无限课程（`iters<=0`）从来不会"跑满"到这里 ⇒ `unknown`（**绝不编原因**）。
    """
    loop = _loop(tmp_path, iters=iters)
    monkeypatch.setattr(TrainingLoop, "_sync_cloud_halt", lambda self, it, v: None)
    loop._stop_cause, loop._stop_detail = cause, detail
    loop.finish_course(239, drain=False)
    row = json.loads((tmp_path / "training_log.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert row["cause"] == want_cause
    assert row["reason"].startswith(want_reason_head)
    assert row["reason"].endswith("，本地停采、云机已停机")
    if detail:
        assert row["reason"].startswith(f"正常收官（it239/{iters}）：{detail}")
    assert loop._stop_cause == "" and loop._stop_detail == "", "读一次即清（重开不得复用旧因）"


def test_budget_hard_cut_records_the_max_hours_cause(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """G5 预算硬断（2026-10-15 y5-term 现场那次真原因）：命中即写 `max_hours` + 带数字的 detail。

    判据基线是**课程首启**（`first_run_start_ts` 读第一条 `run_start`）——detail 必须说清
    「预算多少 h / 累计多少 h」，否则下一个人还会问「为什么重启不续命」。
    """
    import time as _time

    from worker.loop_guards_gate import TrainingGuardsGate

    loop = _loop(tmp_path, max_hours=12.0)
    started = _time.strftime("%Y-%m-%d %H:%M:%S", _time.localtime(_time.time() - 13 * 3600))
    loop._jsonl_path.write_text(
        json.dumps({"event": "run_start", "time": started}) + "\n", encoding="utf-8"
    )
    assert TrainingGuardsGate._budget_hard_cut(loop, 239) is True
    assert loop._stop_cause == "max_hours"
    # 整串相等（不用子串：detail 的交付物就是这句给操作员读的话，全等比子串强，
    # 也不进 `test_source_text_assert_budget` 的计数口径）。
    assert loop._stop_detail == "墙钟 12.0h 预算到顶（从课程首启累计 13.0h）"



def test_should_park_on_done() -> None:
    assert should_park_on_done(_args(), smoke_void=False) is True
    assert should_park_on_done(_args(), smoke_void=True) is False  # --smoke 作废退出不断
    assert should_park_on_done(_args(exit_on_done=True), smoke_void=False) is False


def test_park_halts_cloud_writes_ledger_and_returns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = _loop(tmp_path)
    halts: list[tuple[int, str]] = []
    monkeypatch.setattr(
        TrainingLoop, "_sync_cloud_halt", lambda self, it, v: halts.append((it, v))
    )
    calls = {"n": 0}

    def _sleep_once(_s: float) -> None:
        calls["n"] += 1
        raise KeyboardInterrupt  # 停车 sleep 可中断 → 干净返回

    monkeypatch.setattr("time.sleep", _sleep_once)
    loop._park_after_completion(80)
    assert halts == [(80, "PAUSE")]  # 云停机达令确有下发
    rows = [
        json.loads(line)
        for line in (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows and rows[-1]["event"] == "run_complete" and rows[-1]["iter"] == 80
    assert calls["n"] == 1  # 进过停车等待（没直接返回也没 hang）


def test_park_drains_live_precollect_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loop = _loop(tmp_path)
    monkeypatch.setattr(TrainingLoop, "_sync_cloud_halt", lambda self, it, v: None)
    monkeypatch.setattr("time.sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt()))

    class _Child:
        terminated = False

        def poll(self):
            return None  # 还活着

        def terminate(self):
            self.terminated = True

    child = _Child()
    loop._collect_child = child  # type: ignore[assignment]
    loop._park_after_completion(80)
    assert child.terminated is True  # 在飞预采子进程被收敛，不留孤儿空烧
    assert loop._collect_child is None


def test_park_without_hub_is_still_quiet(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """无 hub（纯本机）时 _sync_cloud_halt 真跑也不碰网络、不抛。"""
    import trainer.loop_guards as guards

    sent: list[tuple[str, str, bool]] = []
    monkeypatch.setattr(
        guards, "set_cloud_halt", lambda url, tok, halt, **kw: sent.append((url, tok, halt))
    )
    loop = _loop(tmp_path, remote_hub_url="http://127.0.0.1:1", remote_token="t")
    loop._sync_cloud_halt(80, "PAUSE")
    assert sent == [("http://127.0.0.1:1", "t", True)]
    loop2 = _loop(tmp_path)  # 无 hub → 短路，零调用
    loop2._sync_cloud_halt(80, "PAUSE")
    assert len(sent) == 1


def test_park_keeps_claiming_evalboard_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """停车期每轮唤醒复用 idle 窗认领 EvalBoard B 批（60s 粒度，配置热读）。"""
    import common.distribution

    loop = _loop(tmp_path)
    monkeypatch.setattr(TrainingLoop, "_sync_cloud_halt", lambda self, it, v: None)
    cfg: dict = {"nodes": [], "policy": {}}
    monkeypatch.setattr(common.distribution, "load_dist_config", lambda: cfg)
    idles: list[tuple[int, object]] = []
    monkeypatch.setattr(
        TrainingLoop, "_evalboard_idle", lambda self, it, dc: idles.append((it, dc))
    )
    wakes = {"n": 0}

    def _sleep_3(_s: float) -> None:
        wakes["n"] += 1
        if wakes["n"] >= 3:
            raise KeyboardInterrupt

    monkeypatch.setattr("time.sleep", _sleep_3)
    loop._park_after_completion(80)
    assert wakes["n"] == 3
    assert idles == [(80, cfg), (80, cfg)]  # 前两次唤醒各认领一次，第三次中断退出


def test_park_claim_failure_never_breaks_parking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """认领抛异常 → 自吞继续停车（不炸进程、不退出）。"""
    loop = _loop(tmp_path)
    monkeypatch.setattr(TrainingLoop, "_sync_cloud_halt", lambda self, it, v: None)

    def _boom(self, it, dc):
        raise RuntimeError("queue unreadable")

    monkeypatch.setattr(TrainingLoop, "_evalboard_idle", _boom)
    monkeypatch.setattr("time.sleep", lambda _s: (_ for _ in ()).throw(KeyboardInterrupt()))
    loop._park_after_completion(80)  # 不抛即过
    rows = [
        json.loads(line)
        for line in (tmp_path / "training_log.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert rows and rows[-1]["event"] == "run_complete"


# ---------------------------------------------------------------- M2 的资格闸（A，2026-10-17）
#
# 2026-10-16 事故（plan/export-must-not-cancel-live-job）：导出全离线任务包的进程（只读快照）
# 也构造了主循环 ⇒ 它在 `__init__` 里跑了 M2「腿启动清理上一代」，把**在线腿正在等的那份**
# job 判成残留作废（hub 侧 409 在飞、目录保留，账本却已撤）⇒ 池里没它、腿还在等它 ⇒
# 那门课再没被接单（3h+）。撤销权只归**会派活的进程**。


def _previous_run_ledger(tmp_path: Path, jid: str = "j" * 16) -> Path:
    """上一代 runId 留在本课账本里的 pending job（+ 它的 job 目录）。"""
    (tmp_path / "remote-jobs" / jid).mkdir(parents=True, exist_ok=True)
    p = tmp_path / "training_log.jsonl"
    p.write_text(
        json.dumps({"event": "job_pending", "job_id": jid, "runId": "r-prev", "it": 3, "ts": 1.0})
        + "\n",
        encoding="utf-8",
    )
    return p


def _ledger_events(p: Path) -> list[str]:
    return [
        str(json.loads(x)["event"])
        for x in p.read_text(encoding="utf-8").strip().splitlines()
        if x.strip()
    ]


def test_export_leg_does_not_run_the_previous_run_cleanup(tmp_path: Path) -> None:
    """★ A：`--export-bundle`（只读快照）构造主循环后，上一代的活**一个字不动**。"""
    ledger = _previous_run_ledger(tmp_path)
    TrainingLoop(  # 现场构造 Way：导出腿也走 TrainingLoop
        _args(traj=str(tmp_path), export_bundle=str(tmp_path / "bundle.zip")), None, "bun", {}
    )
    assert _ledger_events(ledger) == ["job_pending"]


def test_normal_leg_still_cancels_the_previous_run(tmp_path: Path) -> None:
    """反向：会派活的腿清理照旧（闸只放行导出腿，别把 M2 一起关掉）。"""
    ledger = _previous_run_ledger(tmp_path)
    TrainingLoop(_args(traj=str(tmp_path)), None, "bun", {})
    assert _ledger_events(ledger) == ["job_pending", "job_cancelled"]


def test_the_m2_gate_and_the_course_lock_share_one_predicate() -> None:
    """★ A 的判据只有一份：`dispatches_jobs`（M2 资格）与 `takes_course_lock` 同值。

    两处各自写一遍 `getattr(args, "export_bundle", …)` 就是下一次漂移的种子。
    """
    from trainer.dispatch_queue import dispatches_jobs
    from trainer.once_shot import takes_course_lock

    for export in ("", "tmp/out.zip"):
        args = _args(export_bundle=export)
        assert dispatches_jobs(args) is takes_course_lock(args) is (not export)
    # 不带这个键的调用方（serve 的 course_args / smoke）默认「会派活」——默认方向必须是安全的那边。
    assert dispatches_jobs(types.SimpleNamespace()) is True


def test_node_rollout_is_initialized_at_construction(tmp_path: Path) -> None:
    """P0（2026-10-02 事故）：`_node_rollout` 必须在 `__init__` 就有初值。

    它此前只在 `step_course_iter` 里赋值 ⇒ `EnginePool` 驱逐后重建的引擎、而调度器队列已走到
    `ppo`/`cleanup` 时，裸读它 AttributeError ⇒ 该课每秒重启一次、一轮都跑不完
    （`h4-hurt-f75`/`h4-hurt-f150` 实测：1076 次 `run_start` / **0** 条 `iteration`）。

    初值必须是 `False`（= 本地轮）：即便真读到了早值，也只是把采集判成本地，不改变采集行为。
    """
    loop = _loop(tmp_path)
    assert loop._node_rollout is False  # 既不是 AttributeError，也不是 True
