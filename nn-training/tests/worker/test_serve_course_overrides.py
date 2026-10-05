"""单进程服务器（`--serve`）的两条 2026-09-19 加固（R3-5：trainer 收敛为一个进程）。

背景：控制台过去**每门课**起一个 trainer 进程，于是「这门课怎么连云端」用该进程的命令行
表达（`--remote-transport pull` / `--remote-hub-url` / `--remote-degrade-after` /
`--gate-halt-mode`）。收敛成**一个进程服务所有课程**之后，命令行只有一份 —— 那些旋钮
必须换成**按课程的机器侧配置**（`rl-config.json → courses.<课>.*`）。两件事在这里钉住：

> 2026-10-01（plan/gate-halt-platform-level）：`COURSE_MACHINE_OVERRIDE_KEYS` **今天已空**——
> 两个成员先后退役（`remote_degrade_after` 2026-09-21；`gate_halt_mode` 2026-10-01 升平台级）。
> 白名单**机制**仍在（下一条按课旋钮直接往里加），所以机制类用例改用**探针白名单**
> （`monkeypatch.setattr` 临时装一个键）而不是被数据掏空的空断言。

1. **课程机器侧覆盖**（`apply_course_machine_overrides`）：白名单、值域校验、缺字段跳过、
   非白名单键一个字不碰；且**读取点单一**（`_read_rl_config` 是 `worker/course_args.py` 侧唯一的
   rl-config 读——2026-10-05 该链整块搬去 `worker/course_args.py`，`loop_serve` 经旧名重导出；
   注入点随之搬到 course_args）。
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

from trainer import loop_serve
from worker import course_args


def _args(**kw: Any) -> Namespace:
    """最小 args（含覆盖白名单里的字段 + 几个不该被碰的字段）。"""
    base: dict[str, Any] = {
        "remote_transport": "auto",
        "remote_hub_url": "",
        # 2026-10-01：`--gate-halt-mode` 的 argparse 缺省是 **None**（不再读 rl-config；
        # 缺省跟随平台文件）——这里跟着真解析器的形状走。
        "gate_halt_mode": None,
        "push_node_url": "",
        "workers": 8,
    }
    base.update(kw)
    return Namespace(**base)


#: 课程块里**不许**再被读的传输耦合键（2026-09-19：课程任务与 worker 节点正交）。
COUPLING_KEYS = ("remote_transport", "remote_hub_url", "push_node_url", "hub_push")


# ---------------------------------------------------------------- 机器侧覆盖


def test_overlay_applies_only_the_whitelisted_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """白名单里的键生效；其余键（含传输耦合与**已平台化的 gate_halt_mode**）一个字不碰。

    今天真白名单是空的（两人都退役）⇒ 装一个**探针键**（`workers`）来钉机制本身：
    「只有白名单里的键会被 setattr」这条契约不因为数据空了就失去守卫。
    """
    monkeypatch.setattr(course_args, "COURSE_MACHINE_OVERRIDE_KEYS", ("workers",))
    lines: list[str] = []
    args = _args()
    cfg = {
        "courses": {
            "c5-tick": {
                "workers": 4,
                # ↓ 2026-10-01：已平台化（tmp/gate-halt.json）——留着不报错也不生效
                "gate_halt_mode": "skip",
                # ↓ 2026-09-19 起**已不是**被读的键（课程与节点正交）；留着不报错也不生效
                "remote_transport": "pull",
                "remote_hub_url": "http://127.0.0.1:8789",
                "push_node_url": "https://gpu.example",
                "hub_push": True,
            }
        }
    }
    applied = loop_serve.apply_course_machine_overrides(args, "c5-tick", cfg, log_fn=lines.append)
    assert applied == ["workers"]
    assert args.workers == 4
    # 非白名单键：**没有**被 setattr（它们属于别的读者，或者已无读者）
    assert args.gate_halt_mode is None
    assert args.remote_transport == "auto"
    assert args.remote_hub_url == ""
    assert args.push_node_url == ""
    assert not hasattr(args, "hub_push")
    # 生效值必须上屏（静默改写执行面 = 「看起来正常」那类事故）
    assert any("c5-tick" in ln and "workers=4" in ln for ln in lines)


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


def test_overlay_skips_keys_the_args_namespace_does_not_have(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BC 解析器比 RL 少几个键 ⇒ **响亮跳过**，不 setattr 造字段（造出来的字段没有读者）。

    同样用探针白名单（今天真白名单是空的）——跳过分支是**下一条键**的护栏。
    """
    monkeypatch.setattr(course_args, "COURSE_MACHINE_OVERRIDE_KEYS", ("gate_halt_mode",))
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
    monkeypatch.setattr(course_args, "COURSE_MACHINE_OVERRIDE_KEYS", ("workers",))
    monkeypatch.setattr(
        course_args,
        "_read_rl_config",
        lambda: {"courses": {"c5-tick": {"workers": 7}}},
    )
    args = _args()
    assert loop_serve.apply_course_machine_overrides(args, "c5-tick", None, log_fn=lambda _l: None) == [
        "workers"
    ]
    assert args.workers == 7


