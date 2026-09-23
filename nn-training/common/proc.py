"""common/proc.py —— 子进程捕获 + 版本探测的**唯一入口**。

## 1. `run_capture`：显式 UTF-8 的捕获

裸 `subprocess.run(..., text=True)` 的解码编码由**启动期的 locale** 决定（zh-CN
Windows = cp936），而子进程吐的是 UTF-8 字节 ⇒ 解码在 `subprocess` 的 **reader 线程**
里抛 `UnicodeDecodeError`。后果远不止日志脏（`docs/nn/engineering.md` §19 实测）：

1. 父进程被 `Exception in thread ... _readerthread` traceback 刷屏（实测 65 次/100 局）；
2. **`captured stdout/stderr 直接丢成 None`**——异常死在读线程、`communicate` 不重抛
   ⇒ 失败路径的 `RuntimeError(f"rc={rc} ({stderr[-160:]})")` 只剩 rc，诊断全没，
   **响亮错误变哑巴**。

同族的第二个实例（§30 / `remote/iter_rollout`）：GBK 解码把中文输出读死。修法都是
同一条——显式 `encoding="utf-8", errors="replace"`。本模块把这句写**一次**，
调用点不再有机会漏。仓内曾同时有 13 处裸 `text=True`（其中 4 处是 git 调用，
现状安全但随时会踩），全量收敛到本函数。

⚠ **三者不能用本模块**（见 `common/__init__.py`「谁不能用本包」）：
`remote/tailscale_boot.py`、`remote/notebook_boot.py`、`remote/offline_boot.py`
——它们从 GitHub raw 单独拉取，拿不到 `common` 包；其自身的捕获必须**就地**写全
`encoding="utf-8", errors="replace"`。

## 2. `bun_version`：版本探测的两种历史口径

训练机侧（`rl/queue.py`、`rl/dispatch.py`）要的是「拿不到就 `?`」，节点侧
（`remote/iter_rollout.py`）要的是「拿不到就空串」（自检行里空串 = 没装上）。
两种口径都合法，但**实现**只该有一份 ⇒ 这里收 `fallback` 参数，调用方保留自己的
1 参签名做薄包装（同时保住测试的 `monkeypatch.setattr(mod, "bun_version", ...)` 接缝）。
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path

from platform_utils import POPEN_NO_WINDOW

__all__ = ["POPEN_NO_WINDOW", "bun_version", "run_capture", "version_mm"]

#: `bun --version` 的缺省超时（历史口径：训练机侧 10s）。节点侧自检另传 30s。
_BUN_VERSION_TIMEOUT = 10.0


def run_capture(
    cmd: Sequence[str],
    *,
    cwd: str | Path | None = None,
    timeout: float | None = None,
    input: str | bytes | None = None,  # noqa: A002 — 与 subprocess.run 的形参同名
    env: Mapping[str, str] | None = None,
    check: bool = False,
    encoding: str = "utf-8",
    errors: str = "replace",
) -> subprocess.CompletedProcess[str]:
    """`subprocess.run(capture_output=True, text=True)` 的 UTF-8 封装（见模块 docstring）。

    与裸调用的差异**只有**三处，且都是修 bug：
      * `capture_output=True` 恒定（本来每个调用点都这么写）；
      * `text=True` 恒定，且**总是**显式带 `encoding` / `errors`；
      * 自动带上 `platform_utils.POPEN_NO_WINDOW`（Windows 下不弹黑窗抢焦点）。

    `errors="replace"` 是有意选的：解码失败要变成 `U+FFFD` 而不是丢掉整段输出。
    机器可读通道应为 ASCII/JSON（可解析性不受影响），人类可读行的中文正常显示。
    调用方若确需严格解码（拿编码错误当信号）可显式传 `errors="strict"`。
    """
    return subprocess.run(
        list(cmd),
        cwd=None if cwd is None else str(cwd),
        capture_output=True,
        text=True,
        encoding=encoding,
        errors=errors,
        timeout=timeout,
        input=input,
        env=None if env is None else dict(env),
        check=check,
        **POPEN_NO_WINDOW,
    )


def bun_version(
    bun: str,
    *,
    timeout: float = _BUN_VERSION_TIMEOUT,
    fallback: str = "?",
    require_zero: bool = False,
) -> str:
    """`<bun> --version`；探测失败/空输出返回 `fallback`（**绝不抛**）。

    `fallback` 的两种历史口径见模块 docstring。输出按行取第一行（`bun --version`
    本就是单行，取第一行只是对多余空行/警告行免疫）。

    `require_zero`：非零退出码是否也算「探测失败」。**这不是旋钮，是两个调用点的
    既有差异**，刻意保留而不是「统一」掉（统一会静默改行为）：
      * 训练机侧（`rl/queue.py` / `rl/dispatch.py`）历史实现不看退出码 ⇒ 默认 False；
      * 节点侧自检（`remote/iter_rollout.py`）历史实现 `returncode == 0` 才认 ⇒ 传 True。
    两条路径的差异窗口是「bun 存在、退出码非 0、却打印了版本串」——现实中不存在，
    但把它写成一个参数比写在注释里可靠。
    """
    try:
        proc = run_capture([bun, "--version"], timeout=timeout)
    except Exception:  # 找不到 bun / 超时 / 权限……探测失败都不致命
        return fallback
    if require_zero and proc.returncode != 0:
        return fallback
    out = (proc.stdout or "").strip()
    if not out:
        return fallback
    return out.splitlines()[0].strip() or fallback


def version_mm(version: str) -> str:
    """版本串取 major.minor（`"1.2.3"` → `"1.2"`；`"1"` → `"1"`）。

    用途：节点侧 bun 与训练机 bun 的「同不同代」判据——补丁号差异不影响协议兼容，
    所以比对只到 minor（`rl/dispatch.py` 的 `mm(remote_full) != mm(local_bun)`）。
    非数字/畸形输入原样按 `.` 切分，绝不抛：它出现在派发前的对账路径上，
    把畸形版本号变成异常会把「版本对不上」误报成「派发崩了」。
    """
    return ".".join(str(version).split(".")[:2])
