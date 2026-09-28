"""拆分的**契约守卫**：job manifest 契约永住 `common/manifest.py`（S5 第九刀，2026-09-27）。

`common/protocol.py` **1238 → 848 行**；搬走**四段跨度共 38 名（逐字节不动）**：`PROTO` · 角色词汇
（`ROLE_OFFLINE`/`ROLE_ONLINE`/`ROLES`/`ROLE_FIELD`）· `MANIFEST_*` schema + `MANIFEST_KINDS` +
`KIND_ROLES` + `role_of` · TS/plan 产物契约（`ROLLOUT_SCRIPT`/`EVAL_SCRIPT`/`ROLLOUT_SCRIPTS`/
`ITER_OUT_REL`/`ITER_NODE_LABEL`/`PLAN_NAME`/`PLAN_PROTO`/`RUN_NODE_LABEL`/`RUN_MAX_ITERS_HARD_CAP`）·
`normalize_manifest` + rollout 规格校验 + shard 命名 + `data_fp` 指纹。

本文件钉五件事：

1. **定义唯一**——这 38 名不许在 `protocol.py` 里再实现一遍；
2. **依赖面闭集（且无环）**——只准 stdlib + `common.errors`；**不** import `common.protocol`；
3. **不得碰上层包**（`common/` 是 L0，要随 code.zip 解到云机上）；
4. **转发同一对象**——`protocol` 的每个搬走名都是新模块的转发（`is`），`ProtocolError` 尤其重要；
5. **契约语义没变**（功能性）：必填/missing fail fast · kind 红线（bc/iter 的 mode）· role 拼错拒收 ·
   `role_of` 兜底 · kind→role 穷举 · shard 名往返 · `data_fp` 与声明集同源 · rollout argv 白名单/相对路径/重复局。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import common.manifest as manifest_mod
import common.protocol as protocol_mod
from common.errors import ProtocolError

PROTO_FILE = ROOT / "common" / "protocol.py"
MANIFEST_FILE = ROOT / "common" / "manifest.py"

MOVED_NAMES = {
    # 版本 + 角色词汇
    "PROTO",
    "ROLES",
    "ROLE_FIELD",
    "ROLE_OFFLINE",
    "ROLE_ONLINE",
    # manifest schema
    "MANIFEST_BC_EXEMPT",
    "MANIFEST_BC_EXTRA",
    "MANIFEST_ITER_EXTRA",
    "MANIFEST_KINDS",
    "MANIFEST_OPTIONAL_DEFAULTS",
    "MANIFEST_REQUIRED",
    "MANIFEST_RUN_EXTRA",
    "KIND_ROLES",
    "role_of",
    # 产物契约
    "EVAL_SCRIPT",
    "ITER_NODE_LABEL",
    "ITER_OUT_REL",
    "PLAN_NAME",
    "PLAN_PROTO",
    "ROLLOUT_SCRIPT",
    "ROLLOUT_SCRIPTS",
    "RUN_MAX_ITERS_HARD_CAP",
    "RUN_NODE_LABEL",
    # 校验 / 指纹
    "DataFpEntries",
    "ROLLOUT_SPEC_DEFAULTS",
    "_ITER_PATH_FLAGS",
    "_WIN_DRIVE_RE",
    "_iter_flag_value",
    "_iter_rel_path",
    "d14_corpus_match",
    "data_fp",
    "data_fp_entries",
    "iter_declared_entries",
    "iter_expected_data_fp",
    "normalize_manifest",
    "parse_shard_name",
    "shard_name",
    "validate_rollout_spec",
}

ALLOWED_IMPORTS = {
    "__future__",
    "collections.abc",
    "hashlib",
    "json",
    "os",
    "pathlib",
    "re",
    "common.errors",
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


def test_moved_names_are_defined_in_manifest_and_not_in_protocol() -> None:
    """定义唯一：搬走的名字只在新家实现。"""
    assert _defined(MANIFEST_FILE) >= MOVED_NAMES, sorted(MOVED_NAMES - _defined(MANIFEST_FILE))
    leftovers = MOVED_NAMES & _defined(PROTO_FILE)
    assert leftovers == set(), f"protocol.py 里仍在实现这些名字（应只做转发）：{sorted(leftovers)}"


def test_manifest_import_surface_is_closed_and_acyclic() -> None:
    """★ 依赖面闭集：stdlib + `common.errors`；**不得** import `common.protocol`（成环）。"""
    mods = _imported_modules(MANIFEST_FILE)
    extra = sorted(mods - ALLOWED_IMPORTS)
    assert extra == [], f"common/manifest.py 引入了允许面之外的依赖：{extra}"
    assert "common.protocol" not in mods, (
        "manifest 反向 import 了门面 ⇒ 与 protocol 的顶层 import 互引成环"
    )


def test_manifest_does_not_touch_an_upper_layer() -> None:
    """`common/` 是 L0：不得 import 上层包。"""
    banned = {"torch", "numpy", "rl", "remote", "models", "data", "train", "dist_common"}
    tops = {m.split(".")[0] for m in _imported_modules(MANIFEST_FILE)}
    hit = sorted(tops & banned)
    assert hit == [], f"manifest 依赖了上层：{hit}"


def test_protocol_forwards_every_moved_name() -> None:
    """门面：`protocol` 的每个搬走名都还在，且与新家是同一个对象。

    `ProtocolError` 住在 `common.errors`（⑥），搬走的名字必须仍指向**同一**对象。
    """
    for name in sorted(MOVED_NAMES):
        assert hasattr(protocol_mod, name), f"common.protocol 丢了转发名 {name}"
        assert getattr(protocol_mod, name) is getattr(manifest_mod, name), (
            f"common.protocol.{name} 不是 common.manifest.{name}（转发成了副本）"
        )


def test_role_vocabulary_stays_with_the_manifest_not_the_header() -> None:
    """★ 角色词汇只搬「值域」：HTTP 头名与会话解析仍住 `protocol`（请求头面，不是 manifest）。"""
    assert manifest_mod.ROLES == ("online", "offline")
    assert manifest_mod.ROLE_FIELD == "role"
    for name in ("ROLE_HEADER", "ROLE_HEADER_VALUE", "role_from_header"):
        assert hasattr(protocol_mod, name), f"protocol 丢了 {name}（会话角色面必须留守）"
        assert not hasattr(manifest_mod, name), f"manifest 不该有 {name}（请求头面不属它）"


# ─────────────────────── ⑤ 契约语义没变（功能性） ───────────────────────


def _min_manifest(**over: object) -> dict:
    """一份合法的 kind=ppo manifest（必填齐全，mode=per-tick）。"""
    m: dict = {
        "proto": 1,
        "runId": "run-1",
        "it": 3,
        "job_id": "0123456789abcdef",
        "commit": "abc123",
        "code_sha256": "a" * 64,
        "course": "{}",
        "course_fp": "c" * 64,
        "reward_formula": "dense-v1",
        "formula_hash": "fh",
        "metrics_version": 8,
        "gamma": 0.99,
        "lam": 0.95,
        "mode": "per-tick",
        "seed": "s" * 16,
        "epochs": 2,
        "mb": 4,
        "lr": 1e-3,
        "init_weights_fp": "w" * 64,
        "data_fp": "d" * 64,
        "payload_sha256": "p" * 64,
    }
    m.update(over)
    return m


def test_normalize_manifest_fills_defaults_and_rejects_missing_required() -> None:
    out = manifest_mod.normalize_manifest(_min_manifest())
    assert out["kind"] == "ppo"  # 缺省 kind
    assert out["kl_coef"] == 0.0 and out["slim"] is False  # 可选缺省逐字段补上
    assert out["it"] == 3
    with pytest.raises(ProtocolError, match="缺失必填字段"):
        manifest_mod.normalize_manifest({k: v for k, v in _min_manifest().items() if k != "job_id"})
    with pytest.raises(ProtocolError, match="proto="):
        manifest_mod.normalize_manifest(_min_manifest(proto=2))
    with pytest.raises(ProtocolError, match="kind="):
        manifest_mod.normalize_manifest(_min_manifest(kind="nope"))


def test_normalize_manifest_keeps_kind_redlines() -> None:
    """kind 红线（任务类型在 mode 通道上互斥）——串型必须当场拒收。"""
    # bc：免除 PPO 专有必填，但要求 mode='bc' + arch
    bc = _min_manifest(kind="bc", mode="bc", arch="student")
    for k in manifest_mod.MANIFEST_BC_EXEMPT:
        bc.pop(k)
    assert manifest_mod.normalize_manifest(bc)["kind"] == "bc"
    with pytest.raises(ProtocolError, match="mode='bc'"):
        manifest_mod.normalize_manifest({**bc, "mode": "per-tick"})
    # ppo 只认 per-tick
    with pytest.raises(ProtocolError, match="per-tick"):
        manifest_mod.normalize_manifest(_min_manifest(mode="bc"))


def test_normalize_manifest_rejects_a_typoed_role() -> None:
    """拼错的 role 静默变成 online 正是「看不见」的失败 ⇒ 存在就必须合法。"""
    assert manifest_mod.normalize_manifest(_min_manifest(role="offline"))["role"] == "offline"
    with pytest.raises(ProtocolError, match="role="):
        manifest_mod.normalize_manifest(_min_manifest(role="oflline"))


def test_kind_to_role_map_is_exhaustive_and_role_of_falls_back() -> None:
    """kind→role 穷举；`role_of` 先看 manifest.role，缺失/非法按 kind 兜底（不拒单）。"""
    assert set(manifest_mod.KIND_ROLES) == set(manifest_mod.MANIFEST_KINDS)
    assert manifest_mod.KIND_ROLES["run"] == manifest_mod.ROLE_OFFLINE
    assert all(
        v == manifest_mod.ROLE_ONLINE
        for k, v in manifest_mod.KIND_ROLES.items()
        if k != "run"
    )
    assert manifest_mod.role_of({"kind": "run"}) == "offline"  # 缺 role 字段 ⇒ 按 kind
    assert manifest_mod.role_of({"kind": "ppo", "role": "offline"}) == "offline"  # 字段优先
    assert manifest_mod.role_of({"kind": "unknown-kind"}) == "online"  # 未知 kind 回落


def test_shard_name_roundtrip() -> None:
    assert manifest_mod.shard_name(2, 7) == "rl_s2_seed7"
    assert manifest_mod.parse_shard_name("rl_s2_seed7") == (2, 7)
    assert manifest_mod.parse_shard_name("rl_s2_seed") is None
    assert manifest_mod.parse_shard_name("") is None


def test_data_fp_is_order_insensitive_and_matches_the_declared_set() -> None:
    """`data_fp_entries` 排序后逐字段拼接 ⇒ 声明集相同则指纹相同（与顺序无关）。"""
    e1 = [("rl_s1_seed0", "w1", 1, 0), ("rl_s2_seed1", "w1", 2, 1)]
    assert manifest_mod.data_fp_entries(e1) == manifest_mod.data_fp_entries(list(reversed(e1)))
    assert manifest_mod.data_fp_entries(e1) != manifest_mod.data_fp_entries(e1[:1])


def _argv(stage: int, seed: int) -> list[str]:
    return [
        manifest_mod.ROLLOUT_SCRIPT,
        "--out",
        "w",
        "--weights",
        "init_weights.json",
        "--stages",
        str(stage),
        "--seeds",
        str(seed),
    ]


def test_validate_rollout_spec_normalizes_and_rejects_bad_argv() -> None:
    spec = {"argv": [_argv(1, 0), _argv(2, 1)], "wver": "w1"}
    out = manifest_mod.validate_rollout_spec(spec)
    assert out["wver"] == "w1" and out["workers"] == 1 and out["game_timeout_sec"] == 0.0
    assert out["bun"] == "bun"
    assert out["argv"][0][0] == manifest_mod.ROLLOUT_SCRIPT  # argv[0] 归一化

    # 白名单外脚本
    bad = {"argv": [["tools/sim/export-eval-game.ts", "--out", "w", "--weights", "x"]]}
    with pytest.raises(ProtocolError, match="白名单"):
        manifest_mod.validate_rollout_spec(bad)
    # 绝对路径（节点以 job 目录为 cwd）
    abs_argv = _argv(1, 0)
    abs_argv[abs_argv.index("--out") + 1] = "/tmp/w"
    abs_path = {"argv": [abs_argv]}
    with pytest.raises(ProtocolError, match="必须是相对路径"):
        manifest_mod.validate_rollout_spec(abs_path)
    # 重复声明同一局
    dup = {"argv": [_argv(1, 0), _argv(1, 0)]}
    with pytest.raises(ProtocolError, match="重复声明"):
        manifest_mod.validate_rollout_spec(dup)
    # 显式 0 worker 拒收（不静默改成 1）
    zero = {"argv": [_argv(1, 0)], "workers": 0}
    with pytest.raises(ProtocolError, match=">="):
        manifest_mod.validate_rollout_spec(zero)
    # 未知字段拒收（非忽略）
    with pytest.raises(ProtocolError, match="未知字段"):
        manifest_mod.validate_rollout_spec({**_argv_spec(), "nope": 1})


def _argv_spec() -> dict:
    return {"argv": [_argv(1, 0)]}


def test_iter_declared_entries_mirror_the_argv() -> None:
    """声明集 = argv 逐局（stage, seed）⇒ data_fp 与 argv 不可能漂。"""
    spec = manifest_mod.validate_rollout_spec({"argv": [_argv(1, 0), _argv(2, 3)], "wver": "w9"})
    ents = manifest_mod.iter_declared_entries(spec)
    assert ents == [
        ("rl_s1_seed0", "w9", 1, 0),
        ("rl_s2_seed3", "w9", 2, 3),
    ]
    assert manifest_mod.iter_expected_data_fp(spec) == manifest_mod.data_fp_entries(ents)
