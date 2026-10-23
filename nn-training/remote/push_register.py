"""remote/push_register.py — 节点自动注册的**唯一实现**（2026-10-23，plan/push-mode-notebooks §4.3）。

为什么会有这个模块：注册客户端此前只有一份，住在 `remote/rollout_node.py`（采样节点）。push
模式要给另外两条腿加自动注册——GPU（`battle.cloudflared`）与离线（`battle.offline`）——而它们
与采样节点的差别只有两处：payload 多一个 `kind`、应答里要认 `capabilities`。第三份实现会立刻
分叉退避梯子（采样 30s→300s、hub 探活 30s、若照 plan 再写一套 2s→120s = **三套**），而「注册
失败该等多久」这种数值最容易被抄错一次就永远不同步。

落点（`tests/helpers/remote_dag.py` 账本：依赖只有 stdlib ⇒ **L1**）：

* 纯函数：`build_register_payload` / `should_register` / `classify_error` / `default_node_id`
* HTTP：`register` / `unregister`（注入 `urlopen`；**绝不抛**）
* `Registry`：url/token 去重 + 退避 + 能力回显校验 + 注销（GPU/离线共用）

## 两条不可退让的语义

1. **`kind` 缺席即旧字节不变**：`build_register_payload(..., kind="")` 不写这个键。采样节点每
   会话都注册一次，若它带上 `kind="sampler"`，hub 的合并就会清掉控制台手工挂的能力键
   （2026-10-23 评审 P0-2）——所以只有**真的要声明 push 角色**时才传 kind。
2. **`capabilities` 是「hub 听懂了」的证据**：老 hub 静默忽略 `kind`、不回落显。`Registry` 因此
   把「kind 显式传了但应答没有 capabilities」记成 `old_hub`，打一行 WARN + 可粘贴的手工片段，
   而不是假装注册成功（那会让操作员以为自动注册生效了，实际一次也派不下来）。

## node_id 的落点（2026-10-23 评审 P1-5）

用**按角色的稳定 id**（`gpu-<env>` / `offline-<env>`），与采样节点 `rollout-<env>` 同一口径。
不落 `/tmp` 的随机 hex：云机重建即丢，而 hub 的 unregister **不删条目**（只写
`enabled=false` + `unregistered_at`）⇒ 每换一台 VM 就多一条永不消失的 managed 节点，节点列表
线性增长。稳定 id 的代价是「两台同角色并发会互相顶 URL」——GPU/离线各只需一台，可接受；
需要并发时用 `CFG["node_id"]` 显式区分。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

#: 注册退避梯子（秒）：push 节点「注册成功才可能接到活」，所以起点比采样快（2s），
#: 上限比采样低（120s vs 300s）——云机会话只有几分钟到几小时，等 5 分钟等于放弃。
RETRY_LADDER: tuple[float, ...] = (2.0, 5.0, 10.0, 30.0, 60.0, 120.0)

#: `kind` → node_id 前缀（与 `rollout-<env>` 同口径；见模块头「node_id 的落点」）。
_KIND_PREFIX = {"gpu_push": "gpu", "offline_push": "offline"}

RegisterFn = Callable[..., Any]


# ---------------------------------------------------------------- 纯函数（可单测）


def default_node_id(kind: str, env: str) -> str:
    """按角色与运行环境给出稳定 node_id（`gpu-colab` / `offline-kaggle`）。"""
    prefix = _KIND_PREFIX.get(str(kind or ""), "node")
    return f"{prefix}-{str(env or 'local').strip() or 'local'}"


def build_register_payload(
    node_id: str,
    url: str,
    auth_key: str,
    workers: int | str | None = 0,
    label: str = "",
    kind: str = "",
) -> dict:
    """注册载荷。

    ★ `concurrency` **只在 `workers > 0` 时带上**（F2）：派发侧读
    `n.get("concurrency") or ping.cpus`（`trainer/dispatch.py:343`），缺省 = 按节点核数；
    写死一个 1 会把 96 核云机静默限成单核（旧 cell 口径 = 不填/0 = 不额外限流）。

    ★ `kind` 只在**非空**时带上（P0-2：缺席 = 不动能力键）。采样节点调用点的字节因此不变。
    """
    payload: dict[str, Any] = {
        "id": str(node_id or ""),
        "url": str(url or ""),
        "authKey": str(auth_key or ""),
        "managed": True,
    }
    try:
        w = int(workers or 0)
    except (TypeError, ValueError):
        w = 0
    if w > 0:
        payload["concurrency"] = w
    lab = str(label or "").strip()
    if lab:
        payload["label"] = lab
    k = str(kind or "").strip()
    if k:
        payload["kind"] = k
    return payload


def should_register(state: dict | None, url: str, auth_key: str) -> bool:
    """去重：只有 url / authKey 变化才值得重发（心跳每 60s 一次，不许周期性重写文件）。"""
    st = state or {}
    return str(url or "") != str(st.get("url") or "") or str(auth_key or "") != str(
        st.get("authKey") or ""
    )


def classify_error(exc: BaseException) -> str:
    """错误分类（驱动退避）：auth → 立即放弃；net/server → 退避重试。

    ⚠ `HTTPError` 是 `URLError` **也是** `OSError` 的子类 ⇒ 必须先判它。
    """
    if isinstance(exc, urllib.error.HTTPError):
        return "auth" if exc.code in (401, 403) else "server"
    if isinstance(exc, (urllib.error.URLError, TimeoutError, OSError)):
        return "net"
    return "unknown"


def manual_snippet(node_id: str, url: str, auth_key: str, kind: str) -> str:
    """手工兜底的一行 `nodes[]` 片段（★ 只有它可以被粘进 rl-config）。"""
    cap = "offline_push" if str(kind or "") == "offline_push" else "gpu_push"
    return (
        f'{{"id":"{node_id}","url":"{url}","authKey":"{auth_key}",'
        f'"{cap}":true,"enabled":true}}'
    )


# ---------------------------------------------------------------- 注册 HTTP（urlopen 注入）


def _hub_base(hub: str) -> str:
    return str(hub or "").strip().rstrip("/")


def _post_json(
    urlopen: RegisterFn,
    url: str,
    token: str,
    payload: dict,
    timeout: float,
) -> dict:
    """`POST <url>`（Bearer）→ `{ok, status, action, body, error, kind}`；**绝不抛**。"""
    req = urllib.request.Request(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=timeout) as resp:
        raw = resp.read().decode("utf-8") or "{}"
        body = json.loads(raw)
        return {
            "ok": True,
            "status": int(getattr(resp, "status", 200) or 200),
            "action": str((body or {}).get("action") or "") if isinstance(body, dict) else "",
            "body": body if isinstance(body, dict) else {},
            "error": None,
            "kind": "ok",
        }


def register(
    urlopen: RegisterFn,
    hub: str,
    token: str,
    payload: dict,
    log: Callable[[str], None],
    timeout: float = 15.0,
) -> dict:
    """`POST /admin/nodes/register`；返回 `{ok, status, action, body, capabilities, error, kind}`。

    `status == 422` = hub 的 ping 门不过（它从 hub 侧连不到本节点）——调用方据此做
    tailscale → cloudflared 的回落判定。

    `body` / `capabilities`（2026-10-23）：push 节点要用应答里的 `capabilities` 判定「hub 真的
    听懂了 `kind` 吗」。老形状（没有这个键）⇒ `capabilities=[]`，不编。
    """
    base = _hub_base(hub)
    if not base or not token:
        return {
            "ok": False,
            "status": 0,
            "action": None,
            "body": {},
            "capabilities": [],
            "error": "hub/token 未配置",
            "kind": "net",
        }
    try:
        out = _post_json(urlopen, base + "/admin/nodes/register", token, payload, timeout)
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:300]
        except Exception:  # 读体失败不改判定
            pass
        kind = classify_error(e)
        log(f"注册被拒: HTTP {e.code}（{kind}）{detail}")
        return {
            "ok": False,
            "status": int(e.code),
            "action": None,
            "body": {},
            "capabilities": [],
            "error": detail,
            "kind": kind,
        }
    except Exception as e:  # 网络/超时/坏 JSON/其它
        kind = classify_error(e)
        log(f"注册失败: {type(e).__name__}: {e}（{kind}）")
        return {
            "ok": False,
            "status": 0,
            "action": None,
            "body": {},
            "capabilities": [],
            "error": f"{type(e).__name__}: {e}",
            "kind": kind,
        }
    caps = out["body"].get("capabilities")
    out["capabilities"] = [str(c) for c in caps] if isinstance(caps, list) else []
    return out


def unregister(
    urlopen: RegisterFn,
    hub: str,
    token: str,
    node_id: str,
    log: Callable[[str], None],
    timeout: float = 15.0,
) -> dict:
    """`POST /admin/nodes/unregister`（会话收线 best-effort；返回形状同 `register`）。"""
    base = _hub_base(hub)
    if not base or not token or not node_id:
        return {
            "ok": False,
            "status": 0,
            "action": None,
            "body": {},
            "capabilities": [],
            "error": "未配置",
            "kind": "net",
        }
    try:
        out = _post_json(urlopen, base + "/admin/nodes/unregister", token, {"id": str(node_id)}, timeout)
    except urllib.error.HTTPError as e:
        log(f"注销被拒: HTTP {e.code}")
        return {
            "ok": False,
            "status": int(e.code),
            "action": None,
            "body": {},
            "capabilities": [],
            "error": f"HTTP {e.code}",
            "kind": classify_error(e),
        }
    except Exception as e:
        log(f"注销失败: {type(e).__name__}: {e}")
        return {
            "ok": False,
            "status": 0,
            "action": None,
            "body": {},
            "capabilities": [],
            "error": f"{type(e).__name__}: {e}",
            "kind": classify_error(e),
        }
    out["capabilities"] = []
    return out


# ---------------------------------------------------------------- Registry（push 节点用）


class Registry:
    """push 节点自注册状态机（GPU / 离线共用一份实现）。

    契约：

    * `ensure(url, auth_key)` 只在 **url/auth_key 变了**（或从未成功）时才发请求 —— 主循环每
      15s 调一次也不该把 rl-config 每 15s 重写一遍；
    * 失败按 `RETRY_LADDER` 退避，`auth` 类错误**立即放弃**（token 不对，重试只会刷 hub 日志）；
    * 传了 `kind` 而应答没有 `capabilities` ⇒ `old_hub=True`，WARN **只打一次**；
    * `unregister()` best-effort 且幂等；
    * **绝不抛**：注册是旁路，不能让训练/服务陪葬。
    """

    def __init__(
        self,
        *,
        hub: str,
        hub_token: str,
        node_id: str,
        kind: str = "gpu_push",
        workers: int | str | None = 0,
        label: str = "",
        log: Callable[[str], None] = lambda _m: None,
        urlopen: RegisterFn = urllib.request.urlopen,
        now_fn: Callable[[], float] = time.time,
    ) -> None:
        self.hub = _hub_base(hub)
        self.hub_token = str(hub_token or "")
        self.node_id = str(node_id or "")
        self.kind = str(kind or "")
        self.workers = workers
        self.label = str(label or "")
        self._log = log
        self._urlopen = urlopen
        self._now = now_fn
        self._state: dict[str, str] = {"url": "", "authKey": ""}
        self._step = 0
        self.next_at = 0.0
        self.registered = False
        self.give_up = False
        self.old_hub = False
        self.capabilities: list[str] = []
        self.action = ""
        self.last_error = ""
        self._warned = False
        self._closed = False

    # ---- 观测 ----
    def info(self) -> dict:
        return {
            "node_id": self.node_id,
            "kind": self.kind,
            "registered": self.registered,
            "give_up": self.give_up,
            "old_hub": self.old_hub,
            "capabilities": list(self.capabilities),
            "action": self.action,
            "last_error": self.last_error,
            "url": self._state.get("url", ""),
        }

    # ---- 注册 ----
    def ensure(self, url: str, auth_key: str, *, force: bool = False) -> bool:
        """保证 hub 上「本节点 = 这个 url/authKey」；返回本次是否处于已注册状态。"""
        if self._closed or self.give_up:
            return self.registered
        if not self.hub or not self.hub_token:
            self._warn_once(
                "未配置 registry_url/hub_url 或 HUB_TOKEN —— 自动注册不可用，只能手工登记："
            )
            return False
        url = str(url or "").strip().rstrip("/")
        if not url:
            return False
        if not force and self.registered and not should_register(self._state, url, auth_key):
            return True
        if not force and self._now() < self.next_at:
            return self.registered
        payload = build_register_payload(
            self.node_id, url, auth_key, self.workers, self.label, self.kind
        )
        out = register(self._urlopen, self.hub, self.hub_token, payload, self._log)
        if not out["ok"]:
            self.last_error = str(out.get("error") or "")
            if out.get("kind") == "auth":
                self.give_up = True
                self._log(
                    "HUB_TOKEN 被拒（auth）——自动注册放弃：token 不对，重试无意义（检查 secret）"
                )
                return False
            wait = RETRY_LADDER[min(self._step, len(RETRY_LADDER) - 1)]
            self._step += 1
            self.next_at = self._now() + wait
            self._log(f"注册未成功（{out.get('kind')}）——{int(wait)}s 后重试")
            return False
        # 成功：复位退避 + 记住「已注册的是什么」
        self._step = 0
        self.next_at = 0.0
        self.registered = True
        self.action = str(out.get("action") or "")
        self.last_error = ""
        self._state = {"url": url, "authKey": str(auth_key or "")}
        caps = list(out.get("capabilities") or [])
        self.capabilities = caps
        if self.kind and not caps:
            # 老 hub：认了请求（200）但不回能力回显 ⇒ 它没听懂 kind，能力键没写进去。
            self.old_hub = True
            self._warn_once(
                "hub 应答没有 capabilities —— 老 hub 不会写能力键（自动派发不会生效）。"
                "升级 hub，或手工把下面一条加进 rl-config.json 的 nodes[]："
            )
        return True

    # ---- 注销 ----
    def unregister(self) -> bool:
        """会话收线：`enabled=false` + `unregistered_at`。best-effort，绝不抛。"""
        if self._closed:
            return False
        if not (self.registered and self.hub and self.hub_token and self.node_id):
            # 没注册过 ⇒ 不发请求、也**不关死**（后续 ensure 还能注册：会话可能还没开始）
            return False
        self._closed = True
        out = unregister(
            self._urlopen, self.hub, self.hub_token, self.node_id, self._log
        )
        ok = bool(out["ok"])
        self.registered = not ok
        self._log(f"unregister id={self.node_id}: {'ok' if ok else out.get('error')}")
        return ok

    # ---- 日志 ----
    def _warn_once(self, msg: str) -> None:
        if self._warned:
            return
        self._warned = True
        self._log("WARN " + msg)
        if self.node_id and self._state.get("url"):
            # 只在这一行打印 push_token：手动兜底必须可粘贴（★ 产物不入日志，凭据只出现在这里）。
            self._log(
                "  "
                + manual_snippet(
                    self.node_id, self._state.get("url", ""), self._state.get("authKey", ""), self.kind
                )
            )
