"""test_rollout_scratch.py — 热路径落节点本地盘（plan/rollout-local-scratch，2026-10-08）。

覆盖（逐条对着 plan §6 的验收面，编号 = `plan/rollout-local-scratch.review-bf.md` 的 F 号）：

  * **落点链**（P0-1；**速度闸已于 2026-10-14 删除** —— `plan/offline-local-disk-first` §3.1）：
    **容量一道闸** + 逐候选下探、全不过 ⇒ `None`（回退档）；env 显式指定无条件生效、
    **不可写则响亮失败**（F9）、`off` 一族 = 强制回退档（A2 逃生门）；`need` 不含余量比值（F8②）；
    且**解析全程不碰 job 目录**（139s 现场那笔「在网络盘上先测一遍速」已不存在）。
  * **布局**（F5）：目录名带作业 hash ⇒ 同机多会话互不误删；TS 家**内容寻址**（两腿/跨轮共用）。
  * **原子安装**（R9）：临时目录 + 改名 ⇒ 并发拷同一个 sha 不会看见半截树；已在 ⇒ 复用。
  * **搬回**（F1/F3/F4）：暂存在 job 目录**之外**（`scan_shard_dirs` 看不见半截拷贝）；
    校验不过不装；`ENOSPC` 之类确定性失败 ⇒ `ProtocolError`；作废的安装票**装不进去**；
    走 `os.replace` 而不是 `os.rename`。
  * **rollout 腿**：换根档真跑（假 bun + 桩导出器）⇒ 产出在本地盘生成、结算一局搬回一局、
    权重被镜像（桩把 `--weights` 打出来 ⇒ 能直接断言它落在 scratch）、轮末清自己那一份；
    回退档**逐字**走今天的行为（F9）。
  * **R1**：`_clean_attempt` 两个根都扫、两个根之外一个不删；回退档的调用形状不变。
  * **eval 腿**（P0-5）：TS 树/权重/每局 `game_dir` 换到本地盘，而**逐局账本不动**（F2）。
  * **prune**（F1）：搬回暂存根不被 `prune_job_dirs` 算成一个 job 目录。
  * **落点读数**（取代 `plan/rollout-topup-fastpath.plan.md` §3.1 的 `probe_probe`）：轮报带
    `落点=本地:<path>` ∨ `落点=网络(回退原地｜成因=…)｜建布局 x.xxs`；探针与它的上界已删 ⇒
    落点段只剩本地盘操作（`plan/offline-local-disk-first` §3.3 A3）。

真 bun + 真 exporter 的逐位对拍在 `e2e/`（本文件不依赖 bun；也不写 8MB 探针）。
"""

from __future__ import annotations

import errno
import json
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.scratch as scratch
import remote.job_fs as job_fs
import worker.iter_rollout as iter_rollout
from common import game_watch
from common.platform_utils import sandbox_delete_blocked
from common.protocol import INIT_WEIGHTS_NAME, ProtocolError
from worker.iter_rollout import run_iter_rollout, scan_shard_dirs

# ------------------------------------------------------------------ 桩与夹具

#: 桩导出器：**照真导出器的形状**落盘（shard 在 `--out` **里面**，不是平铺），并把 `--weights`
#: 原样打出来 —— 换根有没有生效（`--weights` 指向 scratch）就靠这一行断言。
_STUB = """\
import json, sys
from pathlib import Path
a = sys.argv[1:]
def val(flag):
    return a[a.index(flag) + 1] if flag in a else ""
print("weights=" + val("--weights"))
mark = val("--mark")
if mark:
    with open(mark, "a", encoding="utf-8") as f:
        f.write("run\\n")
out = Path(val("--out"))
out.mkdir(parents=True, exist_ok=True)
stage, seed, wver = int(val("--stages")), int(val("--seeds")), val("--wver")
d = out / f"rl_s{stage}_seed{seed}"
d.mkdir(parents=True, exist_ok=True)
(d / "manifest.json").write_text(json.dumps({"stage": stage, "seed": seed, "wver": wver}))
(out / "_rl_report.json").write_text(json.dumps({
    "games": 1, "winRate": 1.0, "outcomes": {"stage_clear": 1},
    "totalSamples": 3, "totalTicks": 30, "scoreList": [1.0],
    "dimLists": {"kills": [2.0]},
}))
print("stub ok")
"""


def _local_root(tmp_path: Path) -> Path:
    """假「节点本地盘」（候选链里唯一那一档）。"""
    root = tmp_path / "local-disk"
    root.mkdir(exist_ok=True)
    return root


def _watchdog(monkeypatch: pytest.MonkeyPatch) -> None:
    """假 bun（本进程 python）+ 快轮询（生产 0.5s）。"""
    monkeypatch.setattr(game_watch, "GAME_POLL_SEC", 0.05)
    monkeypatch.setattr(iter_rollout, "resolve_bun", lambda name="": sys.executable)
    monkeypatch.setattr(iter_rollout, "bun_version", lambda bun: "")


def _stub_spec(
    tmp_path: Path, games: list[tuple[int, int]], *, with_weights: bool = True, mark: Path | None = None
) -> dict:
    stub = tmp_path / "stub_exporter.py"
    stub.write_text(_STUB, encoding="utf-8")
    argv = []
    for i, (st, sd) in enumerate(games):
        one = [str(stub), "--out", f"w{i}", "--stages", str(st), "--seeds", str(sd), "--wver", "W" * 64]
        if with_weights:
            one += ["--weights", INIT_WEIGHTS_NAME]
        if mark is not None:
            one += ["--mark", str(mark)]
        argv.append(one)
    return {
        "argv": argv,
        "wver": "W" * 64,
        "workers": 2,
        "game_timeout_sec": 3.0,
        "bun": sys.executable,
    }


