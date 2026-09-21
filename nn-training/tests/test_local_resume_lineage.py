"""本地 resume 的 D14 血缘判据同源化（plan/accident.plan.md §2/A，2026-09-21）。

背景：D14（跨课程语料不混训）有两把尺子——
  * `course_fp` = 课程**文件字节** sha256（“这份配置长什么样”）；
  * `corpus_fp` = 语料**语义**身份（env+reward 解析值哈希，“一个样本是什么”）。
远端链路 2026-09-13 就改成语义优先（`remote.protocol.d14_corpus_match`，h​ub 打包 +
worker 装载共用），但**本地对账**一直只比文件字节 ⇒ 改一下课程里的预算/路径/注释
（字节变、语义不变）就把自己历史的 shard 全判成异血缘 ⇒ 全量重采（云端其实照收）。

本文件钉三件事：
  ① 语义相同、文件字节不同 ⇒ **认**（不再全量重采）；
  ② 语义不同（真的换了语料）⇒ **不认**（跨语料绝不混入）；
  ③ 任一侧缺 corpus_fp（legacy shard / 无课程运行）⇒ 回退文件字节（旧行为逐字节不变），
     且扫描缓存必须把 corpus_fp 计入键（否则一份缓存会冒充另一份身份的答案）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.resume import (
    _scan_shards,
    completed_pairs,
    resumed_manifests,
    settled_stage_totals,
)

WVER = "w" * 64
OLD_BYTES = "a" * 64  # 旧的 course_fp（改课程文件前）
NEW_BYTES = "b" * 64  # 新的 course_fp（改了一个注释/预算）
CORPUS = "c" * 64  # 语料语义身份（env+reward），两次编辑之间不变


def _shard(traj: Path, stage: int, seed: int, **m: object) -> Path:
    d = traj / f"rl_s{stage}_seed{seed}"
    d.mkdir(parents=True, exist_ok=True)
    man = {"stage": stage, "seed": seed, "wver": WVER, "nSamples": 10, "ticks": 100}
    man.update(m)
    (d / "manifest.json").write_text(json.dumps(man), encoding="utf-8")
    return d


def test_semantic_match_survives_a_course_file_edit(tmp_path: Path) -> None:
    """① 文件字节变了、语义没变 ⇒ 历史 shard 仍是「已完成」（本地不再全量重采）。"""
    _shard(tmp_path, 1, 10, course_fp=OLD_BYTES, corpus_fp=CORPUS)

    # 旧口径（只比字节）：空集 —— 这就是那个 bug 的形状
    assert completed_pairs(tmp_path, WVER, course_fp=NEW_BYTES) == set()
    # 新口径（同源）：语义一致 ⇒ 认
    assert completed_pairs(tmp_path, WVER, course_fp=NEW_BYTES, corpus_fp=CORPUS) == {(1, 10)}
    # 两个都一致自然还是同一个答案（字节一致 + 语义一致）
    assert completed_pairs(tmp_path, WVER, course_fp=OLD_BYTES, corpus_fp=CORPUS) == {(1, 10)}


def test_different_semantics_is_never_mixed_in(tmp_path: Path) -> None:
    """② 真的换了语料 ⇒ 不认（跨语料绝不混入本轮）；即使文件字节相同也不认。"""
    _shard(tmp_path, 1, 10, course_fp=NEW_BYTES, corpus_fp="d" * 64)
    assert completed_pairs(tmp_path, WVER, course_fp=NEW_BYTES, corpus_fp=CORPUS) == set()
    # 语义不同但字节相同：语义优先 ⇒ 仍然不认
    assert completed_pairs(tmp_path, WVER, course_fp=NEW_BYTES, corpus_fp=CORPUS) == set()


def test_legacy_manifest_falls_back_to_file_bytes(tmp_path: Path) -> None:
    """③ legacy shard（manifest 无 corpus_fp）⇒ 回退字节比对，旧行为逐字节不变。"""
    _shard(tmp_path, 2, 20, course_fp=OLD_BYTES)  # 无 corpus_fp
    assert completed_pairs(tmp_path, WVER, course_fp=OLD_BYTES, corpus_fp=CORPUS) == {(2, 20)}
    assert completed_pairs(tmp_path, WVER, course_fp=NEW_BYTES, corpus_fp=CORPUS) == set()
    # 本腿没有 corpus_fp（非课程运行）⇒ 只看字节
    assert completed_pairs(tmp_path, WVER, course_fp=OLD_BYTES) == {(2, 20)}
    # 两者都 None ⇒ 不过滤（旧行为）
    assert completed_pairs(tmp_path, WVER) == {(2, 20)}


def test_scan_cache_key_includes_corpus_fp(tmp_path: Path) -> None:
    """缓存键必须带 corpus_fp：否则一次扫描的答案会被当成另一份身份的答案。"""
    _shard(tmp_path, 3, 30, course_fp=NEW_BYTES, corpus_fp=CORPUS)
    # 先问一个不匹配的语义身份（结果空），再问匹配的（必须能拿到真结果，不能被缓存冒充）
    assert completed_pairs(tmp_path, WVER, course_fp=NEW_BYTES, corpus_fp="e" * 64) == set()
    assert completed_pairs(tmp_path, WVER, course_fp=NEW_BYTES, corpus_fp=CORPUS) == {(3, 30)}
    assert _scan_shards(tmp_path, WVER, course_fp=NEW_BYTES, corpus_fp=CORPUS) == [
        ((3, 30), tmp_path / "rl_s3_seed30")
    ]


def test_settled_totals_and_resumed_manifests_use_the_same_rule(tmp_path: Path) -> None:
    """另两个消费点（动态采集配额账本 / 报告聚合）同源——判据只有一处。"""
    _shard(tmp_path, 0, 40, course_fp=OLD_BYTES, corpus_fp=CORPUS, nSamples=40)

    # 旧口径：换了个注释就全判异血缘 ⇒ 配额账本归零（会触发无意义的补采）
    assert settled_stage_totals(tmp_path, WVER, course_fp=NEW_BYTES) == {}
    # 新口径：语义一致 ⇒ 账本照旧
    assert settled_stage_totals(tmp_path, WVER, course_fp=NEW_BYTES, corpus_fp=CORPUS) == {
        0: (1, 40)
    }
    assert resumed_manifests(tmp_path, WVER, course_fp=NEW_BYTES, corpus_fp=CORPUS) != []
    assert resumed_manifests(tmp_path, WVER, course_fp=NEW_BYTES) == []


def test_corpus_fp_comes_from_the_same_source_as_the_remote_publish() -> None:
    """`corpus_fp_for_args` 与远端发布同源（loop 与 hub 判的是同一件事）。"""
    from types import SimpleNamespace

    from rl.cmd import corpus_fp_for_args
    from rl.config import CourseConfig, corpus_identity_fp

    course = CourseConfig(name="t5-lineage", mode="per-tick")
    args = SimpleNamespace(course_obj=course, mode="per-tick")
    assert corpus_fp_for_args(args) == corpus_identity_fp(course)
    # 无课程 ⇒ 空串（不过滤；旧行为）
    assert corpus_fp_for_args(SimpleNamespace()) == ""
