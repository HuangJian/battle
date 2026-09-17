"""remote/tunnel_ab_probe.py —— M1 验收：隧道协议 A/B 探针（本机闭环，不需要云机）。

为什么存在：`plan/remote-wire-remediation.plan.md` §3.4 的判据是「`http2` 腿 p50 明显更快
**且**连跑 N 轮不退化；`quic` 腿复现退化」。这是一条**环境测量**（ISP 对 QUIC/UDP-443 的
QoS 降质），不是代码路径 —— 所以它必须可重跑、同一台机器、同一时刻、唯一变量是 `--protocol`。

做法（每次运行都自建环境，不依赖任何已启动的组件）：
  1. 在本机随机空闲端口起一个**真** hub-server（`make_server`，同 `smoke_loopback.py` 的口径）；
  2. 每条腿：`cloudflared tunnel --url http://localhost:<hubPort> --protocol <p> --edge-ip-version 4`
     起一条 quick tunnel，从 logfile 里等出 `https://*.trycloudflare.com` 地址；
  3. 双向各打 N 发 2MiB：上行 = POST `/admin/net-probe`（body 2MiB，服务端只读掉并回字节数）、
     下行 = GET `/admin/net-probe?bytes=2097152`；
  4. 再跑一条**回环基线**（不经隧道），这样「隧道比回环慢多少」与「两条腿谁更快」两个问题
     都能回答 —— 绝对数会把家宽走两遍（请求出一遍、响应回一遍），不可直接换算成每轮耗时；
  5. 每腿报 p50/p90 与判定（≥2 条独立运行才算数，见 §6「噪声地板」纪律）。

纪律：
  - ⚠ **D9**：全程只用**正确** token。`hub_server` 对同 IP 连败 5 次封 3600s，而 cloudflared
    回源会把隧道流量全归成 127.0.0.1 ⇒ 打错 token 会封掉**本机**组件。本脚本不做任何
    「故意打错 token」的尝试（那条在单测里）。
  - cloudflared 一律 `finally` 杀掉（quick tunnel 是外部进程，泄漏会留在 CF 侧）。
  - 输出落日志文件（§16.2），本脚本自己打完整表，调用方不必再解析。

用法：
    bun dashboard/src/launch/cli.ts --script remote/tunnel_ab_probe.py
    # 或直接：
    bash tools/githook/nn-py-safe.sh nn-training/remote/tunnel_ab_probe.py --legs quic,http2 --rounds 5
"""

from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from remote.hub_server import _JobStore, make_server

#: 探针客户端一律**不走环境代理**。
#: 本机（zh-CN 开发环境）设了 `HTTPS_PROXY=http://127.0.0.1:7890`，而 `NO_PROXY` 只含
#: localhost/127.0.0.1/内网网段 —— 于是打 `https://*.trycloudflare.com` 会被丢进本地代理，
#: 拿回 `SSL: UNEXPECTED_EOF_WHILE_READING`（2026-09-17 实测，隧道本身健康且已
#: `Registered tunnel connection`）。云 worker 上没有这种代理，所以 A/B 必须按「直连」量，
#: 否则量到的是本机代理与 CF 边缘之间的链路，而不是隧道协议。
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))

#: 每条腿每方向的探测发数（判据要 p50/p90，5 发是 §3.4 的下限）
DEFAULT_ROUNDS = 5
#: 探测载荷大小（§3.4 指定 2MB）
PROBE_BYTES = 2 * 1024 * 1024
#: quick tunnel 建立超时（实测 5–30s；给足但不无限等）
TUNNEL_WAIT_SEC = 90
#: 单发探测超时（含隧道首包慢启动）
PROBE_TIMEOUT_SEC = 120
#: 隧道路径的实例级超时（整条腿的上限，防止 QUIC 腿静默挂死）
LEG_TIMEOUT_SEC = 900

#: 从 cloudflared logfile 里抠出 quick tunnel 地址
_URL_RE = re.compile(r"(https://[a-z0-9-]+\.trycloudflare\.com)")


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] [ab] {msg}", flush=True)


def _cloudflared_bin() -> str:
    """cloudflared 可执行：env 覆盖 → PATH → Windows 常见安装位置（panel 同口径）。"""
    env = os.environ.get("CLOUDFLARED_BIN", "").strip()
    if env and Path(env).exists():
        return env
    from shutil import which

    found = which("cloudflared")
    if found:
        return found
    for cand in (
        Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links" / "cloudflared.exe",
        Path(r"C:\Program Files\cloudflared\cloudflared.exe"),
        Path(r"C:\Program Files (x86)\cloudflared\cloudflared.exe"),
    ):
        if cand.exists():
            return str(cand)
    raise SystemExit(
        "[ab] 找不到 cloudflared（设 CLOUDFLARED_BIN，或装一次："
        "winget install --id Cloudflare.cloudflared）"
    )


