"""`rl/resume.py` 磁盘对账的**并发删除安全**（2026-09-20 门禁事故回归）。

事故现场（e2e `-n 12`，栈完整）：

    rl/dispatch.py:1121 in run → resumed_manifests(...)
    rl/resume.py:248 in resumed_manifests → traj_dir.rglob("rl_s*_seed*/manifest.json")
    pathlib.py:440 in _select_from → scandir(parent_path)
    FileNotFoundError: [Errno 2] No such file or directory: '…/i9/dist/fake/rl_s0_seed111'
    ⇒ RuntimeError: stream collector failed: …

机制：dup-settle 输家退场由**dispatch 结算线程**执行（`rmtree` 它自己的 shard 目录），
与主线程的磁盘对账同轮并发；`Path.rglob` 遍历到某一层时 `scandir` 抛 ENOENT，而该异常
发生在 **for 语句的迭代**里（不在循环体内），`resumed_manifests` 的 try 只裹了
`read_text` ⇒ 整轮红。症状特征是「No such file or directory + 一个**目录**路径」。

修法：`walk_shard_dirs()` 改用 `os.walk`（scandir 失败按 onerror=None 跳过该层）。
本文件用一个**确定性**钩子复现：在 `os.scandir` 被调用到受害目录的那一刻把它删掉
（就是真实竞态的时序），断言遍历不抛、其余 shard 照常对账。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
from pathlib import Path

import pytest

from rl.resume import (
    MANIFEST_NAME,
    _dir_signature,
    _scan_shards,
    completed_pairs,
    resumed_manifests,
    walk_shard_dirs,
)

WVER = "wv-race"


def _write_shard(root: Path, wave: str, stage: int, seed: int, wver: str = WVER) -> Path:
    """一局一目录的最小完整 shard（要有 manifest.json 才算「完成」）。"""
    d = root / wave / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    (d / MANIFEST_NAME).write_text(
        json.dumps({"wver": wver, "stage": stage, "seed": seed, "nSamples": 30}),
        encoding="utf-8",
    )
    return d


def _racy_scandir(monkeypatch: pytest.MonkeyPatch, victim: Path) -> None:
    """在遍历**刚列出、即将下钻** `victim` 的那一刻 rmtree 它——事故的精确时序。

    事故栈是 `pathlib.py:440  with scandir(parent_path) as scandir_it:` 且报错路径就是
    一个 **shard 目录**（被下钻的那层），所以交错点必须钉在「scandir 的实参 = 受害目录」
    上（首版钉在父目录“列表”那一刻 ⇒ 受害目录从列表里消失但 scandir 不报错 ⇒ 钩子
    静默失效、旧实现也“不抛”）。

    **两个绑定点都要补**：pathlib 在类属性里锁存了 `os.scandir`（cpython 3.10
    `pathlib.py:290  scandir = os.scandir`），只改 os 模块的话 `Path.rglob` 走的仍是老函数。
    """
    real_scandir = os.scandir

    def fake_scandir(path=".", *a, **kw):
        # path 也可能是 int fd（shutil.rmtree 内部走 scandir(fd)）——只认路径形态。
        if isinstance(path, (str, bytes, os.PathLike)) and (
            os.path.abspath(os.fspath(path)) == str(victim)
        ):
            shutil.rmtree(victim, ignore_errors=True)  # 幂等：多扫几次也只删一次
        return real_scandir(path, *a, **kw)  # 受害目录已不在 ⇒ FileNotFoundError(victim)

    monkeypatch.setattr(os, "scandir", fake_scandir)
    acc = getattr(pathlib, "_NormalAccessor", None) or getattr(pathlib, "_Accessor", None)
    assert acc is not None, (
        "找不到 pathlib 的 accessor 类（内部结构变了）——钩子失去意义，请更新本测试，"
        "不要把「钩子失效」伪装成「测试通过」"
    )
    # staticmethod：accessor 是**实例**属性访问，普通函数会被绑成方法（多一个 self 实参，
    # 真 scandir 收不住）。staticmethod 保持与 os.scandir 同形（首参即路径）。
    monkeypatch.setattr(acc, "scandir", staticmethod(fake_scandir))


def test_hook_makes_old_rglob_raise(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """钩子有效性自证（也是事故机制的锚）：同一时序下**旧实现 Path.rglob 必抛**。

    有了这条，上面几条的绿色就不可能是「钩子没生效」的假绿；若将来某个 Python 版本的
    pathlib 变成并发容忍，这条会红——那时本文件的 guard 就已完成使命，可以删掉。
    """
    traj = tmp_path / "it9"
    _write_shard(traj, "w0", 0, 111)
    victim = _write_shard(traj, "w2", 2, 333)
    _racy_scandir(monkeypatch, victim)

    with pytest.raises(FileNotFoundError) as ei:
        for _m in traj.rglob("rl_s*_seed*/manifest.json"):
            pass
    assert str(victim) in str(ei.value), f"报错应指向被退役的下钻目录（got {ei.value}）"


def test_walk_shard_dirs_survives_dir_deleted_mid_walk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """遍历中途目录被删 ⇒ 跳过它，其余照常返回（不抛）。"""
    traj = tmp_path / "it9"
    keep_a = _write_shard(traj, "w0", 0, 111)
    keep_b = _write_shard(traj, "w1", 1, 222)
    victim = _write_shard(traj, "w2", 2, 333)
    _racy_scandir(monkeypatch, victim)

    got = {d.name for d in walk_shard_dirs(traj, with_manifest=True)}
    assert got == {keep_a.name, keep_b.name}, f"被删的输家目录应跳过、其余保留（got {got}）"


def test_resumed_manifests_tolerates_concurrent_retire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """事故回归：`resumed_manifests` 的遍历撞上输家退场不得让整轮红。"""
    traj = tmp_path / "it9"
    keep_a = _write_shard(traj, "w0", 0, 111)
    keep_b = _write_shard(traj, "w1", 1, 222)
    victim = _write_shard(traj, "w2", 2, 333)
    _racy_scandir(monkeypatch, victim)

    out = resumed_manifests(traj, WVER, exclude={(9, 9)}, only={(0, 111), (1, 222), (2, 333)})
    got = {(int(r["stage"]), int(r["seed"])) for r in out}
    assert got == {(0, 111), (1, 222)}, f"输家副本不该进报告，其余照常（got {got}）"
    assert keep_a.is_dir() and keep_b.is_dir()


def test_scan_shards_and_dir_signature_tolerate_concurrent_retire(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同一竞态的两个同族调用点：`_scan_shards`（completed_pairs）与 `_dir_signature`。"""
    traj = tmp_path / "it9"
    _write_shard(traj, "w0", 0, 111)
    _write_shard(traj, "w1", 1, 222)
    victim = _write_shard(traj, "w2", 2, 333)
    _racy_scandir(monkeypatch, victim)

    pairs = completed_pairs(traj, WVER)
    assert pairs == {(0, 111), (1, 222)}, f"completed_pairs 应跳过被删目录（got {pairs}）"
    # 签名缓存口径：被删目录不入签名；其余两个都在。
    names = {rel for rel, _mt in _dir_signature(traj)}
    assert names == {"w0/rl_s0_seed111", "w1/rl_s1_seed222"}, f"签名口径不符（got {names}）"


def test_scan_shards_still_filters_wver_and_completeness(tmp_path: Path) -> None:
    """行为不变式（防修法顺手改口径）：wver 不符 / 缺 manifest 的目录一律不算完成。"""
    traj = tmp_path / "it9"
    _write_shard(traj, "w0", 0, 111)
    _write_shard(traj, "w1", 1, 222, wver="other-wver")
    half = traj / "w2" / "rl_s2_seed333"
    half.mkdir(parents=True)  # 半个目录：没有 manifest.json

    assert completed_pairs(traj, WVER) == {(0, 111)}
    # walk_shard_dirs 只管「目录名 + manifest 存在」，wver 过滤属于 _scan_shards 层：
    # 有 manifest 的两个（含 wver 不符的那个）都在，缺 manifest 的半个目录不在。
    assert {p.name for p in walk_shard_dirs(traj, with_manifest=True)} == {
        "rl_s0_seed111",
        "rl_s1_seed222",
    }
    assert {p.name for p in walk_shard_dirs(traj)} == {
        "rl_s0_seed111",
        "rl_s1_seed222",
        "rl_s2_seed333",
    }
    assert _scan_shards(traj, WVER) == [((0, 111), traj / "w0" / "rl_s0_seed111")]
