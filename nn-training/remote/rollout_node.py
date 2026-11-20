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
import subprocess
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from common import net_http, node_report
from common.env_probe import probe_cloud_env, worker_name
from common.errors import NET_ERRORS
from common.proc import bun_version as _proc_bun_version
from remote import tailscale_boot
from remote.node_toolchain import ensure_bun as _ensure_bun
from remote.node_toolchain import ensure_cloudflared

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
from remote.rollout_relay import RelayChannel
from remote.rollout_tunnel import CLOUDFLARED_PROBE_TIMEOUT as CLOUDFLARED_PROBE_TIMEOUT
from remote.rollout_tunnel import PROBE_SEC as TUNNEL_PROBE_SEC
from remote.rollout_tunnel import dead_reason as _dead_reason

# 预检/姿态工具住 `remote/rollout_tunnel.py`（2026-11-18 下沉：它们与「隧道健康」同一个所有者，
# 也把本模块压回 1000 行以内）。`X as X` = 显式 re-export —— `tests/remote/test_rollout_node.py`
# 的 `rn._net_probe(...)` / `rn._direct_env()` 用法逐字不变（两个探针本身只在新模块内部互调）。
from remote.rollout_tunnel import direct_env as _direct_env
from remote.rollout_tunnel import dump_tail as _dump_tail
from remote.rollout_tunnel import net_probe as _net_probe
from remote.rollout_tunnel import public_ping as _public_ping
from remote.rollout_tunnel import reset_log as _reset_cf_log
from remote.rollout_tunnel import retry_due as _tunnel_retry_due
from remote.rollout_tunnel import tunnel_dead as _tunnel_dead
from remote.rollout_tunnel import wait_url as _wait_tunnel_url

#: 通道名（与 hub/nodes 注册载荷里的 url 形态对应）。
LINK_TAILSCALE = "tailscale"
LINK_CLOUDFLARED = "cloudflared"
#: 反向会合（2026-11-18）：节点长轮询 hub，**不需要任何入站端口**。用于「建不出 quick
#: tunnel」的机器（ModelScope/阿里云 buys 7844 与 api.trycloudflare.com 一起封掉的那种；
#: 真机现场见 `remote/rollout_relay.py` 头部）。`auto` 下它是 cloudflared 失败后的回落档。
LINK_RELAY = "relay"

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
    """通道决策：`cfg_link` 可强压；否则 Colab 优先 tailscale、其余看有没有 TS_AUTHKEY。

    `relay` 也可强压（`CFG.link = "relay"`）；留 `auto` 时它是**运行期回落档**（cloudflared
    建不起来才启用）——所以这里不预选它：预选会把「隧道能用的机器」也降级成 polling。
    """
    forced = str(cfg_link or "").strip().lower()
    if forced in (LINK_TAILSCALE, LINK_CLOUDFLARED, LINK_RELAY):
        return forced
    if str(env or "").strip().lower() == "colab":
        return LINK_TAILSCALE
    return LINK_TAILSCALE if has_ts_authkey else LINK_CLOUDFLARED


def registry_url(hub_ip: str, hub_port: int, cfg_hub_url: str = "") -> str:
    """hub 注册地址 —— **直接转调** `tailscale_boot.resolve_hub_url`（单一实现，F13）。

    口径与 HUB_IP 取值链一致：裸 IP/主机名补 `http://` + 端口；整条 URL 原样；含 `<` 视为未填。
    """
    return tailscale_boot.resolve_hub_url(cfg_hub_url=cfg_hub_url, hub_ip=hub_ip, hub_port=hub_port)


