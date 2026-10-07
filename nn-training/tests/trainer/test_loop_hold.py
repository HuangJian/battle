"""★M2（plan/worker-type-dispatch-model §3-M2）：`trainer/loop_hold.py` 的双通道判据。

「这门课现在归云机吗」是本轮重构里**最容易退化成假事实**的一个问题，因为它曾经确实由一份
**配置**回答（`courses.<课>.rollout_src=run`），而配置与事实不同步时两种错法都疼：

  * 配置留着、云机掉线 ⇒ 本机不跑 + 云机也不跑（旧模型的静默停摆）；
  * 配置删了、云机还在跑 ⇒ 本机自己采样，与云机双跑（取包链最危险的形态）。

新判据是**两条通道的并集**（`hub_live ∨ file_live`）：

  ① hub 直问（权威）：`GET /offline/hold?course=`（只认进度活性；≤3s、异常全消化）；
  ② 控制文件缓存（`tmp/loop-control.json` 的 `held`）：控制台写的快照，本机**就地 900s 自判活**。

两者都拿不到 ⇒ **不接管**（本机照跑：协作派发是缺省，而不是「谁都不敢跑」）。本文件把这条
「保守方向 + 绝不 brick 课程」逐条钉住，包括超时、404、非 JSON、以及新旧文件形状。
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import AUTH_HEADER, OFFLINE_HOLD_PATH
from trainer import loop_hold


@pytest.fixture(autouse=True)
def _control_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """控制文件重定向进 tmp（否则会读真 `tmp/loop-control.json` = 操作员的活状态）。"""
    f = tmp_path / "loop-control.json"
    monkeypatch.setenv("NN_LOOP_CONTROL", str(f))
    return f


class _Resp:
    """够用的 `urlopen` 替身（只被 `with ... as resp: resp.read()` 用）。"""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None


def _opener(payload: object, *, record: list | None = None):
    """返回一个假 opener：把 (url, timeout) 记下来，回一份 payload（或抛）。"""

    def open_fn(req: Any, timeout: float = 0.0) -> Any:
        if record is not None:
            record.append((req.full_url, req.headers.get(AUTH_HEADER), timeout))
        if isinstance(payload, Exception):
            raise payload
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        return _Resp(body)

    return open_fn


# ─────────────────────────── ① hub 直问（权威通道） ───────────────────────────


def test_hub_held_true_false_and_unreachable() -> None:
    """三态：`True`（被接管）/ `False`（没被接管）/ `None`（**问不到** ⇒ 退文件通道）。"""
    assert loop_hold.hub_held("http://hub:1", "tok", "c5", opener=_opener({"held": True})) is True
    assert loop_hold.hub_held("http://hub:1", "tok", "c5", opener=_opener({"held": False})) is False
    assert loop_hold.hub_held("http://hub:1", "tok", "c5", opener=_opener({"held": "yes"})) is False
    # 404 / 超时 / 连接被拒 / 非 JSON / 非对象 ⇒ None（不是 False！后者会把「问不到」当「没接管」，
    # 于是 hub 一抽风本机就开跑 = 与云机双跑）
    for bad in (
        OSError("connection refused"),
        TimeoutError("timed out"),
        b"<html>404</html>",
        [1, 2, 3],
    ):
        assert loop_hold.hub_held("http://hub:1", "tok", "c5", opener=_opener(bad)) is None


def test_hub_held_url_token_and_timeout_shape() -> None:
    """请求形状：路径常量 + `?course=`（引号安全）+ Bearer 头 + ≤3s 超时（P0-5 定死）。"""
    seen: list = []
    loop_hold.hub_held("http://hub:1/", "tok", "c5 gae", opener=_opener({"held": True}, record=seen))
    url, auth, timeout = seen[0]
    assert url == f"http://hub:1{OFFLINE_HOLD_PATH}?course=c5%20gae"
    assert auth == "Bearer tok"
    assert timeout == loop_hold.HOLD_QUERY_TIMEOUT_SEC <= 3.0


def test_hub_held_needs_both_base_and_course() -> None:
    """缺 hub 地址或缺课程 ⇒ 不发请求（`None`）——空地址去连会等满超时，白拖一轮墙钟。"""
    boom = _opener(RuntimeError("不该被调用"))
    assert loop_hold.hub_held("", "tok", "c5", opener=boom) is None
    assert loop_hold.hub_held("http://hub:1", "tok", "", opener=boom) is None


# ─────────────────────────── ② 控制文件缓存（同机通道） ───────────────────────────


def test_file_held_only_counts_fresh_entries(_control_file: Path) -> None:
    """缓存里超窗的条目**不算接管**（与 hub 同一把尺子；超窗 ⇒ 那是掉线的云机）。"""
    _control_file.write_text(
        json.dumps(
            {
                "paused": [],
                "held": [
                    {"course": "live", "last_progress_at": time.time() - 5},
                    {"course": "dead", "last_progress_at": time.time() - 10_000},
                ],
            }
        ),
        encoding="utf-8",
    )

    assert loop_hold.file_held("live") is True
    assert loop_hold.file_held("dead") is False
    assert loop_hold.file_held("nope") is False
    assert loop_hold.file_held("") is False


def test_file_held_absent_file_is_not_held(_control_file: Path) -> None:
    """文件不存在（正常态）⇒ 不接管；读面坏掉也**不得**变成「接管」（否则课永远停着）。"""
    assert not _control_file.exists()
    assert loop_hold.file_held("c5") is False


# ─────────────────────────── 并集语义（`hub_live ∨ file_live`） ───────────────────────────


def _args(**kw: object) -> SimpleNamespace:
    base: dict[str, object] = {
        "course": "c5",
        "remote_hub_url": "",
        "remote_token": "",
    }
    base.update(kw)
    return SimpleNamespace(**base)


def test_course_held_is_a_union_and_never_bricks(_control_file: Path) -> None:
    """两通道**或**：任一说「被接管」即接管；都说没有 ⇒ 本机照跑（缺省不是「谁都不敢跑」）。"""
    # ① 只有文件通道新鲜 ⇒ 接管
    _control_file.write_text(
        json.dumps({"held": [{"course": "c5", "last_progress_at": time.time() - 1}]}),
        encoding="utf-8",
    )
    assert loop_hold.course_held(_args()) is True
    # ② 文件通道过期、hub 说接管 ⇒ 仍接管（hub 是权威）
    _control_file.write_text(
        json.dumps({"held": [{"course": "c5", "last_progress_at": time.time() - 10_000}]}),
        encoding="utf-8",
    )
    args = _args(remote_hub_url="http://hub:1", remote_token="tok")
    assert loop_hold.course_held(args, opener=_opener({"held": True})) is True
    # ③ 两条都说没有 ⇒ 不接管（本机照跑）
    assert loop_hold.course_held(args, opener=_opener({"held": False})) is False
    # ④ hub 问不到 + 缓存过期 ⇒ 不接管（保守方向 = 继续训练）
    assert loop_hold.course_held(_args()) is False


def test_course_held_without_course_key_is_false() -> None:
    """没有课程键（合成 args / BC 课）⇒ False；**不许**抛（读面/调度面都调它）。"""
    assert loop_hold.course_held(SimpleNamespace()) is False


def test_course_key_of_normalises_paths_like_the_ledger() -> None:
    """`--course` 收「课程名**或**路径」⇒ 两把写法必须归到**同一把键**（账本/锁/槽位那把）。

    不过这一道，路径形式的启动会把键写成 `x.jsonc`/`curricula/x.jsonc` ⇒ 本课永远查不到
    自己的 hold（静默双跑，取包链最危险的形态）。
    """
    assert loop_hold.course_key_of(_args(course="c5")) == "c5"
    assert loop_hold.course_key_of(_args(course="curricula/c5.jsonc")) == "c5"
    assert loop_hold.course_key_of(SimpleNamespace(course_path="curricula/c5.jsonc")) == "c5"
    assert loop_hold.course_key_of(SimpleNamespace()) == ""


def test_hub_token_reads_the_same_source_as_the_remote_leg() -> None:
    """token 与远端派发**同源**（`remote_token`）——第二份来源必然分叉（401 → 静默退文件）。"""
    assert loop_hold.hub_token_of(_args(remote_token="tok")) == "tok"
    assert loop_hold.hub_token_of(SimpleNamespace()) == ""
