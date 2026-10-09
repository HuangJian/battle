"""test_rollout_scratch.py — 热路径落节点本地盘（plan/rollout-local-scratch，2026-10-08）。

覆盖（逐条对着 plan §6 的验收面，编号 = `plan/rollout-local-scratch.review-bf.md` 的 F 号）：

  * **落点链**（P0-1）：容量 + **速度**两道闸、逐候选下探、全不过 ⇒ `None`（回退档）；
    env 显式指定无条件生效、**不可写则响亮失败**（F9）；`need` 不含余量比值（F8②）。
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

真 bun + 真 exporter 的逐位对拍在 `e2e/`（本文件不依赖 bun；也不写 8MB 探针）。
"""

from __future__ import annotations

import errno
import json
import os
import sys
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


def _fast_probe(
    monkeypatch: pytest.MonkeyPatch, *, speed: float = 4096.0, base_speed: float = 64.0
) -> None:
    """把探针换成常数（用例绝不真写 8MB；判据是「读数怎么用」，不是盘多快）。

    建模：`_local_root()` 造出来的那个假本地盘快、其余（job 目录/产物目录）慢 —— 速度闸
    因此会放行（F6 的判据本身由专门用例守着）。
    """

    def probe(p: Path, **kw: object) -> float:
        return speed if "local-disk" in Path(p).parts else base_speed

    monkeypatch.setattr(scratch, "probe_write_speed_mbps", probe)


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
    _fast_probe(monkeypatch)


def _half_shard(root: Path, stage: int = 0, seed: int = 0) -> Path:
    """半截 shard：有 `manifest.json`、内容不全（`data_fp` 拦不住的那种）。"""
    d = root / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / "manifest.json").write_text(json.dumps({"stage": stage, "seed": seed}), encoding="utf-8")
    (d / "obs.npy").write_bytes(b"half")
    return d


# ------------------------------------------------------------ 落点链（P0-1）


def test_resolve_takes_the_first_candidate_that_passes_both_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = _local_root(tmp_path) / "a", _local_root(tmp_path) / "b"
    _fast_probe(monkeypatch)
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
    _fast_probe(monkeypatch)
    got = scratch.resolve_scratch_root(
        tmp_path / "job", need_bytes=1_000_000, candidates=(str(tiny), str(roomy))
    )
    assert got == roomy


