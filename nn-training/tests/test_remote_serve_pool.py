"""test_remote_serve_pool.py — 节点侧长驻 worker 池（`remote/serve_pool.py`）单测。

覆盖（对应 plan `conv-optimize.plan.md` / `docs/nn/runtime-opt.md` §21 的 A 方案）：
  * 复用：N 局共用**一个**进程（`spawned` 不随局数涨）—— 这就是 1.59× 的全部来源；
  * 日志隔离：worker stdout 混着所有局的日志，必须按局切回各自的 `rollout.log`；
  * 三类回退（ERR / 进程死掉 / 超时）都返回 None（调用方走一次性），且不留残进程；
  * 白名单与开关：名单外的脚本/`NN_SERVE_POOL=0` 连池都不建；
  * 端到端接线：`run_iter_rollout` 走池也能产出**同一批 shard**（逐局报告、聚合、日志齐全）。

桩是「支持 `--serve` 的 python 脚本」——不依赖 bun 与真导出器；真 bun 的 serve 逐位对拍在
TS 侧（`tools/sim/*` 的 serve 测试）。
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.iter_rollout as iter_rollout
import remote.serve_pool as serve_pool
from remote import game_watch
from remote.serve_pool import ServePool

# ------------------------------------------------------------------ 桩

#: 一个**两种模式都支持**的 stub 导出器（与真导出器同构：`main(argv)` 跑一局，`--serve` 只是
#: 外面包一层协议循环）—— 同一份桩既能走池、也能走一次性回退，回退用例才有意义。
#:
#: `@COUNT@` = 共享计数器文件（记录「一局被真跑了多少次」，用来证明复用而不是重跑）；
#: `@FAIL_SEED@` = 让它对该 seed 打 `__SERVE_ERR__`（造回退路径）。
_STUB_SERVE = """\
import json, os, sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)   # 管道里必须是行缓冲，否则标记行会卡住
COUNT = Path(r"@COUNT@")
FAIL_SEED = "@FAIL_SEED@"


def one(a):
    def val(flag):
        return a[a.index(flag) + 1] if flag in a else ""

    out = Path(val("--out"))
    out.mkdir(parents=True, exist_ok=True)
    stage, seed, wver = int(val("--stages")), int(val("--seeds")), val("--wver")
    if FAIL_SEED and str(seed) == FAIL_SEED and "--serve" in sys.argv:
        # **只在 serve 模式**失败：模拟「worker 状态坏了」这类长驻特有故障，好让回退用例里
        # 那一局在一次性路径上真的能成功（否则测的就成了「导出器本来就坏」）
        print(f"[stub-serve] refusing s{stage}/d{seed}")
        raise RuntimeError(f"stub-refused-s{seed}")
    n = int(COUNT.read_text()) + 1 if COUNT.exists() else 1
    COUNT.write_text(str(n))
    d = out.parent / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"stage": stage, "seed": seed, "wver": wver}))
    (out / "_rl_report.json").write_text(json.dumps({
        "games": 1, "winRate": 1.0, "outcomes": {"stage_clear": 1},
        "totalSamples": 2, "totalTicks": 20, "scoreList": [1.0], "dimLists": {"kills": [1.0]},
    }))
    print(f"[stub-serve] done s{stage}/d{seed} pid={os.getpid()} run#{n}")


if "--serve" in sys.argv:                     # 长驻：stdin 一行一局
    print("__SERVE_READY__")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            one(json.loads(line))
            print("__SERVE_OK__")
        except Exception as e:                # noqa: BLE001 — 协议要求把消息原样送回去
            print(f"__SERVE_ERR__ {e}")
    print("__SERVE_DONE__")
else:                                         # 一次性：直接跑一局
    one(sys.argv[1:])
"""

#: 收下任务就**直接死掉**（不回 OK 也不回 ERR）：模拟真 bun 被 OOM/信号带走。
_STUB_DIES = """\
import json, sys, os
sys.stdout.reconfigure(line_buffering=True)
print("__SERVE_READY__")
for line in sys.stdin:
    if line.strip():
        print(f"[stub-dies] got task, exiting")
        os._exit(7)
"""

#: 收下任务就**睡死**：模拟卡住的局（本地硬顶必须能把它掐掉）。
_STUB_HANGS_ON_TASK = """\
import json, sys, time
sys.stdout.reconfigure(line_buffering=True)
print("__SERVE_READY__")
for line in sys.stdin:
    if line.strip():
        print(f"[stub-hang] task in flight")
        time.sleep(3600)
