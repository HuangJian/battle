"""remote/rollout_node.py — 采样节点（rollout / eval 云机）的运行时。

来源（2026-10-10，plan/rollout-node-auto-register v2）：`ipynb/rollout.ipynb`
（2026-10-10 由 `rollout.cloudflared.ipynb` 改名）的 cell 原来内联了全套逻辑；
本模块把逻辑收回仓库，cell 只留 CFG / 凭据 / 保活 / clone —— 与
`battle.tailscale.ipynb`（逻辑住 `remote/notebook_boot.py`）同构。

本模块经 **clone 导入**（不是 GitHub raw 单文件拉取），因此可以 import
`remote.tailscale_boot` 与 `common.*`（三个自包含引导模块的结构性例外**不适用**）。

三条硬纪律（写错都是事故）：

1. **凭据必须在 `tailscale_boot.ensure()` 之前读完**：ensure 落到 userspace 时会把
   `HTTP_PROXY`/`ALL_PROXY` 指到只转发 tailnet 的本地代理，之后平台 Secrets（公网 HTTPS）
   走不通（2026-09-17 Kaggle 事故；见 `tailscale_boot.py:427` docstring）。取值一律经
   cell 提供的 `secret(key, cfg_val)`，值永不进日志。源码顺序由
   `tests/remote/test_rollout_node_notebook.py` 钉住。
2. **公网下载一律在 `platform_net_env()` 里**（或发生在 ensure 之前）：bun 安装在 ensure 前，
   cloudflared 安装在 `platform_net_env()` 里（先例 `battle.offline.ipynb:408-409`）。
3. **通道自适应**：Colab 优先 tailscale；tailscale 引导失败、或注册被 hub ping 门拒（422 =
   入站不可达，userspace 模式的已知限制）⇒ 回落 cloudflared 并以隧道 URL 重注册。

自动注册（plan §2.2）：`POST /admin/nodes/register`（hub，Bearer = HUB_TOKEN）upsert
`rl-config.json` 的 `nodes[]`；本模块只是**发起方**，`enabled` 等字段语义见 hub 侧实现。
会话收线时 best-effort `POST /admin/nodes/unregister`；hub 未配置/不可达 ⇒ 打印 ★URL + ★authKey
（旧的手工路径，功能不残）。
"""

from __future__ import annotations

import http.client
import json
import os
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from common.env_probe import probe_cloud_env, worker_name
from common.errors import NET_ERRORS
from common.proc import bun_version as _proc_bun_version
from remote import tailscale_boot
from remote.node_toolchain import ensure_bun, ensure_cloudflared

# 注册客户端住在 `remote/push_register.py`（2026-10-23，plan/push-mode-notebooks §4.3）：
# 采样、GPU、离线三条腿共用同一份退避梯子/错误分类/★片段实现。这里 import 进来并**保留同名
# 绑定**（`X as X` = 显式 re-export），`tests/remote/test_rollout_node.py` 的
# `rn.build_register_payload(...)` / `rn.classify_error(...)` 等用法不变。
from remote.push_register import build_register_payload as build_register_payload
from remote.push_register import classify_error as classify_error
from remote.push_register import register as register
from remote.push_register import should_register as should_register
from remote.push_register import unregister as unregister
from remote.push_tunnel import tunnel_url_of as _tunnel_url
from remote.rollout_tunnel import PROBE_SEC as TUNNEL_PROBE_SEC
from remote.rollout_tunnel import dead_reason as _dead_reason
from remote.rollout_tunnel import dump_tail as _dump_tail
from remote.rollout_tunnel import public_ping as _public_ping
from remote.rollout_tunnel import reset_log as _reset_cf_log
from remote.rollout_tunnel import retry_due as _tunnel_retry_due
from remote.rollout_tunnel import tunnel_dead as _tunnel_dead
from remote.rollout_tunnel import wait_url as _wait_tunnel_url

#: 通道名（与 hub/nodes 注册载荷里的 url 形态对应）。
LINK_TAILSCALE = "tailscale"
LINK_CLOUDFLARED = "cloudflared"

#: 「探不活」的真失败形态（`_probe_https` / `_ping` / `_status` 共用）：传输面 = `NET_ERRORS`
#: （OSError 盖住 URLError/超时/TLS/socket 被拒，`RuntimeError` 是传输壳「重试耗尽无结果」的
#: 防御性 raise），再加 `http.client.HTTPException`（体被截断/状态行乱）与 `ValueError`（200 但
#: body 不是 JSON）。**程序员错不落这个集**：`_ping` 的结论是「节点死了 ⇒ 重启 agent」，把
#: `AttributeError`（回执形状变了）读成不健康就是重启环，而那是最贵的一种误诊。
_NODE_PROBE_ERRORS: tuple[type[BaseException], ...] = (
    *NET_ERRORS,
    http.client.HTTPException,
    ValueError,
)

#: 退避上限（net/server 类失败）。
RETRY_MAX_SEC = 300.0
RETRY_FIRST_SEC = 30.0

#: cloudflared 自救（plan/rollout-tunnel-url-retry）：每次 attempt 是新进程，最多 3 次；
#: 第 3 次回到首选姿势并开 debug，把「死因」打出来。
CLOUDFLARED_ATTEMPTS = 3
CLOUDFLARED_RETRY_SEC = 5.0
CLOUDFLARED_PROBE_TIMEOUT = 3.0
#: 首拍失败后的「补拿」节拍与监护循环里的等待上限（不能每拍阻塞 90s）。
TUNNEL_RETRY_SEC = 60.0
TUNNEL_RETRY_WAIT_SEC = 30.0

