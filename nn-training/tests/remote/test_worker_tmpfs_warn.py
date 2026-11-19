"""`--out` 落在 tmpfs 上的体检（2026-11-16，用户提问引出）。

背景：`--out` 下面住着 kind=ppo 的 payload **和重建出来的 `obs.npy`**（35×20 实测 3.97 GB），
那是训练期**热数据**。tmpfs 的页**不可回收**，等于从 torch 的内存预算里硬扣；普通盘上的
page cache 则随时能被内核还回来。对照 `common/scratch.py`：tmpfs 只该给 rollout/eval 那种
「写-读-丢」的短命 scratch，不该长期钉住体积巨大的训练语料。

判定实现在 `common/scratch.py`（tmpfs/scratch 政策的唯一归属），生产调用点是
`remote/notebook_runtime.py` 的 work_dir 落点。本文件只测判定逻辑：真实挂载表由
`/proc/mounts` 提供，这里注入文本，Windows 上也能跑。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.scratch import on_tmpfs, warn_if_tmpfs

MOUNTS = """\
/dev/root / ext4 rw,relatime 0 0
tmpfs /dev/shm tmpfs rw,nosuid,nodev 0 0
/dev/sda1 /tmp xfs rw,relatime 0 0
tmpfs /run tmpfs rw,nosuid 0 0
"""


def test_detects_tmpfs_and_plain_disk() -> None:
    assert on_tmpfs(Path('/dev/shm/battle/tmp/remote-worker'), mounts_text=MOUNTS, name='posix')
    assert not on_tmpfs(Path('/tmp/remote-worker'), mounts_text=MOUNTS, name='posix')
    assert not on_tmpfs(Path('/var/lib/worker'), mounts_text=MOUNTS, name='posix')


def test_picks_the_longest_matching_mount() -> None:
    """`/tmp` 是 xfs、只有 `/run` 是 tmpfs ⇒ 不能被父级 tmpfs 误判。"""
    mounts = MOUNTS + 'tmpfs /run/lock tmpfs rw 0 0\n'
    assert on_tmpfs(Path('/run/lock/x'), mounts_text=mounts, name='posix')
    assert on_tmpfs(Path('/run/other/x'), mounts_text=mounts, name='posix')
    assert not on_tmpfs(Path('/tmp/x'), mounts_text=mounts, name='posix')


def test_escaped_space_in_mount_point() -> None:
    """`/proc/mounts` 把路径空格写成 `\\040`。"""
    mounts = 'tmpfs /mnt/my\\040shm tmpfs rw 0 0\n'
    assert on_tmpfs(Path('/mnt/my shm/job'), mounts_text=mounts, name='posix')


def test_non_posix_never_reports_tmpfs() -> None:
    """Windows 上没有 `/proc/mounts`，判定恒 False（不许因为读不到就瞎报）。"""
    assert not on_tmpfs(Path('/dev/shm/x'), mounts_text=MOUNTS, name='nt')


def test_missing_proc_mounts_is_quiet() -> None:
    """读不到挂载表 ⇒ False，不抛（worker 启动路径不能被体检拖死）。"""
    assert not on_tmpfs(Path('/definitely/not/here'), name='posix')


def test_warn_if_tmpfs_says_path_when_hit(monkeypatch) -> None:
    """命中就喊一行、带上路径；不命中一个字都不打（别用 WARN 污染正常路径）。"""
    from common import scratch as cs

    got: list[str] = []
    monkeypatch.setattr(cs, 'on_tmpfs', lambda _p: True)
    assert cs.warn_if_tmpfs(Path('/dev/shm/battle/tmp/remote-worker'), say=got.append) is True
    assert len(got) == 1, '命中也只能喊一行（别刷屏）'

    got.clear()
    monkeypatch.setattr(cs, 'on_tmpfs', lambda _p: False)
    assert cs.warn_if_tmpfs(Path('/tmp/remote-worker'), say=got.append) is False
    assert got == []
