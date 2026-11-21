"""`remote/push_client.ShaCacheHits`：只记命中的探针记忆（P0-5 对象化，2026-11-21）。

旧形状是一个模块级 `dict[(base_url, kind, sha), True]`，三个写点散在 `_cached_or_last_hit`
（记命中 / 翻面）与 `forget_cache_hit`（428）里，上限是 `if len > CAP: clear()` 两行。
收成对象后，三条**语义**各有名字，也各自可钉：

* 探针问不到 ⇒ 回答「上次是不是命中」（保守上传的兜底）；
* 明确 miss / 节点 428 ⇒ `forget` 必须翻面（不翻面 ⇒ 428 来回打转）；
* 上限 ⇒ **整片清空**（不是 LRU：这份账只在探针失败时被读）。
"""

from __future__ import annotations

import remote.push_client as pc


def test_record_then_known_survives_and_forget_flips_it() -> None:
    hits = pc.ShaCacheHits()
    assert hits.known("http://node", "code", "a" * 64) is False
    hits.record("http://node", "code", "a" * 64)
    assert hits.known("http://node", "code", "a" * 64) is True
    hits.forget("http://node", "code", "a" * 64)
    assert hits.known("http://node", "code", "a" * 64) is False
    assert len(hits) == 0


def test_keys_are_per_node_per_kind_and_per_sha() -> None:
    """三个维度都要分开：换节点 / 换 kind（code·blob·ts）/ 换 sha 都是另一条账。"""
    hits = pc.ShaCacheHits()
    hits.record("http://a", "code", "s1")
    assert hits.known("http://b", "code", "s1") is False
    assert hits.known("http://a", "blob", "s1") is False
    assert hits.known("http://a", "code", "s2") is False
    hits.record("http://a", "blob", "s1")
    assert len(hits) == 2


def test_empty_parts_are_normalized_like_the_callers_expect() -> None:
    """空 base_url / 空 sha 归一成同一把 key（旧 dict 的口径），且空 kind 不与真 kind 串味。"""
    hits = pc.ShaCacheHits()
    hits.record("", "code", "")
    assert hits.known("", "code", "") is True
    assert hits.known("", "", "") is False


def test_cap_clears_the_whole_table_not_an_oldest_entry() -> None:
    """上限 = 整片清空：旧条目与新条目一起没了（不是 LRU，不做「哪条记得住」的解释）。"""
    hits = pc.ShaCacheHits(cap=4)
    for i in range(4):
        hits.record("http://node", "code", f"s{i}")
    assert len(hits) == 4
    assert hits.known("http://node", "code", "s3") is True
    hits.record("http://node", "code", "s4")  # len 4 > cap 4 不成立 ⇒ 先记下
    assert len(hits) == 5
    hits.record("http://node", "code", "s5")  # len 5 > 4 ⇒ 清空后只留这一条
    assert len(hits) == 1
    assert hits.known("http://node", "code", "s3") is False, "清空后旧条目必须一起消失"


def test_forget_is_idempotent() -> None:
    """重复翻面（两条 428 分支 / 明确 miss + 428）不许抛。"""
    hits = pc.ShaCacheHits()
    hits.forget("http://node", "code", "s")
    hits.record("http://node", "code", "s")
    hits.forget("http://node", "code", "s")
    hits.forget("http://node", "code", "s")
    assert len(hits) == 0


def test_default_memory_is_one_object_and_the_public_forget_reaches_it() -> None:
    assert pc.cache_hits() is pc.cache_hits()
    pc.cache_hits().record("http://node", "ts", "z" * 40)
    try:
        assert pc.cache_hits().known("http://node", "ts", "z" * 40) is True
        pc.forget_cache_hit("http://node", "ts", "z" * 40)
        assert pc.cache_hits().known("http://node", "ts", "z" * 40) is False
    finally:
        pc.cache_hits().forget("http://node", "ts", "z" * 40)