#: 「近 30 分钟产出」摘要的打印节拍（用户 2026-10-18：「rollout.ipynb 及 sampler-agent 的 log
#: 无法观察最近贡献情况，请持续统计半小时滑动窗口产出，每十分钟打印一行」）。
#: 窗口计算在 agent 侧（`tools/agent/output-window.ts`，那里才有逐局完成时刻）——本模块
#: **只负责把那一行搬进 notebook/rollout-node 日志**，第二个数字源就是第二个真相。
OUTPUT_STATUS_EVERY_SEC = 600.0

#: 受监护启动标记：与 `tools/agent/restart-guard.ts` 的 `SUPERVISED_ENV` 逐字对齐。
#: 有它时 agent 的 `/v1/restart` 只退出、不 spawn detached child —— 由本模块的监护循环
#: 从盘上重新拉起（notebook 启动链没有控制台那套自交接 owner，抢端口就会把节点打崩）。
AGENT_SUPERVISED_ENV = "SAMPLER_SUPERVISED"
#: 受监护重启的退出码（EX_TEMPFAIL）：计划内重启，不计入 `restart_limit` 的崩溃预算。
AGENT_RESTART_EXIT_CODE = 75

RegisterFn = Callable[..., Any]


# ---------------------------------------------------------------- 纯函数（可单测）

def resolve_link(env: str, cfg_link: str, has_ts_authkey: bool) -> str:
    """通道决策：`cfg_link` 可强压；否则 Colab 优先 tailscale、其余看有没有 TS_AUTHKEY。"""
    forced = str(cfg_link or "").strip().lower()
    if forced in (LINK_TAILSCALE, LINK_CLOUDFLARED):
        return forced
    if str(env or "").strip().lower() == "colab":
        return LINK_TAILSCALE
    return LINK_TAILSCALE if has_ts_authkey else LINK_CLOUDFLARED


def registry_url(hub_ip: str, hub_port: int, cfg_hub_url: str = "") -> str:
    """hub 注册地址 —— **直接转调** `tailscale_boot.resolve_hub_url`（单一实现，F13）。

    口径与 HUB_IP 取值链一致：裸 IP/主机名补 `http://` + 端口；整条 URL 原样；含 `<` 视为未填。
    """
    return tailscale_boot.resolve_hub_url(
        cfg_hub_url=cfg_hub_url, hub_ip=hub_ip, hub_port=hub_port
    )


# ---------------------------------------------------------------- 进程/网络小工具

def _direct_env() -> dict[str, str]:
    """给 cloudflared 子进程的「直连」环境：摘掉代理键，但保留 NO_PROXY 的本地回环。

    为什么不能用 `platform_net_env()`：它只在 tailscale `set_proxy_env()` 跑过之后才有
    还原对象；cf-only 路径（没有 TS_AUTHKEY）里它是空操作，平台代理仍在。
    为什么不能连 `NO_PROXY` 一起清：cloudflared 还要访问 `http://localhost:<port>`，
    没有 bypass 条目时它可能被塞进代理。
    """
    env = dict(os.environ)
    for key in tailscale_boot.PROXY_ENV_KEYS:
        if key.lower() != "no_proxy":
            env.pop(key, None)
    bypass = env.get("NO_PROXY") or env.get("no_proxy") or ""
    parts = [p for p in ("localhost", "127.0.0.1", "::1", bypass) if p]
    env["NO_PROXY"] = ",".join(dict.fromkeys(parts))
    env["no_proxy"] = env["NO_PROXY"]
    return env


def _probe_https(
    posture: str,
    host: str = "api.trycloudflare.com",
    timeout: float = CLOUDFLARED_PROBE_TIMEOUT,
) -> bool:
    """代理感知的 HTTPS 可达性：`direct` 显式不挂代理，`inherit` 用进程当前代理。

    为什么不用 `socket.create_connection`：原始 socket 不读 `HTTP(S)_PROXY`，
    `inherit` 与 `direct` 的结果必然相同（plan/rollout-tunnel-url-retry B3）。
    """
    handlers = [urllib.request.ProxyHandler({})] if posture == "direct" else []
    opener = urllib.request.build_opener(*handlers)
    req = urllib.request.Request(f"https://{host}/", method="HEAD")
    try:
        with opener.open(req, timeout=timeout):
            return True
    except urllib.error.HTTPError:
        return True  # 404/405 也说明 DNS/TLS/代理链通了
    except _NODE_PROBE_ERRORS:
        return False


def _probe_tcp(
    host: str, port: int, timeout: float = CLOUDFLARED_PROBE_TIMEOUT
) -> bool:
    """原始 TCP 可达性（不看代理；只作边缘 7844 的诊断，不参与姿势选择）。"""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _net_probe(log: Callable[[str], None]) -> tuple[str, str]:
    """一次预检：用代理感知的 HTTPS 选首选姿势，原始 TCP 只诊断边缘 7844。"""
    inherit = _probe_https("inherit")
    direct = _probe_https("direct")
    edge = _probe_tcp("region1.v2.argotunnel.com", 7844)
    log(
        "预检: api.trycloudflare.com:443 "
        f"inherit={'ok' if inherit else 'fail'} direct={'ok' if direct else 'fail'} "
        f"｜ 边缘7844 tcp={'ok' if edge else 'fail'}"
    )
    if inherit:
        log("⇒ 采用 inherit 姿势")
        return "inherit", "direct"
    if direct:
        log("⇒ inherit 不通、direct 可达 ⇒ 先试 direct 姿势")
        return "direct", "inherit"
    log(
        "⇒ 公网不可达：api.trycloudflare.com:443 两种姿势都不通（检查出网/DNS）；"
        "仍按 inherit 尝试"
    )
    return "inherit", "direct"