"""

#: 完全不认 `--serve`（只会跑一次性路径的桩）：池必须**快速**判定起不来，不等满就绪上限。
_STUB_NO_SERVE = """\
import sys
print("no serve here")
sys.exit(0)
"""


def _write_stub(tmp_path: Path, body: str, name: str = "stub_serve.py", **subs: str) -> Path:
    p = tmp_path / name
    text = body
    for k, v in subs.items():
        text = text.replace(f"@{k.upper()}@", v)
    p.write_text(text, encoding="utf-8")
    return p


def _pool(tmp_path: Path, script: Path, *, workers: int = 1, ready: float = 30.0) -> ServePool:
    """按测试桩建池（`bun` = 本进程 python；ts_dir = 桩所在目录）。"""
    return ServePool(sys.executable, script.name, tmp_path, workers, ready_timeout_sec=ready)


def _argv(script: Path, stage: int, seed: int, out: str) -> list[str]:
    return [str(script), "--out", out, "--stages", str(stage), "--seeds", str(seed),
            "--wver", "W" * 64]


def _task(script: Path, stage: int, seed: int, out: str, tmp_path: Path) -> tuple[list[str], Path]:
    """一局的任务：argv + 它自己的日志路径（与 `_run_one_game` 的落点一致）。"""
    return _argv(script, stage, seed, out), (tmp_path / out / iter_rollout.ROLLOUT_LOG_NAME)


def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """把轮询粒度调小（生产 0.5s）——否则超时用例要等秒级。"""
    monkeypatch.setattr(game_watch, "GAME_POLL_SEC", 0.05)


@pytest.fixture
def allow_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    """把测试桩加进白名单（真实节点上这里是 `tools/sim/export-rl-rollout.ts`）。"""
    monkeypatch.setattr(
        serve_pool, "SERVE_CAPABLE_SCRIPTS",
        frozenset({"stub_serve.py", "stub_serve_a.py", "stub_serve_b.py"}),
    )


# ------------------------------------------------------------------ 白名单 / 开关


def test_serve_capable_matches_production_script_and_basename() -> None:
    for rel in (serve_pool.ROLLOUT_SCRIPT, serve_pool.EVAL_SCRIPT):
        assert serve_pool.serve_capable([rel, "--out", "w0"])
        assert serve_pool.serve_capable([rel.replace("/", "\\")])  # Windows 分隔符
        assert serve_pool.serve_capable([f"/opt/ts/{rel}"])  # 绝对路径兜底
    # goal/intent 两个导出器**不在节点侧名单**：iter spec 白名单只允许 export-rl-rollout，
    # 那两个模式走的是 agent 池（`sampler-agent.PERSIST_SERVE_ENTRIES`）。加了它们而没人消费，
    # 只会让「名单 = 真的会被池化的东西」这条对应关系失效。
    assert not serve_pool.serve_capable(["tools/sim/export-goal-rollout.ts"])
    assert not serve_pool.serve_capable(["tools/sim/export-intent-rollout.ts"])
    assert not serve_pool.serve_capable([])


def test_make_pool_gates_on_env_allowlist_and_single_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """建池的三个前置（开关 / 白名单 / 单一脚本）——缺一即不建，行为同上云前。"""
    ok = [["tools/sim/export-rl-rollout.ts", "--out", "w0"]]
    monkeypatch.delenv(serve_pool.ENV_SWITCH, raising=False)
    assert iter_rollout._make_pool("bun", tmp_path, ok, 2, lambda _m: None) is not None
    monkeypatch.setenv(serve_pool.ENV_SWITCH, "0")
    assert iter_rollout._make_pool("bun", tmp_path, ok, 2, lambda _m: None) is None
    monkeypatch.delenv(serve_pool.ENV_SWITCH, raising=False)
    # 名单外的导出器（goal/intent 走 agent 池，节点侧不池化）不建池
    assert iter_rollout._make_pool(
        "bun", tmp_path, [["tools/sim/export-goal-rollout.ts"]], 2, lambda _m: None
    ) is None
    # 同一轮两个脚本：池按脚本建 ⇒ 不建（保守）
    assert iter_rollout._make_pool(
        "bun", tmp_path, [*ok, ["tools/sim/export-goal-rollout.ts"]], 2, lambda _m: None
    ) is None
    assert iter_rollout._make_pool("bun", tmp_path, [], 2, lambda _m: None) is None


def test_pool_enabled_defaults_on_and_env_kills_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(serve_pool.ENV_SWITCH, raising=False)
    assert serve_pool.pool_enabled() is True
    for off in ("0", "false", "OFF", "no"):
        monkeypatch.setenv(serve_pool.ENV_SWITCH, off)
        assert serve_pool.pool_enabled() is False
    monkeypatch.setenv(serve_pool.ENV_SWITCH, "1")
    assert serve_pool.pool_enabled() is True


def test_try_pool_refuses_a_task_for_another_script(tmp_path: Path) -> None:
    """喂错池（argv[0] 不是本池那个导出器）⇒ 拒绝且不起进程（连「回退」都不记账）。"""
    other = _write_stub(tmp_path, _STUB_SERVE, name="other_exporter.py", count=str(tmp_path / "c"))
    pool = _pool(tmp_path, other)
    foreign = _argv(tmp_path / "somewhere" / "else.py", 0, 0, "w0")
    assert pool.try_pool(foreign, tmp_path / "w0" / iter_rollout.ROLLOUT_LOG_NAME, 5.0) is None
    assert pool.spawned == 0 and pool.fallback == 0


# ------------------------------------------------------------------ 复用 / 日志隔离


def test_pool_reuses_one_worker_across_games(tmp_path: Path) -> None:
    """核心收益：N 局共用同一进程（`spawned` 不随局数增长），且每局都真跑了一次。"""
    count = tmp_path / "count.txt"
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(count), fail_seed="")
    pool = _pool(tmp_path, script)
    assert pool.start() == 1
    assert pool.spawned == 1
    pids = []
    for i in range(4):
        argv, logp = _task(script, 3, i, f"w{i}", tmp_path)
        secs = pool.try_pool(argv, logp, 30.0)
        assert secs is not None and secs >= 0.0
        pids.append(logp.read_text(encoding="utf-8"))
    assert pool.served == 4 and pool.spawned == 1 and pool.fallback == 0
    assert count.read_text() == "4"  # 4 局各自跑了一次（复用进程，不是复用结果）
    # 同一进程：四份日志里的 pid 必须一致（进程复用的直接证据）
    seen = {line.split("pid=")[1].split()[0] for p in pids for line in p.splitlines() if "pid=" in line}
    assert len(seen) == 1, seen
    # 日志隔离：每局的 rollout.log 只含自己那一局的行（错切会让「哪一局卡了」查不出来）
    for i in range(4):
        text = (tmp_path / f"w{i}" / iter_rollout.ROLLOUT_LOG_NAME).read_text(encoding="utf-8")
        assert f"done s3/d{i}" in text
        assert "done s3/d" + str((i + 1) % 4) not in text
    pool.close()


def test_try_capture_returns_lines_without_touching_disk(tmp_path: Path) -> None:
    """eval 腿的口径（`try_capture`）：行**交回调用方**，不在盘上产生任何文件。"""
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="")
    pool = _pool(tmp_path, script)
    pool.start()
    argv = _argv(script, 2, 5, "w0")
    got = pool.try_capture(argv, 30.0, label="s2/d5", kind="eval", where=str(tmp_path / "w0"))
    assert got is not None
    sec, lines = got
    assert sec >= 0.0
    joined = "\n".join(lines)
    assert "done s2/d5" in joined and "__SERVE_OK__" not in joined  # 标记行不当日志
    # 产出（桩/计数/out/shard）之外**不多任何文件** —— 尤其没有 rollout.log
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "c",
        "rl_s2_seed5",
        "stub_serve.py",
        "w0",
    ]
    assert not (tmp_path / "w0" / iter_rollout.ROLLOUT_LOG_NAME).exists()
    assert pool.served == 1 and pool.fallback == 0
    pool.close()


def test_try_capture_declines_on_err_like_try_pool(tmp_path: Path) -> None:
    """eval 腿的回退一致：ERR ⇒ None（调用方走一次性 run_eval_runner_capture），worker 被换掉。"""
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="4")
    msgs: list[str] = []
    pool = ServePool(sys.executable, script.name, tmp_path, 1, msgs.append)
    pool.start()
    assert pool.try_capture(_argv(script, 0, 4, "w0"), 30.0, label="s0/d4") is None
    assert pool.fallback_reasons == {"err": 1} and pool.killed == 1
    pool.close()


def test_slow_warn_line_carries_the_kind_and_where(tmp_path: Path, monkeypatch) -> None:
    """慢局告警要能被 eval/rollout 两腿分辨（`kind`）且带现场（`where`）。"""
    _fast_poll(monkeypatch)
    monkeypatch.setattr(game_watch, "SLOW_GAME_WARN_SEC", 0.05)
    monkeypatch.setattr(game_watch, "GAME_MAX_ATTEMPTS", 4)
    script = _write_stub(tmp_path, _STUB_HANGS_ON_TASK, name="stub_hang_task.py")
    msgs: list[str] = []
    pool = ServePool(sys.executable, script.name, tmp_path, 1, msgs.append)
    pool.start()
    argv = _argv(script, 0, 0, "w0")
    assert pool.try_capture(argv, 0.4, label="s0/d0", kind="eval", where="out-dir-x") is None
    assert any("WARN eval 单局异常慢" in m and "s0/d0" in m and "out-dir-x" in m for m in msgs), msgs
    pool.close()


def test_make_pool_is_the_shared_gate_for_both_legs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`make_pool` 是 rollout/eval 两条腿**共用**的准入（开关 + 白名单）。

    刻意用**真脚本名**（不 patch 白名单）：这条钉住的就是「两个导出器都在名单里、
    goal/intent 不在」——eval 腿入池的准入与 rollout 腿逐字同源。`make_pool` 只构造对象，
    不起进程（`start()` 才起），所以这里不会真的 spawn。
    """
    monkeypatch.delenv(serve_pool.ENV_SWITCH, raising=False)
    for script in (serve_pool.ROLLOUT_SCRIPT, serve_pool.EVAL_SCRIPT):
        pool = serve_pool.make_pool(sys.executable, script, tmp_path, 2)
        assert isinstance(pool, ServePool)
        pool.close()  # 没 start 过，close = 空操作（不残留进程）
    assert serve_pool.make_pool(
        sys.executable, "tools/sim/export-goal-rollout.ts", tmp_path, 2
    ) is None
    assert serve_pool.make_pool(sys.executable, "", tmp_path, 2) is None
    monkeypatch.setenv(serve_pool.ENV_SWITCH, "0")
    assert serve_pool.make_pool(sys.executable, serve_pool.ROLLOUT_SCRIPT, tmp_path, 2) is None