#: 沙箱 safe-delete 配额耗尽时，把「删除没落地」判成**环境**而非回归（仓内既有约定，
#: 见 `platform_utils.sandbox_delete_blocked`）。`and` 短路保证探针只在已失败的路径上跑。
_SKIP_BLOCKED = "沙箱 safe-delete 配额耗尽拦截删除（环境，非回归）——换 turn 重跑即绿"


def _job_dir(tmp_path: Path, *, with_weights: bool = True) -> Path:
    job = tmp_path / "job"
    job.mkdir(exist_ok=True)
    if with_weights:
        (job / INIT_WEIGHTS_NAME).write_text('{"w": 1}', encoding="utf-8")
    return job


def _only_candidate(monkeypatch: pytest.MonkeyPatch, root: Path) -> None:
    """候选链只留一个假本地盘（真 POSIX 候选在 Windows/CI 上不可控）。"""
    monkeypatch.setattr(scratch, "SCRATCH_CANDIDATES", (str(root),))


def _half_shard(root: Path, stage: int = 0, seed: int = 0) -> Path:
    """半截 shard：有 `manifest.json`、内容不全（`data_fp` 拦不住的那种）。"""
    d = root / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"stage": stage, "seed": seed}), encoding="utf-8")
    (d / "obs.npy").write_bytes(b"half")
    return d


# ------------------------------------------------------------ 落点链（P0-1）


def test_resolve_takes_the_first_candidate_that_passes_the_capacity_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """候选链是优先级：第一个过**容量闸**的就要用（速度闸已删 ⇒ 只剩这一道）。"""
    first, second = _local_root(tmp_path) / "a", _local_root(tmp_path) / "b"
    got = scratch.resolve_scratch_root(
        tmp_path / "job", need_bytes=1, candidates=(str(first), str(second))
    )
    assert got == first, "候选链是优先级：第一个过闸的就要用"