def _terminate(p: Any) -> None:
    if p is None or getattr(p, "poll", None) is None or p.poll() is not None:
        return
    try:
        p.terminate()
        p.wait(timeout=10)
    except Exception:
        try:
            p.kill()
        except Exception:
            # 契约静默（G9 冻结，非旧债）：terminate+wait 失败后的最后一级 kill；
            # 节点收尾不该把「kill 也没成功」变成一个新的异常。
            pass


def _slot_count(explicit: int) -> tuple[int, str]:
    """并发槽位：显式值 > `physical_cores()` **全核**（采样节点不预留，用户 2026-11-04）。

    与训练侧 `cpu_worker_slots()`（留 2 核给数据回传）分家：采样节点只跑采样/评估，
    满核才够用；8 物理核机器起 8 个 worker。

    ★ 2026-11-11 用**物理核**（不是 `effective_cores()`）：口径必须与 agent 自己的
      `CPUS = physicalCores()`（`tools/lib/cores.ts`）一致——同一个节点两套口径就会
      出现「笔记本按 4 开池、agent 自报 2 核」的超额认购（kaggle 实测，父进程 HTTP
      被饿到 30~60s）。HT sibling 只共享执行单元与 L1/L2，每个 worker 是 ~75–100MB
      的重型进程，按物理核铺才是「满核」。
    """
    cores = os.cpu_count() or 1
    try:
        from common.platform_utils import effective_cores, physical_cores

        cores = min(effective_cores(), physical_cores())
        note = f"platform_utils 物理核={cores} 全核"
    except Exception as e:  # 拉不起 platform_utils 时的兜底：同样用满
        note = f"内联回退({type(e).__name__}) cores={cores}"
    if int(explicit or 0) > 0:
        return int(explicit), note + "（CFG.workers 显式）"
    return int(cores), note


def _start_cloudflared(
    cfg: dict,
    port: int,
    cf_log: Path,
    log: Callable[[str], None],
    *,
    protocol: str | None = "http2",
    edge_ip: str | None = "4",
    debug: bool = False,
    env_posture: str = "inherit",
) -> Any:
    """起 cloudflared quick tunnel；二进制由 `node_toolchain.ensure_cloudflared` 备好。

    `protocol` / `edge_ip` 为 None = 不传，交给 cloudflared 自选（第 3 次 attempt 用）；
    参数顺序与 `hub/tunnel_ab_probe.py::_start_tunnel` 同形，避免两套写法漂移。
    """
    cf = ensure_cloudflared(cfg, log)
    if not cf:
        return None
    cmd = [cf, "tunnel", "--url", f"http://localhost:{port}", "--no-autoupdate"]
    if protocol:
        cmd += ["--protocol", protocol]
    if edge_ip:
        cmd += ["--edge-ip-version", edge_ip]
    if debug:
        cmd += ["--loglevel", "debug"]
    cmd += ["--logfile", str(cf_log)]
    env = _direct_env() if env_posture == "direct" else None
    try:
        return subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env=env,
        )
    except OSError as e:
        log(f"cloudflared 启动失败: {e}")
        return None


def _ensure_cloudflared_url(
    cfg: dict,
    port: int,
    cf_log: Path,
    log: Callable[[str], None],
    *,
    attempts: int = CLOUDFLARED_ATTEMPTS,
    postures: tuple[str, ...] | None = None,
    wait_timeout: float = 90.0,
) -> tuple[str, Any]:
    """起 cloudflared 并拿 URL；每次 attempt 都是**新进程**，失败带 rc/死因。

    阶梯（plan/rollout-tunnel-url-retry §2.6-B）：
    1. 预检首选姿势 + http2 + v4；
    2. 另一姿势 + http2 + v4；
    3. **回到首选姿势** + 不指定 protocol/edge（交给 cloudflared 自选）+ debug。

    第 3 次回到首选而不是重复第 2 次，是为了让 debug 落在「最可能成功」的姿势上。
    URL 出现但进程已退出 ⇒ 不采用（死域名会被注册成 530/422，见 §2.2）。
    """
    prefs = tuple(postures or ("inherit", "direct"))
    preferred = prefs[0] or "inherit"
    other = prefs[1] if len(prefs) > 1 else ("direct" if preferred == "inherit" else "inherit")
    ladder: tuple[tuple[str, str | None, str | None, bool], ...] = (
        (preferred, "http2", "4", False),
        (other, "http2", "4", False),
        (preferred, None, None, True),
    )
    total = max(1, int(attempts))
    for i in range(1, total + 1):
        posture, protocol, edge_ip, debug = ladder[min(i - 1, len(ladder) - 1)]
        _reset_cf_log(cf_log, i)
        t0 = time.time()
        cfp = _start_cloudflared(
            cfg,
            port,
            cf_log,
            log,
            protocol=protocol,
            edge_ip=edge_ip,
            debug=debug,
            env_posture=posture,
        )
        if cfp is None:
            log("cloudflared 不可用（二进制缺失/启动失败）—— 不再重试")
            return "", None
        url = _wait_tunnel_url(
            cf_log, cfp, log, timeout=wait_timeout, attempt=(i, total)
        )
        alive = cfp.poll() is None
        if url and alive:
            flag = protocol or "auto"
            log(f"cloudflared 第 {i}/{total} 次就绪（{posture} + {flag}）")
            return url, cfp
        elapsed = time.time() - t0
        rc = cfp.poll()
        if rc is None:
            log(f"cloudflared 第 {i}/{total} 次未就绪（进程仍在连接，{elapsed:.0f}s）")
        else:
            log(
                f"cloudflared 第 {i}/{total} 次未就绪"
                f"（进程已退出 rc={rc}，存活 {elapsed:.0f}s）"
            )
        if url:
            log(f"  日志里有 URL 但进程已退出：{url}（不采用）")
        log(f"  死因: {_dead_reason(cf_log)}")
        _dump_tail(cf_log, log, 6)
        _terminate(cfp)
        if i < total:
            time.sleep(CLOUDFLARED_RETRY_SEC * i)
    return "", None


