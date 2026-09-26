"""rl_config_clean —— 一次性清洗 `rl-config.json`（plan/rl-config-cleanup.plan.md §4 S1/S2）。

原则（用户 2026-09-25）：**已经在别处能配的数据就不再往里填**。本工具只做两件有边界的事：

1. **删显式白名单里的键**（`DELETE_PATHS` + `--drop-course <课>`）——不做「未知键一律删」
   （那是 `rl_config_schema.py` 的活，且**只告警不拒**）。
2. **出「课程 × B 类键」覆盖矩阵**（`--matrix`），标出「只靠 rl-config 兜底」的格子；
   全绿的键可经 `--apply --drop-b-class` 删掉（plan §3.2）。

**矩阵范围**（`--scope`，`--matrix` 与 `--drop-b-class` 共用同一判据）：

- `live`（缺省）= 在训课程（`<traj-root>/<课>/training-enabled.txt`，与训练侧
  `loop_plan.enabled_courses` / hub `_course_dir_live` 同一个闸）；
- `all` = `curricula/` 下**全部**课程文件——**超集**于在训课程 ⇒ 「全绿」更强：
  它证明的是「**任何**课程都不靠这条兜底」，而不只是「眼下开着的课不靠」。
  在训集为空（全停课）时这是唯一能得出结论的范围。

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
#: 先出 `--matrix`，**只删「范围内所有课程都显式声明」的键**（plan §3.2），且要过 `MACHINE_KEYS`。
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

#: **机器级**键（名字落在 B 类候选里，但语义是「这台机器/这个进程」而不是「课程兜底」）
#: ⇒ **矩阵全绿也不删**（删了改变的是机器的读数，不是课程的行为）。
MACHINE_KEYS: tuple[str, ...] = (
    # 本机直跑槽位（plan §3.2-4 显式点名；控制台 NodePills 读它）。
    "local_slots",
    # 本机并发容量：`dashboard/src/core/slots.ts::bareCapacity` = `max(rl.workers, rl.local_slots)`。
    # 全课程都在课程文件里写了 `workers`（108/108）⇒ `--matrix` 会判它「全绿」；但 rl-config
    # 里这条是**裸机容量**，删掉 `Number(undefined ?? 0)` = 0 ⇒ `checkCapacity` 把每门课都
    # 报成超量（假红）。与 `local_slots` 同一条理：名字在课程文件里、语义在机器上。
    "workers",
)

#: 课程文件后缀（`all_courses` 扫盘用；与 `load_course_keys` 的查找顺序一致）。
CURRICULUM_GLOB = "*.jsonc"

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

def plan_deletions(
    cfg: dict[str, Any],
    drop_courses: list[str] | None = None,
    b_class_keys: list[str] | None = None,
) -> list[str]:
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
    rl_val = cfg.get("rl")
    rl_block: dict[str, Any] = rl_val if isinstance(rl_val, dict) else {}
    for key in b_class_keys or []:
        if key in rl_block:
            lines.append(f"rl.{key}: {rl_block[key]!r} → 删除（B 类全绿）")
        else:
            lines.append(f"rl.{key}: 不存在，跳过")
    courses = cfg.get("courses")
    if isinstance(courses, dict):
        for name in droppers:
            if name in courses:
                lines.append(f"courses.{name}: {courses[name]!r} → 删除")
            else:
                lines.append(f"courses.{name}: 不存在，跳过")
    return lines


def apply_deletions(
    cfg: dict[str, Any],
    drop_courses: list[str] | None = None,
    b_class_keys: list[str] | None = None,
) -> dict[str, Any]:
    """返回删除白名单键 + `b_class_keys` 后的**新** dict（不原地改）。结构残缺 ⇒ AssertionError。

    `b_class_keys` 由调用方从矩阵全绿集取（见 `b_class_green`），**本函数不自己判全绿**
    ——判据与删除分开，才能 dry-run 先看清单。（机器级键在 `b_class_green` 里已排除。）
    """
    missing = check_structure(cfg)
    if missing:
        raise AssertionError(f"rl-config 结构残缺，拒绝清洗：缺 {missing}")
    out: dict[str, Any] = json.loads(json.dumps(cfg))
    for dotted in DELETE_PATHS:
        _del_path(out, dotted)
    rl_val = out.get("rl")
    if isinstance(rl_val, dict):
        for key in b_class_keys or []:
            rl_val.pop(key, None)
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
    """「全绿」= 范围内所有课程都**不靠 rl-config 兜底**（course/level 显式声明）。空集 ⇒ False。"""
    if not sources:
        return False
    return all(s in ("course", "level") for s in sources)


def verdict_for(key: str, sources: list[str]) -> str:
    """矩阵末列的文字：机器级键单列标注（它们**永不删**，不是「是不是全绿」的问题）。纯函数。"""
    if key in MACHINE_KEYS:
        return "机器级"
    return "是" if is_green(key, sources) else "否"


def load_jsonc(path: Path) -> dict[str, Any]:
    """读 JSONC —— **必须用产品同一个加载器** `rl.jsonc.loads`。

    ★ 2026-09-26 修（回归）：此前是 `strip_comments` + `json.loads`，**漏了去尾逗号**。
    实测 `curricula/*.jsonc` 108 个里 88 个、`levels/*.jsonc` 25 个里 25 个都带尾逗号
    ⇒ `--matrix` 只要遇到一门在训课程就直接 `JSONDecodeError` 崩掉；更糟的是若哪天
    有人在调用点吞掉异常，「文件没读进来」会被读成「课程没声明该键」= 静默删兜底。
    产品侧 `load_course` 走 `rl.jsonc.loads`（`strip_comments` → `_drop_trailing_commas`
    → `json.loads`），本工具必须同源。
    """
    from rl.jsonc import loads

    raw: Any = loads(path.read_text(encoding="utf-8"))
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


def all_courses() -> list[str]:
    """`curricula/*.jsonc` 的全部课程名（stem）——**超集**于在训课程。

    为什么需要这个范围（2026-09-26）：`live`（marker）判据要求有人正开着课；全停课时它是
    空集 ⇒ 什么结论都出不来（空集 `is_green` 恒 False）。而 B 类兜底的**风险面**是
    「任何课程没声明该键时落到 rl-config 的值」⇒「全部课程都不靠它」是**更强**的证据
    （全绿于 `all` ⇒ 全绿于 `live`；反之不然）。
    """
    from rl.config import CURRICULA_DIR

    return sorted(p.name[: -len(".jsonc")] for p in CURRICULA_DIR.glob(CURRICULUM_GLOB))


def live_courses(traj_root: Path) -> list[str]:
    """在训课程 = 存在 `<traj-root>/<课>/training-enabled.txt`（与训练侧 `enabled_courses` /
    hub `_course_dir_live` / 控制台 `state-view` **同一个闸**）。"""
    from common.protocol import COURSE_ENABLE_MARKER

    return sorted(p.parent.name for p in traj_root.glob(f"*/{COURSE_ENABLE_MARKER}"))


def matrix_courses(scope: str, traj_root: Path) -> list[str]:
    """`--scope` → 课程表：`all` = 全部课程文件；`live` = 在训（开课标记）课程。"""
    return all_courses() if scope == "all" else live_courses(traj_root)


def course_rows(course: str, cfg: dict[str, Any]) -> dict[str, tuple[Any, str]]:
    """一门课的 B 类键 → `(值, 来源)`。课程/level 文件**只读一次**（16 个键共用）。"""
    ck = load_course_keys(course)
    lk = load_level_keys(ck)
    return {
        key: resolve_key_source(key, course_keys=ck, level_keys=lk, rl_cfg=cfg)
        for key in B_CLASS_KEYS
    }


def green_keys_for_rows(rows_by_key: dict[str, list[str]]) -> list[str]:
    """从「键 → 各课来源」判全绿；**机器级键（`MACHINE_KEYS`）永远不入列**。纯函数。"""
    return [
        key
        for key in B_CLASS_KEYS
        if key not in MACHINE_KEYS and is_green(key, rows_by_key.get(key, []))
    ]


def b_class_green(cfg: dict[str, Any], courses: list[str]) -> list[str]:
    """范围内全绿且非机器级的 B 类键（= 可安全删的兜底键）。空课程表 ⇒ 空集。"""
    rows_by_key: dict[str, list[str]] = {key: [] for key in B_CLASS_KEYS}
    for course in courses:
        for key, (_, src) in course_rows(course, cfg).items():
            rows_by_key[key].append(src)
    return green_keys_for_rows(rows_by_key)


def build_matrix(cfg: dict[str, Any], courses: list[str]) -> list[str]:
    """markdown 表：B 类键 × `courses`，格 = `值（来源）`；末列「全绿」（机器级单列标注）。"""
    rows = [(c, course_rows(c, cfg)) for c in courses]
    header = "| 键 | " + " | ".join(courses) + " | 全绿 |"
    sep = "|---" * (len(courses) + 2) + "|"
    lines = [header, sep]
    for key in B_CLASS_KEYS:
        cells = [f"{r[key][0]!r}（{r[key][1]}）" for _, r in rows]
        sources = [r[key][1] for _, r in rows]
        lines.append(
            f"| `{key}` | " + " | ".join(cells) + f" | {verdict_for(key, sources)} |"
        )
    return lines


# ------------------------------------------------------------------ CLI

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="清洗 rl-config.json（默认 dry-run，零写盘）")
    ap.add_argument("--config", default="", help="rl-config 路径（缺省走 BCITY_RL_CONFIG / 仓里那份）")
    ap.add_argument("--apply", action="store_true", help="真删（先备份 + sha 回读校验）")
    ap.add_argument("--drop-course", action="append", default=[], help="额外删掉的 courses.<课> 条目（可重复）")
    ap.add_argument("--matrix", action="store_true", help="出「课程 × B 类键」覆盖矩阵后退出")
    ap.add_argument(
        "--scope",
        choices=("live", "all"),
        default="live",
        help="矩阵范围：live = 在训课程（开课标记）；all = curricula/ 全部课程（更强）",
    )
    ap.add_argument(
        "--drop-b-class",
        action="store_true",
        help="--apply 时一并删掉矩阵全绿的 B 类兜底键（机器级键永不在内；缺省关）",
    )
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

    courses = matrix_courses(args.scope, traj_root)
    scope_desc = (
        f"全部课程（curricula/，{len(courses)} 门）"
        if args.scope == "all"
        else f"在训课程（marker 判据，traj 根 = {traj_root}）"
    )

    if args.matrix:
        print(f"# rl-config 清洗矩阵（范围 = {scope_desc}）")
        print("\n".join(build_matrix(cfg, courses)))
        print()
        green = b_class_green(cfg, courses)
        print(f"## 全绿可删的 B 类兜底键：{green or '（无）'}")
        return 0

    # 只有 `--drop-b-class` 才把矩阵全绿集算进删除清单（否则本工具只删 DELETE_PATHS）。
    green_keys = b_class_green(cfg, courses) if args.drop_b_class else []

    print(f"# rl-config 清洗（{'APPLY' if args.apply else 'DRY-RUN'}）—— {config_path}")
    print(f"## 范围：{scope_desc}")
    print("## 逐键 diff")
    for line in plan_deletions(cfg, args.drop_course, green_keys):
        print(f"  {line}")
    print("## 影响面")
    print(f"  当前顶层键：{sorted(cfg)}")
    print(f"  在训课程（marker 判据）：{live_courses(traj_root) or '（无）'}")
    if args.drop_b_class:
        print(f"  全绿 B 类键：{green_keys or '（无）'}（机器级键 {list(MACHINE_KEYS)} 永不删）")
    if not args.no_redact:
        print("  nodes/token 预览（脱敏）：")
        print("    " + json.dumps(desensitize(cfg).get("rl", {}), ensure_ascii=False))

    if not args.apply:
        print("## 未写盘（dry-run）。加 --apply 执行（会先备份）。")
        return 0

    backup = write_backup(config_path)
    print(f"## 已备份 → {backup}（sha256 已回读校验）")
    new_cfg = apply_deletions(cfg, args.drop_course, green_keys)
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
