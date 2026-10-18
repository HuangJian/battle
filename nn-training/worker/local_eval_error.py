"""本机直跑评估失败的**保真**类型（plan/eval-gap-honesty D1，2026-10-18）。

为什么单独成家：`run_local_eval_game` 原先只抛
`RuntimeError(f"rc={rc} ({(stderr or stdout)[-160:]})")` —— 取 stderr **尾巴** 160 字符，
而 bun 把错误消息打在栈**前面**（`error: ENOENT: … open '<path>'\n    at main (…:1267)\nBun v1.4.2`）
⇒ 2026-10-18 y22-lr it5 的 83 条 meta 只剩 `at main (…:1267)` + `Bun v1.4.2`，errno 正好被切掉；
同时 `common.distribution.is_transient_error` 收到的是 `RuntimeError`（不是 `OSError`）⇒ 恒判
False ⇒ 本机腿拿不到「瞬断免罚」那条通路。

本模块是 **errno 归一化的单一实现**：消费方（`trainer/eval_dispatch.py` 的 meta 写入、日志行）
只许读 `errno_code` / `msg_head` / `stderr_head` / `stderr_tail`，不得各写一份正则。

`transient` 的判据放在类上（而不是让 `common/` import `worker/`）：`common` 是下层，
`is_transient_error` 按鸭子协议认这个属性（等价于它既有的「OSError ⇒ True」那一档）。
"""

from __future__ import annotations

import re

#: stderr 头部里出现的 errno 记号（bun / node / 子进程都可能带）。**单一实现**。
_ERRNO_RE = re.compile(r"\b(ENOENT|EACCES|EBUSY|EMFILE|ENFILE|EPERM|ENOSPC|EISDIR|ETXTBSY)\b")

#: 可自愈的 errno：忙/句柄耗尽/权限抖动。`ENOENT`（文件没了）与 `ENOSPC`（盘满）**不在**此列
#: ——重试不会把它们变回来，应当走改派或机器级故障处置（plan §3）。
TRANSIENT_ERRNOS: frozenset[str] = frozenset(
    {"EBUSY", "EMFILE", "ENFILE", "EACCES", "EPERM", "ETXTBSY"}
)

#: 头尾各留多少字符：头 = 消息（诊断），尾 = 栈 + 版本（定位）。
_HEAD_CHARS = 400
_TAIL_CHARS = 400


def parse_errno(text: str) -> str:
    """从任意文本里取第一个 errno 记号；没有则空串（**不猜**）。"""
    m = _ERRNO_RE.search(text or "")
    return m.group(1) if m else ""


def first_line(text: str) -> str:
    """首行（人读第一眼要看到的那句：哪个路径、哪个 errno）。"""
    return (text or "").strip().splitlines()[0].strip() if (text or "").strip() else ""


class LocalEvalProcessError(RuntimeError):
    """本机直跑子进程非零退出：**保留 stderr 头尾两段** + 归一化 errno。

    `RuntimeError` 子类 ⇒ 既有 `except Exception` / `except RuntimeError` 调用方零改动。
    """

    def __init__(self, returncode: int, stderr: str = "", stdout: str = "") -> None:
        text = (stderr or stdout or "").strip()
        self.returncode = int(returncode)
        #: 前 400 字符：bun 把 `error: ENOENT … open '<path>'` 打在栈**前面**。
        self.stderr_head = text[:_HEAD_CHARS]
        #: 后 400 字符：栈尾 + `Bun v…`（定位用；旧的 `[-160:]` 只剩这一段）。
        self.stderr_tail = text[-_TAIL_CHARS:]
        self.errno_code = parse_errno(self.stderr_head)
        self.msg_head = first_line(self.stderr_head)
        #: 瞬断判定（`common.distribution.is_transient_error` 按鸭子协议认它）。
        self.transient = self.errno_code in TRANSIENT_ERRNOS
        super().__init__(
            f"rc={self.returncode} local-eval: {self.stderr_head} | tail: {self.stderr_tail}"
        )
