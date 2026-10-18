"""test_local_eval_error — 本机评估失败的**保真**契约（plan/eval-gap-honesty D1，2026-10-18）。

缺口（y22-lr it5，83 局）：`run_local_eval_game` 抛
`RuntimeError(f"rc={rc} ({(stderr or stdout)[-160:]})")` —— 只留 stderr **尾巴**，而 bun 把
`error: ENOENT: … open '<path>'` 打在栈**前面** ⇒ 83 条 meta 的 `reason` 只剩
`at main (…:1267)` + `Bun v1.4.2`，errno 被切掉；而 `RuntimeError` 不是 `OSError` ⇒
`is_transient_error` 恒 False ⇒ 本机腿拿不到「瞬断免罚」那条通路。

本文件钉三件事：① 头/尾/errno 都在；② 瞬断协议（`transient` → `is_transient_error`），
`ENOENT`/`ENOSPC` **不得**算瞬断；③ `eval_local` 真的抛这个类型（接线断言）。
"""

from __future__ import annotations

import common.distribution as dist
from worker.local_eval_error import LocalEvalProcessError

#: bun 的真实形状：消息在**前**、栈与版本在后（旧实现恰好切掉第一行）。
_BUN = (
    "error: ENOENT: no such file or directory, open "
    "'/tmp/y22-lr/it5/_eval_frozen_weights.json'\n"
    "    at main (file:///repo/tools/sim/export-eval-game.ts:1267:41)\n"
    "    at processTicksAndRejections (node:internal/process/task_queues:95:5)\n"
    "Bun v1.4.2"
)


def test_head_and_tail_both_survive() -> None:
    """T1：头里要有路径与 errno，尾里要有版本（两边都不能丢）。"""
    e = LocalEvalProcessError(1, _BUN)
    assert e.errno_code == "ENOENT"
    # 头 = 第一行整句（路径就在里面）；尾 = 栈尾 + 版本
    assert e.msg_head == (
        "error: ENOENT: no such file or directory, open "
        "'/tmp/y22-lr/it5/_eval_frozen_weights.json'"
    )
    assert e.stderr_tail.endswith("Bun v1.4.2")
    # 整个异常串也带着头尾（日志行/str(e) 的第一眼就有 errno）
    assert str(e).startswith("rc=1 local-eval: error: ENOENT")
    assert str(e).endswith("Bun v1.4.2")


def test_head_is_not_the_tail_slice() -> None:
    """反探针形态：旧的 `[-160:]` 在长栈下拿不到第一行——本类型必须拿得到。"""
    long_stack = "\n".join(f"    at frame{i} (file:///x:{i}:1)" for i in range(40))
    text = "error: EACCES: permission denied, open '/tmp/w.json'\n" + long_stack
    assert text[-160:].find("EACCES") == -1  # 旧实现的视野里没有它
    e = LocalEvalProcessError(1, text)
    assert e.errno_code == "EACCES"
    assert e.msg_head.startswith("error: EACCES: permission denied")


def test_transient_protocol_matches_errno_classification() -> None:
    """T2：可自愈 errno ⇒ `is_transient_error` 为 True；ENOENT / ENOSPC ⇒ False。"""
    # 注意不能拿 EBUSY 当正例：`is_transient_error` 的「文案含 busy」兑底分支也会判 True,
    # 那样这条用例就不区分「认类型」与「认文案」了（反探针会 MISS）。
    perm = LocalEvalProcessError(1, "error: EACCES: permission denied, open 'w.json'")
    assert perm.transient is True
    assert dist.is_transient_error(perm) is True

    missing = LocalEvalProcessError(1, _BUN)
    assert missing.transient is False
    assert dist.is_transient_error(missing) is False, "文件没了重试也不会回来"

    full = LocalEvalProcessError(1, "error: ENOSPC: no space left on device, write")
    assert full.errno_code == "ENOSPC" and full.transient is False


def test_no_errno_is_empty_not_guessed() -> None:
    """没有 errno 记号 ⇒ 空串 + 非瞬断（不猜、不误判成可重试）。"""
    e = LocalEvalProcessError(2, "TypeError: boom")
    assert e.errno_code == "" and e.transient is False
    assert dist.is_transient_error(e) is False
    # stdout 兜底（stderr 为空时）
    only_stdout = LocalEvalProcessError(3, "", "error: EMFILE: too many open files")
    assert only_stdout.errno_code == "EMFILE" and only_stdout.transient is True


def test_run_local_eval_game_raises_the_typed_error(tmp_path, monkeypatch) -> None:
    """接线（行为面）：跑子进程的失败路径必须抛这个类型，且 errno 能穿到调用方。

    不用源码文本判据：把捕获 helper 换成「rc=1 + bun 形 stderr」直接走真函数。
    """
    import subprocess

    import pytest

    import worker.eval_local as ev

    monkeypatch.setattr(
        ev,
        "run_eval_runner_capture",
        lambda *a, **k: subprocess.CompletedProcess(a[0] if a else [], 1, "stdout boom", _BUN),
    )
    with pytest.raises(LocalEvalProcessError) as ei:
        ev.run_local_eval_game(
            "bun",
            "w.json",
            2000,
            860001,
            tmp_path / "out",
            10,
            "hard",
            5.0,
            "deadbeef",
        )
    assert ei.value.returncode == 1
    assert ei.value.errno_code == "ENOENT"
    assert ei.value.msg_head.endswith("_eval_frozen_weights.json'")