def test_resolve_skips_a_candidate_that_is_too_small(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """容量闸：free < need × 比值 ⇒ 看下一个（`/dev/shm` 默认 64MB 就是这么被弹掉的）。"""
    tiny, roomy = _local_root(tmp_path) / "tiny", _local_root(tmp_path) / "roomy"
    tiny.mkdir()
    roomy.mkdir()
    monkeypatch.setattr(
        scratch, "free_bytes", lambda p: 10.0 if Path(p) == tiny else 10.0**9
    )
    got = scratch.resolve_scratch_root(
        tmp_path / "job", need_bytes=1_000_000, candidates=(str(tiny), str(roomy))
    )
    assert got == roomy


def test_resolve_never_touches_the_job_root(tmp_path: Path) -> None:
    """★ 139s 的反探针（`plan/offline-local-disk-first` §3.1 A1 / 评审 P0-3）：

    落点解析**不许在 job 目录（= 网络盘）上写任何东西** —— 速度闸连同它的 8MB 探针已删。
    判据不看符号名（那是「删掉删除即红」的形状），只看**行为**：解析前后 job 目录的内容与
    它的哨兵文件 mtime 零变化（旧实现会在这里留下 `.nn-scratch-probe-<pid>` 那一笔写 + fsync）。
    """
    cand = _local_root(tmp_path) / "ok"
    cand.mkdir()
    job = tmp_path / "job"
    job.mkdir()
    sentinel = job / "keep.txt"
    sentinel.write_text("x", encoding="utf-8")
    before = (sorted(p.name for p in job.iterdir()), sentinel.stat().st_mtime_ns)

    got = scratch.resolve_scratch_root(
        job, need_bytes=1, candidates=(str(cand),), log=lambda _m: None
    )

    assert got == cand
    after = (sorted(p.name for p in job.iterdir()), sentinel.stat().st_mtime_ns)
    assert after == before, "落点解析在 job 目录（网络盘）上留下了东西"


def test_resolve_falls_back_to_none_when_nothing_qualifies(tmp_path: Path) -> None:
    assert (
        scratch.resolve_scratch_root(tmp_path / "job", need_bytes=1, candidates=()) is None
    ), "没有可用候选 ⇒ None（调用方逐字走今天的行为）"


def test_generic_tests_pin_the_fallback_path(tmp_path: Path) -> None:
    """通用用例必须**确定性**走回退档 —— 机器速度/负载不许决定走哪一档（2026-10-09 门禁红）。

    现场：安静时 /dev/shm 只比 job 目录快 ~2.6×（回落，绿）；`bun run pygate` 8 worker 满载时
    同一台机器测出 ≥5×（换根）⇒「假导出器把 shard 平铺在 `--out` 之外」的那批用例转红。
    钉住它的就是根 conftest 的 autouse `_scratch_off`（本用例是那条钉子的回归守卫）。
    同时钉住「没候选就立刻回落」：一份注定 None 的决策不该付任何 IO。
    """
    assert scratch.SCRATCH_CANDIDATES == (), "缺 pin：见 nn-training/conftest.py::_scratch_off"

    job = tmp_path / "job"
    job.mkdir()
    assert scratch.resolve_scratch_root(job, need_bytes=1) is None
    assert list(job.iterdir()) == [], "回退档不该在 job 目录里留东西"


def test_env_off_forces_the_fallback_hatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ A2（§3.1 / DoD 11）：`NN_ROLLOUT_SCRATCH=off` ⇒ **强制回退档**，且**不建 `off` 目录**。

    回归（`§1.5` 的既有缺陷）：此前 `off` 被当成路径 ⇒ 在 cwd 下建一个叫 `off` 的目录并当
    scratch 根用（而文档把它写成「回退原地」的一键逃生门，见
    `plan/rollout-topup-fastpath.plan.md:939` 的「回退」列）。
    """
    monkeypatch.chdir(tmp_path)  # 「cwd 下有没有冒出 off/」要被判据看见
    local = _local_root(tmp_path)
    monkeypatch.setenv(scratch.SCRATCH_ENV, "off")
    msgs: list[str] = []
    got = scratch.resolve_scratch_root(
        tmp_path / "job", need_bytes=1, candidates=(str(local),), log=msgs.append
    )
    assert got is None, "off ⇒ 回退档（候选链整条跳过）"
    assert not (tmp_path / "off").exists(), "off 不是路径：不许在 cwd 下建目录"
    assert any("强制回退档" in m for m in msgs), msgs
    assert not list(local.iterdir()), "off 档连候选都不碰（不建目录、不探针）"


def test_round_capacity_budget_covers_the_topup_batches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """评审 P0-3：容量门按**整轮**算（首批 + 最多补差批），不是只按单批。

    为什么（§3.5 取消搬回后）：产出留在本地盘 ⇒ 轮内峰值 = 各批之和。只按单批算就选不出够大的盘，
    于是在轮中途 ENOSPC（写盘失败 → 逐局重试 → 机器级停滞重投），而缺盘本该在**进这一轮之前**
    就回退档。本用例盯的就是传给落点闸的那个数。
    """
    _watchdog(monkeypatch)
    seen: dict[str, float] = {}

    def fake_open(job_root: object, *, need_bytes: float, log: object = None) -> None:
        seen["need"] = float(need_bytes)
        return None  # 回退档（本用例只关心“要了多少容量”）

    monkeypatch.setattr(scratch, "open_scratch", fake_open)
    # 每个阶段一个干净的 job 目录：回退档的产出就住在那儿，复用会撞上上一次的 shard 集。
    (tmp_path / "b").mkdir(exist_ok=True)
    (tmp_path / "c").mkdir(exist_ok=True)
    run_iter_rollout(_job_dir(tmp_path), _stub_spec(tmp_path, [(0, 0), (1, 1)]), log=lambda _m: None)
    assert seen["need"] == scratch.need_bytes_for(2), "没配 volume ⇒ 就是首批（与旧行为一致）"

    spec = _stub_spec(tmp_path, [(0, 0), (1, 1)])
    spec["volume"] = {"topup": {"max_batches": 3}}
    run_iter_rollout(_job_dir(tmp_path / "b"), spec, log=lambda _m: None)
    assert seen["need"] == scratch.need_bytes_for(2 * (1 + 3)), seen

    bad = _stub_spec(tmp_path, [(0, 0)])
    bad["volume"] = {"topup": {"max_batches": "nonsense"}}
    run_iter_rollout(_job_dir(tmp_path / "c"), bad, log=lambda _m: None)
    assert seen["need"] == scratch.need_bytes_for(1), "畸形字段 ⇒ 当成没配（不把整轮推去回退档）"


def test_round_capacity_budget_uses_the_round_slot_not_the_first_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A3：基准 = **满铺批宽**（槽位），批数预算分档：est 可求 ⇒ 按需；est=0 ⇒ 有界。

    现场形状：首批 1 局、槽位 94、max_batches 64、est=0。旧估算 `1 × (1+64) = 65 局`；
    而基准若直接乘 `(1+64)`（= 6110 局）会把整轮推去回退档（真包快回找不到）。
    """
    _watchdog(monkeypatch)
    seen: dict[str, float] = {}

    def fake_open(job_root: object, *, need_bytes: float, log: object = None) -> None:
        seen["need"] = float(need_bytes)
        return None  # 回退档（本例只关心“要了多少容量”）

    monkeypatch.setattr(scratch, "open_scratch", fake_open)
    monkeypatch.setattr(iter_rollout, "workers_cap", lambda: 0)

    # ① 冷启动（est=0、无历史）⇒ 有界盘预算 = 首批 + 常规补差批数（3）
    cold = tmp_path / "cold"
    cold.mkdir()
    spec = _stub_spec(tmp_path, [(2000, 1)])
    spec["workers"] = 94
    spec["volume"] = {
        "topup": {"max_batches": 64},
        "per_stage_quota": 24000,
        "est_s_by_stage": {2000: 0},
    }
    run_iter_rollout(cold, spec, log=lambda _m: None)
    assert seen["need"] == scratch.need_bytes_for(94 * (1 + 3)), seen

    # ② est 可求（190 samples/局 ⇒ 待补 ≈ 127 局 ⇒ 2 批）⇒ 94 × 3，不是 94 × 65
    est = tmp_path / "est"
    est.mkdir()
    spec2 = _stub_spec(tmp_path, [(2000, 1)])
    spec2["workers"] = 94
    spec2["volume"] = {
        "topup": {"max_batches": 64},
        "per_stage_quota": 24000,
        "est_s_by_stage": {2000: 190},
    }
    run_iter_rollout(est, spec2, log=lambda _m: None)
    assert seen["need"] == scratch.need_bytes_for(94 * (1 + 2)), seen


def test_cold_start_capacity_budget_matches_the_topup_default() -> None:
    """跨层常量对账（`offline_boot` 的先例：复制一份 + 测试钉逐字相同）。

    `worker.iter_rollout`(L1) 不能 import `worker.volume_alloc`(L2)（`test_remote_dag` 禁上向
    边），而 A3 的冷启动盘预算就取「常规补差批数」⇒ 两边锻死：漂了立刻红。
    """
    from worker.volume_alloc import DEFAULT_TOPUP_MAX_BATCHES

    assert iter_rollout.COLD_START_CAPACITY_BATCHES == DEFAULT_TOPUP_MAX_BATCHES


def test_env_override_wins_even_with_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """操作员显式指定 ⇒ 无条件用它（连候选链都不看：那是他的决定）。"""
    forced = _local_root(tmp_path) / "forced"
    forced.mkdir()
    monkeypatch.setenv(scratch.SCRATCH_ENV, str(forced))
    got = scratch.resolve_scratch_root(tmp_path / "job", need_bytes=1, candidates=())
    assert got == forced


def test_env_override_that_is_unwritable_fails_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F9：给了却不可写 ⇒ **响亮失败**（静默回落 = 操作员以为在测 scratch、其实在跑原地）。"""
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(scratch.SCRATCH_ENV, str(blocker / "sub"))
    with pytest.raises(ProtocolError):
        scratch.resolve_scratch_root(tmp_path / "job", need_bytes=1, candidates=())


def test_need_bytes_does_not_include_the_free_ratio() -> None:
    """F8②：比值只在探测时乘**一次**（否则 `need` 里乘一遍、判据里又乘一遍 = 2.25×）。"""
    need = scratch.need_bytes_for(2, per_game=1000)
    assert need == 2000.0
    assert scratch.SCRATCH_MIN_FREE_RATIO > 1.0  # 判据那边才乘它


# ------------------------------------------------------------ 布局（F5 / R9）


def test_scratch_dir_is_job_scoped_and_cleanup_stays_local(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F5：两个作业（哪怕同名 `run`）各自的 scratch 目录不同；清一个不碰另一个。"""
    root = _local_root(tmp_path)
    (tmp_path / "sess-a" / "run").mkdir(parents=True)
    (tmp_path / "sess-b" / "run").mkdir(parents=True)
    a = scratch.layout_for(root, tmp_path / "sess-a" / "run")
    b = scratch.layout_for(root, tmp_path / "sess-b" / "run")
    assert a.job != b.job, "同名作业也必须各有各的目录（否则一次清理就误删别人的）"
    a.exec_dir.mkdir(parents=True)
    b.exec_dir.mkdir(parents=True)
    # 两种都要能清：`w{i}/` 这种目录 + 导出器顺手落的散文件
    (a.exec_dir / "w0").mkdir()
    (a.exec_dir / "w0" / "rollout.log").write_text("x", encoding="utf-8")
    (a.exec_dir / "loose.txt").write_text("x", encoding="utf-8")
    (b.exec_dir / "w0").mkdir()
    if scratch.clear_dir(a.exec_dir) != 2 and sandbox_delete_blocked(tmp_path):
        pytest.skip(_SKIP_BLOCKED)
    assert not list(a.exec_dir.iterdir()), "轮末的清理面里目录与散文件都要清"
    assert (b.exec_dir / "w0").exists(), "清理只许动自己那一份"


def test_ts_home_is_content_addressed_across_jobs(tmp_path: Path) -> None:
    """TS 家按 sha 寻址、与作业无关 ⇒ 两条腿（不同进程/不同作业根）命中同一份。"""
    root = _local_root(tmp_path)
    one = scratch.layout_for(root, tmp_path / "worker-job")
    two = scratch.layout_for(root, tmp_path / "artifacts")
    assert one.ts_for("deadbeef") == two.ts_for("deadbeef")
    assert one.ts_for("deadbeef") != one.ts_for("cafebabe")


def test_install_tree_is_atomic_and_reused(tmp_path: Path) -> None:
    """R9：临时目录 + 改名（不留半截树）；已在 ⇒ 直接复用（内容寻址只付一次）。"""
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.txt").write_text("one", encoding="utf-8")
    dst = tmp_path / "out" / "sha1"
    assert scratch.install_tree(src, dst) is True
    assert (dst / "sub" / "a.txt").read_text(encoding="utf-8") == "one"
    assert not list(dst.parent.glob("*.tmp-*")), "临时目录不许留在盘上"
    (src / "sub" / "a.txt").write_text("two", encoding="utf-8")
    assert scratch.install_tree(src, dst) is True
    assert (dst / "sub" / "a.txt").read_text(encoding="utf-8") == "one", "已装好就不重装"


def test_install_file_is_atomic(tmp_path: Path) -> None:
    src = tmp_path / "w.json"
    src.write_text('{"w": 1}', encoding="utf-8")
    dst = tmp_path / "nested" / "w.json"
    assert scratch.install_file(src, dst) is True
    assert dst.read_bytes() == src.read_bytes()
    assert not list(dst.parent.glob("*.tmp-*"))
    assert scratch.install_file(tmp_path / "nope.json", dst) is False


# ------------------------------------------------------------ 搬回（F1/F3/F4）


def _game_tree(root: Path, stage: int = 0, seed: int = 0) -> None:
    d = root / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"stage": stage, "seed": seed}), encoding="utf-8")
    (d / "obs.npy").write_bytes(b"obs")
    (root / "rollout.log").write_text("stub ok", encoding="utf-8")
    (root / "_rl_report.json").write_text("{}", encoding="utf-8")