# ---------------------------------------------------------------- 进程/网络小工具



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
    """并发槽位：显式值 > `min(有效核, 物理核) × 2`（2026-11-14 P3）。

    与训练侧 `cpu_worker_slots()`（留 2 核给数据回传）分家：采样节点只跑采样/评估，
    满核才够用（8 物理核机器 ⇒ 16 个 worker）。

    ★ 2026-11-11：用**物理核**（不是 `effective_cores()` 的逻辑核）——同一个节点两套口径就会
      出现「笔记本按 4 开池、agent 自报 2 核」的超额认购（kaggle 实测，父进程 HTTP
      被饿到 30~60s）。HT sibling 只共享执行单元与 L1/L2，每个 worker 是 ~75–100MB 的重型进程。
    ★ 2026-11-14（P3）：在物理核基础上 ×2（IO 重叠）——槽位实际只被服务段占住（`svc 2.16s /
      wall 9.33s`），另一局可以在装载/打包的空档里跑。这确实是 §2026-11-11 事故的同型配置，
      所以护栏从「默认值」改成「观测 + 显式回退」：`workers 自检` / `★ workers 超额认购` 行 +
      `CFG.workers = 物理核数` 即回到旧口径。
    """
    cores = os.cpu_count() or 1
    try:
        from common.platform_utils import effective_cores, physical_cores

        cores = min(effective_cores(), physical_cores())
        note = f"platform_utils 物理核={cores}"
    except Exception as e:  # 拉不起 platform_utils 时的兜底：同样用满
        note = f"内联回退({type(e).__name__}) cores={cores}"
    if int(explicit or 0) > 0:
        return int(explicit), note + "（CFG.workers 显式）"
    # auto = 物理核 ×2（IO 重叠因子）：单局 `svc 2.16s / wall 9.33s`，槽位只被服务段占住，
    # 多一倍的池槽能把装载/打包的空档叠起来。**回退口径：CFG.workers = 物理核数**（§5 R3，
    # §2026-11-11 同型事故靠 `workers 自检` 行最早发现；这里不设默认护栏）。
    return int(cores) * 2, note + f" ×2（auto；回退填 CFG.workers={cores}）"


def ensure_bun(
    cfg: dict | None = None,
    log: Callable[[str], None] | None = None,
    *,
    code_dir: str | Path | None = None,
) -> str:
    """**别的盘**复用这一份装机 + 持久缓存（DECISIONS §2026-11-18-goalnn-online-worker-bun-for-replay）。

    2026-11-18 用户口径：「两块在线盘环境启动后先装 bun，以执行 replay 到 npy 的转换」，
    并「像 rollout 一样定义持久化位置，避免每次新会话都下载」。⇒ 它们**不另写一份安装逻辑**
    （DECISIONS §2026-10-21-goalnn-online-worker-bun-for-self-collect 约束①），而是调本函数：
    同一套候选/PATH 探测、同一套镜像与退避、同一份持久缓存（`bin_cache_dir` ⇒
    `<持久根>/.battle-bin/bun`，跨会话命中即跳过下载）。

    返回 bun 路径；也装不上 ⇒ `""`（调用方**响亮记一行**后继续跑：replay 转换会回落 node，
    `remote/replay_corpus.resolve_js_runtime` 两者皆无才失败）。
    `code_dir` 只给 `cfg.bun_install_deps` 的 `bun install` 用（缺省 `/tmp`；该开关缺省关）。
    """
    return _ensure_bun(Path(code_dir or "/tmp"), dict(cfg or {}), log or (lambda _m: None))


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
    # ★ origin 用 127.0.0.1 字面量，不用 `localhost`：cloudflared 是 Go 二进制，`localhost`
    #   在本机解析出 `::1` 后只拨 IPv6 回环，而 agent（`Bun.serve` 缺省）与 hub 都只绑 IPv4
    #   ⇒ 每条经隧道的请求都吃 `dial tcp [::1]:<port>: connectex: refused`，CF 回 502
    #   （2026-11-19 现场：公网 10 发 8 个 502）。
    cmd = [cf, "tunnel", "--url", f"http://127.0.0.1:{port}", "--no-autoupdate"]
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
        url = _wait_tunnel_url(cf_log, cfp, log, timeout=wait_timeout, attempt=(i, total))
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
            log(f"cloudflared 第 {i}/{total} 次未就绪（进程已退出 rc={rc}，存活 {elapsed:.0f}s）")
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
        # 磁盘阈值（P3）：agent 侧回显的 `diskFloorMB/diskWarnMB` 必须是**生效值**，
        # 否则控制台看 2048、真正拒收用的却是另一个数。
        *node_report.agent_disk_args(cfg),
    ] + [str(a) for a in (cfg.get("agent_extra") or [])]
    env = dict(os.environ)
    env[AGENT_SUPERVISED_ENV] = "1"
    # work_dir 与 code_dir 解耦（2026-11-20）：agent 侧 WORK_DIR 只读 SAMPLER_WORK_DIR，
    # 缺省 = <repo>/tmp/dist-agent（跟着 code_dir 走）。CFG 显式填了就透传给子进程，
    # 这样 code_dir 可以留在网络盘做跨对话持久（代码/凭据），work_dir 单独指内存盘提速。
    # 空/auto = 不动，零行为变化；不做 mkdir（agent 的 sweepWorkdirAtBoot 自己建）。
    work_dir = str(cfg.get("work_dir") or "").strip()
    if work_dir and work_dir.lower() != "auto":
        env["SAMPLER_WORK_DIR"] = work_dir
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


