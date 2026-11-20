"""tests/remote/test_job_exec_spec.py —— `JobExecSpec` 的形状守卫（§P0-3，2026-11-20）。

`_execute_job` 的 14 个参数收成一个 spec，守的是**位置传参**这条老毛病的成因面：

  · 生产调用点 `Runtime.thread(_execute_job, …)` **只收位置参数**（写关键字会直接语法错误），
    所以那个线程曾逐位传 11 个实参；同一形状在 `rescan_nodes`（27 参/19 位置）上已经出过一次
    错位事故 —— 线程启动即抛，功能静默失联；
  · 因此判据是三条：① 值对象 frozen + kw_only 且字段表冻结；② `_execute_job` 只收一个 spec；
    ③ 生产线程调用点传的**位置实参个数 = 1**，且那个实参是 spec 构造。

结构判据走 `tests/helpers/run_setup_probe.py`（`inspect` + AST 生产者）——理由同该文件模块头。
"""

from __future__ import annotations

import dataclasses

import pytest

from remote.worker_server import JobExecSpec, WorkerServerState
from tests.helpers import run_setup_probe as probe

#: 被测模块名（写成常量而不是两处字面量：`tests/test_subproc_util.py` 的「起真服务进程」守卫
#: 按 `"remote.worker_server",` 这一形状认出「这个测试 spawn 了服务」——本文件只读形状，不 spawn）。
_WS = "remote.worker_server"

#: `JobExecSpec` 的**完整**字段表（加/删都得动这里）。
FIELDS = frozenset(
    {
        "state",
        "manifest",
        "payload_zip",
        "code_zip",
        "ts_code_zip",
        "blobs",
        "work_dir",
        "device",
        "torch_threads",
        "echo",
        "hub_url",
        "push_token",
        "epoch_mult",
        "log",
    }
)


def test_job_exec_spec_is_frozen_and_kw_only(tmp_path) -> None:
    """线程里跑的是一份**只读**输入：谁都不许在 job 执行期间就地改它。"""
    spec = JobExecSpec(
        state=WorkerServerState(tmp_path),
        manifest={},
        payload_zip=b"",
        code_zip=None,
        ts_code_zip=None,
        blobs=None,
        work_dir=tmp_path,
        device="cpu",
        torch_threads=0,
        echo=False,
    )
    field_name = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field_name, None)
    assert all(f.kw_only for f in dataclasses.fields(JobExecSpec)), (
        "`JobExecSpec` 不是 kw_only ⇒ 14 个位置参数只是换了个地方写"
    )


def test_job_exec_spec_field_table_is_frozen() -> None:
    got = {f.name for f in dataclasses.fields(JobExecSpec)}
    assert got == FIELDS, (
        f"`JobExecSpec` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_execute_job_takes_only_the_spec() -> None:
    got = probe.param_names(_WS, "_execute_job")
    assert got == ["spec"], f"`_execute_job` 的形参是 {got}，应只有 `spec`"


def test_the_job_thread_passes_exactly_one_positional_spec() -> None:
    """`Runtime.thread(...)` 只收位置参数 ⇒ 唯一合法形状是「一个 spec」。"""
    assert probe.thread_positional_target(_WS, "_execute_job"), (
        "没找到 `_execute_job` 的线程调用点（改名了？本守卫要跟着改）"
    )
    args = probe.thread_positional_arg_kinds(_WS, "_execute_job")
    assert args == ["JobExecSpec_call"], f"线程的位置实参是 {args}，应为单个 `JobExecSpec(...)`"