def test_drain_moves_the_tree_and_removes_the_source(tmp_path: Path) -> None:
    src = tmp_path / "scratch" / "w0"
    dst = tmp_path / "job" / "w0"
    _game_tree(src, 3, 7)
    assert scratch.drain_tree(src, dst) is True
    assert (dst / "rl_s3_seed7" / "obs.npy").exists()
    assert (dst / "rollout.log").exists()
    if src.exists() and sandbox_delete_blocked(tmp_path):
        pytest.skip(_SKIP_BLOCKED)
    assert not src.exists(), "搬完的本地盘副本要清掉（占用有界）"


def test_drain_uses_replace_and_never_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """跨设备安全：`os.replace`（同文件系统改名）+ 绝不 `os.rename`。

    跨设备时 `os.rename` 直接 `EXDEV`，而「半截搬过去」比「没搬」更坏（暂存目录要留在
    目标盘上 ⇒ 只能靠 `replace` 把**已对完账**的暂存目录改名过去）。
    """
    src = tmp_path / "scratch" / "w0"
    dst = tmp_path / "job" / "w0"
    _game_tree(src)
    used: list[str] = []
    real_replace, real_rename = os.replace, os.rename

    def _replace(a: Any, b: Any) -> Any:
        used.append("replace")
        return real_replace(a, b)

    def _rename(a: Any, b: Any) -> Any:
        used.append("rename")
        return real_rename(a, b)

    monkeypatch.setattr(scratch.os, "replace", _replace)
    monkeypatch.setattr(scratch.os, "rename", _rename)
    assert scratch.drain_tree(src, dst) is True
    assert "replace" in used and "rename" not in used, used


