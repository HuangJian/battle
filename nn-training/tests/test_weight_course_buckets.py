"""test_weight_course_buckets —— 多课程权重/任务的**课程归属**（2026-09-18 用户指令）。

两条要求：
  · 「rollout 集群需缓存并行课程的最近权重」→ 上报带 `X-Course`，agent 按
    `(course, kind)` 分桶（桶逻辑的纯单测在 `tests/agent/weight-buckets.test.ts`）；
  · 「避免同一份权重多次传递」→ 上传前先 `GET /v1/weights?sha=` 预检，命中就不传体。

这里钉的是**训练侧**那一半（header 从哪来、预检命中时不发体、任何不确定都往「传」
那边掉）。零 socket：`dist_common._request` 被换成记账假实现。
"""

from __future__ import annotations

import gzip
import json
import sys
import urllib.parse
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import dist_common


class _Recorder:
    """记账版 `_request`：按方法返回预设状态，并记下每次调用的 (method, url, headers, data)。"""

    def __init__(self, get_status: int = 200, get_body: dict | None = None) -> None:
        self.calls: list[dict] = []
        self._get_status = get_status
        self._get_body = get_body if get_body is not None else {"cached": False}

    def __call__(
        self,
        url: str,
        auth_key: str,
        timeout: float,
        data: bytes | None = None,
        headers: dict[str, str] | None = None,
        method: str | None = None,
    ) -> tuple[int, bytes]:
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers or {}), "data": data}
        )
        if method == "GET":
            return self._get_status, json.dumps(self._get_body).encode("utf-8")
        return 200, json.dumps({"cache": "purged"}).encode("utf-8")

    @property
    def gets(self) -> list[dict]:
        return [c for c in self.calls if c["method"] == "GET"]

    @property
    def posts(self) -> list[dict]:
        return [c for c in self.calls if c["method"] == "POST"]

    def query(self, call: dict) -> dict[str, list[str]]:
        return urllib.parse.parse_qs(urllib.parse.urlparse(call["url"]).query)


@pytest.fixture()
def no_course_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(dist_common.COURSE_ENV, raising=False)


def test_course_name_of_is_empty_without_env(no_course_env: None) -> None:
    """没有课程身份 → 空串 = 旧单课程桶（agent 侧两向兼容）。"""
    assert dist_common.course_name_of() == ""


def test_course_name_of_reads_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(dist_common.COURSE_ENV, "  x1-rebirth-a2  ")
    assert dist_common.course_name_of() == "x1-rebirth-a2", "两端空白要 strip"


def test_post_weights_sends_course_header_from_env(
    monkeypatch: pytest.MonkeyPatch, no_course_env: None
) -> None:
    rec = _Recorder()
    monkeypatch.setattr(dist_common, "_request", rec)
    monkeypatch.setenv(dist_common.COURSE_ENV, "tiny-a")
    dist_common.post_weights("http://n", "k", "run.3", "s" * 64, b"weights")
    assert rec.posts[0]["headers"]["X-Course"] == "tiny-a"
    assert rec.posts[0]["headers"]["X-Kind"] == "rollout"


def test_post_weights_omits_course_header_when_unknown(
    monkeypatch: pytest.MonkeyPatch, no_course_env: None
) -> None:
    """未知名 = 不发头（旧 agent 见到空头也无从使用，少一个字段少一份歧义）。"""
    rec = _Recorder()
    monkeypatch.setattr(dist_common, "_request", rec)
    dist_common.post_weights("http://n", "k", "run.3", "s" * 64, b"weights")
    assert "X-Course" not in rec.posts[0]["headers"]


def test_post_weights_explicit_empty_course_beats_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """显式 course="" 是「落旧桶」的意思——不该被进程身份悄悄覆盖。"""
    rec = _Recorder()
    monkeypatch.setattr(dist_common, "_request", rec)
    monkeypatch.setenv(dist_common.COURSE_ENV, "tiny-a")
    dist_common.post_weights("http://n", "k", "run.3", "s" * 64, b"weights", course="")
    assert "X-Course" not in rec.posts[0]["headers"]


