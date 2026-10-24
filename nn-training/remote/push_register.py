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
        #: 最近一次注册 HTTP 的应答码（0 = 没答）。**422 = hub 的 ping 门不过**（它连不到
        #: 本节点，或 authKey 不符），与「hub 不可达」是两件事——调用方据此给人话。
        self.last_status = 0
        self._warned = False
        self._closed = False

    # ---- 观测 ----
    @property
    def configured(self) -> bool:
        """hub/token 齐不齐（缺任一把 = 自动注册不可用，重试也不会变好）。"""
        return bool(self.hub and self.hub_token)

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
            self.last_status = int(out.get("status") or 0)
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
        # ★ 2026-10-24 真机事故：**失败后的成功必须自己说一句**。首拍撞上 quick tunnel 边缘
        #   未就绪（hub 的 ping 门 422）时，重试成功的那个 tick 原来一行不打 —— 操作员只看到
        #   「注册被拒 + 自动注册未生效」的横幅，然后在成功前 2s 把 cell 掐了。首次即成功不
        #   打（启动横幅由调用方负责，别把同一件事说两遍）；url 变化与「失败后转成功」都打。
        recovered = self._step > 0 or bool(self.last_error) or (
            bool(self._state.get("url")) and self._state.get("url") != url
        )
        self._step = 0
        self.next_at = 0.0
        self.registered = True
        self.last_status = 0
        self.action = str(out.get("action") or "")
        self.last_error = ""
        self._state = {"url": url, "authKey": str(auth_key or "")}
        caps = list(out.get("capabilities") or [])
        self.capabilities = caps
        if recovered:
            self._log(
                f"★ 注册成功 id={self.node_id}（{self.action or 'ok'}，caps="
                f"{','.join(caps) or '-'}）——hub 已能经隧道到达本机"
            )
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

    # ---- 退避 ----
    def reset_backoff(self) -> None:
        """把退避梯子退回起点（快重试窗口用完时用：别让一次密集重试抬高后续节拍）。"""
        self._step = 0
        self.next_at = 0.0

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


# ---------------------------------------------------------------- 首拍注册（带就绪窗口）


#: 首拍注册的就地快重试窗口（秒）。quick tunnel 的 URL 在 Cloudflare 边缘**注册完成前**就写进
#: cloudflared 日志（`docs/nn/remote-transport.md` §7「quick tunnel 的 URL 在 edge 注册完成前就
#: 写进日志」），而 hub 的探活门是**一发 3s** ⇒ 首拍必吃 422；主循环又要等 `TICK_SEC`(15s)。
#: 2026-10-24 真机：13:40:21 拿到 URL → 13:40:23 422 → 13:40:38 才成功，操作员在 :40 掐了 cell。
FIRST_REGISTER_WAIT_SEC = 45.0
#: 窗口内的重试间隔（秒）。比 `RETRY_LADDER[0]`(2s) 略慢：hub 那发 3s ping 本身就要时间。
FIRST_REGISTER_STEP_SEC = 3.0


