"""subproc_util — 测试侧 subprocess 统一出口：显式 utf-8 解码 + 端口竞态消化。

裸 ``subprocess.run(..., text=True)`` 的解码端编码 = ``locale.getpreferredencoding(False)``
（解释器**启动期**决定，运行时改不了；zh-CN Windows = cp936，coding agent 沙箱间
PYTHONUTF8 / PYTHONIOENCODING 又各异）。子进程按其它编码输出时——agent 沙箱常设
``PYTHONIOENCODING=utf-8``、bun 管道恒 UTF-8、Python 3.15 起（PEP 686）默认 UTF-8——
CPython 读线程在 ``subprocess._readerthread`` 里解码失败死亡 → ``stdout=None`` →
远处的 ``json.loads(None)`` 抛 TypeError。跨 agent/沙箱唯一确定的做法：

  * 子侧：CLI 入口 ``platform_utils.force_utf8_stdio()`` 把字节流钉成 UTF-8；
  * 父侧：本 helper 显式 ``encoding="utf-8"`` 解码，并对 ``stdout is None``
    （读线程死亡的唯一痕迹）就地报错，而不是让它漏到远处的 TypeError。

背景与复现矩阵：docs/nn/engineering.md §3（2026-09-13，``test_cli_dry_run_exit_code``
在 PYTHONIOENCODING=utf-8 且无 PYTHONUTF8 的机器上必红）。
"""

from __future__ import annotations

import re
import socket
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, TypeVar

#: 服务进程「端口已被占用」的拒绝文案（`remote/_port_guard.py::ensure_port_free`）。
#: 不硬编码在调用点：`tests/test_subproc_util.py` 拿真守卫把它钉死。
PORT_TAKEN_MARKER = "已被占用——拒绝启动"

#: 撞端口后换端口重试的上限（xdist 并行下实测 1 次就够，余量留 5 次）。
SPAWN_PORT_ATTEMPTS = 5

#: 服务自报监听的文案（hub-server / worker-serve 都印这句）。
_LISTENING_RE = re.compile(r"listening on .*?:(\d+)")

T = TypeVar("T")


