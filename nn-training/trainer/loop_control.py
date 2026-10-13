"""loop_control —— 控制台 → 单进程 supervisor 的**控制文件**契约（R2d 操作面）。

为什么是文件而不是 HTTP 服务：训练侧（`trainer/loop_serve.py`）已经是「一个进程服务 N 门课」，
控制台要能**暂停/恢复某一课**却又不想在训练进程里再挂一个服务器（多一个端口、多一份鉴权、
多一处故障域）。而两侧本来就共享一个工作区（`tmp/` 是训练产物与账本的落脚处）⇒ 用一份
**意图文件**最省：控制台写、训练侧只读，hub 挂了也能用，进程重启后意图仍在。

契约（单一来源；控制台侧见 `dashboard/src/server/actions/loop-control.ts`）：

```json
{
  "version": 1,
  "paused": ["c5"],
  "held": [{ "course": "c5", "last_progress_at": 1760000000.0 }],
  "note": "由控制台写入；训练侧只读"
}
```

`held`（★M2，plan/worker-type-dispatch-model §3-M2）是 **hub 事实的缓存**（云机正接管哪
几门课），不是第二事实源：

  * 判据只认 `last_progress_at`（与 hub 的 `hold_state` 同一把尺子）——**本机就地 900s 自判活**，
    超窗的条目等于不存在（缓存过期 ≠ 永久接管）；
  * **同机时钟前提**：两侧共享 `tmp/`，本机拿自己的墙钟去减这个时间戳；跨机部署（trainer 与
    控制台不共享 `tmp/`）下这个通道本就不可用 —— 那时走 hub 直问（`trainer/loop_hold.py`）。
  * 写方是控制台（hub 事实 → 文件），读方只读；`held` 改不了任何训练语义（它只回答
    「本机该不该跑这一段」，与 `paused` 一样是**调度输入**）。

**旧 trainer × 新文件**：旧版本只认 `paused`，多出来的 `held` 键被忽略 ⇒ 旧进程照旧跑
（不会因为新字段 brick）；**新 trainer × 旧文件**：`held` 缺失 ⇒ 空集 ⇒ 退 hub 直问。

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

from common.log import log
from trainer.dispatch_queue import REPO_ROOT

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


def write_applied(
    paused: Iterable[str], path: str | None = None, *, held: Iterable[str] = ()
) -> str:
    """原子写回执（tmp + replace）。返回错误文案（空 = 成功）——回执失败**不影响训练**。

    `held`（★M2）随回执一起报：控制台据此分辨「我写了 held、训练侧到底读到了没有」
    （与 `paused` 同一套 pid 存活核对）。默认空 ⇒ 旧调用方逐字不变。
    """
    p = Path(path or applied_path())
    body = json.dumps(
        {
            "version": 1,
            "at": time.time(),
            "pid": os.getpid(),
            "paused": sorted(paused),
            "held": sorted(held),
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
    """控制意图的快照（`paused` = 要求暂停的课程集；`held` = **仍然新鲜**的接管缓存）。"""

    paused: frozenset[str] = field(default_factory=frozenset)
    #: ★M2：`held` 里**就地自判活后仍新鲜**的课程集（超窗/零进度的一律不算）。
    held: frozenset[str] = field(default_factory=frozenset)
    #: 文件不存在（= 没有任何意图；不是错误）。
    found: bool = False
    #: 解析/形状错误（人读；空 = 无错）。错误时 `paused` 为**空**（保守 = 继续跑）。
    error: str = ""


def parse_control(raw: object, *, now: float | None = None) -> Control:
    """解析控制文件内容 → `Control`（纯函数；形状不对 ⇒ 空 + 错误文案）。

    `held` 在这就地自判活（★M2）：只留 `now - last_progress_at <= hold_progress_stale_sec()`
    的那些课；窗常量与 hub 同一把（不许在控制台侧再抄一个）。`now` 可注入（测试）。
    """
    if not isinstance(raw, dict):
        return Control(error="控制文件根不是对象")
    t = float(now) if now is not None else time.time()
    held_set, held_err = parse_held(raw.get("held"), now=t)
    paused_raw = raw.get("paused", [])
    if paused_raw is None:
        return Control(held=frozenset(held_set), found=True, error=held_err)
    if not isinstance(paused_raw, list):
        return Control(held=frozenset(held_set), found=True, error="paused 不是数组")
    bad: list[str] = []
    paused: set[str] = set()
    for item in paused_raw:
        c = _valid_course(item)
        if c is None:
            bad.append(repr(item))
        else:
            paused.add(c)
    err = f"paused 里有非法课程名（已忽略）：{', '.join(bad)}" if bad else ""
    return Control(
        paused=frozenset(paused),
        held=frozenset(held_set),
        found=True,
        error="; ".join(x for x in (err, held_err) if x),
    )


def parse_held(raw: object, *, now: float) -> tuple[set[str], str]:
    """解析 `held` 条目 → `(仍然新鲜的课程集, 错误文案)`（**纯函数**；见 `loop_hold` 的注释）。

    条目形状 `{"course": c, "last_progress_at": t}`；缺 `/ 非法 / 超窗` ⇒ **不新鲜**
    （只认进度：零进度不算活着，与 hub 的 `hold_state` 同一口径）。
    """
    from hub.task_pack import hold_progress_stale_sec

    if raw is None:
        return set(), ""
    if not isinstance(raw, list):
        return set(), "held 不是数组"
    stale = float(hold_progress_stale_sec())
    out: set[str] = set()
    bad: list[str] = []
    for item in raw:
        if not isinstance(item, dict):
            bad.append(repr(item))
            continue
        c = _valid_course(item.get("course"))
        if c is None:
            bad.append(repr(item.get("course")))
            continue
        try:
            at = float(item.get("last_progress_at") or 0.0)
        except (TypeError, ValueError):
            at = 0.0
        if at > 0.0 and (now - at) <= stale:
            out.add(c)
    err = f"held 里有不新鲜/不合法的条目（已忽略）：{', '.join(bad[:3])}" if bad else ""
    return out, err


def read_control(path: str | None = None, *, now: float | None = None) -> Control:
    """读控制文件。文件不存在/读失败/解析失败 → 空意图（保守：不暂停）+ 错误文案。"""
    p = Path(path or control_path())
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Control()
    except (OSError, ValueError) as e:
        return Control(error=f"控制文件读失败（{type(e).__name__}: {e}）")
    return parse_control(raw, now=now)


# ============================================================ M3「重发本轮 PPO」意图
# （plan/ppo-job-lifecycle-ownership §4）
#
# 为什么沿用 `loop-control.json` 这套「意图文件 + 幂等回执」：控制台要能对**单门课**下
# 「丢弃当前 PPO job、用当前 code 重派」的命令，而训练进程不提供 HTTP 服务（同上：多一个
# 端口/鉴权/故障域）。意图按课落盘（`tmp/<course>/redo-ppo.json`），回执写回同目录
# （`redo-ppo.applied.json`，带 pid/at），控制台/人靠它分辨「读到没有」。
#
# 去重键 = 意图的 `at`（回执里原样记）：同一份意图只被执行一次（`_remote_ppo_step` 每拍
# 都读；没有去重就会把刚重发的活反复再撤）。刷新 `at` = 再来一次。

#: 重发意图文件名（控台写、训练侧读；按课落盘）。
REDO_NAME = "redo-ppo.json"
#: 重发回执文件名（训练侧写、控台/人读）。
REDO_APPLIED_NAME = "redo-ppo.applied.json"


@dataclass(frozen=True)
class RedoIntent:
    """一次「重发本轮 PPO」意图的快照（`found=False` = 没有/坏文件，绝不误触）。

    `it` ≤ 0 或缺省 = 对**当前轮**生效；正数 = 只对那一轮生效（还没轮到就原地等，不抢跑）。
    `at` = 去重键（控制台写入时给；缺失时退回文件 mtime——手写意图也能用）。
    """

    it: int = 0
    at: float = 0.0
    found: bool = False
    error: str = ""


def redo_path(traj_dir: str | os.PathLike[str]) -> Path:
    """某课的重发意图路径（课程级：`tmp/<course>/redo-ppo.json`）。"""
    return Path(traj_dir) / REDO_NAME


def redo_applied_path(traj_dir: str | os.PathLike[str]) -> Path:
    """某课的重发回执路径（与意图同目录）。"""
    return Path(traj_dir) / REDO_APPLIED_NAME


def parse_redo(raw: object) -> RedoIntent:
    """解析重发意图（纯函数；形状不对 ⇒ 空 + 错误文案——保守：宁可不动作）。"""
    if not isinstance(raw, dict):
        return RedoIntent(error="redo 意图根不是对象")
    try:
        it = int(raw.get("it", 0) or 0)
    except (TypeError, ValueError):
        return RedoIntent(error="redo.it 不是整数")
    try:
        at = float(raw.get("at", 0.0) or 0.0)
    except (TypeError, ValueError):
        return RedoIntent(error="redo.at 不是数字")
    return RedoIntent(it=it, at=at, found=True)


def read_redo(traj_dir: str | os.PathLike[str]) -> RedoIntent:
    """读某课的重发意图（**永不抛**；文件不存在/坏 ⇒ 空意图 + 原因）。

    `at` 缺失（手写 `{"it":188}`）⇒ 退回文件 mtime 当去重键：改一次文件 = 新意图。
    """
    p = redo_path(traj_dir)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return RedoIntent()
    except (OSError, ValueError) as e:
        return RedoIntent(error=f"redo 意图读失败（{type(e).__name__}: {e}）")
    intent = parse_redo(raw)
    if intent.error or not intent.found or intent.at > 0.0:
        return intent
    try:
        return RedoIntent(it=intent.it, at=p.stat().st_mtime, found=True)
    except OSError:
        return intent


def write_redo_applied(traj_dir: str | os.PathLike[str], *, it: int, at: float) -> str:
    """原子写重发回执（tmp + replace）。返回错误文案（空 = 成功）——回执失败**不影响训练**。

    形状与 `loop-control.applied.json` 同族：`pid` 供存活核对、`at` = **被消费的意图时间戳**
    （去重键原样回写）、`ts` = 写入时刻。
    """
    p = redo_applied_path(traj_dir)
    body = json.dumps(
        {"version": 1, "it": int(it), "at": float(at), "pid": os.getpid(), "ts": time.time()},
        ensure_ascii=False,
    )
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)  # 同目录替换：读侧要么读到旧的、要么读到新的，永不读到半个
        return ""
    except OSError as e:
        return f"{type(e).__name__}: {e}"


def read_redo_applied(traj_dir: str | os.PathLike[str]) -> tuple[int, float]:
    """读回执的 `(it, at)`（缺/坏 ⇒ `(0, 0.0)`）——某个意图是否已被消费的判据。"""
    p = redo_applied_path(traj_dir)
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0, 0.0
    if not isinstance(raw, dict):
        return 0, 0.0
    try:
        return int(raw.get("it", 0) or 0), float(raw.get("at", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0, 0.0


def consume_redo(
    traj_dir: str | os.PathLike[str], it: int
) -> tuple[RedoIntent | None, str]:
    """把某课的重发意图消费成一次动作（幂等）。

    返回 `(intent, error)`：`intent` 非 None = **本次该执行**（调用方随后必须
    `write_redo_applied` 落去重键）；None = 没有意图 / 还没轮到这一轮 / 同一份意图
    已经消费过。`error` 非空 = 文件坏（调用方负责记一行、保守不动作）。
    """
    intent = read_redo(traj_dir)
    if intent.error:
        return None, intent.error
    if not intent.found:
        return None, ""
    if intent.it > 0 and intent.it != int(it):
        return None, ""
    _a_it, a_at = read_redo_applied(traj_dir)
    if a_at > 0.0 and abs(a_at - intent.at) < 1e-9:
        return None, ""  # 同一份意图已消费（回执即去重键）
    return intent, ""


class ControlApplier:
    """把控制意图施加到调度器上，并**只在变化时**产出日志（轮询友好）+ 写回执。"""

    def __init__(self, logger=log, applied_file: str | None = None) -> None:
        self.logger = logger
        self.applied_file = applied_file
        self._applied: frozenset[str] | None = None
        #: ★M2：上一拍读到的 `held`（回执在它变化时也要重写——否则控制台无法分辨
        #: 「我写的缓存被读到了没有」；两个输入**任一**变化都算回执该刷新）。
        self._applied_held: frozenset[str] | None = None
        self._error = ""

    def apply(self, sup, control: Control, *, path: str | None = None) -> list[str]:
        """按意图暂停/恢复（幂等）。返回本次**变化**的人读行（首次生效也会记）。"""
        lines: list[str] = []
        if control.error and control.error != self._error:
            self._error = control.error
            lines.append(f"[loopcontrol] 忽略控制文件（保守：继续训练）：{control.error}")
        elif not control.error:
            self._error = ""

        paused_changed = self._applied is None or control.paused != self._applied
        held_changed = self._applied_held is None or control.held != self._applied_held
        self._applied = control.paused
        self._applied_held = control.held
        if not paused_changed and not held_changed:
            return lines
        # 回执：控制台靠它分辨「已生效」与「还没读到」（首次施加也写一次：顺带续上 pid/at）。
        # ★M2：`held` 一并回执（控制台写的缓存到底被读到没有，与 paused 同一套 pid 核对）。
        err = write_applied(control.paused, self.applied_file, held=control.held)
        if err:
            lines.append(f"[loopcontrol] WARN 回执写入失败（不影响训练）：{err}")

        if not paused_changed:
            # 只有 `held` 变了：没有任何调度动作要施加/要报（单纯刷新回执）。
            for line in lines:
                self.logger(line)
            return lines

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
