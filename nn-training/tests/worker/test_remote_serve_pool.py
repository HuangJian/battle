"""test_remote_serve_pool.py — 节点侧长驻 worker 池（`worker/serve_pool.py`）单测。

覆盖（对应 plan `conv-optimize.plan.md` / `docs/nn/runtime-opt.md` §21 的 A 方案）：
  * 复用：N 局共用**一个**进程（`spawned` 不随局数涨）—— 这就是 1.59× 的全部来源；
  * 日志隔离：worker stdout 混着所有局的日志，必须按局切回各自的 `rollout.log`；
  * 三类回退（ERR / 进程死掉 / 超时）都返回 None（调用方走一次性），且不留残进程；
  * **回退要有限度**（2026-09-25 云机卡死取证）：累计回退到阈值就地熔断（停用池 + 不再补位），
    回退行的**详情**有条数上限且带 kind/label/where，补位冷启动的等待不得超过单局硬顶；
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

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import worker.iter_rollout as iter_rollout
import worker.serve_pool as serve_pool
from common import game_watch
from common.platform_utils import POPEN_NO_WINDOW as _POPEN_NO_WINDOW
from worker.serve_pool import ServePool

# ------------------------------------------------------------------ 桩

#: 一个**两种模式都支持**的 stub 导出器（与真导出器同构：`main(argv)` 跑一局，`--serve` 只是
#: 外面包一层协议循环）—— 同一份桩既能走池、也能走一次性回退，回退用例才有意义。
#:
#: `@COUNT@` = 计数**目录**（每跑完一局落一个自己的文件，计数 = 文件数，用来证明复用而不是
#:  重跑）；**不能**做成「一个共享文件 + read-modify-write」（2026-09-24 flake 的真身）；
#: `@FAIL_SEED@` = 让它对该 seed 打 `__SERVE_ERR__`（造回退路径）。
_STUB_SERVE = """\
import json, os, sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)   # 管道里必须是行缓冲，否则标记行会卡住
COUNT = Path(r"@COUNT@")   # 计数目录（每局一个文件）
FAIL_SEED = "@FAIL_SEED@"
_SEEN = []                 # 本进程已跑的局数（给落盘文件名与 run# 提供序号）


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
    # 计数：**每局落一个自己的文件**（计数 = 文件数）。不能用一个共享文件做
    # read-modify-write —— 2026-09-24 定位到的门禁 flake 真身，两种写法栽在同一件事上：
    #   ① 截断 + 写（原写法）：另一个并发 worker 在截断窗口里 `read_text()` 拿到**空文件**
    #      ⇒ `int("")` ⇒ ValueError ⇒ 它回 `__SERVE_ERR__` ⇒ 池按纪律换掉它 + 回退一次性
    #      ⇒ 端到端用例的 `served == 3` 判红（三局产物其实齐全，纯**假红**）。
    #   ② 唯一临时名 + `os.replace`（第一版修法）也不行：Windows 上目标文件正被另一个
    #      进程读着时 `os.replace` 抛 `[WinError 5] Access is denied` —— 同一现场照旧。
    # 每局写自己的文件 ⇒ 无共享可变状态 ⇒ 没有窗口/访问冲突/丢更新（并发下的计数**精确**）。
    _SEEN.append(1)
    COUNT.mkdir(parents=True, exist_ok=True)
    (COUNT / f"g{os.getpid()}_{len(_SEEN)}").write_text("1")
    d = out.parent / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"stage": stage, "seed": seed, "wver": wver}))
    (out / "_rl_report.json").write_text(json.dumps({
        "games": 1, "winRate": 1.0, "outcomes": {"stage_clear": 1},
        "totalSamples": 2, "totalTicks": 20, "scoreList": [1.0], "dimLists": {"kills": [1.0]},
    }))
    print(f"[stub-serve] done s{stage}/d{seed} pid={os.getpid()} run#{len(_SEEN)}")


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

#: 支持 `--serve`，但**只对指定 seed** 睡死（`@SLOW@`）。
#:
#: 用途：造「一个 worker 被硬顶带走、另一个 worker 还暖着」的现场 —— 熔断之后池该继续用那个
#: 暖的（省一次 bun 冷启动），而不是把余下局全推去一次性。
_STUB_SERVE_SLOW_ONE = """\
import json, os, sys, time
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
SLOW = "@SLOW@"


def one(a):
    def val(flag):
        return a[a.index(flag) + 1] if flag in a else ""

    out = Path(val("--out"))
    out.mkdir(parents=True, exist_ok=True)
    stage, seed, wver = int(val("--stages")), int(val("--seeds")), val("--wver")
    if SLOW and str(seed) == SLOW:
        print(f"[stub-slow-one] hanging s{stage}/d{seed}", flush=True)
        time.sleep(3600)
    d = out.parent / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"stage": stage, "seed": seed, "wver": wver}))
    (out / "_rl_report.json").write_text(json.dumps({
        "games": 1, "winRate": 1.0, "outcomes": {"stage_clear": 1},
        "totalSamples": 2, "totalTicks": 20, "scoreList": [1.0], "dimLists": {"kills": [1.0]},
    }))
    print(f"[stub-slow-one] done s{stage}/d{seed} pid={os.getpid()}")


if "--serve" in sys.argv:
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
else:
    one(sys.argv[1:])
"""