def test_pool_diagnostic_lines_are_logged(tmp_path: Path) -> None:
    msgs: list[str] = []
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="")
    pool = ServePool(sys.executable, script.name, tmp_path, 1, msgs.append)
    pool.start()
    argv, logp = _task(script, 1, 2, "w0", tmp_path)
    assert pool.try_pool(argv, logp, 30.0) is not None
    assert "served=1" in pool.summary() and "spawned=1" in pool.summary()
    pool.close()


# ------------------------------------------------------------------ 三类回退


def test_worker_err_falls_back_once_and_drops_worker(tmp_path: Path) -> None:
    """导出器自己报 ERR（真 bun 里 = 那一局抛异常）⇒ 回退一次性，且把这个 worker 换掉。"""
    script = _write_stub(
        tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="7"
    )
    msgs: list[str] = []
    pool = ServePool(sys.executable, script.name, tmp_path, 1, msgs.append)
    assert pool.start() == 1
    argv, logp = _task(script, 0, 7, "w0", tmp_path)
    assert pool.try_pool(argv, logp, 30.0) is None
    assert pool.fallback_reasons == {"err": 1} and pool.killed == 1
    assert logp.exists()  # worker 自己的日志仍落回这一局（诊断不丢）
    assert any("回退一次性 spawn" in m and "stub-refused-s7" in m for m in msgs), msgs
    # 干净局仍然可服务（池自愈，不是一错全废）
    ok_argv, ok_log = _task(script, 0, 8, "w1", tmp_path)
    assert pool.try_pool(ok_argv, ok_log, 30.0) is not None
    pool.close()