def _ping(
    port: int, auth: str = "", timeout: float = 5.0, urlopen: RegisterFn | None = None
) -> dict | None:
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
    """打两行：近 30 分钟产出 + 磁盘水位（监护循环每 10 分钟调一次）。**绝不抛**。

    **只取一次** `/v1/status`（零额外请求）；两行读同一份快照，日志里是同一时刻的事。
    读失败 ⇒ 两行各自走「不可知」档：可丢的观测不该让监护循环炸，也不该被读成
    「节点不健康」（判活是 `_ping` 的职责）。
    """
    status = _status(port, auth)
    log(node_report.output_line(status))
    log(node_report.disk_line(status))


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
    channel: str = "",
    hub_url: str = "",
) -> list[str]:
    """启动横幅的纯函数：空 URL 时**不**打印粘进去必 400 的 `nodes[]` 片段。

    `channel` 决定「没有 URL」该怪谁：relay 模式根本没有 cloudflared 进程，
    还去指「看 cloudflared 日志尾部」是把人往错方向送（2026-11-18 DSW 现场）。
    """
    lines = ["=" * 68]
    if hub_configured and reg_state.get("url"):
        lines.append(f"★ 已自动注册 : id={node_id}（rl-config.nodes[] 每轮热读，已生效）")
    elif url:
        lines.append("★ 未注册 —— 如需手工：写入本机 nn-training/rl-config.json：")
        lines.append(f'  {{"nodes":[{{"id":"{node_id}","url":"{url}","authKey":"{auth}"}}]}}')
    elif channel == LINK_RELAY:
        lines.append("★ 未注册：relay 通道没拿到 hub 的本机端口 —— 真因看上面「relay 预检」那行。")
        lines.append(
            f"   relay 只需节点**出网到 hub**（当前 hub_url={hub_url or '未填'}），不建隧道、不要入站端口。"
        )
    else:
        lines.append("★ 未注册：隧道 URL 未取到 —— 不要手工登记（空 url 会被 hub 400 拒）。")
        lines.append("   节点每 60s 重试建隧道；真因看上面的 cloudflared 日志尾部。")
    lines.append(f"★ URL     : {url or '(未取到)'}")
    lines.append(f"★ authKey : {auth or '(未生成)'}")
    lines.append(f"  workers : {workers}  bun {bun_version}  codeHash={code_hash[:12]}")
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
    #: relay 通道的所有者（hub 未配置 ⇒ None）。生命周期归这里，`_Session` 只问
    #: 「起得来吗 / 还活着吗」——线程状态留两份写者就是下一个「重建后忘清 URL」。
    relay: RelayChannel | None = None

    def postures(self) -> tuple[str, str]:
        if self.probe_postures is None:
            self.probe_postures = _net_probe(self.log)
        return self.probe_postures

    def open_relay(self, reason: str) -> bool:
        """起「节点长轮询 hub」通道（无需入站/隧道）。返回是否就绪。

        `local_port` 由 hub 的长轮询应答告知（节点只知道 hub 的**公网**地址，而 trainer
        要打的是**本机回环**）。拿不到 ⇒ 本档放弃、URL 留空 —— 不拿错端口硬填。
        细节（起线程/等端口/失败清理）全在 `RelayChannel`，这里只管 link/url 两个字段。
        """
        if self.relay is None:
            self.log(
                f"relay 通道候选（{reason}）—— hub 未配置（缺 HUB_IP/hub_url 或 HUB_TOKEN），跳过"
            )
            return False
        self.url = self.relay.supervise(reason)
        if not self.url:
            return False
        self.link = LINK_RELAY
        return True

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
            label=worker_name(
                self.env,
                "t" if t.link == LINK_TAILSCALE else ("r" if t.link == LINK_RELAY else "c"),
            ),
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
        # relay 通道的死亡证据 = 轮询线程全停（没有进程/域名可看）。归一成「无 URL」后
        # 重建 + 重注册：重建后回环端口可能变 ⇒ 必须清 reg_state 强注册（新地址≠旧地址）。
        if (
            t.link == LINK_RELAY
            and now >= self.next_tunnel_try
            and (t.relay is None or not t.relay.alive())
        ):
            self.log("★ relay 轮询线程已停 —— 重建通道并重注册")
            t.url = t.relay.supervise("relay 线程死亡") if t.relay is not None else ""
            if t.relay is not None:
                # 重建时若又一次换了端口，注册必须跟着走（单一真相）。
                self.hub_url = t.relay.hub_url or self.hub_url
            self.reg_state["url"] = ""
            if t.url:
                self.next_retry = 0.0
            self.next_tunnel_try = now + TUNNEL_RETRY_SEC
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
        if t.link == LINK_CLOUDFLARED and _tunnel_retry_due(t.url, now, self.next_tunnel_try):
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
    # 磁盘阈值（P3）：校验在**起 agent 之前**（响了就停，不静默夹取）。
    (floor_mb, warn_mb), _disk_why = node_report.startup_disk(cfg)
    if _disk_why:
        log(f"FATAL: {_disk_why}")
        return 2
    node_id = str(cfg.get("node_id") or "").strip() or f"rollout-{env}"
    hub_url = registry_url(hub_ip, hub_port, cfg_hub_url) if (hub_ip or cfg_hub_url) else ""
    hub_configured = bool(hub_url and hub_token)
    log(
        f"环境={env} 通道={link} workers={workers}（{slot_note}）"
        f" 磁盘 free 阈值=warn<{warn_mb}MB / 熔断<{floor_mb}MB node_id={node_id} pid={os.getpid()} "
        f"hub={'已配置 ' + (net_http.host_of(hub_url) or '?') if hub_configured else '未配置（降级为手工登记）'}"
    )
    # workdir 落点单独一行：是不是真切到内存盘必须肉眼可验，不能只靠 CFG 字段猜。
    _work_dir = str(cfg.get("work_dir") or "").strip()
    log(
        f"work_dir={_work_dir or '<code_dir>/tmp/dist-agent（默认）'}"
        f"  code_dir={code_dir}"
    )
    # 同一 node_id 只许一个进程在轮询：两个消费者会**抢同一个 hub 队列**，而且日志会混成
    # 「新旧两种格式同时出现」——2026-11-18 DSW 现场就这么误判过一次（旧进程没死）。
    # 用户口径（同日）：**cell 重跑必须先把上一次的进程杀干净再起**，所以这里不是告警而是清场
    # （先 SIGINT 让旧进程走 finally 收掉自己的 bun agent/cloudflared，超时才 SIGKILL）。
    # 实现在 `common/node_report`；pid 复用靠「pid|启动戳」核验，对不上不动手。
    node_report.reclaim_node_id(node_id, log)

    # ── 2. bun（公网安装在 ensure 之前）───────────────────────────────────
    bun = _ensure_bun(code_dir, cfg, log)
    if not bun:
        log("FATAL: bun 不可用（也装不上）")
        return 2

    # ── 3. 通道：tailscale 优先；失败回落 cloudflared / relay ──────────────
    cf_log = Path("/tmp/rollout-cloudflared.log")
    forced_cf = str(cfg.get("link") or "").strip().lower() == LINK_CLOUDFLARED
    # `workers` = agent 的 worker 数（下限 6）：6 条道会把 16 worker 的节点腰斩成 6 并发
    # （2026-11-19 hub 日志实测，见 `POLL_WORKERS` 注释）。
    relay = (
        RelayChannel(
            hub_url=hub_url,
            token=hub_token,
            node_id=node_id,
            agent_port=port,
            log=log,
            workers=workers,
        )
        if hub_configured
        else None
    )
    tunnel = _Tunnel(cfg=cfg, log=log, port=port, cf_log=cf_log, link=link, relay=relay)

    def _follow_relay_port() -> None:
        """注册地址跟着**通道实际用的那个端口**走。

        443 的 TLS 被中间层按 SNI 掐掉时，`probe_hub` 判 `port-blocked` 并当场把通道改到
        `:2083/:2096`；注册若仍打 `CFG.hub_url` 的 `:443` 就恒 handshake 超时（2026-11-19
        DSW 现场「relay 就绪」与「★ 未注册」同屏的真因）。改这一个变量 ⇒ 首注册、240s
        重注册、横幅、注销全部自动跟随（单一真相）。
        """
        nonlocal hub_url
        if tunnel.relay is not None:
            hub_url = tunnel.relay.hub_url or hub_url

    if tunnel.link == LINK_RELAY:
        tunnel.open_relay("通道决策=relay")
        _follow_relay_port()
    if tunnel.link == LINK_TAILSCALE:
        tunnel.url, ok = _open_tailscale(cfg, ts_authkey, port, log)
        if not ok:
            log("—— 回落 cloudflared")
            tunnel.link = LINK_CLOUDFLARED
    if tunnel.link != LINK_TAILSCALE and tunnel.link != LINK_RELAY:
        tunnel.open_cloudflared("通道决策=cloudflared")
    if not tunnel.url and not forced_cf and tunnel.link != LINK_RELAY and has_ts:
        log("cloudflared 未就绪 —— 回落 tailscale")
        tunnel.url, ok = _open_tailscale(cfg, ts_authkey, port, log)
        if ok:
            tunnel.link = LINK_TAILSCALE
    elif not tunnel.url and forced_cf:
        log("强制通道=cloudflared，跳过 tailscale/relay 回落")
    elif not tunnel.url and tunnel.link != LINK_RELAY and not has_ts:
        log("无 ts_authkey ⇒ 本会话无法回落 tailscale")
    # ── relay 回落（`auto` 的最后一档，2026-11-18）────────────────────────
    # 真机（ModelScope）：`api.trycloudflare.com:443` 与边缘 7844 都不通 ⇒ cloudflared 16s 后
    # rc=1，三次阶梯全败。这时**唯一**还能工作的就是「节点主动会合」：只要它连得上 hub 的
    # 公网地址，就能被派活。显式 `CFG.link=cloudflared` 不回落（明确的意图不该被改写）。
    if not tunnel.url and not forced_cf and tunnel.link != LINK_RELAY:
        tunnel.open_relay("隧道不可用（本机建不出 quick tunnel）")
        _follow_relay_port()
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
    _t_ping0 = time.time()
    ping = _ping(port, auth)
    # 超额认购自检的基准（P3）：worker 刚预热完、还没有任务时的 ping 延迟。
    # agent 未就绪（ping=None）时基准记 0：拿一次超时的 5s 当基准会把告警永久压死。
    ping_base_ms = max(1, int((time.time() - _t_ping0) * 1000)) if ping is not None else 0
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
        channel=tunnel.link,
        hub_url=hub_url,
    ):
        log(line)

    # 超额认购自检（P3）：默认 `workers=物理核×2` 就是 §2026-11-11 事故的配方，基线 ping
    # 是唯一能提前看见「父进程事件循环被饿」的读数（事故里 1.8s → 30~60s）。
    disk_state: dict = {"level": None, "rejecting": False, "warned_at": 0.0}
    log(node_report.workers_selfcheck_line(workers, slot_note, ping_base_ms))

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
                    _t_probe = time.time()
                    _probe = _ping(port, auth)
                    _probe_ms = max(1, int((time.time() - _t_probe) * 1000))
                    if _probe is None:
                        miss += 1
                        if miss >= 3:
                            dead, why = True, "HTTP 探活连续 3 次失败"
                    else:
                        miss = 0
                        # 磁盘档位切换即时行 + 超额认购监控（都复用这一拍 ping，零额外请求）。
                        node_report.monitor_tick(_probe, _probe_ms, ping_base_ms, disk_state, log)
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
                if session.tunnel.relay is not None:
                    log(session.tunnel.relay.stats_line())
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
        if tunnel.relay is not None:
            tunnel.relay.stop()
        _terminate(agent)
        _terminate(cfp)
    log("采样节点结束")
    return 0