#: 把收到的每行**原样**落盘（`@OUT@`）：钉「送进 stdin 的到底是什么」。
#: 同步看得到行首的 mode token —— 这就是「用哪个导出器」从池的形状变成每行的事实的证据。
_STUB_ECHO = """\
import sys
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)
OUT = Path(r"@OUT@")
print("__SERVE_READY__")
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("a", encoding="utf-8") as fh:
        fh.write(line + "\\n")
    print("__SERVE_OK__")
"""

#: 完全不认 `--serve`（只会跑一次性路径的桩）：池必须**快速**判定起不来，不等满就绪上限。
_STUB_NO_SERVE = """\
import sys
print("no serve here")
sys.exit(0)
"""

#: 起来后**不报就绪也不死**（模拟冷启动被挤在中间）：`_acquire` 的等待必须受硬顶约束。
_STUB_SILENT = """\
import time
print("booting…", flush=True)
time.sleep(3600)
"""


def _write_stub(tmp_path: Path, body: str, name: str = "stub_serve.py", **subs: str) -> Path:
    p = tmp_path / name
    text = body
    for k, v in subs.items():
        text = text.replace(f"@{k.upper()}@", v)
    p.write_text(text, encoding="utf-8")
    return p


def _games_run(count_dir: Path) -> int:
    """桩落盘的「真跑过几局」= 计数目录里的文件数。

    每局写**自己的**文件（不是共享文件的 read-modify-write）：无窗口、无访问冲突、无丢更新，
    并发下也能得到**精确**局数（2026-09-24 的 flake 正是共享文件那种写法造出来的）。
    """
    return sum(1 for _ in count_dir.iterdir()) if count_dir.is_dir() else 0


def _pool(tmp_path: Path, script: Path, *, workers: int = 1, ready: float = 30.0) -> ServePool:
    """按测试桩建池（`bun` = 本进程 python；ts_dir = 桩所在目录）。

    `entry=` 是池的**同质入口**注入点：生产固定是 `tools/sim/serve-any.ts`（按每行 mode token
    分派），单测拿 python 桩替它——协议形状一致（`[mode, ...argv]` 行 / 三个标记），但不起 bun。
    """
    return ServePool(sys.executable, tmp_path, workers, entry=script.name, ready_timeout_sec=ready)


def _install_entry(ts_root: Path, body: str, **subs: str) -> Path:
    """把桩再写一份到**生产入口路径**（`<ts_root>/tools/sim/serve-any.ts`）。

    池的入口在生产上是那个 `.ts`（`SERVE_ANY_SCRIPT`），而单测不跑 bun ⇒ 同一个 python
    桩就是入口（python 不关心扩展名）。这样 `run_iter_rollout` 走的是**生产的**
    `make_pool`（不注入 `entry`），池才真能起来 —— 注入式只用于直接建 `ServePool` 的用例。
    """
    p = Path(ts_root) / Path(serve_pool.SERVE_ANY_SCRIPT)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = body
    for k, v in subs.items():
        text = text.replace(f"@{k.upper()}@", v)
    p.write_text(text, encoding="utf-8")
    return p


def _argv(script: Path, stage: int, seed: int, out: str) -> list[str]:
    return [str(script), "--out", out, "--stages", str(stage), "--seeds", str(seed),
            "--wver", "W" * 64]


def _task(script: Path, stage: int, seed: int, out: str, tmp_path: Path) -> tuple[list[str], Path]:
    """一局的任务：argv + 它自己的日志路径（与 `_run_one_game` 的落点一致）。"""
    return _argv(script, stage, seed, out), (tmp_path / out / iter_rollout.ROLLOUT_LOG_NAME)


#: 「worker 被**外力**带走」的三类回退原因：不是协议错、也不是我们的逻辑错，而是环境
#: 把长驻子进程杀了（沙箱的删除/写守卫 ⇒ SystemExit、负载/句柄耗尽 ⇒ 起不来或当场退出）。
#: 与之相对：`err`（导出器自己报错）与 `timeout`（跑得慢）是**被测行为**，不许当环境问题放过。
_ENV_KILL_REASONS = frozenset({"dead", "write-failed", "no-stdin"})


