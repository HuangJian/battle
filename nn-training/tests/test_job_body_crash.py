"""作业体崩溃的归类与响亮回传（plan/accident.plan.md §4.2，2026-09-21）。

背景：`restore`（model/opt/ref 装载）与 `grad`（PPO 更新）里的异常原先落到 `worker_loop`
的 `except Exception` ⇒ 只写一行云机日志就重领。同一份字节上这个崩溃会**逐一重演**，而训练侧
只看到超时——与 §4 毒包事故同一个形状（真实原因在最里面，外面只剩一行「超时」）。

处置：`job_body_error(phase, e)` 把作业体崩溃**归类**——
  * 内容/模型决定性（形状不符、卷积不兼容、缺字节…）⇒ `ProtocolError`（带 traceback 摘要，
    由 `worker_loop` 既有的 `except ProtocolError` 分支 `report_job_failure` 上报 ⇒ hub 终局
    failed ⇒ 训练侧 `JobFailedError` 带原因停腿）；
  * 真瞬态（OOM / MemoryError / OSError）⇒ **原样返回**（调用方 raise，照旧靠租约过期/
    release 重领）——把「换台机器就能跑」钉成终局就是拿停腿换响亮，代价不对等。

两条防线各自独立：
  ① 熔断（§4.1）兜「未知死法」（worker 连报都报不上来，如进程被杀）；
  ② 本节的归类兜「已知死法」（能报，但原先被当成瞬态静默重领）。
判错方向的代价不对称 ⇒ 拿不准时归瞬态（熔断会兜住重复），错钉终局则没有第二道网。
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import ProtocolError, RetryableError
from remote.worker import job_body_error

#: `torch.cuda.OutOfMemoryError` 的真实类名（torch 里这个类就叫这个）——按名字判，
#: 不依赖 torch 能否导入（worker 的 CPU 路径不拉 torch）。
OutOfMemoryError = type("OutOfMemoryError", (RuntimeError,), {})


class _FakeTransportError(Exception):
    """仅靠**消息**命中的一档（类名不在名单里）。"""


# ────────────────────────── ① 归类 ──────────────────────────


def test_content_deterministic_crash_becomes_protocol_error() -> None:
    """形状不符 / 反序列化失败这类崩溃 ⇒ ProtocolError（带阶段名与原始类型，便于人读）。"""
    for e in (
        RuntimeError("Error(s) in loading state_dict for ActorCritic: size mismatch for fc.weight"),
        KeyError("policy_head.weight"),
        ValueError("could not broadcast input array"),
        EOFError("unexpected EOF"),
    ):
        got = job_body_error("restore（model/opt 恢复）", e)
        assert isinstance(got, ProtocolError), type(e).__name__
        assert "restore" in str(got)
        assert type(e).__name__ in str(got)
    got = job_body_error("grad（PPO 更新）", RuntimeError("boom"))
    assert isinstance(got, ProtocolError) and "grad" in str(got)


def test_transient_crash_is_passed_through() -> None:
    """真瞬态原样返回 ⇒ 调用方 raise 它，照旧重领（不能钉成终局）。"""
    cases = [
        MemoryError("cannot allocate"),
        OSError("No space left on device"),
        RetryableError("connection reset"),
        OutOfMemoryError("CUDA out of memory. Tried to allocate 2.00 GiB"),  # 类名判据
        _FakeTransportError("CUDA out of memory. Tried to allocate 2.00 GiB"),  # 消息判据
        RuntimeError("CUDA out of memory. Tried to allocate 512.00 MiB"),
        RuntimeError("CUDA error: initialization error"),
    ]
    for e in cases:
        assert job_body_error("grad（PPO 更新）", e) is e, type(e).__name__


def test_classifier_returns_exception_not_raises() -> None:
    """接口契约：**返回**异常（调用方 `raise job_body_error(...) from e` 保住异常链）。"""
    got = job_body_error("restore", RuntimeError("x"))
    assert isinstance(got, BaseException)
    with pytest.raises(ProtocolError):
        raise got


# ────────────────────────── ② 调用点守卫（run_job 太大，无法单测驱动） ──────────────────────────


def _run_job_node() -> ast.FunctionDef:
    """四处 `job_body_error` 包装的宿主：**训练核**（2026-09-24 S9 从 `run_job` 下沉）。

    它们服务的是「跑一个轮次」那条链（opt/ref/demo 装载与 PPO 更新），所以随训练核搬到
    `remote/train_core.py::run_training_core`——作业壳（`worker.run_job`）只剩网络/校验/上报。
    """
    src = (ROOT / "remote" / "train_core.py").read_text(encoding="utf-8")
    return next(
        n
        for n in ast.parse(src).body
        if isinstance(n, ast.FunctionDef) and n.name == "run_training_core"
    )


def _job_body_error_call_phases(fn: ast.FunctionDef) -> list[str]:
    """run_job 里每个 `job_body_error("<phase>", e)` 调用的 phase 字面量（按出现序）。"""
    out: list[str] = []
    for n in ast.walk(fn):
        if (
            isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name)
            and n.func.id == "job_body_error"
            and n.args
            and isinstance(n.args[0], ast.Constant)
        ):
            out.append(str(n.args[0].value))
    return out


def test_run_job_wraps_restore_and_grad_phases() -> None:
    """四处包装必须在：opt/init 恢复、kickstart ref 装载、demo bank 装载、grad（PPO 更新）。

    少一处的症状都是**静默**的：那个阶段退回「一行云机日志 + 无限重领」，训练侧只剩超时
    ——正是 §4 事故的形状。所以用调用点清单钉住（这条链无法单测驱动，本仓既有同款源码
    守卫：`tests/test_worker_device.py`）。
    """
    phases = _job_body_error_call_phases(_run_job_node())
    assert len(phases) == 4, f"应有 4 处包装（restore×3 + grad），实得 {phases}"
    assert any("restore" in p and "opt" in p for p in phases), phases
    assert any("restore" in p and "ref" in p for p in phases), phases
    assert any("restore" in p and "demo" in p for p in phases), phases
    assert any("grad" in p for p in phases), phases


def test_wrapped_blocks_re_raise_protocol_error_untouched() -> None:
    """包装块必须先 `except ProtocolError: raise`——否则上游已判定的确定性失败被降级重述。"""
    fn = _run_job_node()
    handlers_with_body_error = 0
    for n in ast.walk(fn):
        if not isinstance(n, ast.Try):
            continue
        calls_body_error = any(
            isinstance(c, ast.Call)
            and isinstance(c.func, ast.Name)
            and c.func.id == "job_body_error"
            for h in n.handlers
            for c in ast.walk(h)
        )
        if not calls_body_error:
            continue
        handlers_with_body_error += 1
        bare = [
            h for h in n.handlers if isinstance(h.type, ast.Name) and h.type.id == "ProtocolError"
        ]
        assert bare, "包装块缺少 `except ProtocolError: raise`（会覆盖上游判定）"
    assert handlers_with_body_error == 4, handlers_with_body_error