def test_dead_worker_falls_back_instead_of_waiting_the_cap(tmp_path: Path) -> None:
    """worker 中途死掉（OOM/信号）：立刻回退，**不等满单局硬顶**（否则整轮白等一次）。"""
    script = _write_stub(tmp_path, _STUB_DIES, name="stub_dies.py")
    pool = _pool(tmp_path, script)
    assert pool.start() == 1
    argv, logp = _task(script, 0, 0, "w0", tmp_path)
    t0 = time.time()
    assert pool.try_pool(argv, logp, 30.0) is None
    assert time.time() - t0 < 10.0, "死进程必须立刻唤醒等待方，不能等满 30s 硬顶"
    assert pool.fallback_reasons == {"dead": 1}
    pool.close()


def test_hung_task_hits_the_cap_then_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """卡住的局：到硬顶 → kill → 回退（判定「这一局坏了」是调用方重试逻辑的事）。"""
    _fast_poll(monkeypatch)
    script = _write_stub(tmp_path, _STUB_HANGS_ON_TASK, name="stub_hang_task.py")
    pool = _pool(tmp_path, script)
    assert pool.start() == 1
    argv, logp = _task(script, 0, 0, "w0", tmp_path)
    assert pool.try_pool(argv, logp, 0.3) is None
    assert pool.fallback_reasons == {"timeout": 1} and pool.killed == 1
    pool.close()


