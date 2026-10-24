"""loop_hold —— 「这门课现在被接管了吗」的**双通道**判据（★M2，plan/worker-type-dispatch-model §3-M2）。

旧模型里这件事写在 rl-config 上（`rollout_src=run` / `run_iters`）——那是**配置**，而它
要回答的是**当下的事实**（云机此刻在跑这一段吗）。切一次模式要落盘、要回灌、要控制台与
训练侧两边对齐，而云机掉线之后那个配置还留在盘上 ⇒ 「本机不跑 + 云机也不跑」的静默停摆。
新模型把这件事实交给 hub 的 **hold**（`GET /offline/hold`，只有进度能续命），本模块是两个
通道的唯一裁决点：

  ① **hub 直问**（★P0-5，权威）：`GET /offline/hold?course=<课>`，每轮边界一次、≤3s 超时；
     问到了就是它（`held = state == live`，与 `/offline/tasks` 的行同一把尺子）。
  ② **控制文件缓存**（`tmp/loop-control.json` 的 `held: [{course, last_progress_at}]`）：
     控制台写的 hub 事实缓存，本机**就地 900s 自判活**（与 hub 同一把尺子，见
     `loop_control.parse_held`）。

生效 = `hub_live ∨ file_live`（任一为真即「被接管」）。为什么两个都要：hub 不可达时文件通道
还能用（跨机/内网断），而文件是控制台写的 ⇒ 只有控制台活着时它才新鲜；两者都拿不到 ⇒
**不接管**（本机照跑：协作派发是缺省，而不是「谁都不敢跑」）。

**绝不 brick 课程**（与 `loop_control` 的保守方向同一条纪律）：所有异常都在本模块内消化，
返回 `None`/`False`；一次网络抖动不该让一门课停在原地。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from common import net_http
from common.protocol import AUTH_HEADER, OFFLINE_HOLD_PATH

#: hub 直问的超时（秒；★P0-5 定死 ≤3s）。轮边界上的一次同步等待，压在 3s 以内不会把
#: 一轮的墙钟记账带偏；连不上就是连不上，退文件通道即可。
HOLD_QUERY_TIMEOUT_SEC = 3.0


def hub_hold_doc(
    hub_url: str,
    token: str,
    course: str,
    *,
    timeout: float = HOLD_QUERY_TIMEOUT_SEC,
    opener: Any = None,
) -> dict[str, Any] | None:
    """直问 hub 的**原始应答**（`GET /offline/hold?course=`）；`None` = **问不到**。

    为什么留原始 dict 而不是一步折成 bool：`/offline/hold` 同时给 `state`（live/stale）、
    `progress_ago`、`last_progress_at`（`hub/offline.py::_get_offline_hold`）——
    「云机掉线了」这个负事实**只有 hub 能说**（控制台缓存只说明控制台探过 hub），
    而 2026-10-23 的降级判据（plan/offline-hold-stale-fallback §2-B）正需要它。

    一次网络抖动不是错误路径：返回 `None` 让上层退文件通道（调用方按需记日志——
    每轮边界一行「hub 问不到」会把日志刷爆，故本层不喊）。
    """
    base = str(hub_url or "").strip().rstrip("/")
    name = str(course or "").strip()
    if not base or not name:
        return None
    url = f"{base}{OFFLINE_HOLD_PATH}?course={urllib.parse.quote(name)}"
    req = urllib.request.Request(url, headers={AUTH_HEADER: f"Bearer {token}"}, method="GET")
    try:
        open_fn = opener or net_http.urlopen
        with open_fn(req, timeout=float(timeout)) as resp:
            raw = resp.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    return doc if isinstance(doc, dict) else None


def hub_hold_state(doc: dict[str, Any] | None) -> str | None:
    """原始应答 → 三态（纯函数）：`"live"` / `"stale"` / `""`（没有 hold）；`None` = 问不到。

    `held is True` 优先于 `state`：旧 hub（只有 `held` 没有 `state`）的「live」不能因为缺字段
    被读成「没有 hold」（那会让本机立刻开跑 = 双跑）。反过来，`state == "stale"` 是**显式**
    负事实，只有它能让本机接管。
    """
    if doc is None:
        return None
    if doc.get("held") is True or str(doc.get("state") or "") == "live":
        return "live"
    if str(doc.get("state") or "") == "stale":
        return "stale"
    return ""


def hub_held(
    hub_url: str,
    token: str,
    course: str,
    *,
    timeout: float = HOLD_QUERY_TIMEOUT_SEC,
    opener: Any = None,
) -> bool | None:
    """直问 hub：`True` = 被接管、`False` = 没有被接管（含 stale）、`None` = **问不到**。

    判据 = `held is True`（hub 侧 `state == "live"`，只认进度）——与 `/offline/tasks` 的
    `held` 列同一把尺子（见 `hub/offline.py::_get_offline_hold`）。`hub_url`/`course` 空 ⇒ `None`。
    """
    state = hub_hold_state(
        hub_hold_doc(hub_url, token, course, timeout=timeout, opener=opener)
    )
    if state is None:
        return None
    return state == "live"


def _float_or(value: object, default: float) -> float:
    """宽容读一个数字（JSON 里的 int/float/字符串数字）；不合法 ⇒ `default`（不抛、不编）。"""
    try:
        return float(str(value))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class HoldFacts:
    """「这门课现在归谁」的两个正交事实 + 静默时长（`trainer/loop_round_steps.step_course_iter` 用）。

    · `held`   —— 被接管（hub live ∨ 控制文件缓存新鲜）；
    · `lapsed` —— hub **明确**答 stale（云机掉线）。只认 hub：文件缓存的「条目过期」只等于
      「控制台没探过 hub」，拿它判掉线会让本机与活着的云机双跑；
    · `silent_sec` —— hub 报的进度静默秒数（`progress_ago`；没有就按 `last_progress_at` 算）；
      `None` = 不可知（不编数字）。
    """

    held: bool = False
    lapsed: bool = False
    silent_sec: float | None = None


def course_hold_facts(
    args: Any, *, opener: Any = None, now: float | None = None
) -> HoldFacts:
    """一次 hub 查询 + 一次控制文件读 ⇒ `HoldFacts`（`course_held` 的完整版）。

    判据与 `course_held` **逐条同源**（`hub_live ∨ file_live`）；多出来的只有两件事：
    ① 若两通道都没说「被接管」，再看 hub 是否**显式**答 stale；② 顺手带出静默时长（WARN 用）。
    hub 问不到 ⇒ `lapsed=False`（保守：此时 PPO 也走 hub 队列，本机采了也发不出去）。
    """
    course = course_key_of(args)
    if not course:
        return HoldFacts()
    doc = hub_hold_doc(
        str(getattr(args, "remote_hub_url", "") or ""),
        hub_token_of(args),
        course,
        opener=opener,
    )
    state = hub_hold_state(doc)
    if state == "live" or file_held(course, now=now):
        return HoldFacts(held=True)
    if state != "stale":
        return HoldFacts()
    silent: float | None = None
    ago_f = _float_or((doc or {}).get("progress_ago"), -1.0)
    if ago_f >= 0.0:
        silent = ago_f
    else:
        last_f = _float_or((doc or {}).get("last_progress_at"), 0.0)
        if last_f > 0.0:
            silent = max(0.0, (float(now) if now is not None else time.time()) - last_f)
    return HoldFacts(held=False, lapsed=True, silent_sec=silent)


def hub_token_of(args: Any) -> str:
    """hub token（与远端派发**同源**：`remote_token`；缺省空串 ⇒ hub 直问 401 ⇒ 退文件通道）。"""
    return str(getattr(args, "remote_token", "") or "")


def course_key_of(args: Any) -> str:
    """本进程在跑的课程键（与账本/锁/槽位**同一把键**）。

    `--course` 收的是「课程名**或**路径」（`worker/cli.py`）⇒ 必须过一遍
    `course_key_from_path`（`curricula/x1.jsonc` 与 `x1` 都要归到 `x1`）——不过这一道，
    路径形式的启动会把键写成 `x.jsonc`，于是本课永远查不到自己的 hold（静默双跑）。
    """
    raw = str(
        getattr(args, "course", "")
        or getattr(args, "course_key", "")
        or getattr(args, "course_path", "")
        or ""
    )
    if not raw:
        return ""
    try:
        from worker.train.loop_util import course_key_from_path

        return course_key_from_path(raw)
    except Exception:
        return raw


def course_held(args: Any, *, opener: Any = None, now: float | None = None) -> bool:
    """这门课现在是不是**被接管**（云机在跑这一段）——`hub_live ∨ file_live`。

    2026-10-23：实现搬进 `course_hold_facts`（同一判据 + 降级要用的两个旁证）——本函数保留为
    读面/调度面既有的窄接口，语义一个字不变。
    """
    return course_hold_facts(args, opener=opener, now=now).held


def file_held(course: str, *, now: float | None = None) -> bool:
    """控制文件通道：`held` 里有没有这门课（**就地自判活**，见 `loop_control.parse_held`）。"""
    c = str(course or "")
    if not c:
        return False
    from trainer.loop_control import read_control

    return c in read_control(now=now).held
