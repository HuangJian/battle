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


def _racy_scandir(monkeypatch: pytest.MonkeyPatch, victim: Path) -> list[str]:
    """在遍历**刚列出、即将下钻** `victim` 的那一刻 rmtree 它——事故的精确时序。

    返回钩子实际拦到的 scandir 实参（路径字符串），供自证用例断言「钩子真的生效」。

    事故栈是 `pathlib.py:440  with scandir(parent_path) as scandir_it:` 且报错路径就是
    一个 **shard 目录**（被下钻的那层），所以交错点必须钉在「scandir 的实参 = 受害目录」
    上（首版钉在父目录“列表”那一刻 ⇒ 受害目录从列表里消失但 scandir 不报错 ⇒ 钩子
    静默失效、旧实现也“不抛”）。

    **绑定点随 Python 版本变，必须钉住 pathlib / os 真正用来扫目录的那一层**：
    - ≤3.11：`pathlib._NormalAccessor` / `_Accessor` 类属性锁存 `os.scandir`
      （`pathlib.py:290  scandir = os.scandir`），只改 os 模块不够。
    - ≥3.12：accessor 类已删；`Path._scandir()` 在**调用时**读 `os.scandir(self)`
      （`pathlib.py:1059`），glob 选择器拿的是 `path_cls._scandir`。这里显式改
      `Path._scandir` + `os.scandir`，避免将来再出现锁存时钩子静默失效。
      同版本 `_WildcardSelector._select_from` 对 scandir 的 `except OSError: pass`
      （pathlib.py:206）——pathlib 自身已并发容忍，故**不能再**用「rglob 必抛」
      做钩子自证（那会变成假红）；自证改为断言拦截痕迹 + 删除时刻 + 后续 ENOENT。
    """
    real_scandir = os.scandir
    # rmtree 内部走 os.walk → os.scandir；若钩子在删除中再次进入且又 rmtree，会无限
    # 递归（Py3.12 起 Path._scandir 也打在同一钩子上，实测 RecursionError）。用旗标
    # 只在**首次**命中受害目录时删除，删除过程中的 scandir 一律走 real_scandir。
    deleting = False
    seen: list[str] = []

    def fake_scandir(path=".", *a, **kw):
        nonlocal deleting
        # path 也可能是 int fd（shutil.rmtree 内部走 scandir(fd)）——只认路径形态。
        if isinstance(path, (str, bytes, os.PathLike)):
            seen_path = os.path.abspath(os.fspath(path))
            if seen_path == str(victim):
                seen.append(seen_path)
                if not deleting:
                    deleting = True
                    try:
                        shutil.rmtree(victim, ignore_errors=True)  # 幂等
                    finally:
                        deleting = False
        return real_scandir(path, *a, **kw)  # 受害目录已不在 ⇒ FileNotFoundError(victim)

    monkeypatch.setattr(os, "scandir", fake_scandir)

    patched: list[str] = ["os.scandir"]
    # Python ≤3.11：accessor 是**实例**属性访问，普通函数会被绑成方法（多一个 self
    # 实参，真 scandir 收不住）。staticmethod 保持与 os.scandir 同形（首参即路径）。
    acc = getattr(pathlib, "_NormalAccessor", None) or getattr(pathlib, "_Accessor", None)
    if acc is not None:
        monkeypatch.setattr(acc, "scandir", staticmethod(fake_scandir))
        patched.append(getattr(acc, "__name__", type(acc).__name__))

    # Python ≥3.12（及防御未来锁存）：Path._scandir 是 glob 真正拿走的绑定。
    if hasattr(pathlib.Path, "_scandir"):
        def _fake_path_scandir(self: pathlib.Path):
            return fake_scandir(self)

        monkeypatch.setattr(pathlib.Path, "_scandir", _fake_path_scandir)
        patched.append("Path._scandir")

    # 至少要有一处 pathlib 侧绑定；只有 os.scandir 时若 pathlib 又开始锁存，
    # 钩子会静默失效（假绿）。3.12 起 _scandir 动态读 os.scandir，故 os.scandir
    # 单独也可能有效——但 Path._scandir 必须可打补丁，否则说明内部结构又变了。
    assert hasattr(pathlib.Path, "_scandir") or acc is not None, (
        f"找不到 pathlib 的 scandir 绑定点（_NormalAccessor/_Accessor/_scandir 全无；"
        f"已尝试 {patched}）——钩子失去意义，请更新本测试，"
        "不要把「钩子失效」伪装成「测试通过」"
    )
    return seen


def test_hook_intercepts_scandir_and_deletes_victim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """钩子有效性自证：`scandir(victim)` 必须被拦到，且拦截瞬间删掉 victim。

    有了这条，下面几条的绿色就不可能是「钩子没生效」的假绿。

    Py3.12 起 `_WildcardSelector._select_from` 对 scandir 的 OSError 直接 `pass`
    （pathlib.py:206），**pathlib 自身已并发容忍**——旧版「Path.rglob 必抛
    FileNotFoundError」自证会变成假红（钩子在删、rglob 却不抛）。故自证改为钉住
    钩子的可观察效果：① 看到了 victim 这个实参；② 那一刻目录已删；③ 紧接着对
    victim 的真实 scandir 抛 FileNotFoundError（os.walk onerror=None 会跳过它）。
    """
    traj = tmp_path / "it9"
    _write_shard(traj, "w0", 0, 111)
    victim = _write_shard(traj, "w2", 2, 333)
    seen = _racy_scandir(monkeypatch, victim)

    with pytest.raises(FileNotFoundError) as ei:
        os.scandir(victim)
    assert str(victim) in str(ei.value) or victim.name in str(ei.value), (
        f"报错应指向被退役的下钻目录（got {ei.value}）"
    )
    assert str(victim) in seen, f"钩子未拦到 scandir(victim)（seen={seen}）——钩子失效"
    assert not victim.exists(), "拦截后受害目录应已被删除"

    # 同一钩子下 Path.rglob 在 3.12+ 可能吞掉 OSError（并发容忍）——不强制抛错，
    # 但也不该再把已删的 victim 报出来当结果。
    hits = [p for p in traj.rglob("rl_s*_seed*/manifest.json")]
    assert all(victim.name not in str(p) for p in hits), f"已删目录不应再出现在结果里（{hits}）"


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
    # 签名用 `str(relative_to)`，Windows 是反斜杠；断言侧归一成 `/`（缓存 key 本机自洽即可）。
    names = {rel.replace("\\", "/") for rel, _mt in _dir_signature(traj)}
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
