"""test_eval_local_fallback_guard — in-loop eval 只做本机兜底的源码守卫（2026-10-26）。

用户口径：训练课程时 in-loop eval 只有在 rollout/eval 集群**全部不可用**时才启用本机；
健康集群必须纯远端。这里钉住四个 in-loop 派发点（per-tick A-eval / it0 基线 /
收官 drain / stream 旧路径）的接线，防止以后又退回「固定本机份额」：

- 每次 `dispatch_eval_bg(...)` 都必须带 `local_fallback_only=True`；
- 不得再传 `local_gate=self._eval_gate`（不得自建/复用份额门）；
- 不得在 in-loop 文件里 import 或调用 `local_gate_release_plan` / `eval_local_early_epochs`。

手动 evalA / B/C / m1 不走这些文件，份额模式保持原样（`eval_a_once.py` 不在此守卫内）。

**为什么读源码**：这四个派发点没有可调用的运行时入口（组合类 `TrainingLoop` 需要真 torch +
真课程），而「所有 in-loop eval 都用 fallback 模式」正是这次改动的接线契约。用例把问题收集
齐全后一次 `pytest.fail`，避免第一条失败遮住其余派发点（同族：`test_no_sleep_as_sync`）。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
TRAINER = ROOT / "trainer"
IN_LOOP_FILES = (
    TRAINER / "loop_eval.py",
    TRAINER / "loop_baseline.py",
    TRAINER / "rollout_phase.py",
)


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _dispatch_calls(tree: ast.Module) -> list[ast.Call]:
    calls: list[ast.Call] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.id if isinstance(fn, ast.Name) else getattr(fn, "attr", "")
        if name == "dispatch_eval_bg":
            calls.append(node)
    return calls


def test_every_in_loop_dispatch_is_fallback_only() -> None:
    """四个派发点全部显式 fallback-only；少一个就会在健康集群上跑本机。"""
    problems: list[str] = []
    for path in IN_LOOP_FILES:
        calls = _dispatch_calls(_tree(path))
        if not calls:
            problems.append(f"{path.name} 里应有 in-loop eval 派发点")
        for call in calls:
            kw = {k.arg: k.value for k in call.keywords}
            has_fallback = "local_fallback_only" in kw
            if not has_fallback:
                problems.append(f"{path.name} 的 dispatch_eval_bg 没传 fallback 模式")
                continue
            value = kw["local_fallback_only"]
            if not (isinstance(value, ast.Constant) and value.value is True):
                problems.append(
                    f"{path.name} 的 local_fallback_only 必须是字面 True（实际 {ast.dump(value)}）"
                )
    if problems:
        pytest.fail("\n".join(problems))


def test_in_loop_dispatch_has_no_local_gate() -> None:
    """不再有份额门：local_gate 要么缺席，要么显式 None。"""
    problems: list[str] = []
    for path in IN_LOOP_FILES:
        for call in _dispatch_calls(_tree(path)):
            kw = {k.arg: k.value for k in call.keywords}
            gate = kw.get("local_gate")
            if gate is not None and not (
                isinstance(gate, ast.Constant) and gate.value is None
            ):
                problems.append(
                    f"{path.name} 又把 local_gate 接回 in-loop eval 了：{ast.dump(gate)}"
                )
    if problems:
        pytest.fail("\n".join(problems))


def test_in_loop_no_longer_reads_the_share_policy() -> None:
    """份额放行档/提前 epoch 只服务手动份额模式；in-loop 不再读，也不建 Event 门。"""
    problems: list[str] = []
    for path in IN_LOOP_FILES:
        tree = _tree(path)
        names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        for dead in ("local_gate_release_plan", "eval_local_early_epochs"):
            if not (names.isdisjoint({dead}) and attrs.isdisjoint({dead})):
                problems.append(f"{path.name} 仍引用 {dead}（in-loop 应走 fallback-only）")
        event_calls = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "Event"
            and isinstance(n.func.value, ast.Name)
            and n.func.value.id == "threading"
        ]
        if event_calls:
            problems.append(f"{path.name} 又建了 threading.Event 门（in-loop 没有份额门）")
    if problems:
        pytest.fail("\n".join(problems))