def _open_cloudflared_once(
    *,
    current_url: str,
    cfp: Any,
    ensure: Callable[[], tuple[str, Any]],
) -> tuple[str, Any, bool]:
    """已有**存活** cf 进程则复用；否则跑一轮。返回 `(url, cfp, started)`。

    ★ 判据是 `cfp` 而不是 `url`：422 回落时 `current_url` 是**非空的 tailscale URL**，
    但此刻必须起 cloudflared（plan/rollout-tunnel-url-retry B1）。
    ★ 死进程对象（`poll()` 非 None）必须当作「没有」：否则静默死后再也建不起新隧道
    （plan/rollout-node-tunnel-death-blindspot §3.4）。
    """
    if cfp is not None and cfp.poll() is None:
        return current_url, cfp, False
    url, new_cfp = ensure()
    if url:
        return url, new_cfp, True
    return current_url, None, False


def _open_tailscale(
    cfg: dict, ts_authkey: str, port: int, log: Callable[[str], None]
) -> tuple[str, bool]:
    """tailscale 引导；成功返回 `(url, True)`，失败返回 `("", False)`（不抛）。"""
    ts_cfg = {
        "ts_authkey": ts_authkey,
        "ts_ephemeral": bool(cfg.get("ts_ephemeral", True)),
        "proxy_env": bool(cfg.get("ts_proxy_env", True)),
        "engine": str(cfg.get("ts_engine") or ""),
        "no_proxy_extra": str(cfg.get("ts_no_proxy_extra") or ""),
        "ts_tarball": str(cfg.get("ts_tarball") or ""),
        "ts_prefix": str(cfg.get("ts_prefix") or ""),
    }
    try:
        info = tailscale_boot.ensure(ts_cfg, log)
        url = f"http://{info['ip']}:{port}"
        mode = str(info.get("mode") or "")
        log(f"tailscale 就绪: url={url} mode={mode}")
        if mode.startswith("userspace"):
            log(
                "⚠ userspace 模式：入站到本地端口不通（注册会被 hub ping 门拒 422）——"
                "注册失败会自动回落 cloudflared"
            )
        return url, True
    except Exception as e:
        log(f"tailscale 引导失败（{type(e).__name__}: {e}）")
        return "", False


def _agent_entry(code_dir: Path) -> Path | None:
    for rel in ("tools/agent/sampler-agent.ts", "tools/dist/sampler-agent.ts"):
        p = code_dir / rel
        if p.is_file():
            return p
    return None


def _start_agent(
    bun: str, entry: Path, code_dir: Path, port: int, workers: int, cfg: dict, agent_log: Path
) -> Any:
    cmd = [
        bun,
        str(entry),
        "--port",
        str(port),
        "--workers",
        str(workers),
        "--cache-mb",
        str(int(cfg.get("cache_mb") or 2048)),
    ] + [str(a) for a in (cfg.get("agent_extra") or [])]
    env = dict(os.environ)
    env[AGENT_SUPERVISED_ENV] = "1"
    # `Popen` 在启动时继承/复制句柄，父进程随后关掉自己那一份：长跑的 agent 不该
    # 让 supervisor 每起一次就泄漏一个 fd。
    with open(agent_log, "a", encoding="utf-8", errors="replace") as fh:
        return subprocess.Popen(
            cmd, cwd=str(code_dir), stdout=fh, stderr=subprocess.STDOUT, env=env
        )


def _wait_auth(agent: Any, auth_path: Path, timeout: float = 90.0) -> str:
    t = time.time()
    while time.time() - t < timeout:
        try:
            v = auth_path.read_text(encoding="utf-8").strip()
        except OSError:
            v = ""
        if v:
            return v
        if agent.poll() is not None:
            return ""
        time.sleep(2)
    return ""