def run_utf8(cmd: list[str], **kw: Any) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` 的 UTF-8 合约版（测试专用）。

    强制 ``encoding="utf-8"``（隐含 text 模式）；其余 kwargs 透传（cwd/timeout/
    env/**_POPEN_NO_WINDOW 等）。默认 ``capture_output=True``——读线程死亡时
    stdout 会是 ``None``，这里就地断言并附上 stderr，把「编码不匹配」从远处
    ``json.loads(None)`` 的 TypeError 变成本 helper 里的明确报错。
    """
    assert not ("text" in kw or "universal_newlines" in kw or "encoding" in kw), (
        "run_utf8 自带 encoding='utf-8'——不要与 text/encoding 混传（见模块 docstring）"
    )
    kw.setdefault("capture_output", True)
    out = subprocess.run(cmd, encoding="utf-8", errors="strict", **kw)
    assert out.stdout is not None, (
        f"[run_utf8] stdout 未捕获（子进程输出解码失败/读线程死亡？）stderr={out.stderr!r}"
    )
    return out


def free_port(host: str = "127.0.0.1") -> int:
    """探一个**此刻**空闲的端口号。

    注意它只是「此刻空闲」，不是「归你」：`bind(0)` 之后立刻 close，端口回到
    内核池里。子进程要稍后才 bind 时，窗口足以让另一个 xdist worker 抢走它
    —— 要交到手的端口请用 `spawn_bound_port()`（它把这段 TOCTOU 消化掉）。
    """
    with socket.socket() as s:
        s.bind((host, 0))
        return int(s.getsockname()[1])


@dataclass
class BoundServer:
    """`spawn_bound_port()` 的产物：**已确认自己在监听**的真服务进程。"""

    port: int
    proc: subprocess.Popen[str]
    #: 子进程输出（reader 线程实时收，进程活着也能安全取尾部做诊断）。
    lines: list[str] = field(default_factory=list)

    def tail(self, limit: int = 800) -> str:
        """已捕获输出的尾部——诊断消息专用（永不阻塞，进程活着也能调）。"""
        return "\n".join(self.lines)[-limit:]


class PortStolenError(Exception):
    """整个场景作废：端口在探测后被别的进程抢走 —— 换一个端口重跑即可（非被测行为）。"""


def retry_on_port_stolen(
    scenario: Callable[[int], T], *, attempts: int = SPAWN_PORT_ATTEMPTS
) -> T:
    """跑 `scenario(port)`；它抛 `PortStolenError` 就换端口重跑（其余异常原样上抛）。

    给「**多个**进程抢同一个端口」这类无法用 `spawn_bound_port()` 表达的场景（典型：三启
    同时启动、期望恰好一个成为实例）：这类用例必须自己选端口并交给 N 个子进程，选端口的
    TOCTOU 窗口照样存在。判据由场景自己给：全灭**且**输出里出现 `PORT_TAKEN_MARKER`
    ⇒ 抛 `PortStolenError`（端口被外人抢走，不是被测行为不对）；其它失败照旧红。

    调用方仍需自己收尸：`scenario` 里已死的进程重跑时会再被 `_kill` 一次（无害）。
    """
    for attempt in range(1, attempts + 1):
        port = free_port()
        try:
            return scenario(port)
        except PortStolenError as e:
            print(
                f"[subproc_util] 场景因端口 {port} 被抢而作废（第 {attempt}/{attempts} 次）——{e}",
                flush=True,
            )
    raise AssertionError(
        f"连续 {attempts} 次都撞上端口竞争（xdist 并行）；若本机有残留 hub/worker 进程请先清理"
    )


class _Drain(threading.Thread):
    """把子进程 stdout 逐行收进 list（同时防止满管道把子进程卡死）。"""

    def __init__(self, proc: subprocess.Popen[str], lines: list[str]) -> None:
        super().__init__(daemon=True)
        self._proc = proc
        self._lines = lines

    def run(self) -> None:
        out = self._proc.stdout
        if out is None:
            return
        try:
            for line in out:
                self._lines.append(line.rstrip("\n"))
        except Exception:  # 子进程被杀 / 管道被关：诊断面绝不能再抛
            return


def spawn_bound_port(
    make_argv: Callable[[int], list[str]],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    attempts: int = SPAWN_PORT_ATTEMPTS,
    listen_timeout: float = 20.0,
) -> BoundServer:
    """在探测到的空闲端口上起一个真服务进程；撞端口就换一个重试，直到**它自己**监听。

    为什么需要它（2026-09-19 实测：R4 提交被 pre-commit 拦下的直接原因）：
    `free_port()` 是「bind(0) → close → **交给别的进程** bind」的 TOCTOU。串行跑窗口只有
    微秒级，但 gate 用 pytest xdist：另一个 worker 探测时会拿到刚被释放的同一端口，
    于是先绑的赢、后绑的撞 `_port_guard` 的「禁止双监听」当场退出 —— 表现成与本用例
    毫无关系的假红（`hub-server 未就绪或课程表不对`，真因埋在子进程输出里）。

    成功判据不是「端口有人监听」（那可能是**别人**的服务，甚至会让我们对着陌生 hub
    跑完整用例），而是**这个**子进程自报了 `listening on <host>:<port>`；撞端口的
    子进程会带着 `PORT_TAKEN_MARKER` 退出，被识别出来换端口重试。

    `listen_timeout` 内既没自报也没退出 ⇒ 当作成功（日志文案变了不该变成硬失败），
    就绪等待交给调用方。返回的 `BoundServer.proc` 已在跑，收尸由调用方负责。
    """
    for attempt in range(1, attempts + 1):
        port = free_port()
        proc = subprocess.Popen(
            make_argv(port),
            cwd=cwd,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            # 显式 utf-8 + replace（与 run_utf8 同一条理由，AGENTS §17.6）：服务子进程按
            # platform_utils.force_utf8_stdio() / PYTHONIOENCODING=UTF-8 写 UTF-8，而
            # `text=True` 按**控制台代码页**解码（zh-CN Windows = gbk）——中文输出会让读线程
            # 抛 UnicodeDecodeError 静默死掉（`stdout` 变 None / 此处 `lines` 永远空 ⇒ 自报
            # 监听看不见、端口竞争也识别不出）。`errors="replace"` 保证读线程绝不因编码而死。
            encoding="utf-8",
            errors="replace",
        )
        lines: list[str] = []
        drain = _Drain(proc, lines)
        drain.start()
        if _await_listening(proc, lines, drain, port, listen_timeout):
            return BoundServer(port=port, proc=proc, lines=lines)
        if not any(PORT_TAKEN_MARKER in ln for ln in lines):
            raise AssertionError(
                f"服务进程启动即退出（非端口冲突，不该重试）；输出：{lines[-8:]}"
            )
        print(
            f"[subproc_util] 端口 {port} 在探测后被别的进程抢走——换端口重试"
            f"（xdist 并行竞争；第 {attempt}/{attempts} 次）",
            flush=True,
        )
        proc.wait(timeout=5)  # 撞端口：子进程已自行退出，收尸后换端口重试
    raise AssertionError(
        f"连续 {attempts} 次都撞上已被占用的端口（xdist 并行的端口竞争）；"
        "若本机有残留 hub/worker 进程请先清理"
    )


def _await_listening(
    proc: subprocess.Popen[str],
    lines: list[str],
    drain: _Drain,
    port: int,
    timeout: float,
) -> bool:
    """等「子进程自报监听本端口 / 已退出 / 超时」三种局面里的第一种落定。

    返回 False = 子进程**已死**（真假由调用方看 `lines` 判定）；超时不表态 ⇒ True
    （日志文案变了不该变成硬失败，就绪判定交回调用方）。
    """
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if proc.poll() is not None:
            drain.join(timeout=1.0)  # 把尾部输出读完，失败消息里才带得上真因
            return False
        if any(
            (m := _LISTENING_RE.search(ln)) is not None and int(m.group(1)) == port
            for ln in lines
        ):
            return True
        # sleep-ok: 轮询步长（等的是「子进程自报端口」谓词，超时只当挂起兜底）
        time.sleep(0.05)
    return True