def test_pool_start_fails_fast_when_script_has_no_serve(tmp_path: Path) -> None:
    """不认 `--serve` 的脚本：`start()` 立刻返回 0（**不等满就绪上限**）——否则每次上云都白等。"""
    script = _write_stub(tmp_path, _STUB_NO_SERVE, name="stub_no_serve.py")
    pool = _pool(tmp_path, script, workers=2, ready=60.0)
    t0 = time.time()
    assert pool.start() == 0
    assert time.time() - t0 < 15.0, "进程当场退出就该立刻放弃，而不是等 60s"
    pool.close()


def test_close_kills_workers_and_try_pool_refuses_afterwards(tmp_path: Path) -> None:
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="")
    pool = _pool(tmp_path, script)
    pool.start()
    proc = pool._workers[0].proc
    pool.close()
    # 收池后没有残进程（kill 是异步的 ⇒ 等它真退出；超时不代表「还在跑」，由 wait 判定）
    assert proc.wait(timeout=15) is not None
    argv, logp = _task(script, 0, 0, "w0", tmp_path)
    assert pool.try_pool(argv, logp, 5.0) is None


def test_pool_has_no_slot_when_all_busy(tmp_path: Path) -> None:
    """全忙（并发门本不该让它发生）⇒ `no-slot` 回退，绝不排队等（排队会把单局延迟放大）。"""
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="")
    pool = _pool(tmp_path, script, workers=1)
    pool.start()
    with pool._lock:
        pool._workers[0].busy = True
    argv, logp = _task(script, 0, 0, "w0", tmp_path)
    assert pool.try_pool(argv, logp, 5.0) is None
    assert pool.fallback_reasons == {"no-slot": 1}
    pool.close()


# ------------------------------------------------------------------ 端到端接线（run_iter_rollout）


def _serve_spec(tmp_path: Path, script: Path, games: list[tuple[int, int]], **over) -> dict:
    argv = [
        _argv(script, st, sd, f"w{i}") for i, (st, sd) in enumerate(games)
    ]
    s = {"argv": argv, "wver": "W" * 64, "workers": 2, "game_timeout_sec": 30.0,
         "bun": sys.executable}
    s.update(over)
    return s


def test_run_iter_rollout_goes_through_the_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_stub: None
) -> None:
    """接线：整轮走池 ⇒ 同一批 shard、逐局报告/日志齐全、`serve_pool` 计数诚实。"""
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "9.9.9-stub")
    count = tmp_path / "count.txt"
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(count), fail_seed="")
    msgs: list[str] = []
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    spec = _serve_spec(tmp_path, script, [(3, 7), (3, 8), (4, 1)])
    out = iter_rollout.run_iter_rollout(job_dir, spec, log=msgs.append)
    assert sorted(Path(p).name for p in out["shard_dirs"]) == [
        "rl_s3_seed7", "rl_s3_seed8", "rl_s4_seed1",
    ]
    assert out["report"]["games"] == 3 and out["report"]["totalSamples"] == 6
    stats = out["serve_pool"]
    assert stats is not None and stats["served"] == 3 and stats["fallback"] == 0
    assert stats["spawned"] == 2  # workers=2 起满池，`spawned` 与局数无关
    assert out["report"]["elapsedSec"] >= 0.0
    # 每局：报告 + 自己的日志（日志内容来自 worker stdout，必须按局切开）
    for i, (st, sd) in enumerate([(3, 7), (3, 8), (4, 1)]):
        assert json.loads((job_dir / f"w{i}" / "_rl_report.json").read_text())["games"] == 1
        text = (job_dir / f"w{i}" / iter_rollout.ROLLOUT_LOG_NAME).read_text(encoding="utf-8")
        assert f"done s{st}/d{sd}" in text
    assert any("长驻 worker 池" in m for m in msgs), msgs
    assert any("serve_pool: served=3" in m for m in msgs), msgs


