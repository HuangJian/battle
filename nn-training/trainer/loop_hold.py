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
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from common import net_http
from common.protocol import AUTH_HEADER, OFFLINE_HOLD_PATH

#: hub 直问的超时（秒；★P0-5 定死 ≤3s）。轮边界上的一次同步等待，压在 3s 以内不会把
#: 一轮的墙钟记账带偏；连不上就是连不上，退文件通道即可。
HOLD_QUERY_TIMEOUT_SEC = 3.0


def hub_held(
    hub_url: str,
    token: str,
    course: str,
    *,
    timeout: float = HOLD_QUERY_TIMEOUT_SEC,
    opener: Any = None,
) -> bool | None:
    """直问 hub：`True` = 被接管、`False` = 没有被接管、`None` = **问不到**（退文件通道）。

    判据 = `held is True`（hub 侧 `state == "live"`，只认进度）——与 `/offline/tasks` 的
    `held` 列同一把尺子（见 `hub/offline.py::_get_offline_hold`）。`hub_url`/`course` 空 ⇒ `None`。
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
        # 一次网络抖动不是错误路径：返回 None 让上层退文件通道（调用方按需记日志——
        # 每轮边界一行「hub 问不到」会把日志刷爆，故本层不喊）。
        return None
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(doc, dict):
        return None
    return bool(doc.get("held") is True)


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
    """这门课现在是不是**被接管**（云机在跑这一段）——`hub_live ∨ file_live`。"""
    course = course_key_of(args)
    if not course:
        return False
    hub = str(getattr(args, "remote_hub_url", "") or "")
    if hub_held(hub, hub_token_of(args), course, opener=opener) is True:
        return True
    return file_held(course, now=now)


def file_held(course: str, *, now: float | None = None) -> bool:
    """控制文件通道：`held` 里有没有这门课（**就地自判活**，见 `loop_control.parse_held`）。"""
    c = str(course or "")
    if not c:
        return False
    from trainer.loop_control import read_control

    return c in read_control(now=now).held
