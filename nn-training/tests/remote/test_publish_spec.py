"""PublishSpec：56 参发布签名收成一个对象（`python.refactor.md` §P0-3 / §7.1）。

判据四条：

1. `publish_job` 的签名里**不再有** `job_root` 这类字段（只剩 spec + log），也没有 `**fields`；
2. 字段全部住 `PublishSpec`（frozen，改一个字段必须重建）；
3. 旧 kwargs 形态已删除：`publish_job(it=1)` 直接 `TypeError`；
4. 同输入两次发布 ⇒ 同 job_id / 同 payload_sha256（幂等没被对象化破坏）。
"""

from __future__ import annotations

import inspect
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from remote.hub_client import PublishSpec, publish_job
from tests.helpers.publish_spec import make_spec


def _quiet(_msg: str) -> None:
    return None


def test_publish_job_signature_is_a_spec_not_56_fields() -> None:
    params = inspect.signature(publish_job).parameters
    assert list(params) == ["spec", "log"], list(params)
    assert params.keys().isdisjoint({"job_root", "shard_dirs"}), "字段必须住 PublishSpec"


def test_publish_twice_with_same_spec_is_idempotent(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    m1 = publish_job(spec, log=_quiet)
    m2 = publish_job(spec, log=_quiet)

    assert m1["job_id"] == m2["job_id"]
    assert m1["payload_sha256"] == m2["payload_sha256"]


def test_spec_is_frozen(tmp_path: Path) -> None:
    spec = make_spec(tmp_path)
    with pytest.raises(FrozenInstanceError):
        spec.it = 2  # type: ignore[misc]


def test_field_kwargs_are_rejected() -> None:
    """唯一入口是对象：旧的 `publish_job(it=1, ...)` 形态必须响亮失败。"""
    with pytest.raises(TypeError):
        publish_job(it=1)  # type: ignore[call-arg]
