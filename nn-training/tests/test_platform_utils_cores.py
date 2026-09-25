"""test_platform_utils_cores.py —— 「本机到底有多少核」的单一口径（`platform_utils.effective_cores`）。

为什么单列一个文件（2026-09-25 云机卡死取证）：容器里的 `os.cpu_count()` 报的是**宿主机**的
逻辑核数，不是配额——Kaggle 的 TPU 会话报 224，而 cgroup 只给 **96** 核 ⇒ `cpu_worker_slots()`
算出 220 ⇒ 光 rollout 一条腿就 2.3× 超订（再加一条 eval 腿就是 4.6×），单局墙钟被推过 5s
硬顶 ⇒ 成批超时 + 池回退放大 ⇒ 整轮停摆。物理数目只能按「配额 / 亲和掩码」取小来定，
`os.cpu_count()` 只配当最后的兜底。

本文件只钉三件事：① cgroup v2/v1 的配额怎么换算成核数（含 `max`/`-1` = 不限）；② 三个来源
的优先级（取小）+ 都缺失时回落 `os.cpu_count()`；③ `cpu_worker_slots` 真的走这条口径。
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import platform_utils as pu


def _fake_fs(files: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    """把 `_read_text` 换成内存字典（不碰真文件系统；缺失路径读不到 = None）。"""

    def read(path: str) -> str | None:
        return files.get(path)

    monkeypatch.setattr(pu, "_read_text", read)


def test_cgroup_v2_quota_converts_to_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 的 `cpu.max` = `"<quota> <period>"`（微秒）⇒ 核数 = quota ÷ period。"""
    _fake_fs({pu._CGROUP_V2_CPU_MAX: "9600000 100000"}, monkeypatch)
    assert pu.cgroup_cpu_quota() == 96

    # 有余数用**向上取整**（2.5 核必须算 3 核：按 2 核算仍然超订）
    _fake_fs({pu._CGROUP_V2_CPU_MAX: "250000 100000"}, monkeypatch)
    assert pu.cgroup_cpu_quota() == 3

    # `max 100000` = 不限 ⇒ 不是事实，交给下一个来源（这里 v1 也缺）
    _fake_fs({pu._CGROUP_V2_CPU_MAX: "max 100000"}, monkeypatch)
    assert pu.cgroup_cpu_quota() is None


def test_cgroup_v1_quota_converts_to_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    """v1：`cpu.cfs_quota_us` ÷ `cpu.cfs_period_us`；period 缺失按内核缺省 100000µs。"""
    _fake_fs(
        {pu._CGROUP_V1_QUOTA: "4800000", pu._CGROUP_V1_PERIOD: "100000"}, monkeypatch
    )
    assert pu.cgroup_cpu_quota() == 48

    _fake_fs({pu._CGROUP_V1_QUOTA: "300000"}, monkeypatch)  # 缺 period ⇒ 3 核
    assert pu.cgroup_cpu_quota() == 3

    # `-1` = 不限；坏值（非整数）也不算事实（调用方回落到别的来源，而不是抛）
    _fake_fs({pu._CGROUP_V1_QUOTA: "-1"}, monkeypatch)
    assert pu.cgroup_cpu_quota() is None
    _fake_fs({pu._CGROUP_V1_QUOTA: "lots"}, monkeypatch)
    assert pu.cgroup_cpu_quota() is None
    # v2 是 max（不限）时**必须继续看 v1**（两种 cgroup 混挂的镜像真的存在）
    _fake_fs(
        {
            pu._CGROUP_V2_CPU_MAX: "max 100000",
            pu._CGROUP_V1_QUOTA: "2000000",
            pu._CGROUP_V1_PERIOD: "100000",
        },
        monkeypatch,
    )
    assert pu.cgroup_cpu_quota() == 20


def test_effective_cores_takes_the_smallest_signal_then_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配额与亲和掩码**取小**；两者都读不到才回落 `os.cpu_count()`（Windows/裸机）。"""
    monkeypatch.setattr(pu, "cgroup_cpu_quota", lambda: 96)
    monkeypatch.setattr(pu, "affinity_cores", lambda: 224)  # 宿主机报 224、配额只给 96
    assert pu.effective_cores() == 96

    monkeypatch.setattr(pu, "cgroup_cpu_quota", lambda: None)
    monkeypatch.setattr(pu, "affinity_cores", lambda: 8)
    assert pu.effective_cores() == 8

    monkeypatch.setattr(pu, "affinity_cores", lambda: None)
    monkeypatch.setattr(pu.os, "cpu_count", lambda: 224)
    assert pu.effective_cores() == 224, "两条事实都没有时才信 os.cpu_count()"

    # 坏读数（0）不得把核数算成 0（除零/零并发会当场卡死）
    monkeypatch.setattr(pu.os, "cpu_count", lambda: None)
    assert pu.effective_cores() == 1


def test_affinity_cores_is_none_when_the_platform_lacks_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没有 `sched_getaffinity`（Windows）⇒ None（不是异常、也不是冒充一个核数）。"""
    monkeypatch.delattr(os, "sched_getaffinity", raising=False)
    assert pu.affinity_cores() is None


def test_cpu_worker_slots_uses_the_effective_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    """缺省走 `effective_cores()`：96 核配额 ⇒ 92（**不是**按宿主机的 224 算成 220）。"""
    monkeypatch.setattr(pu, "effective_cores", lambda: 96)
    assert pu.cpu_worker_slots() == 92

    # 显式给数仍然完全按它走（本函数只管缺省）
    monkeypatch.setattr(pu, "effective_cores", lambda: 96)
    assert pu.cpu_worker_slots(40) == 36