def register_or_warn(
    reg: Any,
    url: str,
    auth_key: str,
    log: Callable[[str], None],
    *,
    wait_sec: float = FIRST_REGISTER_WAIT_SEC,
    step_sec: float = FIRST_REGISTER_STEP_SEC,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], float] = time.time,
) -> bool:
    """首拍注册 + 人话横幅；失败 ⇒ 打印**可粘贴**的手工片段（GPU / 离线两条腿共用）。

    为什么不是一个 `ensure(force=True)`（2026-10-24 真机事故）：quick tunnel 刚起来的几秒里 hub
    的 ping 门必然 422，而「一次失败 + 一句『hub 不可达/未配置』」会让人以为整条链没配好——
    真机现场就是这样把已经成功的注册当成失败掐掉的。这里给一个有界的就绪窗口（`wait_sec`），
    窗口内就地快重试；窗口外才给手工片段，并按 **`last_status`** 说清楚是哪一类失败：

    * `422` = 注册请求**到了** hub，但 hub 连不到本机隧道（或 authKey 不符）⇒ 不是 HUB_TOKEN；
    * `auth`（401/403）= HUB_TOKEN 不对 ⇒ 重试无意义（`Registry` 已放弃）；
    * 其余 = hub 不可达 / 没配。

    返回是否注册成功（调用方不需要据此改变行为：注册是旁路）。

    ★ 窗口只给 **422**：那是「hub 答了、但我的隧道还没在边缘就绪」，等几秒真会好；
    「连不上 hub」（无应答 / 连接被拒 / 5xx）就地等没有信息增益 —— 交给主循环的退避梯子，
    操作员也该立刻看到手工片段（真机上 hub 长时间不可达是另一类故障）。
    """
    if not url:
        log("★ 没有隧道 URL，自动注册暂不可用——用下面一条手工登记：")
        if reg.node_id:
            log(f'  {{"nodes":[{manual_snippet(reg.node_id, "", auth_key, reg.kind)}]}}')
        return False
    if getattr(reg, "configured", True) is False:
        # 缺 hub/token：重试一万次也不会好（`Registry.ensure` 只 WARN 一次）⇒ 直接给片段，
        # 别把就绪窗口烧在这种「配置错」上（那 45s 里操作员以为在等隧道）。
        log("★ 自动注册未配置（registry_url/hub_url 或 HUB_TOKEN 缺失）——用下面一条手工登记：")
        log(f'  {{"nodes":[{manual_snippet(reg.node_id, url, auth_key, reg.kind)}]}}')
        return False
    deadline = now() + max(0.0, float(wait_sec))
    told = False
    while True:
        if reg.ensure(url, auth_key, force=True):
            log(
                f"★ 已自动注册节点 id={reg.node_id} kind={reg.kind or '-'}"
                "（hub 按能力键派发，无需手工登记）"
            )
            return True
        if reg.give_up:
            log("★ 自动注册已放弃（HUB_TOKEN 被拒）——用下面一条手工登记：")
            break
        status = int(getattr(reg, "last_status", 0) or 0)
        if status != 422:
            log("★ 自动注册未生效（hub 不可达/未配置）——用下面一条手工登记：")
            break
        if now() >= deadline:
            log(
                "★ 自动注册未生效：hub 收下了请求，但**它的探活门打不通本机隧道**"
                f"（HTTP 422：{url}/ping 无应答，或 authKey 不符）——不是 HUB_TOKEN 的问题；"
                "隧道就绪/换名后会自动重注册。要立刻接管就手工登记："
            )
            break
        if not told:
            told = True
            log(
                f"首拍注册未通过（HTTP {status}）——"
                f"{int(wait_sec)}s 内快重试（quick tunnel 边缘就绪有延迟，先别手工介入）"
            )
        sleep(min(float(step_sec), max(0.2, deadline - now())))
    # 窗口用完 ⇒ 退避梯子退回起点：后面由主循环按 2s→5s→… 接着试，别继承这 15 次的步数。
    if hasattr(reg, "reset_backoff"):
        reg.reset_backoff()
    log(f'  {{"nodes":[{manual_snippet(reg.node_id, url, auth_key, reg.kind)}]}}')
    return False


# ---------------------------------------------------------------- 派发器是否挂载


def probe_offline_dispatch(
    hub: str,
    token: str,
    *,
    timeout: float = 5.0,
    urlopen: RegisterFn = urllib.request.urlopen,
) -> bool | None:
    """hub 上**离线 push 派发器**挂没挂（`GET /admin/offline-push-workers`）。

    为什么 worker 要问这一句（2026-10-24 真机两连击）：注册成功只证明「hub 收下了节点、
    能 ping 通你」——**不证明有人会派单**。hub 没带 `--offline-push` 时 `offline_push=None`，
    这个读面回 **409**（老 hub 没有这条路由 ⇒ **404**），而 worker 那边会一直 `phase=idle`
    地守候，日志上一个字都不提示。

    返回：`True` = 已挂载；`False` = 没挂（404/409，注册进来也不会派单）；`None` = 问不到
    （不可达/鉴权被拒）——不可知 ≠ 没有，调用方不许把 `None` 说成「没挂」。
    """
    base = _hub_base(hub)
    if not base or not token:
        return None
    req = urllib.request.Request(
        base + "/admin/offline-push-workers",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urlopen(req, timeout=timeout) as resp:
            return int(getattr(resp, "status", 200) or 200) == 200
    except urllib.error.HTTPError as e:
        if e.code in (404, 409):
            return False
        return None
    except Exception:
        return None


#: 派发器没挂时给操作员的那句话（空串 = 不用说话）。**纯函数**：用例断返回值，不碰日志文本。
NO_DISPATCH_NOTE = (
    "⚠ hub 的离线 push 派发器**没挂载**（/admin/offline-push-workers 404/409）——"
    "注册成功也**不会收到派单**：在控制台重启 hub-server（启动参数要带 --offline-push；"
    "若控制台本身还是旧进程，先重启控制台——它按自己的代码拼 hub 的参数）"
)


def dispatch_mount_note(mounted: bool | None) -> str:
    """探测结论 → 一句人话：没挂 ⇒ 那句警告；挂载/不知道 ⇒ 空串（不编、也不刷屏）。"""
    return NO_DISPATCH_NOTE if mounted is False else ""


__all__ = [
    "FIRST_REGISTER_STEP_SEC",
    "FIRST_REGISTER_WAIT_SEC",
    "NO_DISPATCH_NOTE",
    "RETRY_LADDER",
    "Registry",
    "build_register_payload",
    "classify_error",
    "default_node_id",
    "dispatch_mount_note",
    "manual_snippet",
    "probe_offline_dispatch",
    "register",
    "register_or_warn",
    "should_register",
    "unregister",
]
