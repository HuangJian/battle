"""rl_config_clean —— 一次性清洗 `rl-config.json`（plan/rl-config-cleanup.plan.md §4 S1/S2）。

原则（用户 2026-09-25）：**已经在别处能配的数据就不再往里填**。本工具只做两件有边界的事：

1. **删显式白名单里的键**（`DELETE_PATHS` + `--drop-course <课>`）——不做「未知键一律删」
   （那是 `rl_config_schema.py` 的活，且**只告警不拒**）。
2. **出「在训课程 × B 类键」覆盖矩阵**（`--matrix`），标出「只靠 rl-config 兜底」的格子——
   B 类键**删不删由矩阵决定**，本工具不自动删它们。

默认 `--dry-run`：打印逐键 diff、影响面、备份路径，**零写盘**。`--apply`：先写
`rl-config.json.bak.<YYYYMMDD-HHMMSS>`（sha256 回读校验）再删。

**脱敏**：`rl.remote_token` / `nodes[].authKey` 只以 `…（len=N）` 出现，任何输出（含 dry-run、
备份日志、报错）都不得泄露原文。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

#: 本次清洗要删的点号路径（plan §3.1 + §0.5.1）。**只删这里列出的键。**
DELETE_PATHS: tuple[str, ...] = (
    "intent_rl",  # 已下线（用户 2026-09-26：删配置、留代码）
    "policy.upgradeBranch",  # 事故载体；读点已随本次一并去掉
    "policy.minDiskFreeMB",  # 零消费者
    "policy.streamKlCapIntent",  # intent 专属
    "policy.streamWaveGamesIntent",  # intent 专属
    "rl.stream",  # 单一 PPO 路径下恒 0（用户 2026-09-26：删配置、留代码）
    "rl.double_buffer",  # 同上
    "rl.precollect_early",  # 同上
)

#: B 类（全局缺省）候选键：课程文件 schema 有同名键，rl-config 里的值只是兜底。
#: **不由本工具删除**——先出 `--matrix`，只删「所有在训课程都显式声明」的键（plan §3.2）。
B_CLASS_KEYS: tuple[str, ...] = (
    "difficulty",
    "max_ticks",
    "seed_rotate",
    "mb",
    "workers",
    "keep_iters",
    "eval_window_sec",
    "total_stages",
    "rotate_stages",
    "seed",
    "lr",
    "epochs",
    "gamma",
    "lam",
    "target_transitions",
    "local_slots",
)

#: 必须仍然存在的顶层键（删到结构残缺 = 响亮拒启，而不是写坏盘）。`courses` 允许整块消失。
REQUIRED_SECTIONS: tuple[str, ...] = ("version", "policy", "rl", "nodes")

_SECRET_PATHS: tuple[str, ...] = ("rl.remote_token", "rl.remote_hub_url")


# ------------------------------------------------------------------ 脱敏 / 结构

def redact(value: Any) -> str:
    """凭据只露长度；非字符串值不泄露形状细节。"""
    if isinstance(value, str):
        return f"…（len={len(value)}）"
    return "…"


def desensitize(cfg: dict[str, Any]) -> dict[str, Any]:
    """返回一份**可安全打印**的副本：`remote_token` / 节点 `authKey` 换成占位符。

    用于 dry-run 的「影响面」输出与任何日志路径——**任何输出都不得带出凭据**（红线 1）。
    """
    out: dict[str, Any] = json.loads(json.dumps(cfg))  # 深拷贝（cfg 是纯 JSON 树）
    rl = out.get("rl")
    if isinstance(rl, dict):
        for key in ("remote_token",):
            if key in rl:
                rl[key] = redact(rl[key])
    for node in out.get("nodes") or []:
        if isinstance(node, dict) and "authKey" in node:
            node["authKey"] = redact(node["authKey"])
    return out


def check_structure(cfg: dict[str, Any]) -> list[str]:
    """返回缺失的必需顶层键（空 = 完整）。"""
    return [k for k in REQUIRED_SECTIONS if k not in cfg]


# ------------------------------------------------------------------ 删除（纯）

def plan_deletions(cfg: dict[str, Any], drop_courses: list[str] | None = None) -> list[str]:
    """逐键 diff 行（`键：旧值 → 删除` 或 `键：不存在，跳过`）。**只读，不改 cfg。**"""
    droppers = list(drop_courses or [])
    lines: list[str] = []
    for dotted in DELETE_PATHS:
        val, present = _get_path(cfg, dotted)
        if not present:
            lines.append(f"{dotted}: 不存在，跳过")
        elif dotted in _SECRET_PATHS or "token" in dotted or "authKey" in dotted:
            lines.append(f"{dotted}: {redact(val)} → 删除")
        else:
            lines.append(f"{dotted}: {val!r} → 删除")
    courses = cfg.get("courses")
    if isinstance(courses, dict):
        for name in droppers:
            if name in courses:
                lines.append(f"courses.{name}: {courses[name]!r} → 删除")
            else:
                lines.append(f"courses.{name}: 不存在，跳过")
    return lines


def apply_deletions(cfg: dict[str, Any], drop_courses: list[str] | None = None) -> dict[str, Any]:
    """返回删除白名单键后的**新** dict（不原地改）。结构残缺 ⇒ AssertionError。"""
    missing = check_structure(cfg)
    if missing:
        raise AssertionError(f"rl-config 结构残缺，拒绝清洗：缺 {missing}")
    out: dict[str, Any] = json.loads(json.dumps(cfg))
    for dotted in DELETE_PATHS:
        _del_path(out, dotted)
    if drop_courses:
        courses = out.get("courses")
        if isinstance(courses, dict):
            for name in drop_courses:
                courses.pop(name, None)
            if not courses:
                out.pop("courses", None)
    return out


def _get_path(cfg: dict[str, Any], dotted: str) -> tuple[Any, bool]:
    node: Any = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return None, False
        node = node[part]
    return node, True


def _del_path(cfg: dict[str, Any], dotted: str) -> None:
    parts = dotted.split(".")
    node: Any = cfg
    for part in parts[:-1]:
        if not isinstance(node, dict) or part not in node:
            return
        node = node[part]
    if isinstance(node, dict):
        node.pop(parts[-1], None)


# ------------------------------------------------------------------ 备份 / 写盘

def write_backup(config_path: Path, *, now: datetime | None = None) -> Path:
    """写 `rl-config.json.bak.<YYYYMMDD-HHMMSS>` 并**回读校验 sha256**；不符即抛。

    刻意**不用** `common.hashing.sha256_file`：本工具要能独立于包布局跑，且
    `tests/test_common_layer.py` 钉住 `sha256_file` 只在 `common/hashing.py` 定义一处
    （同名的第二处定义会直接让门禁转红）。这里只需一次文件对账，直接 hashlib。
    """
    stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
    backup = config_path.with_name(f"{config_path.name}.bak.{stamp}")
    shutil.copy2(config_path, backup)
    src = hashlib.sha256(config_path.read_bytes()).hexdigest()
    dst = hashlib.sha256(backup.read_bytes()).hexdigest()
    if src != dst:
        raise AssertionError(f"备份校验失败：{backup} sha 与源不符（{src} != {dst}）")
    return backup


def write_config(config_path: Path, cfg: dict[str, Any]) -> None:
    """原子写回（临时文件 + replace）：避免半截 JSON 落在唯一开训入口上。"""
    text = json.dumps(cfg, indent=2, ensure_ascii=False) + "\n"
    tmp = config_path.with_suffix(config_path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(config_path)


# ------------------------------------------------------------------ 矩阵（§4 S2）

def resolve_key_source(
    key: str,
    *,
    course_keys: dict[str, Any],
    level_keys: dict[str, Any],
    rl_cfg: dict[str, Any],
) -> tuple[Any, str]:
    """B 类键的生效来源（高→低）：课程文件 > level 文件 > rl-config > 缺省。**纯函数。**"""
    if key in course_keys:
        return course_keys[key], "course"
    if key in level_keys:
        return level_keys[key], "level"
    rl_val = rl_cfg.get("rl")
    rl_block: dict[str, Any] = rl_val if isinstance(rl_val, dict) else {}
    if key in rl_block:
        return rl_block[key], "rl-config"
    return None, "default"


def is_green(key: str, sources: list[str]) -> bool:
    """「全绿」= 所有在训课程都**不靠 rl-config 兜底**（course/level 显式声明）。空集 ⇒ False。"""
    if not sources:
        return False
    return all(s in ("course", "level") for s in sources)


def load_jsonc(path: Path) -> dict[str, Any]:
    from rl.jsonc import strip_comments

    raw: Any = json.loads(strip_comments(path.read_text(encoding="utf-8")))
    return raw if isinstance(raw, dict) else {}


def load_course_keys(course: str) -> dict[str, Any]:
    """课程文件的**原始顶层键**（不经 pydantic 补默认——补了就分不出「显式声明」）。"""
    from rl.config import CURRICULA_DIR

    for suffix in (".jsonc", ".bc.jsonc"):
        p = CURRICULA_DIR / f"{course}{suffix}"
        if p.exists():
            return load_jsonc(p)
    return {}


def load_level_keys(course_keys: dict[str, Any]) -> dict[str, Any]:
    from rl.config import LEVELS_DIR

    level = str(course_keys.get("level") or "").strip()
    if not level:
        return {}
    p = LEVELS_DIR / f"{level}.jsonc"
    return load_jsonc(p) if p.exists() else {}


def live_courses(traj_root: Path) -> list[str]:
    """在训课程 = 存在 `<traj-root>/<课>/training-enabled.txt`（与训练侧 `enabled_courses` /
    hub `_course_dir_live` / 控制台 `state-view` **同一个闸**）。"""
    from common.protocol import COURSE_ENABLE_MARKER

    return sorted(p.parent.name for p in traj_root.glob(f"*/{COURSE_ENABLE_MARKER}"))


def build_matrix(cfg: dict[str, Any], traj_root: Path) -> list[str]:
    """markdown 表：B 类键 × 在训课程，格 = `值（来源）`；末列标「全绿」。"""
    courses = live_courses(traj_root)
    header = "| 键 | " + " | ".join(courses) + " | 全绿 |"
    sep = "|---" * (len(courses) + 2) + "|"
    lines = [header, sep]
    for key in B_CLASS_KEYS:
        cells: list[str] = []
        sources: list[str] = []
        for course in courses:
            ck = load_course_keys(course)
            val, src = resolve_key_source(
                key, course_keys=ck, level_keys=load_level_keys(ck), rl_cfg=cfg
            )
            cells.append(f"{val!r}（{src}）")
            sources.append(src)
        lines.append(f"| `{key}` | " + " | ".join(cells) + f" | {'是' if is_green(key, sources) else '否'} |")
    return lines


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清洗 rl-config.json（默认 dry-run，零写盘）")
    ap.add_argument("--config", default="", help="rl-config 路径（缺省走 BCITY_RL_CONFIG / 仓里那份）")
    ap.add_argument("--apply", action="store_true", help="真删（先备份 + sha 回读校验）")
    ap.add_argument("--drop-course", action="append", default=[], help="额外删掉的 courses.<课> 条目（可重复）")
    ap.add_argument("--matrix", action="store_true", help="出「在训课程 × B 类键」覆盖矩阵后退出")
    ap.add_argument("--traj-root", default="", help="在训判据的 traj 根（缺省 <repo>/tmp）")
    ap.add_argument("--no-redact", action="store_true", help="禁脱敏（**仅本地排障**，默认关）")
    args = ap.parse_args(argv)

    import dist_common

    nn_dir = Path(__file__).resolve().parent.parent
    repo_root = nn_dir.parent
    config_path = Path(args.config) if args.config else Path(dist_common.rl_config_path())
    traj_root = Path(args.traj_root) if args.traj_root else repo_root / "tmp"

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    missing = check_structure(cfg)
    if missing:
        print(f"✗ 结构残缺（缺 {missing}）——拒绝清洗：{config_path}", file=sys.stderr)
        return 2

    if args.matrix:
        print(f"# rl-config 清洗矩阵（traj 根 = {traj_root}）")
        print("\n".join(build_matrix(cfg, traj_root)))
        return 0

    print(f"# rl-config 清洗（{'APPLY' if args.apply else 'DRY-RUN'}）—— {config_path}")
    print("## 逐键 diff")
    for line in plan_deletions(cfg, args.drop_course):
        print(f"  {line}")
    print("## 影响面")
    print(f"  当前顶层键：{sorted(cfg)}")
    print(f"  在训课程（marker 判据）：{live_courses(traj_root) or '（无）'}")
    if not args.no_redact:
        print("  nodes/token 预览（脱敏）：")
        print("    " + json.dumps(desensitize(cfg).get("rl", {}), ensure_ascii=False))

    if not args.apply:
        print("## 未写盘（dry-run）。加 --apply 执行（会先备份）。")
        return 0

    backup = write_backup(config_path)
    print(f"## 已备份 → {backup}（sha256 已回读校验）")
    new_cfg = apply_deletions(cfg, args.drop_course)
    write_config(config_path, new_cfg)
    print(f"## 已写回。剩余顶层键：{sorted(new_cfg)}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    # 以脚本直跑时 `sys.path[0]` 是 `tools/` ⇒ 仓库内 `common` / `rl` / `dist_common` 找不到；
    # 把 nn-training/ 塞回去（测试里由 tests/conftest.py 做同一件事）。
    _NN_DIR = Path(__file__).resolve().parent.parent
    if str(_NN_DIR) not in sys.path:
        sys.path.insert(0, str(_NN_DIR))
    raise SystemExit(main())