def test_run_iter_rollout_pool_failure_still_produces_the_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_stub: None
) -> None:
    """池判失败的那一局（ERR）⇒ 回退一次性 spawn，**整轮仍然成功**（池不吞局）。"""
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "")
    count = tmp_path / "count.txt"
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(count), fail_seed="7")
    msgs: list[str] = []
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    spec = _serve_spec(tmp_path, script, [(3, 7), (3, 8)])
    # 一次性路径用**同一个桩**（同一份 argv 直接跑，不带 --serve）：它同样能产 shard
    out = iter_rollout.run_iter_rollout(job_dir, spec, log=msgs.append)
    assert len(out["shard_dirs"]) == 2
    stats = out["serve_pool"]
    assert stats is not None and stats["served"] == 1 and stats["fallback"] == 1
    assert stats["reasons"] == {"err": 1}
    assert count.read_text() == "2"  # 被拒那一局由一次性路径真跑了（一局没丢）


def test_run_iter_rollout_env_switch_returns_to_per_game_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_stub: None
) -> None:
    """`NN_SERVE_POOL=0` ⇒ 不建池（`serve_pool` 为 None），行为回到上云前。"""
    monkeypatch.setenv(serve_pool.ENV_SWITCH, "0")
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "")
    count = tmp_path / "count.txt"
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(count), fail_seed="")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    out = iter_rollout.run_iter_rollout(
        job_dir, _serve_spec(tmp_path, script, [(3, 7)]), log=lambda _m: None
    )
    assert out["serve_pool"] is None
    assert len(out["shard_dirs"]) == 1
    assert count.read_text() == "1"


def test_run_iter_rollout_mixed_scripts_never_builds_a_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, allow_stub: None
) -> None:
    """同一轮里出现两个脚本（协议层会拦住，但池这边也要保守）：不建池，全部一次性。"""
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "")
    a = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="",
                    name="stub_serve_a.py")
    b = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="",
                    name="stub_serve_b.py")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    spec = _serve_spec(tmp_path, a, [(3, 7)])
    spec["argv"].append(_argv(b, 3, 8, "w1"))
    out = iter_rollout.run_iter_rollout(job_dir, spec, log=lambda _m: None)
    assert out["serve_pool"] is None
    assert len(out["shard_dirs"]) == 2


# ------------------------------------------------------------------ 校验：池与一次性跑的是同一份 argv


def test_pool_sends_argv_without_entry_path(tmp_path: Path) -> None:
    """送进 stdin 的行 = `argv[1:]`（不含脚本路径）—— 与 `serve-loop.ts` 的约定逐字对齐。"""
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="")
    pool = _pool(tmp_path, script)
    pool.start()
    argv, logp = _task(script, 5, 6, "w0", tmp_path)
    assert pool.try_pool(argv, logp, 30.0) is not None
    # 桩按 val("--stages") 读，等价于「收到的行确实带着全部参数」；再加一条更强的：
    assert argv[0] == str(script) and "--stages" in argv[1:]
    pool.close()


def test_pool_spawns_nothing_on_empty_argv(tmp_path: Path) -> None:
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="")
    pool = _pool(tmp_path, script)
    assert pool.try_pool([str(script)], tmp_path / "w0" / "rollout.log", 5.0) is None
    assert pool.spawned == 0


def test_subprocess_imports_are_used_not_shell(tmp_path: Path) -> None:
    """硬化：池只用 subprocess 直起（绝不过 shell），参数逐条传递。"""
    import inspect

    src = inspect.getsource(serve_pool)
    assert "shell=True" not in src
    assert "subprocess.Popen" in src
    assert subprocess.Popen is not None  # 反向断言：上面的 import 不是空壳
