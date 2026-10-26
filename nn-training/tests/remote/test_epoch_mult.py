"""test_epoch_mult —— 云机（pull/push notebook）的训练侧放大：PPO 每轮更新遍数 × N。

## 为什么只放大 epochs（用户口径 2026-10-26）

离线盘已有 `CFG.sample_mult`（采样量）+ `CFG.epoch_mult`（更新遍数）两个旋钮；云机
notebook 这一轮**只要 `epoch_mult`**：高配机上「同一份 rollout 多重训几遍」不改采样量、
不改评估语料、不改关卡/命数 ⇒ 与教师/课程口径都不打架，指标仍可比。`sample_mult`
故意不接（用户明确否掉）：它改的是训练语料本身，和 hub 的对账/可复现性纠缠更深。

## 本文件钉住什么

1. 语义（纯函数 `effective_epochs`）：`ceil(epochs × N)`、最少 1；**1.0 = 原样**；
   `epochs <= 0` 不放大（不许凭空造出训练量）；
2. 取值校验（`epoch_mult_of`）：有限正数，坏值**开跑前**拒绝（不静默当 1.0）；
3. 两条起点都真的把 `--epoch-mult` 交给子进程：pull 的 `worker_restart_argv`（监督器
   重启也带）与 push 的 `spawn_full_worker_server`；**缺省时 argv 逐字不变**（旧部署
   不受影响——这是非目标守卫）。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from remote import notebook_runtime as nbr
from remote import push_bootstrap as pb
from remote.train_core import effective_epochs
from remote.worker import epoch_mult_of

# ------------------------------------------------------------------ 语义


def test_default_mult_keeps_the_manifest_epochs() -> None:
    """1.0 = 课程/manifest 原样（老路径逐字不变）。"""
    assert effective_epochs(4, 1.0) == 4
    assert effective_epochs(0, 1.0) == 0


def test_mult_scales_epochs_and_rounds_up() -> None:
    """离线盘同一条口径：向上取整（1.5 × 3 = 4.5 ⇒ 5），不四舍五入。"""
    assert effective_epochs(4, 2.0) == 8
    assert effective_epochs(3, 1.5) == 5
    assert effective_epochs(3, 1.1) == 4


def test_mult_never_produces_zero_epochs() -> None:
    """小于 1 的倍数（0.5）最少仍跑 1 遍；但 epochs=0 仍是 0（非 PPO 腿不造训练量）。"""
    assert effective_epochs(3, 0.5) == 2  # ceil(1.5)
    assert effective_epochs(1, 0.5) == 1  # ceil(0.5) = 1
    assert effective_epochs(0, 3.0) == 0


# ------------------------------------------------------------------ 取值校验


def test_epoch_mult_accepts_positive_numbers_and_numeric_strings() -> None:
    assert epoch_mult_of(1) == 1.0
    assert epoch_mult_of(2.5) == 2.5
    assert epoch_mult_of("1.5") == 1.5


@pytest.mark.parametrize("bad", [0, -1, -0.5, float("nan"), float("inf"), "abc", "", None, True])
def test_epoch_mult_rejects_bad_values(bad: object) -> None:
    """坏值必须**响亮拒绝**：静默当 1.0 = 操作员以为放大了、实际没放大。"""
    with pytest.raises(ValueError):
        epoch_mult_of(bad)


# ------------------------------------------------------------------ argv：pull 腿


def _pull_cfg(**over: Any) -> dict[str, Any]:
    cfg: dict[str, Any] = {
        "hub_url": "http://hub.invalid",
        "device_resolved": "cpu",
        "poll_interval_sec": 5,
    }
    cfg.update(over)
    return cfg


def test_pull_restart_argv_carries_epoch_mult_only_when_configured(tmp_path: Path) -> None:
    """监督器重启也要带同一个倍数（首启与重启同 argv）；缺省时逐字旧形状。"""
    kw: dict[str, Any] = dict(work_dir=tmp_path / "work", token_file=tmp_path / "token", max_idle=3600)
    plain = nbr.worker_restart_argv(_pull_cfg(), **kw)
    assert plain.count("--epoch-mult") == 0

    scaled = nbr.worker_restart_argv(_pull_cfg(epoch_mult=2.0), **kw)
    assert scaled[scaled.index("--epoch-mult") + 1] == "2"

    frac = nbr.worker_restart_argv(_pull_cfg(epoch_mult="1.5"), **kw)
    assert frac[frac.index("--epoch-mult") + 1] == "1.5"

    with pytest.raises(ValueError):
        nbr.worker_restart_argv(_pull_cfg(epoch_mult=0), **kw)


# ------------------------------------------------------------------ argv：push 腿


def test_push_spawn_carries_epoch_mult_only_when_configured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """push 的完整 worker_server 从 spawn 起就带着倍数（不在 job 里二次配置）。"""
    seen: list[list[str]] = []

    class _Proc:
        pid = 4242

    def fake_popen(argv: list[str], **_kw: Any) -> _Proc:
        seen.append(list(argv))
        return _Proc()

    monkeypatch.setattr(pb.subprocess, "Popen", fake_popen)
    kw: dict[str, Any] = dict(
        port=1234,
        token="tok",
        work_dir=tmp_path,
        device="cpu",
        code_dir=tmp_path,
        log_path=tmp_path / "serve.log",
    )
    pb.spawn_full_worker_server(**kw, epoch_mult=2.0)
    assert seen[-1][seen[-1].index("--epoch-mult") + 1] == "2"

    pb.spawn_full_worker_server(**kw)
    assert seen[-1].count("--epoch-mult") == 0
