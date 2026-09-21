"""单进程服务器（`--serve`）的两条 2026-09-19 加固（R3-5：trainer 收敛为一个进程）。

背景：控制台过去**每门课**起一个 trainer 进程，于是「这门课怎么连云端」用该进程的命令行
表达（`--remote-transport pull` / `--remote-hub-url` / `--remote-degrade-after` /
`--gate-halt-mode`）。收敛成**一个进程服务所有课程**之后，命令行只有一份 —— 那些旋钮
必须换成**按课程的机器侧配置**（`rl-config.json → courses.<课>.*`）。两件事在这里钉住：

1. **课程机器侧覆盖**（`apply_course_machine_overrides`）：白名单、值域校验、缺字段跳过、
   非白名单键一个字不碰；且**读取点单一**（`_read_rl_config` 是本模块唯一的 rl-config 读）。
2. **进程级单实例锁**（`acquire_cluster_lock` / `release_cluster_lock`）：一个进程服务所有课程
   ⇒ 双开 = 两套调度器抢同一批 traj（按课锁拦不住这一类）。

为什么不写进 `curricula/<课>.jsonc`：课程文件字节 = `course_fp`（语料血缘 / 熔断口径，D14）——
往里加一个传输旋钮，熔断会把同一份语料读成新语料。
"""

from __future__ import annotations

import os
from argparse import Namespace
from pathlib import Path
from typing import Any

import pytest

from rl import loop_serve


def _args(**kw: Any) -> Namespace:
    """最小 args（含覆盖白名单里的字段 + 几个不该被碰的字段）。"""
    base: dict[str, Any] = {
        "remote_transport": "auto",
        "remote_hub_url": "",
        "gate_halt_mode": "halt",
        "push_node_url": "",
        "workers": 8,
    }
    base.update(kw)
    return Namespace(**base)


#: 课程块里**不许**再被读的传输耦合键（2026-09-19：课程任务与 worker 节点正交）。
COUPLING_KEYS = ("remote_transport", "remote_hub_url", "push_node_url", "hub_push")


# ---------------------------------------------------------------- 机器侧覆盖


def test_overlay_applies_only_the_whitelisted_keys() -> None:
    """白名单键（只有两个训练策略旋钮）生效；其余键（含传输耦合）一个字不碰。"""
    lines: list[str] = []
    args = _args()
    cfg = {
        "courses": {
            "c5-tick": {
                "gate_halt_mode": "skip",
                # ↓ 2026-09-19 起**已不是**被读的键（课程与节点正交）；留着不报错也不生效
                "remote_transport": "pull",
                "remote_hub_url": "http://127.0.0.1:8789",
                "push_node_url": "https://gpu.example",
                "hub_push": True,
                "workers": 4,
            }
        }
    }
    applied = loop_serve.apply_course_machine_overrides(args, "c5-tick", cfg, log_fn=lines.append)
    assert applied == ["gate_halt_mode"]
    assert args.gate_halt_mode == "skip"
    # 非白名单键：**没有**被 setattr（它们属于别的读者，或者已无读者）
    assert args.remote_transport == "auto"
    assert args.remote_hub_url == ""
    assert args.push_node_url == ""
    assert args.workers == 8
    assert not hasattr(args, "hub_push")
    # 生效值必须上屏（静默改写执行面 = 「看起来正常」那类事故）
    assert any("c5-tick" in ln and "gate_halt_mode='skip'" in ln for ln in lines)


def test_overlay_is_inert_without_the_course_block() -> None:
    """没有 `courses.<课>` 块（今天真机的常态）⇒ 逐字段不变、一行日志都不多。"""
    args = _args(remote_transport="push")
    lines: list[str] = []
    assert loop_serve.apply_course_machine_overrides(args, "c5-tick", {}, log_fn=lines.append) == []
    assert args.remote_transport == "push"  # 原值原样
    assert lines == []


def test_transport_coupling_keys_are_not_read_anymore() -> None:
    """★ 正交性尺子：课程块里那四个传输耦合键**不在**白名单里，且不再有任何值域校验。

    旧行为：非法 `remote_transport` 会在这里响亮 SystemExit（因为它来自文件、绕过 argparse
    choices）。现在它压根不该被读——读它就是把「哪门课走哪条传输路」重新变成课程的属性。
    """
    assert not (set(COUPLING_KEYS) & set(loop_serve.COURSE_MACHINE_OVERRIDE_KEYS))
    args = _args()
    assert (
        loop_serve.apply_course_machine_overrides(
            args,
            "c5-tick",
            {"courses": {"c5-tick": {k: "pull-typo" for k in COUPLING_KEYS}}},
            log_fn=lambda _l: None,
        )
        == []
    )
    assert args.remote_transport == "auto"


