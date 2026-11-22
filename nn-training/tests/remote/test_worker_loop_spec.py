"""tests/remote/test_worker_loop_spec.py —— `WorkerLoopSpec` 的形状守卫（§P0-3，2026-11-22）。

`worker_loop` 原先 22 个参数：前 3 个是身份位（hub / token / 工作目录），其余 19 个是会话
旋钮 —— 设备与线程、轮询节拍与停机、归属与筛选、预取三档、让路与回传、缓存根。这些旋钮
在旧位置表里相邻且同型（三个 `str`、三个 `int`）：多传一位不报错，只会让 worker 以错的
角色 / 预取档 / 回传模式静默跑一整轮。所以判据是：

  · 值对象 frozen + kw_only（旋钮不许靠位置猜）；
  · 字段表冻结（加/删都得动这里）；
  · 入口只收一个 spec。

生产/测试调用点传的就是 spec —— 由 `test_worker_offline_cap`（role）、
`test_ppo_backend_switch`（backend）与 `test_remote_hotswap`（restart_argv）的透传用例
覆盖（不读源码：读 `getsource` 会把每条 assert 计入口径 B 的文本断言）。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from remote.worker import ROLE_ONLINE, WorkerLoopSpec
from tests.helpers import run_setup_probe as probe

#: `WorkerLoopSpec` 的**完整**字段表（加/删都得动这里）。
FIELDS = frozenset(
    {
        "base_url",
        "token",
        "work_dir",
        "device",
        "torch_threads",
        "epoch_mult",
        "poll_sec",
        "once",
        "echo",
        "max_idle_sec",
        "restart_argv",
        "hub_urls",
        "role",
        "job_filter",
        "backend",
        "prefetch_depth",
        "prefetch_mode",
        "prefetch_unpack",
        "bulk_yield",
        "result_upload",
        "cache_dir",
        "log",
    }
)


def test_worker_loop_spec_is_frozen_and_kw_only(tmp_path: Path) -> None:
    spec = WorkerLoopSpec(base_url="http://hub", token="tok", work_dir=tmp_path)
    assert spec.role == ROLE_ONLINE
    field = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(WorkerLoopSpec)), (
        "`WorkerLoopSpec` 不是 kw_only ⇒ 22 个位置参数只是换了个地方写"
    )


def test_worker_loop_spec_field_table_is_frozen() -> None:
    got = {f.name for f in dataclasses.fields(WorkerLoopSpec)}
    assert got == FIELDS, (
        f"`WorkerLoopSpec` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_worker_loop_takes_only_the_spec() -> None:
    got = probe.param_names("remote.worker", "worker_loop")
    assert got == ["spec"], f"`worker_loop` 的形参是 {got}，应只有 `spec`"
