"""loop_control —— 控制台 → 单进程 supervisor 的**控制文件**契约（R2d 操作面）。

为什么是文件而不是 HTTP 服务：训练侧（`rl/loop_serve.py`）已经是「一个进程服务 N 门课」，
控制台要能**暂停/恢复某一课**却又不想在训练进程里再挂一个服务器（多一个端口、多一份鉴权、
多一处故障域）。而两侧本来就共享一个工作区（`tmp/` 是训练产物与账本的落脚处）⇒ 用一份
**意图文件**最省：控制台写、训练侧只读，hub 挂了也能用，进程重启后意图仍在。

契约（单一来源；控制台侧见 `dashboard/src/server/actions/loop-control.ts`）：

```json
{ "version": 1, "paused": ["c5"], "note": "由控制台写入；训练侧只读" }
```

**保守方向是刻意的**：读不到 / 解析失败 / 形状不对 ⇒ 一律当作「没有任何暂停意图」——
宁可持续训练，绝不因为控制面坏掉而误停整条腿（与 `already_done` 的「算不出的判据不得
当成完成」同一条纪律）。反过来，暂停只影响**调度**：队列与账本一个字都不动（用户口径
「暂停 = 保留队列，不删」），恢复后从原处接着跑。

失效安全与可见性：本模块只在**意图变化时**产出日志行（每秒轮询不得刷屏），解析失败也只报
一次（记住上次的错误签名）。

## 回执面（意图 ≠ 事实）

光有意图文件，控制台点完暂停只能盲猜「生效了没」：进程可能没在跑，也可能还没轮到读文件。
所以训练进程把**自己实际施加了什么**写回去（`loop-control.applied.json`：`pid` + `at` +
`paused`），控制台用 `pid` 存活核对来分辨「生效」「未读到」「进程已死（残留文件不作数）」。
这是**进程自己的一面之词**（没人能替它算），所以它跟意图文件一样是事实源而不是第二份真相；
写入只在**施加结果变化时**发生（不心跳、不轮询写盘）。
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

from rl.log import log
from rl.queue import REPO_ROOT

#: 控制文件默认位置（repo 根 `tmp/`——训练机与控制台共享的工作区）。
DEFAULT_CONTROL_PATH = str(REPO_ROOT / "tmp" / "loop-control.json")
#: 回执文件默认位置（同上；由训练进程写、控制台读）。
DEFAULT_APPLIED_PATH = str(REPO_ROOT / "tmp" / "loop-control.applied.json")

#: 课程名合法字符集（与 dashboard `core/slots.validateCourseName` 同一约束）。
_ALLOWED = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")


def control_path() -> str:
    """控制文件路径（`NN_LOOP_CONTROL` 可覆盖：测试/多工作区）。"""
    return os.environ.get("NN_LOOP_CONTROL") or DEFAULT_CONTROL_PATH


def applied_path() -> str:
    """回执文件路径（`NN_LOOP_CONTROL_APPLIED` 可覆盖；与 `control_path` 同一目录约定）。"""
    return os.environ.get("NN_LOOP_CONTROL_APPLIED") or DEFAULT_APPLIED_PATH


def write_applied(paused: Iterable[str], path: str | None = None) -> str:
    """原子写回执（tmp + replace）。返回错误文案（空 = 成功）——回执失败**不影响训练**。"""
    p = Path(path or applied_path())
    body = json.dumps(
        {
            "version": 1,
            "at": time.time(),
            "pid": os.getpid(),
            "paused": sorted(paused),
        },
        ensure_ascii=False,
    )
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)  # 同目录替换：控制台要么读到旧的、要么读到新的，永不读到半个
        return ""
    except OSError as e:
        return f"{type(e).__name__}: {e}"


def _valid_course(name: object) -> str | None:
    c = str(name or "")
    if not c or ".." in c or any(ch not in _ALLOWED for ch in c):
        return None
    return c


@dataclass(frozen=True)
class Control:
    """控制意图的快照（`paused` = 要求暂停的课程集）。"""

    paused: frozenset[str] = field(default_factory=frozenset)
    #: 文件不存在（= 没有任何意图；不是错误）。
    found: bool = False
    #: 解析/形状错误（人读；空 = 无错）。错误时 `paused` 为**空**（保守 = 继续跑）。
    error: str = ""


def parse_control(raw: object) -> Control:
    """解析控制文件内容 → `Control`（纯函数；形状不对 ⇒ 空 + 错误文案）。"""
    if not isinstance(raw, dict):
        return Control(error="控制文件根不是对象")
    paused_raw = raw.get("paused", [])
    if paused_raw is None:
        return Control(found=True)
    if not isinstance(paused_raw, list):
        return Control(error="paused 不是数组")
    bad: list[str] = []
    paused: set[str] = set()
    for item in paused_raw:
        c = _valid_course(item)
        if c is None:
            bad.append(repr(item))
        else:
            paused.add(c)
    err = f"paused 里有非法课程名（已忽略）：{', '.join(bad)}" if bad else ""
    return Control(paused=frozenset(paused), found=True, error=err)


def read_control(path: str | None = None) -> Control:
    """读控制文件。文件不存在/读失败/解析失败 → 空意图（保守：不暂停）+ 错误文案。"""
    p = Path(path or control_path())
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Control()
    except (OSError, ValueError) as e:
        return Control(error=f"控制文件读失败（{type(e).__name__}: {e}）")
    return parse_control(raw)


class ControlApplier:
    """把控制意图施加到调度器上，并**只在变化时**产出日志（轮询友好）+ 写回执。"""

    def __init__(self, logger=log, applied_file: str | None = None) -> None:
        self.logger = logger
        self.applied_file = applied_file
        self._applied: frozenset[str] | None = None
        self._error = ""

    def apply(self, sup, control: Control, *, path: str | None = None) -> list[str]:
        """按意图暂停/恢复（幂等）。返回本次**变化**的人读行（首次生效也会记）。"""
        lines: list[str] = []
        if control.error and control.error != self._error:
            self._error = control.error
            lines.append(f"[loopcontrol] 忽略控制文件（保守：继续训练）：{control.error}")
        elif not control.error:
            self._error = ""

        changed = self._applied is None or control.paused != self._applied
        self._applied = control.paused
        if not changed:
            return lines
        # 回执：控制台靠它分辨「已生效」与「还没读到」（首次施加也写一次：顺带续上 pid/at）
        err = write_applied(control.paused, self.applied_file)
        if err:
            lines.append(f"[loopcontrol] WARN 回执写入失败（不影响训练）：{err}")

        where = path or control_path()
        for course, q in sup.courses.items():
            want_paused = course in control.paused
            if want_paused and q.state != "paused":
                sup.pause(course, reason=f"控制台暂停（{where}）")
                lines.append(f"[loopcontrol] 暂停课程 {course}（队列与账本保留，恢复后从原处接着跑）")
            elif not want_paused and q.state == "paused":
                sup.resume(course)
                lines.append(f"[loopcontrol] 恢复课程 {course}")
        for line in lines:
            self.logger(line)
        return lines
