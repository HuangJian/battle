"""`LifecycleSpec` 的形状护栏（`python.refactor.md` §P0-3 批 F）。

`remote/lifecycle.Lifecycle.__init__` 原来是 3 个位置参数 + 8 个关键字。它没被写成
「一次构造、四个调用点」那么简单：`base_url` / `token` / `worker_id` 三个相邻身份串互换一个，
云机的「遗言」就落到别的 hub / 别的机器名下；`work_dir`（Path）/ `device` / `role` 三个相邻
自由位互换，读点会去量错的目录；`log` 与 `now_fn` 是两个**签名不同**的回调（mypy 抓得住，
运行时互换只会把时间戳写进日志）。这里钉住：字段表 / frozen + kw_only / 入口只吃一份 spec /
三个身份位各自落到自己的槽（走真实出口的 payload，不读私有字段）。
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

import pytest

from remote import lifecycle as LC
from remote.lifecycle import LIFECYCLE_COMPONENT, Lifecycle, LifecycleSpec

#: 字段表（按声明顺序；前三位 = 旧的位置面）。
FIELDS = [
    "base_url",
    "token",
    "worker_id",
    "work_dir",
    "device",
    "role",
    "once",
    "reading_sec",
    "log",
    "now_fn",
]


@pytest.fixture()
def sent(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """拦截两条出口，记下「发到哪个 hub / 用哪个身份」—— 不读任何私有字段。"""
    box: list[dict[str, object]] = []

    def _post(base: str, token: str, payload: dict[str, object], **_: object) -> bool:
        box.append({"url": base, "token": token, **payload})
        return True

    monkeypatch.setattr(LC, "post_error", _post)
    monkeypatch.setattr(LC, "report_error", lambda spec: box.append(dict(vars(spec))))
    return box


def test_field_table_is_frozen() -> None:
    assert [f.name for f in dataclasses.fields(LifecycleSpec)] == FIELDS


def test_spec_is_frozen_and_keyword_only(tmp_path: Path) -> None:
    assert all(f.kw_only for f in dataclasses.fields(LifecycleSpec))
    spec = LifecycleSpec(base_url="", token="", worker_id="w", work_dir=tmp_path)
    field = FIELDS[2]
    with pytest.raises(dataclasses.FrozenInstanceError):
        setattr(spec, field, "other")
    maker: Callable[..., LifecycleSpec] = LifecycleSpec
    with pytest.raises(TypeError):
        maker("", "", "w", tmp_path)


def test_entry_takes_one_spec_only(tmp_path: Path) -> None:
    spec = LifecycleSpec(base_url="", token="", worker_id="w", work_dir=tmp_path)
    assert isinstance(Lifecycle(spec), Lifecycle)
    ctor: Callable[..., object] = Lifecycle
    with pytest.raises(TypeError):
        ctor("", "", "w", work_dir=tmp_path)
    with pytest.raises(TypeError):
        ctor()


def test_identity_fields_land_in_their_own_slots(
    tmp_path: Path, sent: list[dict[str, object]]
) -> None:
    """`base_url` / `token` / `worker_id` 三格互换 ⇒ 记录归到别的 hub/机器 —— 这里逐格钉住。"""
    spec = LifecycleSpec(
        base_url="http://hub.example:8787",
        token="tok-A",
        worker_id="kaggle-c",
        work_dir=tmp_path,
        device="cuda:0",
        role="ppo",
    )
    life = Lifecycle(spec)
    life.start()
    boss = sent[0]
    assert boss["url"] == "http://hub.example:8787"
    assert boss["token"] == "tok-A"
    assert boss["worker"] == "kaggle-c"
    assert boss["component"] == LIFECYCLE_COMPONENT


def test_no_hub_means_no_request(sent: list[dict[str, object]], tmp_path: Path) -> None:
    """空 `base_url` / `token` ⇒ 一条都不发（本地预演 / 离线 / 单测的旧契约）。"""
    life = Lifecycle(LifecycleSpec(base_url="", token="", worker_id="w", work_dir=tmp_path))
    life.start()
    life.tick()
    life.close(done=0)
    assert sent == []
