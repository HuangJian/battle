"""`common/settings.py` 的行为面（P1-5 第一批）。

钉三件事：**默认值唯一**（`EVALBOARD_DATA_ROOT` 就是舱里那一份）· **`env=` 注入**（测试不必
`monkeypatch.setenv`，并行用例不互相踩）· **归一化只手写一次**（空白 / 未设一律落回缺省，不然
「设了个空串」会变成相对当前目录的 `Path('')`）。

门禁 `tests/test_env_reads_budget.py` 管的是「别处不许再解同一个旋钮」，这里管的是「解出来的值对不对」。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from common.paths import REPO_ROOT
from common.settings import (
    EVALBOARD_DATA_ENV,
    EVALBOARD_DATA_ROOT,
    HUB_TOKEN_ENV,
    MIGRATED_ENV,
    evalboard_data_root,
    hub_token_from_env,
)


def test_default_is_the_single_constant() -> None:
    assert EVALBOARD_DATA_ROOT == REPO_ROOT / "dashboard" / "data" / "evalboard"
    assert evalboard_data_root({}) == EVALBOARD_DATA_ROOT


def test_override_wins() -> None:
    assert evalboard_data_root({EVALBOARD_DATA_ENV: "/tmp/eb"}) == Path("/tmp/eb")


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_falls_back_to_default(blank: str) -> None:
    """空串不能变成 `Path("")`（那是当前目录）—— 那会让台账默默写到 cwd。"""
    assert evalboard_data_root({EVALBOARD_DATA_ENV: blank}) == EVALBOARD_DATA_ROOT


def test_env_injection_never_reads_the_real_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(EVALBOARD_DATA_ENV, "/tmp/real-env")
    assert evalboard_data_root({EVALBOARD_DATA_ENV: "/tmp/injected"}) == Path("/tmp/injected")
    assert evalboard_data_root(None) == Path("/tmp/real-env")  # `None` = 现读环境（对照）


def test_hub_token_is_stripped_and_optional() -> None:
    assert hub_token_from_env({}) == ""
    assert hub_token_from_env({HUB_TOKEN_ENV: "  tk-1 \n"}) == "tk-1"


def test_migrated_knobs_are_registered() -> None:
    """G13 的单家判据从 `MIGRATED_ENV` 取；新旋钮忘了登记 = 门禁静默放行。"""
    assert set(MIGRATED_ENV) == {EVALBOARD_DATA_ENV, HUB_TOKEN_ENV}
