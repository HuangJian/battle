"""move-label semantics 语料执法（plan/new-era-stop.plan.md #7）。

B案把 move index 0 从「保持朝向」改成 **STOP**——同一个 `a_move==0` 字节的物理含义
翻转。这套用例钉三件事：

1. **双端同锚**：`common.schema.MOVE_LABEL_SEMANTICS` == TS `action-space.ts::MOVE_LABEL_SEMANTICS`
   （任一端漏同步即红，与 SCHEMA_FINGERPRINT 同式）。
2. **旧 rollout 必拒**：标签版本进 `biz.config.corpus_identity_fp` ⇒ 旧映射产出的 shard
   在 D14 判据下成为异血缘（四个 funnel 共用 `common.protocol.d14_corpus_match` 单点）。
3. **demo 祖父保留**：BC/demo 语料身份（`bc_corpus_identity_fp`）**不含**该键 ⇒ 旧
   demo shard 不被本次变更拒收（它们的 null→0 标注物理上本来就正确）。

纯 stdlib + pydantic，零 torch。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.schema
from common.protocol import d14_corpus_match
from worker.config import CourseConfig, corpus_identity_fp

MOVE_LABEL_SEMANTICS = common.schema.MOVE_LABEL_SEMANTICS

# 必须与 src/nn/action-space.ts::MOVE_LABEL_SEMANTICS 逐字相同（两边单测共锚）。
TS_ANCHOR = "MOVE_LABEL_SEMANTICS = 'stop0'"


def test_move_label_semantics_matches_ts_anchor() -> None:
    assert MOVE_LABEL_SEMANTICS == "stop0"
    ts = (ROOT.parent / "src" / "nn" / "action-space.ts").read_text(encoding="utf-8")
    assert TS_ANCHOR in ts, "TS 侧 action-space.ts 的标签版本与 Python common/schema.py 不同步"


def test_corpus_identity_carries_move_label_semantics(monkeypatch) -> None:
    """改标签版本 ⇒ RL 语料身份必变（否则旧 rollout 会静默混入同一训练）。"""
    course = CourseConfig(name="era-stop")
    base = corpus_identity_fp(course)
    monkeypatch.setattr(common.schema, "MOVE_LABEL_SEMANTICS", "keep0")
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
    src = (ROOT / "worker" / "bc_config.py").read_text(encoding="utf-8")
    assert "MOVE_LABEL_SEMANTICS" not in src


def test_all_funnels_share_one_d14_predicate() -> None:
    """四漏斗必须共用 `common.protocol.d14_corpus_match`——判据单点，否则标签版本只在
    部分漏斗生效（本地拒、云端收，或反之）。"""
    for rel in ("worker/resume.py", "remote/hub_client.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "d14_corpus_match" in src, f"{rel} 未走共享 D14 判据（标签版本执法会漏）"


# ───────────── B1 v6 动作空间：身份随**起点权重**走，不翻全局常量 ─────────────


def test_v6_action_space_enters_identity_from_bc_arch(tmp_path: Path) -> None:
    """B1 v6（2026-10-20）：`action_space` 键**只对 v6 腿**进身份。

    为什么不是翻全局 `MOVE_LABEL_SEMANTICS`：动作空间是**每条腿自己的**属性
    （y25 系 5/2、新 v6 腿 4/3/2）。翻常量会让所有历史 5/2 shard 被判成异血缘
    ⇒ 已跑完的语料全废。正确做法 = 从起点权重的 `arch.actionV6` 读（与 `build_ppo`
    同一判据，也是 rollout 真正会走的动作空间）。

    三条断言分别钉：① 5/2 权重 ⇒ 指纹**逐字节不变**（历史语料继续有效）；
    ② v6 权重 ⇒ 指纹变（D14 分流，防止 5/2 与 4/3/2 的 `a_move` 混训）；
    ③ 读不到/坏文件 ⇒ 保守按 legacy（老行为，不猜）。
    """
    legacy_w = tmp_path / "legacy.json"
    legacy_w.write_text('{"schema_major": 6, "arch": {"kind": "student"}}', encoding="utf-8")
    v6_w = tmp_path / "v6.json"
    v6_w.write_text(
        '{"schema_major": 6, "arch": {"kind": "student", "actionV6": true}}', encoding="utf-8"
    )

    base = corpus_identity_fp(CourseConfig(name="v6-leg"))
    # ① 5/2：与「没有 bc」完全一致 ⇒ payload 里没有新键
    assert corpus_identity_fp(CourseConfig(name="v6-leg", bc=str(legacy_w))) == base
    # ② v6：异血缘
    assert corpus_identity_fp(CourseConfig(name="v6-leg", bc=str(v6_w))) != base
    # ③ 缺失/坏 JSON ⇒ legacy（不猜 v6）
    assert corpus_identity_fp(CourseConfig(name="v6-leg", bc=str(tmp_path / "nope.json"))) == base
    bad = tmp_path / "bad.json"
    bad.write_text("not json", encoding="utf-8")
    assert corpus_identity_fp(CourseConfig(name="v6-leg", bc=str(bad))) == base


def test_bc_path_itself_stays_out_of_identity(tmp_path: Path) -> None:
    """`bc` 是**路径**不是语义：两份同 arch 的权重放不同目录 ⇒ 指纹必须相同。

    （防止有人图省事把 `course.bc` 字符串直接塞进 payload —— 那会让「挪个目录」
    断掉全部血缘，D14 会把同一份语料判成异身份。）
    """
    a = tmp_path / "a.json"
    b = tmp_path / "dir2" / "b.json"
    b.parent.mkdir()
    payload = '{"arch": {"kind": "student", "actionV6": true}}'
    a.write_text(payload, encoding="utf-8")
    b.write_text(payload, encoding="utf-8")
    assert corpus_identity_fp(CourseConfig(name="v6-leg", bc=str(a))) == corpus_identity_fp(
        CourseConfig(name="v6-leg", bc=str(b))
    )
