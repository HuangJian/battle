"""gate_halt —— 门禁停机模式**平台级单开关**的契约（意图 + 回执，2026-10-01）。

## 为什么是一份平台文件，而不是课程级旋钮 / rl-config 键

「门禁触发时停不停」问的是**有没有人在盯盘**——这是操作员此刻的状态，不是某门课的属性，
更不是机器配置（`rl-config` 每进程启动才读一次，而这里要**每轮热读**、途中热切）。
课程级三写面（每课 argv / `courses.<课>.gate_halt_mode` / `<traj>/gate-halt-mode.txt`）
会造成「同一实验的两条腿门禁行为不同」那类事故：配对臂各自漂，序列不可比。

契约（单一事实源；控制台侧见 `dashboard/src/stack/gate-halt.ts`）：

```json
{ "version": 1, "mode": "notify", "until": 1760000000, "by": "console", "at": 1759996400 }
```

- `mode`：`halt`（默认，历史行为：下发停机达令）/ `notify`（只记 verdict + 告警，不停机）。
- `until`：epoch 秒；**null / 缺失 = 不过期**（仅 `mode=notify` 有意义）。
- `by` / `at`：写入者与写入时刻（审计）。

## 保守方向是刻意的

**缺省 `halt`**（没人盯盘 = 该停就停）。文件不存在 ⇒ 交给 CLI 启动参数（单机调试用），
再缺省 `halt`；文件**存在但坏**（非法 JSON / 非法 mode / `until` 不是数字）⇒ 一律 `halt` +
告警——**绝不**回落到 notify（读到了一个坏东西时，最不该发生的事就是「静默继续跑」）。
`until` 过期由**读时求值**（`effective_mode`），所以到点自动回落 `halt`，不需要任何进程去定时翻牌。

## 回执面（意图 ≠ 事实）

控制台光看意图文件只能显示「我让它 notify」，回答不了「训练那边真读到了吗、真生效的是什么」。
所以训练侧把**自己实际施加了什么**写 `tmp/gate-halt.applied.json`（逐课 `effective_mode` +
`source` + `until` + `at`），与 `trainer/loop_control.py` 的 `loop-control.applied.json` 同规：
· **只在结果变化时**写（不心跳、不轮询写盘）；· 按课程**合并**（多进程在跑时互不覆盖）；
· 两侧都**原子替换**（`tmp` + `replace`）⇒ 读者要么读到旧的、要么读到新的，永不读到半个。
· 回执失败**不影响训练**（观测面坏不得变成训练的故障）。

`effective_mode` 是**读时求值**的结果（不是文件里的原值）：`until` 已过时意图是 `notify`
而回执是 `halt`——控制台两栏都读回执算，别在浏览器里重算过期（时钟偏差会造出假分歧）。
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from common.log import log

#: 仓库根（本模块住 nn-training/worker/，上溯两级 —— 与 biz/course_archive.py 同规）。
REPO_ROOT = Path(__file__).resolve().parents[2]

#: 平台意图文件默认位置（repo 根 `tmp/`——训练机与控制台共享的工作区，已 gitignore）。
DEFAULT_INTENT_PATH = str(REPO_ROOT / "tmp" / "gate-halt.json")
#: 回执文件默认位置（同上；由训练进程写、控制台读）。
DEFAULT_APPLIED_PATH = str(REPO_ROOT / "tmp" / "gate-halt.applied.json")

#: 合法模式（与 `worker/cli.py --gate-halt-mode` 的 choices、TS `GateHaltMode` 同一字面量）。
MODES = ("halt", "notify")


def intent_path() -> str:
    """意图文件路径（`NN_GATE_HALT` 可覆盖：测试/多工作区隔离）。

    与 dashboard 侧 `BCITY_GATE_HALT` 是**对偶**（同一惯例：python 侧 `NN_*`、控制台侧
    `BCITY_*`，见 `trainer/loop_control.py` 与 `dashboard/src/core/paths.ts`）。
    """
    return os.environ.get("NN_GATE_HALT") or DEFAULT_INTENT_PATH


def applied_path() -> str:
    """回执文件路径（`NN_GATE_HALT_APPLIED` 可覆盖）。"""
    return os.environ.get("NN_GATE_HALT_APPLIED") or DEFAULT_APPLIED_PATH


def leg() -> str:
    """运行腿：`local`（缺省）| `offline`（离线包 / 云机 —— **不读**本机平台文件）。

    `NN_GATE_HALT_LEG=offline` 时 `resolve()` 直接返回缺省 `halt`：云机没有「本机控制台」，
    共享盘上出现一份 `tmp/gate-halt.json` 不该把云机变成 notify（缺省 halt 正是离线语义：
    没人盯着那条腿）。今天云机侧**没有** gate_halt 读点（读点只在 supervisor 判门路径），
    这个开关是给「将来有读点」与「整仓目录挂到云机」两种情形兜底的机制。
    """
    return (os.environ.get("NN_GATE_HALT_LEG") or "local").strip().lower()


def effective_mode(mode: object, until: object, now: float) -> str:
    """读时求值（纯函数）：只有 `mode=notify` 且未过期才是 `notify`，其余一律 `halt`。

    `until` 缺失 / `null` = 不过期（「不限时」）；不是数字 ⇒ `halt`（坏值向保守方向掉）。
    """
    if str(mode or "").strip().lower() != "notify":
        return "halt"
    if until is None:
        return "notify"
    if isinstance(until, bool) or not isinstance(until, (int, float)):
        return "halt"
    return "halt" if now >= float(until) else "notify"


@dataclass(frozen=True)
class Intent:
    """平台意图的文件级快照（`mode=None` = 没有可用意图 ⇒ 调用方回退）。"""

    #: 文件里写的模式（已归一化小写）；`None` = 文件不存在或不可用。
    mode: str | None = None
    until: float | None = None
    by: str = ""
    at: float | None = None
    #: 人读的错误/告警文案（空 = 无）。
    error: str = ""


@dataclass(frozen=True)
class Resolved:
    """一次判定的结果：生效模式 + 来源 + 意图的 `until`（控制台展示用）。"""

    #: 生效模式（`halt` | `notify`）——`until` 已过时是 `halt`，不是文件里的原值。
    mode: str
    #: 来源：`platform`（平台文件）| `cli`（启动参数兜底）| `default`（缺省 halt / leg 短路 / 坏文件）。
    source: str
    until: float | None = None
    #: 人读的问题文案（坏文件/坏 until；空 = 无）。只进日志，不进判定。
    error: str = ""


def parse_intent(raw: object) -> Intent:
    """解析意图文件内容 → `Intent`（纯函数；形状不对 ⇒ `mode=None` + 错误文案）。"""
    if not isinstance(raw, dict):
        return Intent(error="意图文件根不是对象")
    mode_raw = str(raw.get("mode") or "").strip().lower()
    if mode_raw not in MODES:
        return Intent(error=f"意图文件 mode 非法：{raw.get('mode')!r}（只接受 halt|notify）")
    until_raw = raw.get("until")
    until: float | None = None
    if until_raw is not None:
        if isinstance(until_raw, bool) or not isinstance(until_raw, (int, float)):
            return Intent(error=f"意图文件 until 非法：{until_raw!r}（需 epoch 秒或 null）")
        until = float(until_raw)
    at_raw = raw.get("at")
    return Intent(
        mode=mode_raw,
        until=until,
        by=str(raw.get("by") or ""),
        at=float(at_raw) if isinstance(at_raw, (int, float)) and not isinstance(at_raw, bool) else None,
    )


def read_intent(path: str | None = None) -> Intent:
    """读平台意图。文件不存在 ⇒ 空意图（**不是错误**）；读失败/坏形状 ⇒ 空意图 + 错误文案。"""
    p = Path(path or intent_path())
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Intent()
    except (OSError, ValueError) as e:
        return Intent(error=f"意图文件读失败（{type(e).__name__}: {e}）")
    return parse_intent(raw)


def intent_to_dict(mode: str, until: float | None, by: str, now: float | None = None) -> dict:
    """意图文件的规范形状（写面与测试共用一处，避免两侧抄第二份）。"""
    return {
        "version": 1,
        "mode": mode,
        "until": until,
        "by": by,
        "at": time.time() if now is None else now,
    }


def write_intent(
    mode: str, until: float | None = None, by: str = "console", path: str | None = None
) -> str:
    """原子写平台意图（`tmp` + `replace`）。返回错误文案（空 = 成功）——写失败不是训练的故障。"""
    m = str(mode or "").strip().lower()
    if m not in MODES:
        return f"未知门禁模式：{mode!r}（只接受 halt|notify）"
    p = Path(path or intent_path())
    body = json.dumps(intent_to_dict(m, until, by), ensure_ascii=False)
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)
        return ""
    except OSError as e:
        return f"{type(e).__name__}: {e}"


def resolve(cli_mode: object = None, *, path: str | None = None, now: float | None = None) -> Resolved:
    """**唯一读点**的裁决：平台文件 > CLI 启动参数 > 缺省 `halt`（每次判定都读 ⇒ 可热切）。

    平台文件优先（不是 CLI）：平台开关的意义是「一处切、全局生效 + 到点回落」，若启动参数
    能压过它，控制台写出的意图会被历史命令行静默盖掉。CLI 只兜底「文件不存在」这一种情形
    （单机调试/一次性覆盖）；文件存在但坏 ⇒ `halt`（见模块头注：坏文件不许被旧 CLI 值接管）。
    """
    t = time.time() if now is None else now
    if leg() == "offline":
        return Resolved("halt", "default")
    intent = read_intent(path)
    if intent.mode is not None:
        return Resolved(
            effective_mode(intent.mode, intent.until, t), "platform", intent.until, intent.error
        )
    if intent.error:
        return Resolved("halt", "default", None, intent.error)
    v = str(cli_mode or "").strip().lower()
    if v in MODES:
        return Resolved(v, "cli")
    return Resolved("halt", "default")


def read_applied(path: str | None = None) -> dict[str, dict]:
    """读回执的 `courses` 段（缺/坏 ⇒ 空 dict：观测面坏掉不得变成训练的故障）。"""
    p = Path(path or applied_path())
    try:
        doc = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    courses = doc.get("courses") if isinstance(doc, dict) else None
    if not isinstance(courses, dict):
        return {}
    return {str(k): v for k, v in courses.items() if isinstance(v, dict)}


def write_applied_if_changed(
    course: str, resolved: Resolved, *, path: str | None = None, now: float | None = None
) -> str:
    """把「本课实际生效了什么」写进回执（**只在变化时**写、按课合并）。返回错误文案（空 = 成功/未变）。

    比较的是 `(effective_mode, source, until)`——`at`/`pid` 每次都新，若把它算进「变化」，
    这里就变成了心跳写盘（与 `loop_control` 的纪律相反）。
    """
    name = str(course or "").strip() or "nocourse"
    p = Path(path or applied_path())
    courses = read_applied(path)
    prev = courses.get(name) or {}
    entry = {
        "effective_mode": resolved.mode,
        "source": resolved.source,
        "until": resolved.until,
        "at": time.time() if now is None else now,
    }
    same = (
        prev.get("effective_mode") == entry["effective_mode"]
        and prev.get("source") == entry["source"]
        and prev.get("until") == entry["until"]
    )
    if same:
        return ""
    merged = dict(courses)
    merged[name] = entry
    body = json.dumps(
        {"version": 1, "at": entry["at"], "pid": os.getpid(), "courses": merged},
        ensure_ascii=False,
    )
    tmp = p.with_name(f".{p.name}.{os.getpid()}.tmp")
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(body, encoding="utf-8")
        os.replace(tmp, p)
        return ""
    except OSError as e:
        return f"{type(e).__name__}: {e}"


#: 进程内「旧 txt 已告警」去重集合（**纯日志去重**：不参与判定、不跨进程、无可复现语义
#: —— 不是隐藏的训练状态；`seen` 参数只是给用例一个干净的桶）。
_WARNED: set[str] = set()


def warn_once_on_legacy_txt(traj_root: object, seen: set[str] | None = None) -> str:
    """课程级 `<traj>/gate-halt-mode.txt` 已退役：存在就告警一次（缺省按模块级集合去重）。

    返回**人读的告警行**（空 = 无需告警）；不抛、**不读内容**（内容无论如何都不生效）。
    """
    if traj_root is None:
        return ""
    p = Path(str(traj_root)) / "gate-halt-mode.txt"
    key = str(p)
    bucket = _WARNED if seen is None else seen
    if key in bucket:
        return ""
    try:
        if not p.exists():
            return ""
    except OSError:
        return ""
    bucket.add(key)
    return f"[gate] 忽略课程级 gate-halt-mode.txt（门禁已是平台级）：{p}，可删"


def log_resolved(resolved: Resolved, prefix: str = "[run_rl]") -> None:
    """把 `gate_mode=… source=…` 打一行（G5/G7 的可观测面；坏文件另带 error）。"""
    until = f" until={resolved.until:.0f}" if resolved.until is not None else ""
    extra = f" （{resolved.error}）" if resolved.error else ""
    log(f"{prefix} gate_halt: gate_mode={resolved.mode} source={resolved.source}{until}{extra}")