def test_drain_refuses_a_mismatched_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """校验不过 ⇒ 不安装（半截拷贝进了 job 目录就是「目录齐、obs 截断」的静默错一局）。"""
    src = tmp_path / "scratch" / "w0"
    dst = tmp_path / "job" / "w0"
    _game_tree(src)
    real_stat = scratch._dir_stat

    def stat(p: Path) -> tuple[int, int]:
        return (0, 0) if Path(p).parent.name.endswith(scratch.DRAIN_STAGE_SUFFIX) else real_stat(p)

    monkeypatch.setattr(scratch, "_dir_stat", stat)
    with pytest.raises(ProtocolError):
        scratch.drain_tree(src, dst)
    assert not dst.exists(), "校验不过时绝不许安装"
    leftover = list(scratch.drain_stage_root(dst).glob("*")) if scratch.drain_stage_root(dst).exists() else []
    if leftover and sandbox_delete_blocked(tmp_path):
        pytest.skip(_SKIP_BLOCKED)
    assert not leftover, "暂存要清掉（否则下一轮会被当成产出扫到）"


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EROFS, errno.EACCES])
def test_drain_fatal_errno_becomes_protocol_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    """F3：确定性失败（job 目录满/只读）⇒ 响亮上抛，**不**进「不限次重投」那条循环。"""
    src = tmp_path / "scratch" / "w0"
    dst = tmp_path / "job" / "w0"
    _game_tree(src)

    def boom(*a: object, **kw: object) -> None:
        raise OSError(code, os.strerror(code))

    monkeypatch.setattr(scratch.shutil, "copytree", boom)
    with pytest.raises(ProtocolError):
        scratch.drain_tree(src, dst)


def test_drain_transient_errno_propagates_as_oserror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """瞬态（EIO 等）**不是**内容决定性失败 ⇒ 原样上抛，由调用方按机器级停滞处置。"""
    src = tmp_path / "scratch" / "w0"
    dst = tmp_path / "job" / "w0"
    _game_tree(src)

    def boom(*a: object, **kw: object) -> None:
        raise OSError(errno.EIO, os.strerror(errno.EIO))

    monkeypatch.setattr(scratch.shutil, "copytree", boom)
    with pytest.raises(OSError) as ei:
        scratch.drain_tree(src, dst)
    assert not isinstance(ei.value, ProtocolError)


def test_cancelled_ticket_cannot_install_stale_data(tmp_path: Path) -> None:
    """F4：被放弃的 copier（`call_bounded` 不杀线程）**永远装不进去**。

    判据与 `os.replace` 在同一把锁里 ⇒ 没有「查完票、还没改名就被作废」的窗口。
    """
    gate = scratch.InstallGate()
    src = tmp_path / "scratch" / "w0"
    dst = tmp_path / "job" / "w0"
    _game_tree(src)
    ticket = gate.ticket("w0")
    ticket.cancel()
    assert scratch.drain_tree(src, dst, ticket=ticket) is False
    assert not dst.exists(), "作废的票不许安装（否则旧数据会占据 w0）"
    assert src.exists(), "没安装 ⇒ 源不动（下一个写者自己决定）"
    # 新票（= 重投后那一次）能装
    assert scratch.drain_tree(src, dst, ticket=gate.ticket("w0")) is True
    assert (dst / "rl_s0_seed0" / "obs.npy").exists()


def test_staging_dir_is_invisible_to_the_shard_scan(tmp_path: Path) -> None:
    """F1：暂存在 job 目录**之外** ⇒ `scan_shard_dirs`（`job_dir.rglob`）看不见半截拷贝。

    旧形状（`job_dir/w{i}.__drain__/`）下这个半截 shard 会被当成合法产出，而 `data_fp`
    只读 manifest 的 `{wver,stage,seed}` ⇒ 对账也拦不住 ⇒ 截断的 obs 进训练语料。
    """
    job = _job_dir(tmp_path, with_weights=False)
    stage_root = scratch.drain_stage_root(job)
    assert stage_root == job.parent / f"{job.name}{scratch.DRAIN_STAGE_SUFFIX}"
    assert job not in stage_root.parents and stage_root != job, "暂存必须在 job 目录之外"
    _half_shard(stage_root / "w0")
    assert scan_shard_dirs(job) == [], "暂存里的半截 shard 不算产出"
    assert scan_shard_dirs(stage_root / "w0"), "（对照：真在扫描面里的话它就会被认成产出）"
    # 真产出当然要被认出来
    _half_shard(job / "w0", 1, 1)
    assert [p.name for p in scan_shard_dirs(job)] == ["rl_s1_seed1"]


# ------------------------------------------------------------ rollout 腿（换根档）


