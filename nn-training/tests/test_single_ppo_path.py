"""单一 PPO 路径的**入口面**验收（plan/accident.plan.md §3，2026-09-21）。

「PPO 跑在哪」在训练侧**不是一个概念**：loop 只发布 job 到 hub 队列并等 worker 认领。
本文件把这条口径钉在入口面（旗标 / 属性 / env / 命令行模板），因为这类「旋钮删一半」
的毛病不会让任何行为测试变红——它只是留下一条**静默失效**的承诺（本期事故的根形态：
`--ppo` 漏传 ⇒ 悄悄本机 CPU 跑 13 小时）。

四件事：
  ① argparse 面：无 `--ppo`、无 `args.ppo`（连属性都不该存在）；
  ② 源码面：非测试代码零 `args.ppo` / `getattr(args, "ppo"` / `--remote-degrade-after`
     残留（含被删旗标的名字，防「读一个恒缺省的属性」式僵尸分支复活）；
  ③ env 面：没有任何 `*PPO*BACKEND*` 环境变量读点（launcher env 也是旋钮）；
  ④ 课程面：`extra="forbid"` 下任何 backend 类键仍**响亮失败**（不是被悄悄忽略）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from tests.helpers import source_scan

NN_DIR = Path(__file__).resolve().parents[1]

#: 非测试代码根（tests/ 与 e2e/ 除外：它们要**提到**这些名字来钉「已删除」）。
SOURCE_ROOTS = ("rl", "remote", "train", "ppo", "models", "data")
SOURCE_FILES = ("run_rl.py", "run_rl_cluster.py", "dist_common.py")


def _source_paths() -> list[Path]:
    out: list[Path] = []
    for root in SOURCE_ROOTS:
        d = NN_DIR / root
        if d.is_dir():
            out.extend(p for p in d.rglob("*.py") if "__pycache__" not in p.parts)
    out.extend(p for f in SOURCE_FILES if (p := NN_DIR / f).exists())
    return out


def _option_strings(argv_parser) -> list[str]:
    """全部 option string（扫 `_actions` 而不是 `format_help()`：help 文本里有 `%` + 全角字符，
    argparse 的 `%` 展开会直接抛 ValueError——想读 help 得先踩这个坑）。"""
    return [s for a in argv_parser._actions for s in a.option_strings]


def test_argparser_has_no_ppo_flag_nor_attribute() -> None:
    """`--ppo` 旗标与 `args.ppo` 属性**都不存在**（不是一个「留着但没用」的空壳）。"""
    from rl.cli import build_argparser

    ap = build_argparser("per-tick", {})
    assert not [s for s in _option_strings(ap) if "ppo" in s.lower()]
    ns = ap.parse_args([])
    assert not hasattr(ns, "ppo")
    # 反向：`--ppo remote` 现在必须是**未识别参数**（响亮失败，不是静默吃下）
    with pytest.raises(SystemExit):
        ap.parse_args(["--ppo", "remote"])


def test_no_degrade_chain_flags() -> None:
    """`--remote-degrade-after`（整条降级链的入口）同样不存在。"""
    from rl.cli import build_argparser

    ap = build_argparser("per-tick", {})
    assert not [s for s in _option_strings(ap) if "degrade" in s.lower()]
    assert not hasattr(ap.parse_args([]), "remote_degrade_after")


def test_source_has_no_ppo_placement_reads() -> None:
    """非测试代码里零 `args.ppo` / `getattr(args, "ppo"` 残留（AST 级，不误伤注释/文档串）。

    为什么要扫源码而不是只测行为：`getattr(args, "ppo", "local")` 这类写法在旗标删掉后
    **不报错**，只是恒取缺省值——本次 §3 落地时它就制造过两个僵尸分支（导出全拒、
    本机份额错拿 on_join 档）。行为测试抓不到，只有语法面能钉。

    用 AST 而不是文本行：迁移说明写在注释与 docstring 里（「原先这里是 …」），行级正则
    会把**记录**当成**代码**误杀。
    """
    offenders: list[str] = []
    for p in _source_paths():
        tree = source_scan.parse(str(p), errors="replace")
        for node in ast.walk(tree):
            # args.ppo
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "ppo"
                and isinstance(node.value, ast.Name)
                and node.value.id == "args"
            ):
                offenders.append(f"{p.relative_to(NN_DIR)}:{node.lineno}: args.ppo")
            # getattr(args, "ppo"[, ...])
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "ppo"
            ):
                offenders.append(f"{p.relative_to(NN_DIR)}:{node.lineno}: getattr(args, 'ppo')")
    assert not offenders, "PPO 位置判定残留：\n" + "\n".join(offenders)


def test_no_ppo_backend_env_knob() -> None:
    """env 面也没有旋钮：没有任何 `PPO_BACKEND` 类环境变量读点。"""
    offenders: list[str] = []
    pat = re.compile(r"PPO[_A-Z]*BACKEND|BACKEND[_A-Z]*PPO")
    for p in _source_paths():
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if pat.search(line):
                offenders.append(f"{p.relative_to(NN_DIR)}:{i}: {line.strip()}")
    assert not offenders, "backend env 旋钮残留：\n" + "\n".join(offenders)


def test_course_schema_rejects_backend_keys(tmp_path: Path) -> None:
    """课程文件里任何 backend 类键**响亮失败**（`extra="forbid"` 仍在，不是放宽校验）。"""
    from rl.config import CourseConfig

    with pytest.raises(Exception) as ei:
        CourseConfig.model_validate({"name": "c", "ppo_backend": "remote"})
    assert "ppo_backend" in str(ei.value)