def test_resolve_requires_a_real_speedup_over_the_job_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F6：候选不比 job 目录快 `SCRATCH_PROBE_MIN_SPEEDUP` 倍 ⇒ **回落**（宁可原地跑）。

    这条闸存在的理由就是「Kaggle 的 /tmp 可能也在同一块慢后端上」⇒ 那时换根是零收益
    而失败面照旧。
    """
    cand = _local_root(tmp_path) / "slow"
    cand.mkdir()
    calls: list[Path] = []

    def probe(p: Path, **kw: object) -> float:
        calls.append(Path(p))
        return 100.0 if Path(p).parent == tmp_path else 110.0  # job 目录 100、候选 110（1.1×）

    monkeypatch.setattr(scratch, "probe_write_speed_mbps", probe)
    assert (
        scratch.resolve_scratch_root(
            tmp_path / "job", need_bytes=1, candidates=(str(cand),), log=lambda _m: None
        )
        is None
    ), "不显著快就该回退"
    assert calls, "速度闸必须真的测过（否则它是个摆设）"


def test_resolve_falls_back_to_none_when_nothing_qualifies(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fast_probe(monkeypatch)
    assert (
        scratch.resolve_scratch_root(tmp_path / "job", need_bytes=1, candidates=()) is None
    ), "没有可用候选 ⇒ None（调用方逐字走今天的行为）"


def test_generic_tests_pin_the_fallback_path(tmp_path: Path) -> None:
    """通用用例必须**确定性**走回退档 —— 机器速度/负载不许决定走哪一档（2026-10-09 门禁红）。

    现场：安静时 /dev/shm 只比 job 目录快 ~2.6×（回落，绿）；`bun run pygate` 8 worker 满载时
    同一台机器测出 ≥5×（换根）⇒「假导出器把 shard 平铺在 `--out` 之外」的那批用例转红。
    钉住它的就是根 conftest 的 autouse `_scratch_off`（本用例是那条钉子的回归守卫）。
    同时钉住「没候选就**连基准探针都不跑**」：回退档不该为一次注定 None 的决策写 8MB。
    """
    assert scratch.SCRATCH_CANDIDATES == (), "缺 pin：见 nn-training/conftest.py::_scratch_off"

    def boom(*a: object, **kw: object) -> float:
        raise AssertionError("回退档不该跑探针（runtime-opt §34.2：连候选探针都不跑）")

    job = tmp_path / "job"
    job.mkdir()
    assert scratch.resolve_scratch_root(job, need_bytes=1, probe=boom) is None


def test_env_override_wins_even_with_no_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """操作员显式指定 ⇒ 无条件用它（连速度闸都不纠正：那是他的决定）。"""
    forced = _local_root(tmp_path) / "forced"
    forced.mkdir()
    monkeypatch.setenv(scratch.SCRATCH_ENV, str(forced))
    monkeypatch.setattr(scratch, "probe_write_speed_mbps", lambda p, **kw: 0.0)
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


def test_rollout_reroots_to_local_scratch_and_drains_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """换根档全链路：产出写在本地盘 → 结算一局搬回一局 → job 目录形状与今天逐字一致。"""
    _watchdog(monkeypatch)
    local = _local_root(tmp_path)
    _only_candidate(monkeypatch, local)
    job = _job_dir(tmp_path)
    msgs: list[str] = []
    out = run_iter_rollout(job, _stub_spec(tmp_path, [(0, 0), (1, 1)]), log=msgs.append)
    layout = scratch.layout_for(local, job)

    # ① 搬回后的形状 = 今天的形状（`scan_shard_dirs` / `collect_reports` / `data_fp` 零改动）
    assert sorted(p.name for p in scan_shard_dirs(job)) == ["rl_s0_seed0", "rl_s1_seed1"]
    assert out["report"]["shards"] == 2
    for i, (st, sd) in enumerate([(0, 0), (1, 1)]):
        assert (job / f"w{i}" / f"rl_s{st}_seed{sd}" / "manifest.json").exists()
        # F9②：`rollout.log` 是诊断现场（`_first_rollout_log_tail` 靠它）⇒ 直接断言它在
        assert (job / f"w{i}" / "rollout.log").exists()
    # ② 换根真的生效了：桩把 `--weights` 打了出来，它必须在 scratch 里（= 镜像 + 再绝化）
    tail = (job / "w0" / "rollout.log").read_text(encoding="utf-8")
    assert str(layout.exec_dir) in tail, tail
    # ③ 读数可见（P1-1）
    assert any("产出落点" in m and "本地盘" in m for m in msgs), msgs
    assert any("搬回=2 局/失败 0 局" in m for m in msgs), msgs
    # ④ 轮末清自己那一份 + 暂存根回收
    if list(layout.exec_dir.iterdir()) and sandbox_delete_blocked(tmp_path):
        pytest.skip(_SKIP_BLOCKED)
    assert not list(layout.exec_dir.iterdir()), "轮末要把本地盘上的镜像清掉"
    assert layout.ts_for(Path(str(layout.job)).name) is not None  # ts 家不受影响（内容寻址）
    assert not scratch.drain_stage_root(job).exists(), "暂存根也要回收（否则会被 prune 当 job）"
    assert list(local.glob(f"{scratch.SCRATCH_TS_PREFIX}*")), "TS 家不清（跨轮复用）"


def test_rollout_falls_back_byte_for_byte_when_no_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F9：候选链全不过 ⇒ **逐字**走今天的行为（不搬、不 drain、不换根、不留暂存）。"""
    _watchdog(monkeypatch)
    monkeypatch.setattr(scratch, "SCRATCH_CANDIDATES", ())
    job = _job_dir(tmp_path)
    msgs: list[str] = []
    out = run_iter_rollout(job, _stub_spec(tmp_path, [(0, 0)]), log=msgs.append)
    assert out["report"]["shards"] == 1
    assert (job / "w0" / "rl_s0_seed0" / "manifest.json").exists()
    assert any("回退原地" in m for m in msgs), msgs
    assert not any("搬回=" in m for m in msgs), "回退档没有搬回这一说"
    assert not scratch.drain_stage_root(job).exists()
    assert not list(tmp_path.glob(f"{scratch.SCRATCH_DIR_PREFIX}*")), "回退档不建 scratch"


def test_rollout_drain_failure_is_not_counted_as_settled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """F7：搬不回去的局**不算结算**（并入 stuck ⇒ 重投补它），且报一行搬回失败。"""
    _watchdog(monkeypatch)
    local = _local_root(tmp_path)
    _only_candidate(monkeypatch, local)
    job = _job_dir(tmp_path)
    mark = tmp_path / "runs.txt"
    real_drain = scratch.drain_tree
    calls = {"n": 0}

    def flaky(
        src: Path, dst: Path, *, ticket: scratch.InstallTicket | None = None, log: Any = None
    ) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:
            return False  # 第一次搬回失败（票被作废的形状）
        return bool(real_drain(src, dst, ticket=ticket, log=log))

    monkeypatch.setattr(scratch, "drain_tree", flaky)
    msgs: list[str] = []
    out = run_iter_rollout(job, _stub_spec(tmp_path, [(0, 0)], mark=mark), log=msgs.append)
    assert calls["n"] == 2
    assert mark.read_text(encoding="utf-8").count("run") == 2, "这一局必须被重投（= 真重跑了一次）"
    assert any("搬回失败" in m and "s0/d0" in m for m in msgs), msgs
    assert any("整轮重投第 1 次" in m for m in msgs), msgs
    assert any("搬回=1 局/失败 1 局" in m for m in msgs), msgs
    assert out["report"]["shards"] == 1, "最终还是要有一局产出"


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
    assert any("本地盘" in m for m in msgs), msgs


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
