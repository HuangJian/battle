"""拆分的**契约守卫**：shard 清单 + 结果容器校验 + 唯一落盘出口永住 `common/shard.py`（S5 第十一刀，2026-09-27）。

`common/distribution.py` **1503 → 1373 行**；搬走**七段跨度共 7 名（逐字节不动）**：
`SHARD_FILES` · `INTENT_SHARD_FILES` · `BC_SHARD_FILES` · `BC_COLLECTOR` ·
`_shard_files_for` · `validate_result` · `write_shard`。

本文件钉五件事：

1. **定义唯一**——搬走名不许在 `common/distribution.py` 里再实现一遍；
2. **依赖面闭集（且无环）**——只准 stdlib（`base64`/`json`/`os`）；**不** import `common.distribution`
   （否则与门面成环）；
3. **不得碰上层包**（L0：要能随 code.zip 解到云机上）；
4. **门面对象恒等**——`common.distribution.X is common.shard.X`（历史调用点一行不改）；
5. **契约语义没变**（功能性）：三份清单 = 与 TS 侧同表的双语契约 · collector/marker 三态选择 ·
   七条拒收理由 + BC 败局跳过分支 · 落盘只写清单内文件 + `manifest.json`（indent=2）·
   v1 base64 / v2 bytes 双模。
"""

from __future__ import annotations

import ast
import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.distribution as common_mod
import common.shard as shard_mod
from tests.helpers import source_scan

SHARD_FILE = ROOT / "common/shard.py"
COMMON_FILE = ROOT / "common/distribution.py"

MOVED_NAMES = {
    "SHARD_FILES",
    "INTENT_SHARD_FILES",
    "BC_SHARD_FILES",
    "BC_COLLECTOR",
    "_shard_files_for",
    "validate_result",
    "write_shard",
}

ALLOWED_IMPORTS = {"__future__", "base64", "json", "os"}


def _tree(path: Path) -> ast.Module:
    return source_scan.parse(str(path))


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


def test_moved_names_are_defined_in_dist_shard_and_not_in_dist_common() -> None:
    """定义唯一：搬走的名字只在新家实现（原家只留门面转发）。"""
    assert _defined(SHARD_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(SHARD_FILE))
    leftovers = MOVED_NAMES & _defined(COMMON_FILE)
    assert leftovers == set(), f"common/distribution.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_shard_import_surface_is_closed_and_acyclic() -> None:
    """★ 依赖面闭集：stdlib-only；**不得** import `common.distribution`（门面反向 ⇒ 成环）。"""
    mods = _imported_modules(SHARD_FILE)
    extra = sorted(mods - ALLOWED_IMPORTS)
    assert extra == [], f"common/shard.py 引入了允许面之外的依赖：{extra}"
    assert "common.distribution" not in mods, "common.shard 反向 import 了门面 ⇒ 顶层互引成环"


def test_dist_shard_does_not_touch_an_upper_layer() -> None:
    """L0 是叶子：不得 import 上层包。"""
    banned = {"torch", "numpy", "trainer", "worker", "biz", "remote", "models", "data", "train", "common.distribution"}
    tops = {m.split(".")[0] for m in _imported_modules(SHARD_FILE)}
    hit = sorted(tops & banned)
    assert hit == [], f"common.shard 依赖了上层：{hit}"


def test_dist_common_forwards_every_moved_name() -> None:
    """门面：每个搬走名都还在 `common.distribution`，且与新家是**同一个对象**；源码里是 `X as X`。"""
    src = source_scan.read_text(str(COMMON_FILE))
    assert "from common.shard import (" in src
    for name in sorted(MOVED_NAMES):
        assert hasattr(common_mod, name), f"common.distribution 丢了转发名 {name}"
        assert getattr(common_mod, name) is getattr(shard_mod, name), (
            f"common.distribution.{name} 不是 common.shard.{name}（转发成了副本）"
        )
        assert f"    {name} as {name},\n" in src, f"common.distribution 的转发不是自别名形态：{name}"


# ─────────────────────── ⑤ 契约语义没变（功能性） ───────────────────────

PETICK = (
    "obs.npy",
    "scalars.npy",
    "a_move.npy",
    "a_fire.npy",
    "lp_move.npy",
    "lp_fire.npy",
    "value.npy",
    "metrics.npy",
    "done.npy",
    "mask.npy",
)
INTENT = (
    "obs.npy",
    "scalars.npy",
    "inject.npy",
    "a_intent.npy",
    "lp_intent.npy",
    "value.npy",
    "reward.npy",
    "done.npy",
    "mask.npy",
    "dt.npy",
)
BC = ("obs.npy", "scalars.npy", "actions.npy", "masks.npy", "conditions.npy", "returns.npy")


def test_shard_lists_are_the_petick_intent_bc_contract() -> None:
    """三份清单是双语契约（TS 导出器同表）：逐项钉死，改一个字节即红。"""
    assert tuple(shard_mod.SHARD_FILES) == PETICK
    assert tuple(shard_mod.INTENT_SHARD_FILES) == INTENT
    assert tuple(shard_mod.BC_SHARD_FILES) == BC
    assert shard_mod.BC_COLLECTOR == "BC-GOD"


def test_shard_files_for_picks_by_collector_and_intent_marker() -> None:
    """三态选择：BC collector → BC 表；INTENT-RL / 带 a_intent.npy → 意图表；其余 per-tick。"""
    assert shard_mod._shard_files_for({"collector": "BC-GOD"}) == shard_mod.BC_SHARD_FILES
    assert shard_mod._shard_files_for({"collector": "INTENT-RL"}) == shard_mod.INTENT_SHARD_FILES
    assert shard_mod._shard_files_for({"a_intent.npy": 1}) == shard_mod.INTENT_SHARD_FILES
    assert shard_mod._shard_files_for({}) == shard_mod.SHARD_FILES
    assert shard_mod._shard_files_for({"collector": "other"}) == shard_mod.SHARD_FILES


