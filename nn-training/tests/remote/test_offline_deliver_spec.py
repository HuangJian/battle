"""tests/remote/test_offline_deliver_spec.py —— `OfflineDelivererSpec` 的形状守卫（P0-3，2026-11-22）。

`OfflineDeliverer.__init__` 原先 20 个关键字参数：身份（hub/run/产物根/课程）、网络与探测
（三个 `float` 超时/宽限）、备份抽屉、模式开关与三个注入点。旧表里选项段相邻且同型 ——
把 `probe_ttl` 写成 `probe_fail_ttl` 不会报错，只把抖动恢复窗口从 60s 变 15s，只有现场能
看出来。所以判据是：

  · 值对象 frozen + kw_only；
  · 字段表冻结（加/删都得动这里）；
  · 入口只收一个 spec。

调用点的透传/行为由 `test_offline_deliver` 族覆盖；本类刻意住在 `remote/deliver_spec.py`
（`offline_deliver.py` 的 LOC 预算已到顶，见该模块头）。
"""

from __future__ import annotations

import dataclasses
import inspect
from pathlib import Path

import pytest

from remote.deliver_spec import OfflineDelivererSpec
from remote.offline_deliver import OfflineDeliverer

#: `OfflineDelivererSpec` 的**完整**字段表（加/删都得动这里）。
FIELDS = frozenset(
    {
        "base_url",
        "token",
        "run_id",
        "artifacts_dir",
        "course",
        "hub_lease",
        "worker_name",
        "backup_root",
        "backup_grace",
        "timeout",
        "probe_ttl",
        "probe_fail_ttl",
        "sync_cap",
        "eval_every",
        "metrics_parts",
        "adaptive",
        "background",
        "opener",
        "now_fn",
        "log",
    }
)


def test_offline_deliverer_spec_is_frozen_and_kw_only(tmp_path: Path) -> None:
    spec = OfflineDelivererSpec(base_url="", token="", run_id="r", artifacts_dir=tmp_path)
    field = sorted(FIELDS)[0]  # 变量而非字面量：B009/B010 只拦常量
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, None)
    assert all(f.kw_only for f in dataclasses.fields(OfflineDelivererSpec)), (
        "`OfflineDelivererSpec` 不是 kw_only ⇒ 20 个参数只是换了个地方写"
    )


def test_offline_deliverer_spec_field_table_is_frozen() -> None:
    got = {f.name for f in dataclasses.fields(OfflineDelivererSpec)}
    assert got == FIELDS, (
        f"`OfflineDelivererSpec` 的字段表和守卫不一致：多了 {sorted(got - FIELDS)}，"
        f"少了 {sorted(FIELDS - got)}（确属正解就把这张表一起改了）"
    )


def test_offline_deliverer_takes_only_the_spec() -> None:
    got = list(inspect.signature(OfflineDeliverer.__init__).parameters)
    assert got == ["self", "spec"], f"`OfflineDeliverer.__init__` 的形参是 {got}"