def _boot_hub(port: int, token: str) -> object:
    """本机真 hub-server（0 = 随机空闲端口）；返回 (srv, port)。"""
    work = Path(tempfile.mkdtemp(prefix="tunnel-ab-"))
    store = _JobStore(work / "jobs", work / "training_log.jsonl")
    srv = make_server(store, port, token, host="127.0.0.1")
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def _start_tunnel(bin_path: str, hub_port: int, protocol: str, logfile: Path) -> subprocess.Popen:
    cmd = [
        bin_path,
        "tunnel",
        "--url",
        f"http://localhost:{hub_port}",
        "--no-autoupdate",
        "--protocol",
        protocol,
        "--edge-ip-version",
        "4",
        "--logfile",
        str(logfile),
    ]
    return subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _wait_url(logfile: Path, proc: subprocess.Popen, deadline: float) -> str:
    """等 quick tunnel 地址出现；进程提前退出/超时都响亮报错。"""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"cloudflared 提前退出（exit={proc.returncode}）")
        try:
            text = logfile.read_text(encoding="utf-8", errors="replace")
        except OSError:
            text = ""
        m = _URL_RE.search(text)
        if m:
            return m.group(1)
        time.sleep(0.5)
    raise RuntimeError(f"{TUNNEL_WAIT_SEC}s 内没等到 trycloudflare 地址")


def _wait_ready(base: str, token: str, deadline: float, log=_log) -> None:
    """等隧道**真的开始服务**再计时。

    为什么必需：quick tunnel 的 URL 在 edge 连接**注册完成前**就写进日志了，紧接着打第一发
    会拿到 `SSL: UNEXPECTED_EOF_WHILE_READING`（2026-09-17 实测）——那会把「连接没建好」
    记成「协议慢」，恰好污染本探针唯一要量的东西。只用**正确** token（D9）。
    """
    last = ""
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                base + "/ping", headers={"Authorization": f"Bearer {token}"}
            )
            with _OPENER.open(req, timeout=10) as resp:
                if resp.status == 200:
                    return
                last = f"HTTP {resp.status}"
        except Exception as e:  # 就绪前一切网络异常都只是「还没好」
            last = f"{type(e).__name__}: {e}"
        time.sleep(1.0)
    raise RuntimeError(f"隧道在期限内未就绪（最后错误：{last}）")


def _probe_with_warmup(fn, *, what: str, timeout: float = 60.0) -> float:
    """单发探测带一次暖机容忍：前几发可能撞上首包慢启动/连接回收，重试不计入统计。"""
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            return float(fn())
        except Exception as e:
            last = e
            _log(f"  {what} 首发未成（{type(e).__name__}）—— 2s 后重试（不计入统计）")
            time.sleep(2.0)
    raise RuntimeError(f"{what} 暖机后仍失败: {last}")


def _probe_up(base: str, token: str, payload: bytes) -> float:
    """上行一发：POST 2MiB，返回秒。"""
    req = urllib.request.Request(
        base + "/admin/net-probe",
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/octet-stream"},
        method="POST",
    )
    t0 = time.perf_counter()
    with _OPENER.open(req, timeout=PROBE_TIMEOUT_SEC) as resp:
        got = json.loads(resp.read().decode("utf-8"))
    dt = time.perf_counter() - t0
    if int(got.get("bytes", -1)) != len(payload):
        raise RuntimeError(f"上行对账失败：服务端收到 {got.get('bytes')} != {len(payload)}")
    return dt


