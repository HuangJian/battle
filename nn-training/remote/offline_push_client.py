"""remote/offline_push_client.py —— hub → worker 的**离线 push 客户端**（2026-10-24，W5）。

方向与 `remote/push_client.py`（hub → GPU worker 的 job 上传）相反的那一半：这条腿 hub 是
**拉的一方**（逐轮产物 / 段末结果），同时也要**推**两件小东西（任务包 zip、ack/cancel）。

端点契约住 `common/protocol.py`（`OFFLINE_PUSH_*_PATH`），实现面是
`remote/offline_push_face.py`（worker 侧）。本模块只做一件事：把「一张字节面 + 三个小 JSON」
发出去，且**区分两种失败**：

  * **网络层失败**（连不上 / 超时 / 读断）⇒ `None` —— 这一拍没答，调用方重试；
  * **4xx/5xx** ⇒ `(状态码, 体)` —— 有答。409「它正忙」与「连不上」被混成一件事，就会让
    调度器把一台好好的机器判死（或反过来，把死机器当成忙）。

三条不是口味问题的设计：

  * **大件流式**：任务包（几百 MB）与逐轮 body（几十 MB）都走文件指针，绝不 `read_bytes()`
    再 `Request(data=…)` —— 一次推送 = 一份等长内存副本，hub 的 RSS 峰就是这么来的。
  * **下载先落 `.part` 再换入**：半截 body 被 `json.load` 读到会变成「补传被拒」，而真因
    （隧道断了）在几百行日志之外。`.part` 让「有没有收到一份完整体」是文件系统的事实。
  * **`Content-Length` 显式给**：worker 的 `/offline/push/task` 按它判上界并拒绝缺它的请求
    （chunked 上传在对面是 400 —— 那条闸是防「不知道多大就敢收」的）。

秩 **L0**：只依赖 stdlib（`urllib` / `os.replace`）+ `common.net_http`（回环绕代理）+
`common.protocol` 的常量。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from common import net_http
from common.protocol import (
    AUTH_HEADER,
    OFFLINE_PUSH_ACK_PATH,
    OFFLINE_PUSH_CANCEL_PATH,
    OFFLINE_PUSH_PROGRESS_PATH,
    OFFLINE_PUSH_RESULT_PATH,
    OFFLINE_PUSH_ROUND_PATH,
    OFFLINE_PUSH_STATE_PATH,
    OFFLINE_PUSH_TASK_PATH,
)

#: 下载时每次读的块（1MiB）：body 是 base64 的权重，几十 MB 是常态。
_CHUNK = 1024 * 1024
#: 大件默认超时（任务包上行 / 逐轮下行）；云机隧道抖一下不该被当成「这一轮没了」。
BIG_TIMEOUT = 300.0
#: 小 JSON（state/progress/ack/cancel）的超时。
SMALL_TIMEOUT = 10.0


def _auth_headers(key: str) -> dict[str, str]:
    return {AUTH_HEADER: f"Bearer {key}"}


def _open(
    url: str,
    key: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    method: str = "GET",
    data: Any = None,
    timeout: float = SMALL_TIMEOUT,
    extra_headers: dict[str, str] | None = None,
) -> Any:
    """发一次请求 → 响应对象。

    **4xx/5xx 照抛 `HTTPError`**（那是有答，调用方要读状态码），其余异常（连不上/超时）
    收敛成 `None` —— 两种失败在调用方是两种决策，不能在这里糊成一种。
    """
    full = f"{url.rstrip('/')}{path}"
    if query:
        pairs = {k: v for k, v in query.items() if v != ""}
        if pairs:
            full = f"{full}?{urllib.parse.urlencode(pairs)}"
    headers = _auth_headers(key)
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(full, data=data, method=method, headers=headers)
    try:
        return net_http.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError:
        raise
    except Exception:
        return None


def _read_json_response(resp: Any) -> tuple[int, dict] | None:
    """读完一个小 JSON 应答（`resp` 可以是 `HTTPError`）；读断 ⇒ None。

    解不开 / 不是对象 ⇒ 空 dict：调用方只关心「有没有那句 `status`/`proto`」，
    一段坏体不该把整条腿判成网络失败（那是两种不同的重试决策）。
    """
    try:
        raw = resp.read()
        status = int(resp.code) if isinstance(resp, urllib.error.HTTPError) else int(resp.status)
        if not isinstance(resp, urllib.error.HTTPError):
            resp.close()
    except Exception:
        return None
    try:
        doc = json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        doc = {}
    return status, doc if isinstance(doc, dict) else {}




def call_json(
    url: str,
    key: str,
    path: str,
    *,
    query: dict[str, Any] | None = None,
    method: str = "GET",
    payload: dict | None = None,
    timeout: float = SMALL_TIMEOUT,
) -> tuple[int, dict] | None:
    """`(状态码, JSON 体)`；连不上/读断 ⇒ `None`。`payload` 给了就按 JSON POST。"""
    data: bytes | None = None
    extra: dict[str, str] | None = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        extra = {"Content-Type": "application/json"}
    try:
        resp = _open(
            url,
            key,
            path,
            query=query,
            method=method,
            data=data,
            timeout=timeout,
            extra_headers=extra,
        )
    except urllib.error.HTTPError as e:
        resp = e
    if resp is None:
        return None
    return _read_json_response(resp)


def get_state(url: str, key: str, *, timeout: float = SMALL_TIMEOUT) -> dict | None:
    """`GET /offline/push/state`：200 且是对象才返回（其它一律 None —— 版本门在调用方）。"""
    out = call_json(url, key, OFFLINE_PUSH_STATE_PATH, timeout=timeout)
    if out is None or out[0] != 200:
        return None
    return out[1]


def get_progress(
    url: str, key: str, since: int = 0, *, timeout: float = SMALL_TIMEOUT
) -> dict | None:
    """`GET /offline/push/progress?since=N`（`seq <= since` ⇒ 体里 `unchanged=true`）。"""
    out = call_json(
        url, key, OFFLINE_PUSH_PROGRESS_PATH, query={"since": int(since)}, timeout=timeout
    )
    if out is None or out[0] != 200:
        return None
    return out[1]


def _download(
    url: str,
    key: str,
    path: str,
    query: dict[str, Any],
    dest: str | Path,
    *,
    timeout: float,
) -> tuple[int, int] | None:
    """流式落盘（先 `.part` 再换入）→ `(状态码, 字节数)`；非 200 不落文件。"""
    dest = Path(dest)
    try:
        resp = _open(url, key, path, query=query, timeout=timeout)
    except urllib.error.HTTPError as e:
        return int(e.code), 0
    if resp is None:
        return None
    try:
        status = int(getattr(resp, "status", 200))
        if status != 200:
            return status, 0
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        written = 0
        try:
            with open(part, "wb") as f:
                while True:
                    chunk = resp.read(_CHUNK)
                    if not chunk:
                        break
                    f.write(chunk)
                    written += len(chunk)
            os.replace(part, dest)
        finally:
            try:
                part.unlink()
            except OSError:
                pass
        return status, written
    except Exception:
        return None
    finally:
        try:
            resp.close()
        except Exception:
            pass


def download_round(
    url: str, key: str, it: int, dest: str | Path, *, timeout: float = BIG_TIMEOUT
) -> tuple[int, int] | None:
    """`GET /offline/push/round?it=N` → 落文件（与 `/offline/artifact` 同字节的 wire body）。"""
    return _download(url, key, OFFLINE_PUSH_ROUND_PATH, {"it": int(it)}, dest, timeout=timeout)


def download_result(
    url: str, key: str, dest: str | Path, *, timeout: float = BIG_TIMEOUT
) -> tuple[int, int] | None:
    """`GET /offline/push/result` → 落文件（202 = 还没收尾，不落）。"""
    return _download(url, key, OFFLINE_PUSH_RESULT_PATH, {}, dest, timeout=timeout)


def post_task(
    url: str, key: str, zip_path: str | Path, *, timeout: float = BIG_TIMEOUT
) -> tuple[int, dict] | None:
    """`POST /offline/push/task`：流式上传外层 zip（显式 `Content-Length`）。

    202 = 受理 / 409 = 它正忙（在跑别的单）/ 413 = 超对面 2GiB 上界 / 400 = 包坏了。
    网络层失败 ⇒ None（由调用方 release + 冷却，不下结论）。
    """
    path = Path(zip_path)
    try:
        size = path.stat().st_size
    except OSError:
        return None
    try:
        fh = open(path, "rb")
    except OSError:
        return None
    try:
        try:
            resp = _open(
                url,
                key,
                OFFLINE_PUSH_TASK_PATH,
                method="POST",
                data=fh,
                timeout=timeout,
                extra_headers={"Content-Type": "application/zip", "Content-Length": str(size)},
            )
        except urllib.error.HTTPError as e:
            resp = e
    finally:
        fh.close()
    if resp is None:
        return None
    return _read_json_response(resp)


def post_ack(url: str, key: str, payload: dict, *, timeout: float = 30.0) -> tuple[int, dict] | None:
    """`POST /offline/push/ack`：`{task_id, result, upto_it, revs}` → 按 rev 删 body。"""
    return call_json(url, key, OFFLINE_PUSH_ACK_PATH, method="POST", payload=payload, timeout=timeout)


def cancel_task(url: str, key: str, *, timeout: float = 15.0) -> tuple[int, dict] | None:
    """`POST /offline/push/cancel`：写 stop 文件（worker 在**轮边界**停，产物自洽）。"""
    return call_json(url, key, OFFLINE_PUSH_CANCEL_PATH, method="POST", payload={}, timeout=timeout)


__all__ = [
    "BIG_TIMEOUT",
    "SMALL_TIMEOUT",
    "call_json",
    "cancel_task",
    "download_result",
    "download_round",
    "get_progress",
    "get_state",
    "post_ack",
    "post_task",
]