def test_rollout_reroots_and_keeps_the_produce_in_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """换根档全链路（§3.5 取消搬回后）：产出**就住在**本地盘，PPO 读轮 scratch 根。

    本用例取代旧的「搬回后 job 目录形状不变」（§3.5b 判据迁移表第 1 行）：搬回这个步骤本身没了，
    取而代之的是三件可判的事——① 产出在 scratch 且**轮末不再被清**（反探针：重新加回清理 ⇒ 本条红）；
    ② `job_dir` 下不再出现 `w{i}/` 产出副本（= 真没搬回）；③ `shards_root` = 轮 scratch 目录
    （节点自己的 PPO 读它；评审 P0-1 的承重面）。
    """
    _watchdog(monkeypatch)
    local = _local_root(tmp_path)
    _only_candidate(monkeypatch, local)
    job = _job_dir(tmp_path)
    msgs: list[str] = []
    out = run_iter_rollout(job, _stub_spec(tmp_path, [(0, 0), (1, 1)]), log=msgs.append)
    layout = scratch.layout_for(local, job)

    # ① 产出在 scratch（形状与出厂时逐字相同：`scan_shard_dirs` / `collect_reports` / `data_fp` 零改动）
    assert sorted(p.name for p in scan_shard_dirs(layout.exec_dir)) == ["rl_s0_seed0", "rl_s1_seed1"]
    assert out["report"]["shards"] == 2
    for i, (st, sd) in enumerate([(0, 0), (1, 1)]):
        assert (layout.exec_dir / f"w{i}" / f"rl_s{st}_seed{sd}" / "manifest.json").exists()
        # F9②：`rollout.log` 是诊断现场（`_first_rollout_log_tail` 靠它）⇒ 直接断言它在
        assert (layout.exec_dir / f"w{i}" / "rollout.log").exists()
    # ② 换根真的生效了：桩把 `--weights` 打了出来，它必须在 scratch 里（= 镜像 + 再绝化）
    tail = (layout.exec_dir / "w0" / "rollout.log").read_text(encoding="utf-8")
    assert str(layout.exec_dir) in tail, tail
    # ③ 没搬回：真 job 目录下一个产出副本都没有（`--out` 从没落到那里）
    assert scan_shard_dirs(job) == [], "§3.5：产出不许再往 job 目录搬"
    assert not (job / "w0").exists()
    # ④ 读数可见 + PPO 的装载根（P0-1）
    assert any("产出落点" in m and "本地盘" in m for m in msgs), msgs
    assert out["shards_root"] == str(layout.job), out["shards_root"]
    # ⑤ 轮末**不再清** scratch（清在这里 = 没得训练）；回收由调用方在 PPO 之后 `ctx.cleanup()`
    assert list(layout.exec_dir.iterdir()), "产出必须留在本地盘上等 PPO 读"
    assert list(local.glob(f"{scratch.SCRATCH_TS_PREFIX}*")), "TS 家不在轮目录里（跨轮复用）"


def test_rollout_falls_back_byte_for_byte_when_no_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F9：候选链全不过 ⇒ **逐字**走今天的行为（不换根、产出住真 job 目录）。"""
    _watchdog(monkeypatch)
    monkeypatch.setattr(scratch, "SCRATCH_CANDIDATES", ())
    job = _job_dir(tmp_path)
    msgs: list[str] = []
    out = run_iter_rollout(job, _stub_spec(tmp_path, [(0, 0)]), log=msgs.append)
    assert out["report"]["shards"] == 1
    assert (job / "w0" / "rl_s0_seed0" / "manifest.json").exists()
    assert any("回退原地" in m for m in msgs), msgs
    # 回退档的装载根就是 job 目录（与改动前逐字相同）
    assert out["shards_root"] == str(job), out["shards_root"]
    assert not scratch.drain_stage_root(job).exists()
    assert not list(tmp_path.glob(f"{scratch.SCRATCH_DIR_PREFIX}*")), "回退档不建 scratch"


def test_rollout_reports_the_landing_point_and_keeps_the_scratch_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ A3 的正面判据：换根档的轮报带**落点读数**（取代 `probe_probe`），且不多付一笔探测。

    与旧「界内 ⇒ 照旧换根」用例等价的那一半（候选可用 ⇒ 真换根、产出落本地盘、装载根 =
    轮目录）保留；新增的唯一一条是读数：`落点=本地:<路径>｜建布局 x.xxs`。
    """
    _watchdog(monkeypatch)
    local = _local_root(tmp_path)
    monkeypatch.setattr(scratch, "SCRATCH_CANDIDATES", (str(local),))
    job = _job_dir(tmp_path)
    msgs: list[str] = []
    out = run_iter_rollout(job, _stub_spec(tmp_path, [(0, 0)]), log=msgs.append)

    # 行为判据：换根真的发生了（候选盘上有 scratch 家），且产出落在本地盘（§3.5：不搬回）
    assert list(local.glob(f"{scratch.SCRATCH_DIR_PREFIX}*")), "候选可用 ⇒ 换根"
    layout = scratch.layout_for(local, job)
    assert (layout.exec_dir / "w0" / "rl_s0_seed0" / "manifest.json").exists()
    assert out["shards_root"] == str(layout.job)
    # 读数（操作员接口）：本地落点 + 建布局耗时，一条轮报行
    landed = [m for m in msgs if "落点=" in m]
    assert landed, msgs
    assert any("落点=本地:" in m and str(local) in m and "建布局" in m for m in landed), landed
    assert not any("probe_probe" in m for m in msgs), "旧读数键必须消失"