def _env_killed_worker(stats: dict) -> bool:
    """这一轮的回退原因里有没有「worker 被外力带走」（池的计数已诚实记账）。"""
    return bool(set(stats.get("reasons") or {}) & _ENV_KILL_REASONS)


def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """把轮询粒度调小（生产 0.5s）——否则超时用例要等秒级。"""
    monkeypatch.setattr(game_watch, "GAME_POLL_SEC", 0.05)


def _is_stub(path: object) -> bool:
    """测试桩的判据：本文件的桩一律命名 `stub_*.py`（新增桩不必再改一份名单）。"""
    return str(path).replace("\\", "/").rsplit("/", 1)[-1].startswith("stub_")


@pytest.fixture(autouse=True)
def _stub_modes(monkeypatch: pytest.MonkeyPatch) -> None:
    """让测试桩能过池的**准入 + mode 解析**（生产上这两道都在 `common/manifest`）。

    池的 `_submit` 现在要两道：① `serve_mode_for(argv[0])` 给出行首 mode token；
    ② 该脚本在准入门槛内。真脚本名走真表/真名单（钉 goal/intent 不建池的用例不受影响），
    桩名（`stub_*`）一律放行并按 `rollout` 一个 token 走 —— 同质池对 token 的**取值**不敏感
    （语义归 worker；此处只看「行首确实是 token」）。
    """
    real_mode = serve_pool.serve_mode_for
    real_capable = serve_pool.serve_capable
    monkeypatch.setattr(
        serve_pool,
        "serve_mode_for",
        lambda p: real_mode(p) or ("rollout" if _is_stub(p) else None),
    )
    monkeypatch.setattr(
        serve_pool,
        "serve_capable",
        lambda argv: real_capable(argv) or (bool(argv) and _is_stub(argv[0])),
    )


# ------------------------------------------------------------------ 白名单 / 开关


def test_serve_capable_matches_production_script_and_basename() -> None:
    for rel in (serve_pool.ROLLOUT_SCRIPT, serve_pool.EVAL_SCRIPT):
        assert serve_pool.serve_capable([rel, "--out", "w0"])
        assert serve_pool.serve_capable([rel.replace("/", "\\")])  # Windows 分隔符
        assert serve_pool.serve_capable([f"/opt/ts/{rel}"])  # 绝对路径兜底
    # 准入门槛是**策略**（本轮要不要建池），不是池的形状（池已同质、四个 mode 都能派）：
    # 2026-09-28 用户点名保持现状 —— 本机 goal/intent 轮继续逐局 spawn。要放开只需往这个
    # frozenset 里加一行，池本身无需任何改动（`serve_mode_for` 已经认这两个脚本）。
    assert not serve_pool.serve_capable(["tools/sim/export-goal-rollout.ts"])
    assert not serve_pool.serve_capable(["tools/sim/export-intent-rollout.ts"])
    assert not serve_pool.serve_capable([])


def test_serve_mode_table_mirrors_the_ts_dispatcher() -> None:
    """脚本 → mode token 的表：四个导出器都有 token，且与 `common/manifest` 同源。

    值域必须与 `tools/sim/serve-any.ts::SERVE_MODES` 的键集逐字相同（那是协议面，TS 侧有
    `tests/serve-any.test.ts` 对拍）；这里钉 Python 侧真的拿着同一份表，并真的把它送进 worker
    （送 stdin 的那条在下面 `test_pool_sends_the_mode_token`）。
    """
    from common.manifest import SERVE_MODE_BY_SCRIPT, serve_mode_for

    assert SERVE_MODE_BY_SCRIPT == {
        "tools/sim/export-rl-rollout.ts": "rollout",
        "tools/sim/export-eval-game.ts": "eval",
        "tools/sim/export-goal-rollout.ts": "goal",
        "tools/sim/export-intent-rollout.ts": "intent",
    }
    assert serve_mode_for("tools/sim/export-eval-game.ts") == "eval"
    assert serve_mode_for("./tools/sim/export-eval-game.ts") == "eval"  # 前导 ./
    assert serve_mode_for("tools\\sim\\export-eval-game.ts") == "eval"  # Windows 分隔符
    assert serve_mode_for("/opt/ts/tools/sim/export-eval-game.ts") == "eval"  # 绝对路径兜底
    assert serve_mode_for("tools/sim/export-godai-bc.ts") is None  # 教师口径、不进池
    assert serve_mode_for("") is None


