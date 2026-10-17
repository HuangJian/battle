"""test_hub_discover_eviction.py — 课程表**可淘汰**（2026-10-17，plan/hub-server-memory-slim L3）。

**治什么**：`discover` 此前只增不减（`if ent.name in self._stores: continue`）⇒ 一门课的目录
被删 / tmp 被清之后，它的 `_JobStore` 与那份 `_ledger_cache`（整份账本解析出来的 Python 对象）
**永久驻留**。hub 那次 6.2GB 里就有这一类：tmp 被清掉了，内存里的账本一份没少。

本文件钉四条：

1. 目录没了 ⇒ 被摘（`_stores` / `_order` 都不再认它）；
2. **在飞 / 有人接管**的课**不摘**（淘汰是为了释放内存，不是给正在跑的活断电）；
3. 摘课时四个旁表一起清（`_halts` / `_peeked` / `_cursor` / `_locate_cache`）——
   留着就是「表里没这门课、别处还认它」的半死状态；
4. **活着的课不摘**（判据只认「目录没了」，停课删标记**不算**——停课后还要能取包/导段）。
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import COURSE_ENABLE_MARKER, PAYLOAD_NAME
from hub.queue import _HubQueue


def _hub(root: Path, courses: list[str]) -> _HubQueue:
    for c in courses:
        d = root / c
        (d / "remote-jobs").mkdir(parents=True, exist_ok=True)
        (d / COURSE_ENABLE_MARKER).write_text("", encoding="utf-8")
        (d / "training_log.jsonl").write_text("", encoding="utf-8")
    return _HubQueue({}, order=[], discover_root=str(root))


def test_a_course_whose_dir_is_gone_is_dropped(tmp_path: Path) -> None:
    """★ 判据 ①：目录被删（tmp 被清的典型形状）⇒ 从课程表摘掉。"""
    hub = _hub(tmp_path, ["a", "b"])
    assert hub.discover(force=True) == ["a", "b"]
    shutil.rmtree(tmp_path / "a")
    assert hub.discover(force=True) == []
    assert set(hub._stores) == {"b"} and hub._order == ["b"], (set(hub._stores), hub._order)


def test_a_live_course_is_not_dropped(tmp_path: Path) -> None:
    """★ 判据 ④：目录还在（哪怕开课标记被删 = 停课）⇒ **不摘**。

    停课之后控制台仍要看得见这门课（导出任务包 / 云机取包都要按课名找目录），摘表只会
    让那些入口 404。派发那层由 `_serves_course` 挡住，不需要靠摘表。
    """
    hub = _hub(tmp_path, ["a"])
    hub.discover(force=True)
    (tmp_path / "a" / COURSE_ENABLE_MARKER).unlink()  # 停课
    assert hub.discover(force=True) == []
    assert set(hub._stores) == {"a"}, "停课 ≠ 死课：课程表必须还在"


def test_a_course_with_an_inflight_job_is_not_dropped(tmp_path: Path) -> None:
    """★ 判据 ②：有活租约在跑 ⇒ 不摘（哪怕目录已经没了）。"""
    hub = _hub(tmp_path, ["a"])
    hub.discover(force=True)
    st = hub._stores["a"]
    jid = "j" * 16
    jd = st.job_root / jid
    jd.mkdir(parents=True)
    (jd / PAYLOAD_NAME).write_bytes(b"payload")
    (jd / "manifest.json").write_text("{}", encoding="utf-8")
    st.publish(jid, {"it": 1}, b"payload")
    out = st.claim_outcome(jid, mode="exclusive", worker_id="w1")
    assert out.ok, out
    assert st.inflight_job_ids(), "前置：这份活得真的在飞"

    shutil.rmtree(tmp_path / "a")
    assert hub.discover(force=True) == []
    assert set(hub._stores) == {"a"}, "在飞的课不摘——摘了就是把正在跑的活断电"


def test_drop_course_clears_the_side_tables(tmp_path: Path) -> None:
    """★ 判据 ③：摘课时四个旁表一起清（不留「表里没它、别处还认它」的半死状态）。"""
    hub = _hub(tmp_path, ["a", "b"])
    hub.discover(force=True)
    jid = "j" * 16
    # 先把旁表填脏：a 的停机达令、peek 时刻、轮转游标、归属缓存
    hub.set_halt(True, course="a")
    hub._peeked["a"] = 1.0
    hub._cursor = "a"
    hub._locate_cache[jid] = "a"
    assert hub.drop_course("a") is True
    assert hub.drop_course("a") is False, "重复摘 = 幂等 False"
    assert hub._halts == {}, hub._halts
    assert hub._peeked == {}, hub._peeked
    assert hub._cursor is None, "游标指向被摘的课 ⇒ 下一个轮转起点是个空壳"
    assert hub._locate_cache == {}, hub._locate_cache
    assert hub._order == ["b"]
    # 落盘的两个不动（`_dispatch` 落 `offline-dispatch.json`、`_leases` 关系重启后接管）
    assert isinstance(hub._dispatch, dict) and isinstance(hub._leases, dict)
