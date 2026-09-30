"""拆分的**契约守卫**：job 身份簇永住 `common/job_identity.py`（S5 第七刀，2026-09-27）。

`common/protocol.py` **1482 → 1389 行**；搬走一段跨度共 4 名（**逐字节不动**）：
`idempotency_key` · `job_id` · `SIBLING_MANIFEST_GLOB` · `collision_rows`。

本文件钉五件事：

1. **定义唯一**——这 4 名不许在 `protocol.py` 里再实现一遍；
2. **依赖面闭集（且无环）**——`job_identity` 只准 stdlib：**不** import `common.errors`、
   **不** import `common.protocol`（身份是叶子，谁都向下依赖它，没人该被它依赖）；
3. **不得碰上层包**（`common/` 是 L0，要随 code.zip 解到云机上）；
4. **转发同一对象**——`protocol` 的每个搬走名都是新模块的转发（`is`）；
5. **身份语义没变**（功能性）：幂等键**含 `course_fp`**、`job_id` 稳定且 16 位十六进制、
   撞名守卫只在「**完整幂等键**相同 且 落在**别的 store**」时命中（本课自己的历史 job、
   不同键、缺键的 manifest 都不算冲突）。
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.job_identity as identity_mod
import common.protocol as protocol_mod

PROTO_FILE = ROOT / "common" / "protocol.py"
IDENTITY_FILE = ROOT / "common" / "job_identity.py"

MOVED_NAMES = {
    "SIBLING_MANIFEST_GLOB",
    "collision_rows",
    "idempotency_key",
    "job_id",
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _defined(path: Path) -> set[str]:
    out: set[str] = set()
    for node in _tree(path).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Assign):
            out.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.add(node.target.id)
    return out


def _imported_modules(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and not node.level and node.module:
            out.add(node.module)
        elif isinstance(node, ast.Import):
            out.update(a.name for a in node.names)
    return out


def test_moved_names_are_defined_in_identity_and_not_in_protocol() -> None:
    """定义唯一：搬走的名字只在新家实现。"""
    assert _defined(IDENTITY_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(IDENTITY_FILE))
    leftovers = MOVED_NAMES & _defined(PROTO_FILE)
    assert leftovers == set(), f"protocol.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_identity_depends_on_stdlib_only_and_never_on_protocol() -> None:
    """★ 依赖面闭集：stdlib（`hashlib`/`json`/`pathlib`）；**不得** import `common.protocol`。"""
    mods = _imported_modules(IDENTITY_FILE)
    allowed = {"__future__", "hashlib", "json", "pathlib"}
    extra = sorted(mods - allowed)
    assert extra == [], f"common/job_identity.py 引入了允许面之外的依赖：{extra}"
    assert "common.protocol" not in mods, (
        "job_identity 反向 import 了门面 ⇒ 与 protocol 的顶层 import 互引成环"
    )


def test_identity_does_not_touch_an_upper_layer() -> None:
    """`common/` 是 L0：不得 import 上层包（否则云端解包即 ImportError）。"""
    banned = {"torch", "numpy", "trainer", "biz", "remote", "models", "data", "train", "common.distribution"}
    tops = {m.split(".")[0] for m in _imported_modules(IDENTITY_FILE)}
    hit = sorted(tops & banned)
    assert hit == [], f"job_identity 依赖了上层：{hit}"


def test_protocol_forwards_every_moved_name() -> None:
    """门面：`protocol` 的每个搬走名都还在，且与新家是同一个对象（函数尤其重要）。"""
    for name in sorted(MOVED_NAMES):
        assert hasattr(protocol_mod, name), f"common.protocol 丢了转发名 {name}"
        assert getattr(protocol_mod, name) is getattr(identity_mod, name), (
            f"common.protocol.{name} 不是 common.job_identity.{name}（转发成了副本）"
        )


# ─────────────────────── ⑤ 身份语义没变（功能性） ───────────────────────


def _manifest(**over: object) -> dict:
    base: dict = {
        "runId": "run-1",
        "course_fp": "cfp-A",
        "it": 7,
        "init_weights_fp": "wfp",
        "data_fp": "dfp",
    }
    base.update(over)
    return base


def test_idempotency_key_carries_course_fp() -> None:
    """★ 幂等键**必须**含 `course_fp`（2026-09-24 静默污染事故的修复点）。"""
    m = _manifest()
    assert identity_mod.idempotency_key(m) == ("run-1", "cfp-A", 7, "wfp", "dfp")
    # 只有课程身份不同的两份 manifest ⇒ 键不同（这正是事故的配置）
    assert identity_mod.idempotency_key(_manifest(course_fp="cfp-B")) != identity_mod.idempotency_key(m)


def test_job_id_is_stable_hex16() -> None:
    m = _manifest()
    jid = identity_mod.job_id(m)
    assert re.fullmatch(r"[0-9a-f]{16}", jid), jid
    assert identity_mod.job_id(dict(m)) == jid, "同键必须永远同 id（天然幂等）"
    assert identity_mod.job_id(_manifest(it=8)) != jid


def test_collision_rows_flags_only_cross_store_same_key(tmp_path: Path) -> None:
    """★ 判据 = 「**完整幂等键**相同 且 落在**别的 store**」——自课历史、不同键、缺键都不算。"""
    m = _manifest()
    jid = identity_mod.job_id(m)

    def put(course: str, body: object) -> Path:
        d = tmp_path / course / "remote-jobs" / jid
        d.mkdir(parents=True, exist_ok=True)
        p = d / "manifest.json"
        p.write_text(json.dumps(body), encoding="utf-8")
        return p

    own_root = tmp_path / "courseA" / "remote-jobs"
    put("courseA", {**m, "job_id": jid})  # 本课自己的历史 job
    assert identity_mod.collision_rows(own_root, m) == [], "本课自己的 job 不算冲突"

    # 别的课程发了**同一个键**（旁路制造的同身份）⇒ 响亮命中
    put("courseB", {**m, "job_id": jid})
    hits = identity_mod.collision_rows(own_root, m)
    assert len(hits) == 1, hits
    assert hits[0]["course"] == "courseB"
    assert hits[0]["manifest_path"].endswith("manifest.json")

    # 同一门课程但**键不同**（course_fp 不同）⇒ 合法，不命中
    other = _manifest(course_fp="cfp-B")
    assert identity_mod.collision_rows(own_root, other) == []


def test_collision_rows_is_best_effort_on_missing_keys_and_missing_root(tmp_path: Path) -> None:
    """缺键的 manifest / 扫不到的扫描根 ⇒ 返回空：哨兵绝不误伤（也绝不越权报错）。"""
    assert identity_mod.collision_rows(tmp_path / "nope" / "remote-jobs", _manifest()) == []
    # 本份 manifest 自己就不完整（缺 data_fp）——调用方另有校验，守卫不越权
    incomplete = _manifest()
    del incomplete["data_fp"]
    assert identity_mod.collision_rows(tmp_path / "nope" / "remote-jobs", incomplete) == []


def test_collision_rows_ignores_unreadable_sibling_manifests(tmp_path: Path) -> None:
    """读不懂/非对象的兄弟 manifest ⇒ 跳过该行，不把「读不懂」升级成「拒发」。"""
    m = _manifest()
    jid = identity_mod.job_id(m)
    d = tmp_path / "courseB" / "remote-jobs" / jid
    d.mkdir(parents=True)
    (d / "manifest.json").write_text("{ not json", encoding="utf-8")
    assert identity_mod.collision_rows(tmp_path / "courseA" / "remote-jobs", m) == []