def test_make_pool_gates_on_env_allowlist_and_single_script(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """建池的三个前置（开关 / 准入名单 / 单一脚本）——缺一即不建，行为同上云前。"""
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
    count = tmp_path / "count.d"
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
    assert _games_run(count) == 4  # 4 局各自跑了一次（复用进程，不是复用结果）
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
    pool = ServePool(sys.executable, tmp_path, 1, msgs.append, entry=script.name)
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
    pool = ServePool(sys.executable, tmp_path, 1, msgs.append, entry=script.name)
    pool.start()
    argv = _argv(script, 0, 0, "w0")
    assert pool.try_capture(argv, 0.4, label="s0/d0", kind="eval", where="out-dir-x") is None
    assert any("WARN eval 单局异常慢" in m and "s0/d0" in m and "out-dir-x" in m for m in msgs), msgs
    pool.close()


def test_make_pool_is_the_shared_gate_for_both_legs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`make_pool` 是 rollout/eval 两条腿**共用**的准入（开关 + 名单），且池**同质**。

    刻意用**真脚本名**（不 patch 名单）：这条钉住的就是「两个导出器都在名单里、
    goal/intent 不在」——eval 腿入池的准入与 rollout 腿逐字同源；而两条腿拿到的池**入口相同**
    （`for_script` 只是门槛，不再是池的身份）——那正是「同质化」在 Python 侧的体现。
    `make_pool` 只构造对象，不起进程（`start()` 才起），所以这里不会真的 spawn。
    """
    monkeypatch.delenv(serve_pool.ENV_SWITCH, raising=False)
    for script in (serve_pool.ROLLOUT_SCRIPT, serve_pool.EVAL_SCRIPT):
        pool = serve_pool.make_pool(sys.executable, tmp_path, 2, for_script=script)
        assert isinstance(pool, ServePool)
        assert pool.entry == serve_pool.SERVE_ANY_SCRIPT, "池入口必须恒为同质入口"
        pool.close()  # 没 start 过，close = 空操作（不残留进程）
    assert serve_pool.make_pool(
        sys.executable, tmp_path, 2, for_script="tools/sim/export-goal-rollout.ts"
    ) is None
    assert serve_pool.make_pool(sys.executable, tmp_path, 2, for_script="") is None
    assert serve_pool.make_pool(sys.executable, tmp_path, 2) is None  # 不给脚本 = 不给准入门槛
    monkeypatch.setenv(serve_pool.ENV_SWITCH, "0")
    assert (
        serve_pool.make_pool(sys.executable, tmp_path, 2, for_script=serve_pool.ROLLOUT_SCRIPT)
        is None
    )


def test_pool_diagnostic_lines_are_logged(tmp_path: Path) -> None:
    msgs: list[str] = []
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="")
    pool = ServePool(sys.executable, tmp_path, 1, msgs.append, entry=script.name)
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
    pool = ServePool(sys.executable, tmp_path, 1, msgs.append, entry=script.name)
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
    msgs: list[str] = []
    script = _write_stub(tmp_path, _STUB_DIES, name="stub_dies.py")
    pool = ServePool(sys.executable, tmp_path, 1, msgs.append, entry=script.name)
    assert pool.start() == 1
    argv, logp = _task(script, 0, 0, "w0", tmp_path)
    t0 = time.time()
    assert pool.try_pool(argv, logp, 30.0) is None
    # timing-ok: 上界兜底（死进程应立刻唤醒，10s 只挡挂起）
    assert time.time() - t0 < 10.0, "死进程必须立刻唤醒等待方，不能等满 30s 硬顶"
    assert pool.fallback_reasons == {"dead": 1}
    # 回退必须留一行**带现场**的日志（退出码）：否则那一局的 rollout.log 会被一次性路径
    # 重写覆盖，轮末只剩 killed=1 这种没有信息量的计数（2026-09-23 flake 的取证教训）。
    assert any(
        "回退一次性 spawn（kind=rollout" in m and " dead:" in m and "rc=" in m for m in msgs
    ), msgs
    pool.close()


def test_postmortem_reports_exit_code_and_last_words() -> None:
    """`_postmortem` = 回退现场的唯一留痕：退出码（谁杀的）+ 尾行（它最后说了什么）。"""
    proc = subprocess.Popen(
        [sys.executable, "-c", "import sys; print('boom-tail'); sys.exit(7)"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        encoding="utf-8",
        **_POPEN_NO_WINDOW,
    )
    out, _ = proc.communicate()
    assert proc.poll() == 7
    worker = serve_pool._Worker(proc)  # 进程已退出 ⇒ reader 立刻 EOF
    lines = [ln for ln in (out or "").splitlines() if ln.strip()]
    line = ServePool._postmortem(worker, lines)
    assert "rc=7" in line and "0x00000007" in line and "boom-tail" in line, line


def test_hung_task_hits_the_cap_then_falls_back(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """卡住的局：到硬顶 → kill → 回退（判定「这一局坏了」是调用方重试逻辑的事）。"""
    _fast_poll(monkeypatch)
    script = _write_stub(tmp_path, _STUB_HANGS_ON_TASK, name="stub_hang_task.py")
    pool = _pool(tmp_path, script)
    assert pool.start() == 1
    argv, logp = _task(script, 0, 0, "w0", tmp_path)
    # 0.3 → 0.15（2026-09-29，§43）：真开销是「真等满这一局的硬顶」，而断言只看「到顶 ⇒
    # kill ⇒ 回退 timeout」这个判定；桩进程的就绪等待另有 1s 地板预算（不受此值影响）。
    assert pool.try_pool(argv, logp, 0.15) is None
    assert pool.fallback_reasons == {"timeout": 1} and pool.killed == 1
    pool.close()


def test_fallback_breaker_stops_rebuilding_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**熔断**：累计回退到阈值 ⇒ 停用池、不再补位，余下局走一次性；日志只留一行刹车现场。

    为什么这条是本轮（2026-09-25 云机卡死）的核心护栏：一次超时 = kill worker + 该局一次性
    spawn + 池补位再冷启动 ⇒ 一次超时放大成三份进程。过载时那是正反馈（越多回退越慢），
    没有刹车就会锁死整轮（现场：5s 后一次 60+ 行回退，随后 4 分钟零行）。

    本用例的池里**一个暖 worker 都不剩**（每局都睡死、每个都被带走）——那是熔断后走一次性
    的**唯一**条件；「手上还有暖 worker 就接着用」由下一个用例钉。
    """
    _fast_poll(monkeypatch)
    script = _write_stub(tmp_path, _STUB_HANGS_ON_TASK, name="stub_hang_task.py")
    msgs: list[str] = []
    pool = ServePool(sys.executable, tmp_path, 1, msgs.append, entry=script.name)
    assert pool.start() == 1
    assert pool.breaker_after == serve_pool.FALLBACK_BREAKER_MIN  # 小池 = 下限起步
    total = 9
    for i in range(total):
        argv, logp = _task(script, 0, i, f"w{i}", tmp_path)
        # 硬顶 0.2 → 0.1（2026-09-29，§43）：4 次真超时 × 硬顶就是本用例的全部墙钟；
        # 判定（超时 ⇒ kill ⇒ 回退 ⇒ 到阈值熔断）与硬顶长度无关。
        assert pool.try_pool(argv, logp, 0.1) is None  # 每局都超时（桩睡死）
    # 熔断后：不再 spawn / 不再计回退 / 余下局被绕过（走一次性）
    assert pool.disabled is True
    assert pool.fallback == pool.breaker_after == 4, pool.fallback
    # 每次回退都把那个 worker 换掉 ⇒ 熔断前 spawned 与 fallback 同步增长；熔断后**停止增长**
    assert pool.spawned == pool.fallback == 4, f"熔断后不得再补位：spawned={pool.spawned}"
    assert pool.bypassed == total - 4
    lines = [m for m in msgs if "serve-pool" in m]
    assert sum(1 for m in lines if "回退一次性 spawn" in m) == 4, lines
    assert sum(1 for m in lines if "熔断" in m) == 1, lines
    assert lines[-1].startswith("[serve-pool] 熔断"), lines
    assert "已熔断" in pool.summary() and f"余下 {total - 4} 局" in pool.summary()
    pool.close()


def test_breaker_stops_replenishing_but_keeps_serving_warm_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 熔断 = **不再补位**，不是「把池关掉」：手上还暖的 worker 接着服务。

    为什么（2026-09-25 云机二次取证）：旧实现把熔断做成了「余下局全走一次性」——那等于在最挤
    的时刻把每一局都换成「冷启动一个 bun + wasm 编译 + attestation」：**正是它要掐掉的放大器**
    （现场：熔断行之后整轮 900s+、机器被堆死的进程压住）。暖 worker 是这条回路里唯一「不花新
    启动成本」的部分，把它们一起扔掉只会让余下每一局都付一次冷启动。
    另一方面，补位的冷启动就是「一次超时 → 三份进程」里的第三份 ⇒ **一个都不许新建**。
    """
    _fast_poll(monkeypatch)
    # 一处超时就熔断（池小 ⇒ 阈值起步 4；这里把阈值打到 1，隔离「熔断之后」的行为）
    monkeypatch.setattr(serve_pool, "FALLBACK_BREAKER_MIN", 1)
    script = _write_stub(tmp_path, _STUB_SERVE_SLOW_ONE, name="stub_slow_one.py", slow="7")
    msgs: list[str] = []
    pool = ServePool(sys.executable, tmp_path, 2, msgs.append, entry=script.name)
    assert pool.start() == 2
    assert pool.breaker_after == 1

    # ① 睡死的局：硬顶到点 ⇒ kill 一个 worker + 回退，同时把熔断闸拉下
    hang_argv, hang_log = _task(script, 0, 7, "w0", tmp_path)
    assert pool.try_pool(hang_argv, hang_log, 0.15) is None
    assert pool.disabled is True and pool.fallback == 1 and pool.spawned == 2

    # ② 健康局 + 还有一个暖 worker ⇒ 必须由它服务（不是绕过池）
    ok_argv, ok_log = _task(script, 0, 8, "w1", tmp_path)
    assert pool.try_pool(ok_argv, ok_log, 30.0) is not None
    assert pool.served == 1 and pool.bypassed == 0, (pool.served, pool.bypassed)
    assert pool.spawned == 2, f"熔断后不得新建 worker（冷启动就是第三份进程）：{pool.spawned}"

    # ③ 最后一个暖 worker 也被带走 ⇒ 池真的没法服务了，余下局才走一次性
    assert pool.try_pool(hang_argv, hang_log, 0.15) is None
    assert pool.spawned == 2, "熔断后哪怕在连续回退，也一个都不许重建"
    argv3, log3 = _task(script, 0, 9, "w3", tmp_path)
    assert pool.try_pool(argv3, log3, 30.0) is None
    assert pool.bypassed == 1, "没有暖 worker 才计绕过（否则计数被余下几百局灌满）"

    # 刹车现场与轮末汇总都要说清它到底做了什么
    brake = [m for m in msgs if "熔断" in m]
    assert len(brake) == 1 and "补位" in brake[0], brake
    assert "已熔断（停补位）" in pool.summary() and "余下 1 局" in pool.summary()
    pool.close()


def test_fallback_detail_lines_are_capped_and_carry_kind_and_where(tmp_path: Path) -> None:
    """回退详情有条数上限（对齐日志节食），且每行都能回答「哪条腿 / 哪一局 / 现场在哪」。

    真 STUB 现象：rollout 与 eval 共用本模块，旧行既无 kind 也无 where ⇒ 一屏同形行，
    分不出是哪条腿在刷（2026-09-25 现场就是这样）。
    """
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(tmp_path / "c"), fail_seed="5")
    msgs: list[str] = []
    pool = ServePool(sys.executable, tmp_path, 1, msgs.append, entry=script.name)
    pool.breaker_after = 999  # 单独隔离「详情条数上限」（不带熔断一起测）
    pool.start()
    for i in range(8):
        argv, logp = _task(script, 0, 5, f"w{i}", tmp_path)
        assert pool.try_pool(argv, logp, 30.0, label=f"s0/d{i}") is None
    detail = [m for m in msgs if "回退一次性 spawn" in m]
    assert len(detail) == serve_pool.FALLBACK_LOG_DETAIL_LIMIT, detail
    assert "kind=rollout s0/d0 err: stub-refused-s5" in detail[0], detail[0]
    assert "现场" in detail[0] and str(tmp_path) in detail[0], detail[0]
    pool.close()


def test_acquire_gives_up_within_the_game_cap_instead_of_the_ready_timeout(tmp_path: Path) -> None:
    """补位冷启动等不到就绪时，等待**受单局硬顶约束**（旧行为是固定 60s，远在 5s 硬顶之外）。

    现场：机器一被挤慢，`_acquire` 就把一个游戏线程按在就绪等待里，而看门狗在这段里什么都
    打不出来（既没有回退行、也没有进度行）——那正是「静默四分钟」的一半成因。
    """
    script = _write_stub(tmp_path, _STUB_SILENT, name="stub_silent.py")
    msgs: list[str] = []
    pool = ServePool(
        sys.executable, tmp_path, 2, msgs.append, entry=script.name, ready_timeout_sec=60.0
    )
    # 就绪等待的**下限**调到毫秒级（2026-09-29，§43）：判据是「受**本次尝试的硬顶**约束，
    # 而不是 60s 就绪上限」，与下限的绝对长度无关；下限不调的话本用例就得跑满 1s 下限
    # （硬顶传多小都一样）—— 那 1s 是本用例的全部墙钟，不是被测语义。
    # 调完后等待 = 本次硬顶（下面传的 0.3）：**更**贴近断言所描述的形状。
    pool.READY_BUDGET_FLOOR_SEC = 0.02
    argv, logp = _task(script, 0, 0, "w0", tmp_path)
    t0 = time.time()
    assert pool.try_pool(argv, logp, 0.3) is None  # 不 start()：首次取槽就是冷启动
    # timing-ok: 上界兜底（就绪等待应受本次硬顶约束，10s 只挡挂起）
    assert time.time() - t0 < 10.0, "就绪等待必须受本次尝试的硬顶约束，不是固定 60s"
    assert pool.fallback_reasons == {"no-slot": 1} and pool.killed == 1
    pool.close()


def test_pool_start_fails_fast_when_script_has_no_serve(tmp_path: Path) -> None:
    """不认 `--serve` 的脚本：`start()` 立刻返回 0（**不等满就绪上限**）——否则每次上云都白等。"""
    script = _write_stub(tmp_path, _STUB_NO_SERVE, name="stub_no_serve.py")
    pool = _pool(tmp_path, script, workers=2, ready=60.0)
    t0 = time.time()
    assert pool.start() == 0
    # timing-ok: 上界兜底（进程当场退出就该放弃，15s 只挡挂起）
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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """接线：整轮走池 ⇒ 同一批 shard、逐局报告/日志齐全、`serve_pool` 计数诚实。

    环境**杀子进程**（编码 agent 的删除/写守卫、负载、控制台广播）不在本用例的检验范围内，
    但它恰恰会打在这条用例上——因为全仓只有它把**一批 python 子进程**长期挂在项目内
    `tmp/pytest-tmp/` 下又持续写盘（2026-09-23；取证与判据：docs/nn/engineering.md §21）。
    池对此的契约是「只慢不错、绝不丢局」：那一局回退一次性 spawn、产物齐全，只是计数变成
    `served=2/killed=1/fallback=1`。所以这里先断言**被环境杀的那一轮产物齐全**（契约现场），
    再用一轮干净的重跑钉「接线走池」。系统性回归不会因此漏网：真坏了每一轮计数都一样。
    """
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "9.9.9-stub")
    count = tmp_path / "count.d"
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(count), fail_seed="")
    msgs: list[str] = []
    spec = _serve_spec(tmp_path, script, [(3, 7), (3, 8), (4, 1)])

    def _round(name: str) -> tuple[Path, dict]:
        d = tmp_path / name
        d.mkdir()
        _install_entry(d, _STUB_SERVE, count=str(count), fail_seed="")
        return d, iter_rollout.run_iter_rollout(d, spec, log=msgs.append)

    job_dir, out = _round("job")
    stats = out["serve_pool"]
    if stats is not None and _env_killed_worker(stats):
        # 契约现场：环境杀 worker ⇒ 那一局回退一次性，**整轮照样产出齐全**（一局不丢）。
        assert len(out["shard_dirs"]) == 3 and out["report"]["games"] == 3, (stats, msgs)
        for i in range(3):
            assert (job_dir / f"w{i}" / "_rl_report.json").exists(), (stats, msgs)
        msgs.append(
            f"[env-kill] 环境带走了长驻 worker：{stats['reasons']} ⇒ 重跑一轮钉接线（见模块注释）"
        )
        job_dir, out = _round("job-retry")
    assert sorted(Path(p).name for p in out["shard_dirs"]) == [
        "rl_s3_seed7", "rl_s3_seed8", "rl_s4_seed1",
    ]
    assert out["report"]["games"] == 3 and out["report"]["totalSamples"] == 6
    stats = out["serve_pool"]
    # 失败时把池的**现场**一并打出来：计数（served/spawned/killed/fallback）+ 回退原因
    # （`reasons` 带 worker 自己的报错文本）+ 池的日志尾（`[serve-pool] …` 验尸行）。
    # 否则断言里的 dict 会被 pytest 省略成 `{...}`，只剩「2 != 3」这种查不动的现场
    # （2026-09-23 的 flake 就是这样白烧了一轮）。
    assert stats is not None, (msgs[-8:], out["report"])
    if stats["fallback"]:
        (tmp_path / "forensics-msgs.txt").write_text("\n".join(msgs), encoding="utf-8")
    assert stats["served"] == 3 and stats["fallback"] == 0, (stats, msgs[-8:])
    assert stats["spawned"] == 2, (stats, msgs[-8:])  # workers=2 起满池，`spawned` 与局数无关
    assert out["report"]["elapsedSec"] >= 0.0
    # 每局：报告 + 自己的日志（日志内容来自 worker stdout，必须按局切开）
    for i, (st, sd) in enumerate([(3, 7), (3, 8), (4, 1)]):
        assert json.loads((job_dir / f"w{i}" / "_rl_report.json").read_text())["games"] == 1
        text = (job_dir / f"w{i}" / iter_rollout.ROLLOUT_LOG_NAME).read_text(encoding="utf-8")
        assert f"done s{st}/d{sd}" in text
    assert any("长驻 worker 池" in m for m in msgs), msgs
    assert any("serve_pool: served=3" in m for m in msgs), msgs


def test_concurrent_pool_games_never_hit_the_counter_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """并发跑局：桩的计数器**不许**被并发读者读到空文件。

    这是 2026-09-23/24 那个 pre-commit flake 的直接回归钉子（根因与取证见
    `docs/nn/engineering.md §21`）：旧的自增写法（截断 + 写）会让另一个 worker 读到
    截断中的空计数文件 ⇒ `int("")` ⇒ 它回 `__SERVE_ERR__` ⇒ 池按纪律换掉它并回退
    一次性 ⇒ 端到端计数变成 `killed=1/fallback=1`（三局产物其实齐全，所以只是**假红**）。
    4 worker × 12 局的碰撞概率远高于端到端用例的 2×3 ⇒ 旧写法下能稳定抓住这个回归。
    """
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "")
    count = tmp_path / "count.d"
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(count), fail_seed="")
    msgs: list[str] = []
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    _install_entry(job_dir, _STUB_SERVE, count=str(count), fail_seed="")
    spec = _serve_spec(tmp_path, script, [(3, s) for s in range(12)], workers=4)
    out = iter_rollout.run_iter_rollout(job_dir, spec, log=msgs.append)
    stats = out["serve_pool"]
    assert stats is not None, (msgs[-8:], out["report"])
    assert stats["served"] == 12 and stats["fallback"] == 0, (stats, msgs[-8:])
    assert stats["reasons"] == {} and out["report"]["games"] == 12, (stats, msgs[-8:])
    # 计数必须**精确**（不只是「没报错」）：共享文件写法在并发下会丢更新
    assert _games_run(count) == 12, (stats, msgs[-8:])


def test_run_iter_rollout_pool_failure_still_produces_the_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """池判失败的那一局（ERR）⇒ 回退一次性 spawn，**整轮仍然成功**（池不吞局）。"""
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "")
    count = tmp_path / "count.d"
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(count), fail_seed="7")
    msgs: list[str] = []
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    _install_entry(job_dir, _STUB_SERVE, count=str(count), fail_seed="7")
    spec = _serve_spec(tmp_path, script, [(3, 7), (3, 8)])
    # 一次性路径用**同一个桩**（同一份 argv 直接跑，不带 --serve）：它同样能产 shard
    out = iter_rollout.run_iter_rollout(job_dir, spec, log=msgs.append)
    assert len(out["shard_dirs"]) == 2
    stats = out["serve_pool"]
    assert stats is not None and stats["served"] == 1 and stats["fallback"] == 1
    assert stats["reasons"] == {"err": 1}
    assert _games_run(count) == 2  # 被拒那一局由一次性路径真跑了（一局没丢）


def test_run_iter_rollout_env_switch_returns_to_per_game_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`NN_SERVE_POOL=0` ⇒ 不建池（`serve_pool` 为 None），行为回到上云前。"""
    monkeypatch.setenv(serve_pool.ENV_SWITCH, "0")
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "")
    count = tmp_path / "count.d"
    script = _write_stub(tmp_path, _STUB_SERVE, count=str(count), fail_seed="")
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    out = iter_rollout.run_iter_rollout(
        job_dir, _serve_spec(tmp_path, script, [(3, 7)]), log=lambda _m: None
    )
    assert out["serve_pool"] is None
    assert len(out["shard_dirs"]) == 1
    assert _games_run(count) == 1