def _probe_down(base: str, token: str, n: int) -> float:
    """下行一发：GET N 字节，返回秒（校验长度，防截断被算成「快」）。"""
    req = urllib.request.Request(
        base + f"/admin/net-probe?bytes={n}",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    t0 = time.perf_counter()
    with _OPENER.open(req, timeout=PROBE_TIMEOUT_SEC) as resp:
        body = resp.read()
    dt = time.perf_counter() - t0
    if len(body) != n:
        raise RuntimeError(f"下行对账失败：收到 {len(body)} != {n}")
    return dt


def _stats(secs: list[float], nbytes: int) -> dict:
    mbps = [nbytes * 8 / s / 1e6 for s in secs if s > 0]
    return {
        "n": len(secs),
        "p50_sec": round(statistics.median(secs), 3),
        "p90_sec": round(sorted(secs)[max(0, int(len(secs) * 0.9) - 1)], 3),
        "max_sec": round(max(secs), 3),
        "p50_mbps": round(statistics.median(mbps), 1),
    }


def _run_leg(label: str, base: str, token: str, rounds: int, payload: bytes) -> dict:
    _log(f"腿 {label}: 上行 {rounds}×{len(payload) // 1024 // 1024}MiB（POST）")
    up: list[float] = []
    for i in range(rounds):
        dt = _probe_with_warmup(
            lambda: _probe_up(base, token, payload), what=f"{label} up[{i + 1}/{rounds}]"
        )
        up.append(dt)
        _log(f"  {label} up[{i + 1}/{rounds}] {dt:.2f}s ({len(payload) * 8 / dt / 1e6:.1f} Mbps)")
    _log(f"腿 {label}: 下行 {rounds}×{len(payload) // 1024 // 1024}MiB（GET）")
    down: list[float] = []
    for i in range(rounds):
        dt = _probe_with_warmup(
            lambda: _probe_down(base, token, len(payload)), what=f"{label} down[{i + 1}/{rounds}]"
        )
        down.append(dt)
        _log(f"  {label} down[{i + 1}/{rounds}] {dt:.2f}s ({len(payload) * 8 / dt / 1e6:.1f} Mbps)")
    return {"up": _stats(up, len(payload)), "down": _stats(down, len(payload))}


def main() -> int:
    ap = argparse.ArgumentParser(description="M1 隧道协议 A/B 探针（本机闭环）")
    ap.add_argument("--legs", default="quic,http2", help="逗号分隔的协议腿（quic,http2）+ 可选 loopback")
    ap.add_argument("--rounds", type=int, default=DEFAULT_ROUNDS)
    ap.add_argument("--bytes", type=int, default=PROBE_BYTES)
    ap.add_argument("--token", default="ab-probe-token")
    ap.add_argument("--json-out", default="", help="把结果表写成 JSON（给多次独立运行汇总用）")
    args = ap.parse_args()

    legs = [s.strip() for s in args.legs.split(",") if s.strip()]
    payload = bytes((i * 131 + 7) & 0xFF for i in range(args.bytes))
    hub_port = 0
    srv = _boot_hub(hub_port, args.token)
    hub_port = srv.server_address[1]  # type: ignore[attr-defined]
    loopback = f"http://127.0.0.1:{hub_port}"
    _log(f"hub-server 就绪 {loopback}（token 长度 {len(args.token)}）")

    results: dict = {"bytes": len(payload), "rounds": args.rounds, "legs": {}}
    try:
        # 回环基线：不经隧道。用于回答「隧道慢多少」，不参与协议优劣判定。
        if "loopback" in legs:
            results["legs"]["loopback"] = _run_leg("loopback", loopback, args.token, args.rounds, payload)

        bin_path = _cloudflared_bin()
        for proto in [p for p in legs if p in ("quic", "http2", "auto")]:
            logfile = Path(tempfile.gettempdir()) / f"tunnel-ab-{proto}-{int(time.time())}.log"
            _log(f"腿 {proto}: 起 quick tunnel（cloudflared {bin_path}）")
            proc = _start_tunnel(bin_path, hub_port, proto, logfile)
            try:
                url = _wait_url(logfile, proc, time.time() + TUNNEL_WAIT_SEC)
                _log(f"腿 {proto}: 隧道地址已出，等 edge 真的开始服务 {url}")
                _wait_ready(url, args.token, time.time() + TUNNEL_WAIT_SEC)
                _log(f"腿 {proto}: 就绪，开测")
                results["legs"][proto] = _run_leg(proto, url, args.token, args.rounds, payload)
                results["legs"][proto]["url"] = url
            finally:
                proc.terminate()
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
                _log(f"腿 {proto}: cloudflared 已退出")
                # 腿间隔：让 CF 边缘把上一条连接清掉，避免腿间互相干扰
                time.sleep(3)
    finally:
        srv.shutdown()  # type: ignore[attr-defined]
        srv.server_close()  # type: ignore[attr-defined]

    _log("===== A/B 结果（每方向：p50 / p90 / max 秒，p50 吞吐 Mbps）=====")
    for name, r in results["legs"].items():
        for direction in ("up", "down"):
            s = r[direction]
            _log(
                f"  {name:9s} {direction:4s}  n={s['n']}  p50={s['p50_sec']:.2f}s  "
                f"p90={s['p90_sec']:.2f}s  max={s['max_sec']:.2f}s  ({s['p50_mbps']:.1f} Mbps)"
            )
    print(json.dumps(results, ensure_ascii=False), flush=True)
    if args.json_out:
        Path(args.json_out).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        _log(f"JSON → {args.json_out}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except urllib.error.HTTPError as e:
        # 鉴权/越界类错误点名（**绝不重试**——D9）
        print(f"[ab] HTTP {e.code}: {e.read()[:200]!r}", flush=True)
        sys.exit(1)
    except Exception as e:  # CLI 顶层：把类型与消息打全再退非零
        print(f"[ab] FAILED {type(e).__name__}: {e}", flush=True)
        sys.exit(1)