def test_overlay_skips_keys_the_args_namespace_does_not_have() -> None:
    """BC 解析器比 RL 少几个键 ⇒ **响亮跳过**，不 setattr 造字段（造出来的字段没有读者）。"""
    lines: list[str] = []
    args = Namespace()  # 白名单键一个都没有（BC 解析器就是这个形状）
    applied = loop_serve.apply_course_machine_overrides(
        args,
        "c5-tick",
        {"courses": {"c5-tick": {"gate_halt_mode": "halt", "workers": 4}}},
        log_fn=lines.append,
    )
    assert applied == []
    assert not hasattr(args, "gate_halt_mode")
    assert any("gate_halt_mode" in ln and "跳过" in ln for ln in lines)


def test_overlay_reads_rl_config_through_one_seam(monkeypatch: pytest.MonkeyPatch) -> None:
    """不传 cfg 时走 `_read_rl_config`（**唯一**读取点，也是用例的注入点）。"""
    monkeypatch.setattr(
        loop_serve,
        "_read_rl_config",
        lambda: {"courses": {"c5-tick": {"gate_halt_mode": "notify"}}},
    )
    args = _args()
    assert loop_serve.apply_course_machine_overrides(args, "c5-tick", None, log_fn=lambda _l: None) == [
        "gate_halt_mode"
    ]
    assert args.gate_halt_mode == "notify"


def test_real_rl_config_keeps_course_args_unchanged() -> None:
    """真机 rl-config 今天没有 `courses.<课>` 覆盖块 ⇒ `course_args` 的输出与改造前一致。

    （这条是**反悔门禁**：覆盖机制一旦被顺手写成「总是改写某些字段」，本用例会红。）
    """
    args = loop_serve.course_args("c4-dodge")
    assert args.remote_transport == "auto"  # rl-config 未配 ⇒ argparse 默认
    assert args.gate_halt_mode == "halt"
    # ★ §3：`--remote-degrade-after` 已删除 ⇒ 该字段不该再存在于 args
    assert not hasattr(args, "remote_degrade_after")


# ---------------------------------------------------------------- 单实例锁


def test_cluster_lock_path_is_a_dedicated_file() -> None:
    p = loop_serve.cluster_lock_path()
    assert Path(p).name == ".run_cluster.lock"
    assert Path(p).parent == loop_serve.NN_DIR


def test_cluster_lock_refuses_a_second_holder_and_frees_on_release(tmp_path: Path) -> None:
    """双开拒启 + 自己释放；holder 死了的陈旧锁自动收回（与其它 PID 文件锁同一种形状）。"""
    lock = str(tmp_path / ".run_cluster.lock")
    assert loop_serve.acquire_cluster_lock(lock) is True
    try:
        # 同一进程再拿（= 第二个服务器进程在真机上就是不同 pid，这里用「已持有」等价表达）
        assert loop_serve.acquire_cluster_lock(lock) is False
    finally:
        loop_serve.release_cluster_lock(lock)
    assert not Path(lock).exists()
    # 释放后可再拿
    assert loop_serve.acquire_cluster_lock(lock) is True
    loop_serve.release_cluster_lock(lock)


def test_cluster_lock_stale_holder_is_reclaimed(tmp_path: Path) -> None:
    lock = tmp_path / ".run_cluster.lock"
    lock.write_text("999999999|python|0", encoding="utf-8")  # 不存在的 pid
    path = str(lock)
    assert loop_serve.acquire_cluster_lock(path) is True
    loop_serve.release_cluster_lock(path)


def test_cluster_lock_force_takes_over(tmp_path: Path) -> None:
    lock = tmp_path / ".run_cluster.lock"
    lock.write_text(f"{os.getpid()}|python|0", encoding="utf-8")  # 活着的 holder（就是自己）
    path = str(lock)
    assert loop_serve.acquire_cluster_lock(path) is False
    assert loop_serve.acquire_cluster_lock(path, force=True) is True
    loop_serve.release_cluster_lock(path)