def test_settlement_is_future_return_and_missing_produce_is_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§3.5b 判据迁移表第 3 行：F7「搬不回去的局不算结算」随搬回一起消失。

    取代它的是两件可判的事：

      ① 结算判据 = **跑局 future 正常返回**（进度行跑到 N/N）；
      ② 「结了算却没产出」不再能静默溜过 —— 轮末逐位对账（`verify_shards`）响亮拒收。
     ⚠ 这是一次**判据前移**（发现从「搬回那一刻」退到「轮末」）——代价已记进 DECISIONS。
    """
    _watchdog(monkeypatch)
    local = _local_root(tmp_path)
    _only_candidate(monkeypatch, local)
    job = _job_dir(tmp_path)
    msgs: list[str] = []
    out = run_iter_rollout(job, _stub_spec(tmp_path, [(0, 0), (1, 1)]), log=msgs.append)
    assert any("2/2 games settled" in m for m in msgs), msgs
    assert out["report"]["shards"] == 2

    # ② 空局（rc=0 但什么都没写）：轮末响亮（而不是“已经结算了两局”就这么过去）
    empty = tmp_path / "empty_exporter.py"
    empty.write_text("print('nothing')\n", encoding="utf-8")
    spec = _stub_spec(tmp_path, [(0, 0)])
    spec["argv"] = [
        [str(empty), "--out", "w0", "--stages", "0", "--seeds", "0", "--wver", "W" * 64,
         "--weights", INIT_WEIGHTS_NAME]
    ]
    with pytest.raises(ProtocolError) as ei:
        run_iter_rollout(job, spec, log=msgs.append)
    assert "没有任何 shard" in str(ei.value), ei.value


def test_clean_attempt_scans_both_roots_and_nothing_else(tmp_path: Path) -> None:
    """R1（本 plan 最容易埋雷的一处）：两个根都扫；两个根**之外**一个都不删。"""
    job = tmp_path / "job"
    ex = tmp_path / "scratch" / "r"
    job.mkdir()
    ex.mkdir(parents=True)
    argv = ["stub.py", "--out", "w0", "--stages", "0", "--seeds", "0"]
    in_job = _half_shard(job / "w0")
    in_exec = _half_shard(ex / "w0")
    elsewhere = _half_shard(tmp_path / "other")
    # out 目录本身也要清（半截 rollout.log/report 都在里面）
    (job / "w0" / "_rl_report.json").write_text("{}", encoding="utf-8")
    (ex / "w0" / "rollout.log").write_text("x", encoding="utf-8")
    iter_rollout._clean_attempt(job, argv, exec_root=ex)
    if (in_job.exists() or in_exec.exists()) and sandbox_delete_blocked(tmp_path):
        pytest.skip(_SKIP_BLOCKED)
    assert not in_job.exists() and not in_exec.exists(), "两个根都要扫（漏一边 = 静默错一局）"
    assert not (job / "w0").exists() and not (ex / "w0").exists()
    assert elsewhere.exists() and (elsewhere / "manifest.json").exists(), "越界路径一个都不删"


def test_clean_attempt_survives_rglob_race(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """清理的 `rglob` 与并发 `rmtree` 竞态 ⇒ **必须吞掉 OSError，不许杀死整轮**。

    2026-10-26 云机现场（4 核 · 35 关 · 41 批补差后）：同批两局同时打到 5s 硬顶 ⇒ 两个
    worker 各跑一次 `_clean_attempt`；一个 `rmtree` 掉 `topup41/r/w196` 的同时，另一个的
    `rglob` 正 stat 到那一层 ⇒ Python 3.10 的 `rglob` 抛 `FileNotFoundError` ⇒ 异常穿过
    `run_iter_rollout` 的 try（`ok` 停在 False）⇒ `kind=iter rollout 中断` ⇒ 冒到
    `run_loop` 顶层，整条腿当场死（日志：`FileNotFoundError: …/topup41/r/w196`）。

    判据：清理是 best-effort —— 扫不到就当没有。这一条钉住「清理不许反向杀死这一轮」。
    """
    job = tmp_path / "job"
    job.mkdir()
    (job / "r").mkdir()
    _half_shard(job / "r" / "w196", stage=2012, seed=5)
    argv = ["stub.py", "--out", "r/w196", "--stages", "2012", "--seeds", "5"]

    real_rglob = Path.rglob

    def racing_rglob(self: Path, pattern: str):  # type: ignore[no-untyped-def]
        # 只在扫 job 根时模拟「并发 rmtree 把目录删了」的窗口
        if self == job:
            raise FileNotFoundError(2, "No such file or directory", str(self / "r" / "w196"))
        return real_rglob(self, pattern)

    monkeypatch.setattr(Path, "rglob", racing_rglob)
    iter_rollout._clean_attempt(job, argv)  # 不得抛

    # out 目录那一支仍然清掉了（rglob 挂了不代表整次清理失效）
    monkeypatch.undo()
    assert not (job / "r" / "w196").exists(), "out 目录必须照清（半截产出不许留在盘上）"


def test_clean_partial_shape_is_unchanged_on_the_fallback_path(tmp_path: Path) -> None:
    """回退档的调用形状与今天**逐字相同**（连关键字都不多传一个）。

    既有用例里那种两参替身（`def clean(jd, argv)`）就是这条的守卫：换根档不许把它的
    形状改掉（否则「协议零改动」的回归面第一次红就红在这里）。
    """
    job = tmp_path / "job"
    job.mkdir()
    argv = ["stub.py", "--out", "w0", "--stages", "0", "--seeds", "0"]
    ex = tmp_path / "ex"
    assert iter_rollout._clean_partial(job, job, argv).keywords == {}, "回退档不多传参数"
    assert iter_rollout._clean_partial(job, ex, argv).keywords == {"exec_root": ex}
    iter_rollout._clean_attempt(job, argv)  # 两参直调（替身的形状）照旧可用


def test_prune_exempts_the_drain_stage_root(tmp_path: Path) -> None:
    """F1 的另一半：暂存根就在 `work_dir` 下 ⇒ 不豁免就会被算成一个 job 目录。"""
    work = tmp_path / "work"
    work.mkdir()
    for name in ("j1", "j2"):
        (work / name).mkdir()
        (work / name / "manifest.json").write_text("{}", encoding="utf-8")
    stage = scratch.drain_stage_root(work / "j1")
    stage.mkdir()
    os.utime(stage, None)  # 最新的 mtime：不豁免的话它会把 keep-2 窗口里的 j2 挤掉
    assert job_fs.prune_job_dirs(work, keep=2, log=lambda _m: None) == 0
    assert (work / "j1").exists() and (work / "j2").exists()
    assert stage.exists()


# ------------------------------------------------------------ eval 腿（P0-5/F2）


def _eval_ctx(tmp_path: Path) -> SimpleNamespace:
    ts = tmp_path / "ts_code_cache" / ("f" * 8)
    (ts / "tools").mkdir(parents=True)
    (ts / "tools" / "sim").mkdir()
    art = tmp_path / "artifacts"
    art.mkdir()
    weights = art / "it3" / "weights.json"
    weights.parent.mkdir()
    weights.write_text('{"w": 1}', encoding="utf-8")
    return SimpleNamespace(
        ts_tree_dir=ts,
        store=SimpleNamespace(root=art, weights_path=lambda it: art / f"it{it}" / "weights.json"),
        work_dir=tmp_path / "run-work",
        course=None,
        manifest={"course_fp": "c" * 64},
        eval_slots=3,
        eval_game_timeout_sec=0.0,
        log=lambda _m: None,
    )


def _eval_plan() -> SimpleNamespace:
    return SimpleNamespace(stages=[2000, 2001], n_seeds=50)


def test_eval_builder_moves_ts_weights_and_games_to_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P0-5：94 路冷启动读的那棵树、当轮权重、200 个 game 目录全换到本地盘。"""
    from remote.artifacts import ArtifactStore
    from remote.plan_handoff import _eval_job_builder

    local = _local_root(tmp_path)
    _only_candidate(monkeypatch, local)
    ctx = _eval_ctx(tmp_path)
    msgs: list[str] = []
    ctx.log = msgs.append
    job = _eval_job_builder(cast(Any, ctx), _eval_plan())(3)
    layout = scratch.layout_for(local, ctx.store.root)

    assert Path(job["ts_root"]) == layout.ts_for(ctx.ts_tree_dir.name)
    assert Path(job["work_dir"]) == layout.eval_dir
    assert Path(job["weights_path"]) != ctx.store.weights_path(3)
    assert Path(job["weights_path"]).read_bytes() == ctx.store.weights_path(3).read_bytes(), (
        "逐字节拷贝 ⇒ 账本里的 wver（key16）与今天相同（两腿读数可配对）"
    )
    # F2：**账本不动**（`settle_eval_summary` 的聚合读面是文件，且那是 cell 死后续跑的凭据）
    assert Path(job["eval_jsonl"]) == ctx.store.root / ArtifactStore.EVAL_LOG_NAME
    # A4 的读数（取代旧的「云机评估热路径换到本地盘」那一行）：落点 + 建布局耗时
    landed = [m for m in msgs if "落点=" in m]
    assert landed and all("落点=本地:" in m and "建布局" in m for m in landed), msgs


