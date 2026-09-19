"""test_batch_eval_wver — B 层 wver 身份契约（2026-09-19）。

agent 的 `/v1/task` 用 `weightsOf(kind, wver)` 在**全量 sha** 的桶里精确查表
（`sampler-agent.ts:452`），所以传 `wver[:16]` 必然 409「wver not cached here」。
实测（2026-09-19 判决批冒烟）：unit 的 2 局全部 409 → requeued → dropped，
日志表象像节点故障，实际是身份截断——本地路径因 exporter 拿到的是同一个截断值
而自洽，所以本地跑的批看不出问题（bug 只在远端显形）。

本用例按 AST 钉住 `batch_eval.py` 的 wver 实参：不得是 `key16` 变量，也不得是
切片表达式（`[:16]` 一类）。新增调用点时同样受约束。
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "rl" / "batch_eval.py"

#: 需要 wver 身份的调用点（远端取任务 / 本机直跑 / manifest 对账）。
WVER_CALLS = ("fetch_task", "run_local_eval_game")


def _callee_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _kw_or_pos(call: ast.Call, name: str, pos: int) -> ast.expr | None:
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value
    if len(call.args) > pos:
        return call.args[pos]
    return None


def _is_truncated(node: ast.expr | None) -> str | None:
    if isinstance(node, ast.Name) and node.id == "key16":
        return "传了 key16（截断量）"
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Slice):
        return "传了切片表达式"
    return None


def _all_calls(tree: ast.AST) -> list[ast.Call]:
    return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]


def test_wver_args_are_full_sha() -> None:
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    checked = 0
    for call in _all_calls(tree):
        name = _callee_name(call)
        if name in WVER_CALLS:
            bad = _is_truncated(_kw_or_pos(call, "wver", 99))
            assert bad is None, f"{name} 的 wver {bad}（agent 桶按全量 sha 存）"
            checked += 1
        elif name == "validate_eval_result":
            # 位置实参 (manifest, expected_wver)：第二个必须与发车用的 wver 同值。
            val = call.args[1] if len(call.args) > 1 else None
            bad = _is_truncated(val)
            assert bad is None, f"validate_eval_result 的 expected_wver {bad}"
            checked += 1
    assert checked >= 3, f"只检查到 {checked} 个 wver 调用点——调用点被改名/移动了？"


def test_god_placeholder_sha_is_the_wver() -> None:
    """god 局也要 POST 占位 `{}` 并把它的 sha 当 wver（否则 /v1/task 必 409）。"""
    src = SRC.read_text(encoding="utf-8")
    assert 'weights_bytes = b"{}"' in src
    assert "wver = hashlib.sha256(weights_bytes).hexdigest()" in src
    # POST 不再是 else 分支：god 也下发（占位）。
    assert "if not god:\n            assert weights_bytes is not None" not in src