def test_precheck_hit_skips_the_body(monkeypatch: pytest.MonkeyPatch) -> None:
    """命中即不传体——这就是「避免同一份权重多次传递」的落点。"""
    rec = _Recorder(get_body={"cached": True})
    monkeypatch.setattr(dist_common, "_request", rec)
    monkeypatch.setenv(dist_common.COURSE_ENV, "tiny-a")
    mode = dist_common.post_weights_cached("http://n", "k", "run.3", "ab" * 32, b"x" * 1000)
    assert mode == "cached"
    assert len(rec.posts) == 0, "命中还上传 = 白传 ~0.5MB"
    assert rec.query(rec.gets[0])["sha"] == ["ab" * 32]
    assert rec.query(rec.gets[0])["course"] == ["tiny-a"], "预检也要带课程（否则查错桶）"


def test_precheck_miss_posts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    rec = _Recorder(get_body={"cached": False})
    monkeypatch.setattr(dist_common, "_request", rec)
    mode = dist_common.post_weights_cached("http://n", "k", "run.3", "ab" * 32, b"x" * 1000)
    assert mode == "purged"
    assert len(rec.posts) == 1


@pytest.mark.parametrize("status", [404, 500])
def test_precheck_unknown_falls_back_to_upload(
    monkeypatch: pytest.MonkeyPatch, status: int
) -> None:
    """旧 agent 没有这个端点（404）或临时 5xx ⇒ 保守上传。

    少传一次是省流量，**错判不传是 409 停活**（`wver not cached here` ⇒ 那一局作废）。
    """
    rec = _Recorder(get_status=status)
    monkeypatch.setattr(dist_common, "_request", rec)
    assert dist_common.post_weights_cached("http://n", "k", "run.3", "ab" * 32, b"x") == "purged"
    assert len(rec.posts) == 1


def test_precheck_empty_sha_does_not_ask(monkeypatch: pytest.MonkeyPatch) -> None:
    """空 sha（未开瘦身的旧路径）→ 连问都不问，直接按老路子上传。"""
    rec = _Recorder(get_body={"cached": True})
    monkeypatch.setattr(dist_common, "_request", rec)
    dist_common.post_weights_cached("http://n", "k", "run.3", "", b"x")
    assert rec.gets == []
    assert len(rec.posts) == 1


class _TaskRecorder(_Recorder):
    """任务端点假实现：回一个**最小合法容器**，于是 `fetch_task` 能走完整链路。"""

    def __call__(self, url, auth_key, timeout, data=None, headers=None, method=None):
        self.calls.append(
            {"method": method, "url": url, "headers": dict(headers or {}), "data": data}
        )
        frame = json.dumps({"manifest": {}, "files": {}}).encode("utf-8")
        return 200, gzip.compress(frame)


def _task_call(monkeypatch: pytest.MonkeyPatch) -> dict:
    rec = _TaskRecorder()
    monkeypatch.setattr(dist_common, "_request", rec)
    dist_common.fetch_task(
        "http://n",
        "k",
        iter_id="run.3",
        wver="w" * 64,
        stage=1,
        seed=2,
        max_ticks=100,
        difficulty="hard",
        timeout=5.0,
    )
    return rec.calls[0]


def test_fetch_task_carries_course_from_env(
    monkeypatch: pytest.MonkeyPatch, no_course_env: None
) -> None:
    """rollout 任务下发也带课程 —— agent 侧正是靠它选 (course, kind) 桶。"""
    monkeypatch.setenv(dist_common.COURSE_ENV, "tiny-a")
    call = _task_call(monkeypatch)
    assert urllib.parse.parse_qs(urllib.parse.urlparse(call["url"]).query)["course"] == ["tiny-a"]


def test_fetch_task_omits_course_without_identity(
    monkeypatch: pytest.MonkeyPatch, no_course_env: None
) -> None:
    """没有身份就不带字段（旧行为逐字节不变；agent 侧落到空串桶）。"""
    call = _task_call(monkeypatch)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(call["url"]).query)
    assert "course" not in qs
