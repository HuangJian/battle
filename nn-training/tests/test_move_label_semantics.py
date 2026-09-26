"""move-label semantics 语料执法（plan/new-era-stop.plan.md #7）。

B案把 move index 0 从「保持朝向」改成 **STOP**——同一个 `a_move==0` 字节的物理含义
翻转。这套用例钉三件事：

1. **双端同锚**：`schema.MOVE_LABEL_SEMANTICS` == TS `action-space.ts::MOVE_LABEL_SEMANTICS`
   （任一端漏同步即红，与 SCHEMA_FINGERPRINT 同式）。
2. **旧 rollout 必拒**：标签版本进 `rl.config.corpus_identity_fp` ⇒ 旧映射产出的 shard
   在 D14 判据下成为异血缘（四个 funnel 共用 `common.protocol.d14_corpus_match` 单点）。
3. **demo 祖父保留**：BC/demo 语料身份（`bc_corpus_identity_fp`）**不含**该键 ⇒ 旧
   demo shard 不被本次变更拒收（它们的 null→0 标注物理上本来就正确）。

纯 stdlib + pydantic，零 torch。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import schema
from common.protocol import d14_corpus_match
from rl.config import CourseConfig, corpus_identity_fp

MOVE_LABEL_SEMANTICS = schema.MOVE_LABEL_SEMANTICS

# 必须与 src/nn/action-space.ts::MOVE_LABEL_SEMANTICS 逐字相同（两边单测共锚）。
TS_ANCHOR = "MOVE_LABEL_SEMANTICS = 'stop0'"


def test_move_label_semantics_matches_ts_anchor() -> None:
    assert MOVE_LABEL_SEMANTICS == "stop0"
    ts = (ROOT.parent / "src" / "nn" / "action-space.ts").read_text(encoding="utf-8")
    assert TS_ANCHOR in ts, "TS 侧 action-space.ts 的标签版本与 Python schema.py 不同步"


def test_corpus_identity_carries_move_label_semantics(monkeypatch) -> None:
    """改标签版本 ⇒ RL 语料身份必变（否则旧 rollout 会静默混入同一训练）。"""
    course = CourseConfig(name="era-stop")
    base = corpus_identity_fp(course)
    monkeypatch.setattr(schema, "MOVE_LABEL_SEMANTICS", "keep0")
    assert corpus_identity_fp(course) != base


def test_old_policy_rollout_shard_is_rejected_new_is_accepted() -> None:
    """双向：旧映射的 shard 被拒、新映射的 shard 被接受。"""
    new_fp = corpus_identity_fp(CourseConfig(name="era-stop"))
    old_manifest = {"corpus_fp": "deadbeef" + new_fp, "wver": "w", "stage": 0, "seed": 0}
    new_manifest = {"corpus_fp": new_fp, "wver": "w", "stage": 0, "seed": 0}
    assert d14_corpus_match("", new_fp, old_manifest) is False
    assert d14_corpus_match("", new_fp, new_manifest) is True


def test_demo_identity_excludes_move_label_semantics() -> None:
    """祖父保留：BC 语料身份不引用该键 ⇒ 旧 demo shard 不会被本次变更拒收。"""
    src = (ROOT / "rl" / "bc_config.py").read_text(encoding="utf-8")
    assert "MOVE_LABEL_SEMANTICS" not in src


def test_all_funnels_share_one_d14_predicate() -> None:
    """四漏斗必须共用 `common.protocol.d14_corpus_match`——判据单点，否则标签版本只在
    部分漏斗生效（本地拒、云端收，或反之）。"""
    for rel in ("rl/resume.py", "remote/hub_client.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "d14_corpus_match" in src, f"{rel} 未走共享 D14 判据（标签版本执法会漏）"