def _ping(port: int, auth: str = "", timeout: float = 5.0, urlopen: RegisterFn | None = None) -> dict | None:
    """本地探活。`/v1/ping` **要鉴权**（`sampler-agent.ts`，只有 `/pool` 公开）。"""
    opener = urlopen or urllib.request.urlopen
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/ping")
        if auth:
            req.add_header("Authorization", f"Bearer {auth}")
        with opener(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except _NODE_PROBE_ERRORS:
        return None


def _status(
    port: int, auth: str = "", timeout: float = 5.0, urlopen: RegisterFn | None = None
) -> dict | None:
    """读本地 agent 的 `/v1/status`（同 `/v1/ping` 要鉴权）。**绝不抛**：拿不到就 None。

    为什么与 `_ping` 分开：探活是监护的关键路径（它决定「agent 判定死亡/重启」），
    而摘要是**可丢**的观测——两者共一个函数会让「摘要报错」被当成「节点不健康」。
    """
    opener = urlopen or urllib.request.urlopen
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/status")
        if auth:
            req.add_header("Authorization", f"Bearer {auth}")
        with opener(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
            return data if isinstance(data, dict) else None
    except _NODE_PROBE_ERRORS:
        return None


def emit_output_summary(port: int, auth: str, log: Callable[[str], None]) -> None:
    """打一行「近 30 分钟产出」（监护循环每 10 分钟调一次）。**绝不抛**。

    读 `/v1/status` 失败 ⇒ `output_line(None)` 那一档「不可知」：可丢的观测不该让监护循环炸，
    也不该被读成「节点不健康」（判活是 `_ping` 的职责）。
    """
    log(output_line(_status(port, auth)))


def output_line(status: dict | None) -> str:
    """把 `/v1/status` 的产出摘要拼成**一行**（空/旧 agent 如实说不可知）。

    分母、窗口、分列全在 agent 侧算好（`outputLine`）——这里不重算任何数字：
    notebook 日志与 `sampler-agent.log` 必须逐字相同，否则两个日志对账时先要怀疑口径。
    """
    if not isinstance(status, dict):
        return "近30min产出 不可知（agent 未应答）"
    line = status.get("outputLine")
    if isinstance(line, str) and line.strip():
        return f"近30min产出 {line.strip()}"
    # 旧 agent（没随本仓同步升级）不能编一个「窗口」出来：累计数冒充 30 分钟窗口是静默错数。
    return "近30min产出 不可知（旧 agent 无 outputLine）"


def _try_register_once(
    *,
    url: str,
    auth: str,
    node_id: str,
    workers: int,
    label: str,
    hub_url: str,
    hub_token: str,
    log: Callable[[str], None],
    urlopen: RegisterFn | None = None,
) -> dict:
    """注册一次；空 URL 是第一行硬闸（零 HTTP，也不写 `reg_state`）。

    `kind="nourl"` 是**本地**失败，调用方据此走「等隧道」而不是注册退避。
    """
    if not url:
        return {
            "ok": False,
            "status": 0,
            "action": None,
            "body": {},
            "capabilities": [],
            "error": "no tunnel url",
            "kind": "nourl",
        }
    payload = build_register_payload(node_id, url, auth, workers, label)
    return register(urlopen or urllib.request.urlopen, hub_url, hub_token, payload, log)


def _banner_lines(
    *,
    node_id: str,
    url: str,
    auth: str,
    workers: int,
    bun_version: str,
    code_hash: str,
    hub_configured: bool,
    reg_state: dict[str, str],
) -> list[str]:
    """启动横幅的纯函数：空 URL 时**不**打印粘进去必 400 的 `nodes[]` 片段。"""
    lines = ["=" * 68]
    if hub_configured and reg_state.get("url"):
        lines.append(f"★ 已自动注册 : id={node_id}（rl-config.nodes[] 每轮热读，已生效）")
    elif url:
        lines.append("★ 未注册 —— 如需手工：写入本机 nn-training/rl-config.json：")
        lines.append(
            f'  {{"nodes":[{{"id":"{node_id}","url":"{url}","authKey":"{auth}"}}]}}'
        )
    else:
        lines.append(
            "★ 未注册：隧道 URL 未取到 —— 不要手工登记（空 url 会被 hub 400 拒）。"
        )
        lines.append("   节点每 60s 重试建隧道；真因看上面的 cloudflared 日志尾部。")
    lines.append(f"★ URL     : {url or '(未取到)'}")
    lines.append(f"★ authKey : {auth or '(未生成)'}")
    lines.append(
        f"  workers : {workers}  bun {bun_version}  codeHash={code_hash[:12]}"
    )
    lines.append("=" * 68)
    return lines


# ---------------------------------------------------------------- 主流程（cell 调用）

class _Proc(Protocol):
    """子进程句柄的最小面（`Popen` 与测试替身都满足）。"""

    returncode: int | None

    def poll(self) -> int | None: ...

    def terminate(self) -> None: ...


@dataclass
class _Tunnel:
    """隧道状态：tailscale 只给初始 URL，cloudflared 的重建 / 补拿都走这里。"""

    cfg: dict[str, object]
    log: Callable[[str], None]
    port: int
    cf_log: Path
    link: str
    url: str = ""
    cfp: _Proc | None = None
    probe_postures: tuple[str, str] | None = None

    def postures(self) -> tuple[str, str]:
        if self.probe_postures is None:
            self.probe_postures = _net_probe(self.log)
        return self.probe_postures

    def open_cloudflared(
        self,
        reason: str,
        *,
        attempts: int = CLOUDFLARED_ATTEMPTS,
        wait_timeout: float = 90.0,
    ) -> bool:
        """起 / 复用 cloudflared；`reason` 只进日志，返回「本次是否新起」。"""
        self.log(f"cloudflared 通道（{reason}）")
        self.url, self.cfp, started = _open_cloudflared_once(
            current_url=self.url,
            cfp=self.cfp,
            ensure=lambda: _ensure_cloudflared_url(
                self.cfg,
                self.port,
                self.cf_log,
                self.log,
                attempts=attempts,
                postures=self.postures(),
                wait_timeout=wait_timeout,
            ),
        )
        if started:
            self.link = LINK_CLOUDFLARED
        return started


@dataclass
class _Session:
    """采样节点会话：注册 / 重启 / 隧道自愈（`run` 只做一次装配与循环编排）。"""

    tunnel: _Tunnel
    env: str
    cfg: dict[str, object]
    log: Callable[[str], None]
    port: int
    workers: int
    code_dir: Path
    bun: str
    entry: Path
    agent: _Proc
    auth: str
    agent_log: Path
    auth_path: Path
    node_id: str
    hub_url: str
    hub_token: str
    hub_configured: bool
    reg_state: dict[str, str]
    backoff: float
    next_retry: float
    next_tunnel_try: float
    next_tunnel_probe: float
    tunnel_probe_fails: int = 0

    def _try_register(self) -> dict:
        t = self.tunnel
        out = _try_register_once(
            url=t.url,
            auth=self.auth,
            node_id=self.node_id,
            workers=self.workers,
            label=worker_name(self.env, "t" if t.link == LINK_TAILSCALE else "c"),
            hub_url=self.hub_url,
            hub_token=self.hub_token,
            log=self.log,
            urlopen=urllib.request.urlopen,
        )
        if out["ok"]:
            self.reg_state.update({"url": t.url, "authKey": self.auth})
            self.log(f"已注册节点 id={self.node_id} url={t.url} action={out['action']}")
        return out

    def register_first(self) -> None:
        """首拍注册；422（tailscale 入站不可达）⇒ 回落 cloudflared 并重注册。"""
        t = self.tunnel
        out = self._try_register()
        if not out["ok"] and int(out.get("status") or 0) == 422 and t.link == LINK_TAILSCALE:
            self.log("hub ping 门 422：tailscale 入站不可达 —— 回落 cloudflared 并重注册")
            if not t.open_cloudflared("ping 门 422"):
                # B1：cf 全败时**不能**拿旧 tailscale URL 再发一次必然 422 的注册。
                self.hub_configured = False
                t.url = ""
                self.log(
                    "两种通道都不可用（tailscale 入站不通 + cloudflared 未建起）"
                    "—— 本会话不再自动注册"
                )
            else:
                out = self._try_register()
                if not out["ok"] and int(out.get("status") or 0) == 422:
                    self.hub_configured = False
                    self.log(
                        "两种通道都不可用（tailscale 入站不通 + cloudflared 未建起）"
                        "—— 本会话不再自动注册"
                    )
        if not out["ok"] and self.hub_configured:
            if out["kind"] == "auth":
                self.hub_configured = False
                self.log("HUB_TOKEN 被拒（auth）——注册放弃：token 不对，重试无意义（检查 secret 后重跑）")
            elif out["kind"] == "nourl":
                self.next_tunnel_try = time.time() + TUNNEL_RETRY_SEC
                self.log("首拍未注册：隧道 URL 未取到 —— 等待补拿（不进入注册退避）")
            else:
                self.backoff = RETRY_FIRST_SEC
                self.next_retry = time.time() + self.backoff
                self.log(f"首拍注册失败（{out['kind']}）——{int(self.backoff)}s 后重试")

    def restart_agent(self) -> None:
        """受监护重启：清旧进程、从盘上拉起、等 authKey（变了就让外层重注册）。"""
        _terminate(self.agent)
        self.agent = _start_agent(
            self.bun, self.entry, self.code_dir, self.port, self.workers, self.cfg, self.agent_log
        )
        auth2 = _wait_auth(self.agent, self.auth_path, 30)
        if auth2 and auth2 != self.auth:
            self.auth = auth2
            self.next_retry = 0.0
            self.log("★ authKey 变了 —— 将重注册")

    def check_tunnel(self, now: float) -> None:
        """隧道死亡检测 / 补拿 / 换名（每拍一次；判死后归一成「无隧道」再重建）。"""
        t = self.tunnel
        # 隧道死亡检测（plan/rollout-node-tunnel-death-blindspot）：静默死的隧道 URL 不变、
        # 日志尾部仍是旧域名，既有「补拿/换名」两道闸都看不见。判死后归一成「无隧道」
        # （cfp=None + url=""），复用补拿闸重建、复用 should_register 语义强制重注册。
        agent_ok = self.agent.poll() is None
        if not agent_ok:
            self.tunnel_probe_fails = 0  # 本机不健康时连败数不作数（问题不在隧道）
        if (
            t.link == LINK_CLOUDFLARED
            and agent_ok
            and t.url
            and t.cfp is not None
            and t.cfp.poll() is None
            and now >= self.next_tunnel_probe
        ):
            probe_ok = _public_ping(t.url, self.auth) is not None
            self.tunnel_probe_fails = 0 if probe_ok else self.tunnel_probe_fails + 1
            self.next_tunnel_probe = now + TUNNEL_PROBE_SEC
        if _tunnel_dead(
            is_cloudflared=t.link == LINK_CLOUDFLARED,
            url=t.url,
            cfp=t.cfp,
            agent_ok=agent_ok,
            probe_fails=self.tunnel_probe_fails,
        ):
            why = (
                "cloudflared 进程已退出"
                if t.cfp is None or t.cfp.poll() is not None
                else f"公网探针连续 {self.tunnel_probe_fails} 次失败"
            )
            self.log(f"★ 隧道判定死亡（{why}）—— 重建 cloudflared 并重注册")
            _terminate(t.cfp)
            t.cfp = None
            t.url = ""
            # 绕过 should_register 的去重（重建后即便拿到同名 URL 也要重注册）。
            # 副作用：重新注册成功前，收线时不会发 unregister（hub 侧由 ping 门兜底）。
            self.reg_state["url"] = ""
            self.tunnel_probe_fails = 0
            self.next_tunnel_try = now
        if _tunnel_retry_due(t.url, now, self.next_tunnel_try):
            self.log("隧道补拿：尝试重新建 cloudflared…")
            t.url, t.cfp, started = _open_cloudflared_once(
                current_url=t.url,
                cfp=t.cfp,
                ensure=lambda: _ensure_cloudflared_url(
                    self.cfg,
                    self.port,
                    t.cf_log,
                    self.log,
                    attempts=1,
                    postures=t.postures(),
                    wait_timeout=TUNNEL_RETRY_WAIT_SEC,
                ),
            )
            if started:
                t.link = LINK_CLOUDFLARED
                self.log(f"★ 隧道就绪（补拿）：{t.url} —— 将重注册")
                self.next_retry = 0.0
                self.tunnel_probe_fails = 0
                self.next_tunnel_probe = now + TUNNEL_PROBE_SEC
            self.next_tunnel_try = now + TUNNEL_RETRY_SEC
        if t.url and t.cfp is not None:
            cur = _tunnel_url(t.cf_log)
            if cur and cur != t.url:
                t.url = cur
                self.next_retry = 0.0
                self.tunnel_probe_fails = 0
                self.next_tunnel_probe = now + TUNNEL_PROBE_SEC
                self.log(f"★ 隧道换名（quick tunnel 重连）：{t.url} —— 将重注册")

    def maybe_register(self, now: float) -> None:
        """URL / authKey 变了或退避到点 ⇒ 重注册（失败按指数退避）。"""
        t = self.tunnel
        if (
            self.hub_configured
            and t.url
            and should_register(self.reg_state, t.url, self.auth)
            and now >= self.next_retry
        ):
            out = self._try_register()
            if out["ok"]:
                self.backoff, self.next_retry = 0.0, 0.0
            elif out["kind"] == "auth":
                self.hub_configured = False
                self.log("HUB_TOKEN 被拒（auth）——注册放弃")
            else:
                self.backoff = RETRY_FIRST_SEC if self.backoff <= 0 else min(RETRY_MAX_SEC, self.backoff * 2)
                self.next_retry = now + self.backoff
                self.log(f"重注册失败（{out['kind']}）——{int(self.backoff)}s 后重试")


def run(cfg: dict, log: Callable[[str], None], secret: Callable[[str, Any], Any]) -> int:
    """采样节点主流程。返回进程退出码（0 = 正常收线）。

    cell 契约：CFG / `_secret` / 保活线程 / clone / `sys.path` 都已就绪；本函数**不再碰
    clone**（它需要 GITHUB_TOKEN，住 cell 的 clone 段）。
    """
    cfg = cfg if isinstance(cfg, dict) else {}
    code_dir = Path(str(cfg.get("code_dir") or "")).expanduser()
    if not code_dir.is_dir():
        log(f"FATAL: code_dir 不存在: {code_dir}")
        return 2

    # ── 1. 凭据（★ ensure 之前读完；值永不进日志）────────────────────────
    ts_authkey = str(secret("TS_AUTHKEY", cfg.get("ts_authkey")) or "").strip()
    hub_ip = str(secret("HUB_IP", cfg.get("hub_ip")) or "").strip()
    hub_token = str(secret("HUB_TOKEN", cfg.get("hub_token")) or "").strip()
    hub_port = int(cfg.get("hub_port") or 0)
    cfg_hub_url = str(cfg.get("hub_url") or "").strip()

    env = probe_cloud_env()
    has_ts = bool(ts_authkey)
    link = resolve_link(env, str(cfg.get("link") or "auto"), has_ts)
    port = int(cfg.get("agent_port") or 8443)
    workers, slot_note = _slot_count(int(cfg.get("workers") or 0))
    node_id = str(cfg.get("node_id") or "").strip() or f"rollout-{env}"
    hub_url = registry_url(hub_ip, hub_port, cfg_hub_url) if (hub_ip or cfg_hub_url) else ""
    hub_configured = bool(hub_url and hub_token)
    log(
        f"环境={env} 通道={link} workers={workers}（{slot_note}）node_id={node_id} "
        f"hub={'已配置' if hub_configured else '未配置（降级为手工登记）'}"
    )

    # ── 2. bun（公网安装在 ensure 之前）───────────────────────────────────
    bun = ensure_bun(code_dir, cfg, log)
    if not bun:
        log("FATAL: bun 不可用（也装不上）")
        return 2

    # ── 3. 通道：tailscale 优先；失败回落 cloudflared ─────────────────────
    cf_log = Path("/tmp/rollout-cloudflared.log")
    forced_cf = str(cfg.get("link") or "").strip().lower() == LINK_CLOUDFLARED
    tunnel = _Tunnel(cfg=cfg, log=log, port=port, cf_log=cf_log, link=link)

    if tunnel.link == LINK_TAILSCALE:
        tunnel.url, ok = _open_tailscale(cfg, ts_authkey, port, log)
        if not ok:
            log("—— 回落 cloudflared")
            tunnel.link = LINK_CLOUDFLARED
    if tunnel.link != LINK_TAILSCALE:
        tunnel.open_cloudflared("通道决策=cloudflared")
    if not tunnel.url and not forced_cf and has_ts:
        log("cloudflared 未就绪 —— 回落 tailscale")
        tunnel.url, ok = _open_tailscale(cfg, ts_authkey, port, log)
        if ok:
            tunnel.link = LINK_TAILSCALE
    elif not tunnel.url and forced_cf:
        log("强制通道=cloudflared，跳过 tailscale 回落")
    elif not tunnel.url and not has_ts:
        log("无 ts_authkey ⇒ 本会话无法回落 tailscale")
    next_tunnel_try = time.time() + TUNNEL_RETRY_SEC if not tunnel.url else 0.0

    # ── 4. sampler-agent ─────────────────────────────────────────────────
    entry = _agent_entry(code_dir)
    if entry is None:
        log(f"FATAL: 找不到 sampler-agent.ts（{code_dir / 'tools'}）")
        return 2
    agent_log = Path("/tmp/rollout-agent.log")
    auth_path = entry.parent / "agent.auth"
    agent = _start_agent(bun, entry, code_dir, port, workers, cfg, agent_log)
    auth = _wait_auth(agent, auth_path)
    ping = _ping(port, auth)
    if ping is None:
        log("agent 30–60s 未就绪 —— 下面是 agent 日志尾部（真因通常在这）：")
        _dump_tail(agent_log, log)

    session = _Session(
        tunnel=tunnel,
        env=env,
        cfg=cfg,
        log=log,
        port=port,
        workers=workers,
        code_dir=code_dir,
        bun=bun,
        entry=entry,
        agent=agent,
        auth=auth,
        agent_log=agent_log,
        auth_path=auth_path,
        node_id=node_id,
        hub_url=hub_url,
        hub_token=hub_token,
        hub_configured=hub_configured,
        reg_state={"url": "", "authKey": ""},
        backoff=0.0,
        next_retry=0.0,
        next_tunnel_try=next_tunnel_try,
        next_tunnel_probe=time.time() + TUNNEL_PROBE_SEC,
    )

    # ── 5. 注册（首拍；422 ⇒ tailscale 入站不可达 ⇒ 回落重注册）───────────
    if hub_configured:
        session.register_first()

    # ── 6. 横幅（注册成功 = 自动；否则保留手工路径）───────────────────────
    for line in _banner_lines(
        node_id=node_id,
        url=tunnel.url,
        auth=session.auth,
        workers=workers,
        bun_version=_proc_bun_version(bun, fallback=""),
        code_hash=str((ping or {}).get("codeHash", "")),
        hub_configured=session.hub_configured,
        reg_state=session.reg_state,
    ):
        log(line)

    # ── 7. 监护：agent 死重启 / 隧道换名重注册 / 会话到点收线 ─────────────
    deadline = time.time() + max(0.0, float(cfg.get("max_session_hours") or 0)) * 3600
    restart_limit = int(cfg.get("restart_limit") or 5)
    restarts, tick, miss = 0, 0, 0
    # 「近 30 分钟产出」摘要：按**墙钟**而不是 tick 计数（sleep 漂移不会把 10 分钟变成 11 分钟）
    next_output_at = time.time() + OUTPUT_STATUS_EVERY_SEC
    try:
        while True:
            if time.time() > deadline:
                log("会话上限到点 —— 收线")
                break
            dead, why = False, ""
            if session.agent.poll() is not None:
                dead, why = True, f"rc={session.agent.returncode}"
            else:
                tick += 1
                if tick % 6 == 0:
                    if _ping(port, session.auth) is None:
                        miss += 1
                        if miss >= 3:
                            dead, why = True, "HTTP 探活连续 3 次失败"
                    else:
                        miss = 0
            if dead:
                planned = session.agent.returncode == AGENT_RESTART_EXIT_CODE
                if planned:
                    # 远控 /v1/restart：agent 已 pull 完并按受监护模式退出。重新拉起即加载新代码；
                    # 这是计划内重启，不能吃崩溃预算（否则几次正常升级就把节点停了）。
                    log("agent 按远控重启请求退出（受监护）—— 重新拉起加载新代码")
                else:
                    if restarts >= restart_limit:
                        log(f"agent 已重启 {restarts} 次仍不健康 —— 停止（看 {agent_log}）")
                        break
                    restarts += 1
                    log(f"agent 判定死亡（{why}）—— 第 {restarts} 次重启")
                session.restart_agent()
                miss = 0
            now = time.time()
            session.check_tunnel(now)
            session.maybe_register(now)
            # 产出摘要（每 10 分钟一行）：先于 sleep 判定，保证第一行不会因重启/重注册被推迟。
            # 取不到就如实说不可知（不等于「没产出」）——观测面坏不得让监护循环抛异常。
            if time.time() >= next_output_at:
                emit_output_summary(port, session.auth, log)
                next_output_at = time.time() + OUTPUT_STATUS_EVERY_SEC
            time.sleep(10)
    except KeyboardInterrupt:
        log("收到中断")
    finally:
        agent = session.agent
        cfp = tunnel.cfp
        if session.hub_configured and session.reg_state["url"]:
            out = unregister(urllib.request.urlopen, hub_url, hub_token, node_id, log)
            log(f"unregister id={node_id}: {'ok' if out['ok'] else out['error']}")
        _terminate(agent)
        _terminate(cfp)
    log("采样节点结束")
    return 0