def test_gate_halt_mode_is_no_longer_a_course_machine_override() -> None:
    """★ 门禁停机模式已**平台级化**（2026-10-01 / plan/gate-halt-platform-level）：

    课程级写面整体摘除：白名单里没有它、课程块里的旧值一个字不生效（读者只认
    `tmp/gate-halt.json`）。旧值不进报错——白名单本来就是「谁还会被读」的答案。
    """
    assert "gate_halt_mode" not in loop_serve.COURSE_MACHINE_OVERRIDE_KEYS
    lines: list[str] = []
    args = _args()
    applied = loop_serve.apply_course_machine_overrides(
        args,
        "c5-tick",
        {"courses": {"c5-tick": {"gate_halt_mode": "notify"}}},
        log_fn=lines.append,
    )
    assert applied == []
    assert args.gate_halt_mode is None  # 未被课程块改写（缺省 = 读平台文件）
    assert not any("机器侧覆盖" in ln for ln in lines)


def test_rl_config_path_is_env_overridable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`BCITY_RL_CONFIG` = rl-config 路径的**唯一**缝（`biz.config` 委托 `common.distribution`）。

    为什么这条重要（2026-09-22，用户指令「测试应该使用自己的 fixtures」）：rl-config.json
    **永不入库**（gitignore），于是不用夹具的用例实际上是在「读别人机器上的文件」——
    本机 `rl.stream=1` 曾让解析链对拍常年红（`tests/trainer/test_serve_wiring.py`）。
    """
    import json as _json

    from common.distribution import rl_config_path as dist_path
    from worker.config import read_rl_config_file, rl_config_path

    fixture = tmp_path / "rl-config.fixture.json"
    fixture.write_text(_json.dumps({"courses": {"c5-tick": {"workers": 4}}}), "utf-8")
    monkeypatch.setenv("BCITY_RL_CONFIG", str(fixture))
    assert rl_config_path() == fixture and Path(dist_path()) == fixture
    assert read_rl_config_file()["courses"]["c5-tick"]["workers"] == 4
    # 相对路径按 nn-training/ 下解析（与默认值同一约定）；未设 env ⇒ 仓里那份
    monkeypatch.setenv("BCITY_RL_CONFIG", "rl-config.fixture.json")
    assert rl_config_path() == Path(dist_path())
    assert rl_config_path().name == "rl-config.fixture.json"
    monkeypatch.delenv("BCITY_RL_CONFIG")
    assert rl_config_path().name == "rl-config.json" and rl_config_path().parent.name == "nn-training"
    # 形状坏了（顶层是数组）⇒ 空 dict，不是 AttributeError 落在开课路径上
    fixture.write_text("[1, 2]", "utf-8")
    monkeypatch.setenv("BCITY_RL_CONFIG", str(fixture))
    assert read_rl_config_file() == {}


def test_rl_config_is_gitignored_forever() -> None:
    """rl-config.json **永不入库**（它承载机器侧事实且被控制台直接改写）。

    这条守的是「有人为了让它可测就把它提交了」这种修法——那会把一台机器的 hub 端口 /
    token / 节点表变成全仓契约。正确修法是夹具（见上一条）。
    """
    ignored = (Path(__file__).resolve().parents[2] / ".gitignore").read_text(encoding="utf-8")
    assert "rl-config.json" in [ln.strip() for ln in ignored.splitlines()]


def test_course_args_with_a_fixture_keeps_fields_unchanged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**夹具** rl-config（无 `courses.<课>` 覆盖块）⇒ `course_args` 输出与 argparse 默认一致。

    （这条是**反悔门禁**：覆盖机制一旦被顺手写成「总是改写某些字段」，本用例会红。
    夹具版比读本机真配置更强：不再依赖未入库文件的内容。）
    """
    fixture = tmp_path / "rl-config.fixture.json"
    fixture.write_text('{"rl": {}}', encoding="utf-8")
    monkeypatch.setenv("BCITY_RL_CONFIG", str(fixture))
    args = loop_serve.course_args("c4-dodge")
    assert args.remote_transport == "auto"  # 夹具未配 ⇒ argparse 默认
    # 2026-10-01：门禁停机模式不再读 rl-config（缺省 None ⇒ 由 loop_guards 读平台文件，
    # 再缺省 halt）——argparse 层**不能**预先定成 "halt"，否则「谁给的」就分不出来了。
    assert args.gate_halt_mode is None
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