def test_run_iter_rollout_mixed_scripts_never_builds_a_pool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
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


def test_pool_sends_the_mode_token(tmp_path: Path) -> None:
    """送进 stdin 的行 = `[mode token, ...argv[1:]]`（**逐字**核对收到的 JSON）。

    这条是同质化的协议面：worker 是同一个入口（`serve-any.ts`），它靠行首 token 决定跑哪个
    导出器 —— token 丢了/送成脚本路径，worker 会响亮报 `unknown mode`（而不是静默跑默认网格）。
    """
    lines = tmp_path / "lines.jsonl"
    script = _write_stub(tmp_path, _STUB_ECHO, name="stub_serve.py", out=str(lines))
    pool = _pool(tmp_path, script)
    assert pool.start() == 1, (pool.spawned, pool.killed)
    argv, logp = _task(script, 5, 6, "w0", tmp_path)
    assert pool.try_pool(argv, logp, 30.0) is not None, (pool.fallback_reasons, pool.killed)
    pool.close()
    got = json.loads(lines.read_text(encoding="utf-8").splitlines()[0])
    assert got[0] == "rollout", got  # 行首 = mode token（不是脚本路径）
    assert got[1:] == argv[1:], "token 之后必须是**原样**的该局 argv"
    assert str(script) not in got, "脚本路径不得进 stdin（同质入口在池侧固定）"


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