def test_eval_builder_falls_back_when_a_step_exceeds_its_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ A4 / DoD-4（镜像旧「探针超界」用例）：落点段任一步超界 ⇒ **本段**回退原地 + 响亮一行。

    判定不看措辞：① 三条路径全是「今天的」（不换根）；② **后续轮不再等** —— 超界一次就
    把本段降档（`state["layout"] = None`），不是每轮各付一遍注定等满的上界；③ 有一条 WARN。
    """
    from remote.plan_handoff import _eval_job_builder

    local = _local_root(tmp_path)
    _only_candidate(monkeypatch, local)
    monkeypatch.setattr(game_watch, "DRAIN_CEILING_SEC", 0.3)

    def slow_install(*a: Any, **kw: Any) -> bool:
        # 夹具模拟「坏盘上既不返回也不抛」的那一笔写；睡的不是「等对方先跑」。
        time.sleep(2.0)  # sleep-ok: 夹具模拟的工作量
        return True

    monkeypatch.setattr(scratch, "install_file", slow_install)
    ctx = _eval_ctx(tmp_path)
    msgs: list[str] = []
    ctx.log = msgs.append
    build = _eval_job_builder(cast(Any, ctx), _eval_plan())
    t0 = time.time()
    job = build(3)
    first = time.time() - t0
    # 判据就是被测的那条上界本身（未包上界时这里实测 2s+）
    assert first < 1.5, f"超界必须在上界内返回（实测 {first:.2f}s）"  # timing-ok: 契约上界（0.3s×5）
    assert Path(job["weights_path"]) == ctx.store.weights_path(3), "超界 ⇒ 逐字回退档（不换根）"
    assert Path(job["work_dir"]) == ctx.work_dir / "eval-work"
    assert Path(job["ts_root"]) == ctx.ts_tree_dir
    assert any("超界或失败" in m for m in msgs), msgs
    # 第二次：本段已降档 ⇒ 不再进落点段（不许每轮再等一遍）
    t1 = time.time()
    job4 = build(4)
    second = time.time() - t1
    assert second < 0.5, f"降档后不许每轮再等（实测 {second:.3f}s）"  # timing-ok: 契约（降档即零落点 IO）
    assert Path(job4["weights_path"]) == ctx.store.weights_path(4)


def test_eval_builder_falls_back_and_keeps_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F9：（评估腿的）回退档 = 今天的三个路径一个不换。"""
    from remote.artifacts import ArtifactStore
    from remote.plan_handoff import _eval_job_builder

    monkeypatch.setattr(scratch, "SCRATCH_CANDIDATES", ())
    ctx = _eval_ctx(tmp_path)
    job = _eval_job_builder(cast(Any, ctx), _eval_plan())(3)
    assert Path(job["ts_root"]) == ctx.ts_tree_dir
    assert Path(job["work_dir"]) == ctx.work_dir / "eval-work"
    assert Path(job["weights_path"]) == ctx.store.weights_path(3)
    assert Path(job["eval_jsonl"]) == ctx.store.root / ArtifactStore.EVAL_LOG_NAME


def test_eval_builder_clears_the_previous_round(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """占用有界：上一轮的 game 目录与权重在下一轮开始时回收（轮与轮串行，不会删到在跑的）。"""
    from remote.plan_handoff import _eval_job_builder

    local = _local_root(tmp_path)
    _only_candidate(monkeypatch, local)
    ctx = _eval_ctx(tmp_path)
    build = _eval_job_builder(cast(Any, ctx), _eval_plan())
    build(3)
    layout = scratch.layout_for(local, ctx.store.root)
    stale = layout.eval_dir / "eval-3-s2000-d1"
    stale.mkdir(parents=True)
    (stale / "_eval_report.json").write_text("{}", encoding="utf-8")
    build(4)
    if stale.exists() and sandbox_delete_blocked(tmp_path):
        pytest.skip(_SKIP_BLOCKED)
    assert not stale.exists(), "上一轮的 game 目录该当场回收"