def _petick_files(**over: str | bytes) -> dict:
    files: dict = {name: b"\x00\x01" for name in shard_mod.SHARD_FILES}
    files.update(over)
    return files


def test_validate_result_accepts_an_exact_container() -> None:
    assert (
        shard_mod.validate_result(
            {"wver": "w1", "stage": 3, "seed": 7}, _petick_files(), "w1", {(3, 7)}, set()
        )
        is None
    )
    # v1 旧 agent：值是 base64 str —— 双模兼容（不是拒收理由）
    b64 = {name: base64.b64encode(b"\x00\x01").decode("ascii") for name in shard_mod.SHARD_FILES}
    assert shard_mod.validate_result({"wver": "w1", "stage": 3, "seed": 7}, b64, "w1", {(3, 7)}, set()) is None


def test_validate_result_rejections_are_the_contract_branches() -> None:
    m = {"wver": "w1", "stage": 3, "seed": 7}
    why = shard_mod.validate_result
    assert why(
        {"wver": "w2", "stage": 3, "seed": 7}, _petick_files(), "w1", {(3, 7)}, set()
    ) == "wver mismatch: got 'w2'"
    assert why(m, _petick_files(), "w1", {(1, 2)}, set()) == "unexpected (stage,seed)=(3, 7)"
    assert why(m, _petick_files(), "w1", {(3, 7)}, {(3, 7)}) == "duplicate (stage,seed)=(3, 7)"
    files = _petick_files()
    files.pop("mask.npy")
    files["opt.pt"] = b"\x00"
    assert why(m, files, "w1", {(3, 7)}, set()) == (
        "file set mismatch (extra=['opt.pt'], missing=['mask.npy'])"
    )
    assert why(m, _petick_files(**{"value.npy": "!!!"}), "w1", {(3, 7)}, set()) == (
        "value.npy: invalid base64"
    )
    assert why(m, _petick_files(**{"value.npy": b""}), "w1", {(3, 7)}, set()) == (
        "value.npy: empty payload"
    )
    non_dict = ["not-a-manifest"]
    assert why(non_dict, {}, "w1", {(3, 7)}, set()) == "manifest is not an object"  # type: ignore[arg-type]


def test_validate_result_bc_loss_skip_needs_an_empty_container() -> None:
    """BC wins-only 败局：合法「跳过」结果，但必须**不带文件**（带了就是契约违规）。"""
    loss = {"wver": "bc", "stage": 3, "seed": 7, "collector": shard_mod.BC_COLLECTOR, "kept": False}
    assert shard_mod.validate_result(loss, {}, "bc", {(3, 7)}, set()) is None
    assert shard_mod.validate_result(loss, {"obs.npy": b"\x00"}, "bc", {(3, 7)}, set()) == (
        "bc loss-skip shard must carry no files (got ['obs.npy'])"
    )
    # 同一 BC collector 的正常容器走 BC 表：per-tick 文件集必须被拒
    bc_ok = {"wver": "bc", "stage": 3, "seed": 7, "collector": shard_mod.BC_COLLECTOR}
    bc_files = {name: b"\x00" for name in shard_mod.BC_SHARD_FILES}
    assert shard_mod.validate_result(bc_ok, bc_files, "bc", {(3, 7)}, set()) is None
    assert shard_mod.validate_result(bc_ok, _petick_files(), "bc", {(3, 7)}, set()) is not None


def test_write_shard_writes_only_listed_files_plus_manifest(tmp_path: Path) -> None:
    """唯一落盘出口：只写清单内文件 + `manifest.json`（indent=2，单次写——F8.3 契约）。"""
    manifest = {"wver": "w1", "stage": 1, "seed": 2, "collector": "god-ai", "outcome": "win"}
    files: dict = {name: bytes([i]) * 8 for i, name in enumerate(shard_mod.SHARD_FILES)}
    files["obs.npy"] = base64.b64encode(b"ABCD").decode("ascii")  # v1 形态照收
    out = tmp_path / "rl_s1_seed2"

    shard_mod.write_shard(files, manifest, str(out))

    assert sorted(p.name for p in out.iterdir()) == sorted([*shard_mod.SHARD_FILES, "manifest.json"])
    assert (out / "obs.npy").read_bytes() == b"ABCD"
    assert (out / "mask.npy").read_bytes() == files["mask.npy"]
    text = (out / "manifest.json").read_text(encoding="utf-8")
    assert text == json.dumps(manifest, indent=2)
    assert text.count("{\n") == 1, "manifest.json 被写了两次（F8.3 双写回归）"


def test_write_shard_selects_the_bc_list_for_bc_containers(tmp_path: Path) -> None:
    """落盘表选择与校验共用同一判据：BC collector 的容器落 BC 表，per-tick 键会 KeyError。"""
    manifest = {"wver": "bc", "stage": 4, "seed": 0, "collector": shard_mod.BC_COLLECTOR}
    files = {name: b"\x00\x01" for name in shard_mod.BC_SHARD_FILES}
    out = tmp_path / "bc_s4_seed0"

    shard_mod.write_shard(files, manifest, str(out))

    assert sorted(p.name for p in out.iterdir()) == sorted([*shard_mod.BC_SHARD_FILES, "manifest.json"])
