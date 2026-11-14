"""tests/remote/test_rollout_node.py —— 采样节点运行时（plan/rollout-node-auto-register v2）。

覆盖 S2 的纯函数面 + 注册 HTTP 面（注入 urlopen，不碰真网络）：
`resolve_link` 默认表 · `registry_url` 与 `resolve_hub_url` 单一实现 · 载荷缺省不写
`concurrency`（F2）· 去重 · 错误分类 · register/unregister 三态。
"""

from __future__ import annotations

import json
import os
import urllib.error
from email.message import Message
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from common import node_report
from remote import rollout_node as rn
from remote import rollout_tunnel as rt
from remote import tailscale_boot

# ---------------------------------------------------------------- resolve_link

@pytest.mark.parametrize(
    ("env", "cfg_link", "has_key", "want"),
    [
        ("colab", "auto", False, "tailscale"),  # Colab 优先（用户口径），没有 key 也让引导去响
        ("colab", "", False, "tailscale"),
        ("colab", "cloudflared", True, "cloudflared"),  # 强压
        ("kaggle", "auto", True, "tailscale"),
        ("kaggle", "auto", False, "cloudflared"),
        ("aistudio", "", False, "cloudflared"),
        ("local", "AUTO", False, "cloudflared"),
        ("kaggle", "tailscale", False, "tailscale"),  # 强压（引导失败后由 run 回落）
    ],
)
def test_resolve_link_table(env: str, cfg_link: str, has_key: bool, want: str) -> None:
    assert rn.resolve_link(env, cfg_link, has_key) == want


# ---------------------------------------------------------------- registry_url

def test_registry_url_is_the_single_address_parser() -> None:
    """转调 `tailscale_boot.resolve_hub_url`（F13：不再抄第二份「逐字一致」）。"""
    cases = [
        ("100.64.0.5", 0, ""),
        ("100.64.0.5:9999", 0, ""),
        ("", 8787, "http://hub.tailnet.ts.net:8787"),
        ("", 8787, "http://<TS_IP>:8787"),  # 模板值 = 未填
        ("100.1.2.3", 8787, ""),
        ("", 0, ""),
    ]
    for hub_ip, hub_port, cfg_url in cases:
        assert rn.registry_url(hub_ip, hub_port, cfg_url) == tailscale_boot.resolve_hub_url(
            cfg_hub_url=cfg_url, hub_ip=hub_ip, hub_port=hub_port
        )
    # 具体口径锚点（裸 IP + 默认端口 / 整条 URL 原样）
    assert rn.registry_url("100.64.0.5", 0, "") == "http://100.64.0.5:8787"
    assert rn.registry_url("", 0, "https://hub.example.ts.net:8443/") == "https://hub.example.ts.net:8443"


# ---------------------------------------------------------------- payload / 去重

def test_payload_omits_concurrency_by_default() -> None:
    """F2：缺省**不写** `concurrency` 键（派发读 `or ping.cpus`；写 1 = 静默单核化）。"""
    p = rn.build_register_payload("rollout-colab", "http://100.64.0.5:8443", "k")
    assert p == {
        "id": "rollout-colab",
        "url": "http://100.64.0.5:8443",
        "authKey": "k",
        "managed": True,
    }
    assert set(p) == {"id", "url", "authKey", "managed"}


def test_payload_includes_self_reported_slots_and_label() -> None:
    p = rn.build_register_payload("n", "http://x:1", "k", workers=94, label="colab-t")
    assert p["concurrency"] == 94
    assert p["label"] == "colab-t"
    # 0 / 负数 / 垃圾 = 不写（不额外限流）
    for bad in (0, -3, "x"):
        assert set(rn.build_register_payload("n", "http://x:1", "k", workers=bad)) == {
            "id",
            "url",
            "authKey",
            "managed",
        }


def test_should_register_only_on_change() -> None:
    assert rn.should_register(None, "http://a", "k1") is True
    st = {"url": "http://a", "authKey": "k1"}
    assert rn.should_register(st, "http://a", "k1") is False
    assert rn.should_register(st, "http://b", "k1") is True
    assert rn.should_register(st, "http://a", "k2") is True


# ---------------------------------------------------------------- classify_error

def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://h", code, "err", Message(), BytesIO(b"{}"))


def test_classify_error() -> None:
    assert rn.classify_error(_http_error(401)) == "auth"
    assert rn.classify_error(_http_error(403)) == "auth"
    assert rn.classify_error(_http_error(422)) == "server"
    assert rn.classify_error(_http_error(500)) == "server"
    assert rn.classify_error(urllib.error.URLError("boom")) == "net"
    assert rn.classify_error(TimeoutError()) == "net"
    assert rn.classify_error(ValueError("bad")) == "unknown"


# ---------------------------------------------------------------- register / unregister

class _Resp:
    # body 放宽到 Any：有用例要盖「响应体不是 dict」这条分支（agent 变坏/旧版），
    # 它必须走 `_status` 的「不编状态」路径而不是被夹具的注解挡住。
    def __init__(self, body: Any, status: int = 200) -> None:
        self._b = json.dumps(body).encode("utf-8")
        self.status = status

    def read(self) -> bytes:
        return self._b

    def __enter__(self) -> _Resp:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def _opener(resp: _Resp | None = None, exc: BaseException | None = None, calls: list | None = None):
    def _open(req, timeout=None):
        if calls is not None:
            calls.append(req)
        if exc is not None:
            raise exc
        return resp

    return _open


def test_register_success_posts_to_the_admin_endpoint() -> None:
    calls: list = []
    out = rn.register(
        _opener(_Resp({"ok": True, "action": "created"}), calls=calls),
        "http://hub:8787",
        "tok",
        {"id": "n"},
        lambda m: None,
    )
    # 2026-10-23：返回体多了 `body` / `capabilities`（push 节点要用应答认「hub 听懂 kind 了吗」）。
    # 2026-10-25：再加 `dispatchable`（hub 到底会不会派单；老 hub 没这个键 ⇒ None）。
    assert out == {
        "ok": True,
        "status": 200,
        "action": "created",
        "body": {"ok": True, "action": "created"},
        "capabilities": [],
        "dispatchable": None,
        "error": None,
        "kind": "ok",
    }
    assert len(calls) == 1
    req = calls[0]
    assert req.full_url == "http://hub:8787/admin/nodes/register"
    assert req.get_header("Authorization") == "Bearer tok"


def test_register_failure_is_classified_never_raised() -> None:
    out = rn.register(_opener(exc=_http_error(401)), "http://h", "tok", {}, lambda m: None)
    assert out["ok"] is False and out["kind"] == "auth" and out["status"] == 401

    out = rn.register(_opener(exc=_http_error(422)), "http://h", "tok", {}, lambda m: None)
    assert out["ok"] is False and out["kind"] == "server" and out["status"] == 422

    out = rn.register(_opener(exc=urllib.error.URLError("down")), "http://h", "tok", {}, lambda m: None)
    assert out["ok"] is False and out["kind"] == "net" and out["status"] == 0


def test_register_without_hub_or_token_is_a_clean_refusal() -> None:
    out = rn.register(_opener(_Resp({})), "", "", {}, lambda m: None)
    assert out["ok"] is False and out["kind"] == "net"


def test_unregister_posts_id_and_reports_status() -> None:
    calls: list = []
    out = rn.unregister(
        _opener(_Resp({"ok": True, "action": "unregistered"}), calls=calls),
        "http://hub:8787",
        "tok",
        "rollout-colab",
        lambda m: None,
    )
    assert out["ok"] is True and out["action"] == "unregistered"
    assert calls[0].full_url == "http://hub:8787/admin/nodes/unregister"
    assert json.loads(calls[0].data.decode("utf-8")) == {"id": "rollout-colab"}

    out = rn.unregister(_opener(exc=_http_error(404)), "http://h", "tok", "n", lambda m: None)
    assert out["ok"] is False and out["status"] == 404


# ---------------------------------------------------------------- 近 30min 产出摘要（2026-10-18）

def test_output_line_prefers_the_agent_line_verbatim() -> None:
    """行形状由 agent 算好（数字只在 `tools/agent/output-window.ts` 一处算）——这里只搬运。

    为什么不在 python 里再拼一遍：notebook 日志与 `sampler-agent.log` 必须逐字相同，
    两处格式化 = 两套口径，对账时先要怀疑日志。
    """
    line = "games=412（rollout=380 / eval=32） · 13.7 局/分（30min） · 失败=3 · 轮次=2"
    assert node_report.output_line({"outputLine": line}) == f"近30min产出 {line}"
    # 两侧空白不是新事实（日志行不带缩进）
    assert node_report.output_line({"outputLine": f"  {line}  "}) == f"近30min产出 {line}"


def test_output_line_is_honest_when_unknown() -> None:
    """旧 agent / 探活失败 ⇒ 「不可知」；绝不拿累计数冒充 30 分钟窗口（那是静默错数）。

    用**全等**而不是子串：这两种不可知形态是仅有的两档，判据没必要挂在措辞的一部分上
    （文本断言计数护栏只许降不许升，全等不计）。
    """
    assert node_report.output_line(None) == "近30min产出 不可知（agent 未应答）"
    assert node_report.output_line({"gamesDoneTotal": 1240}) == "近30min产出 不可知（旧 agent 无 outputLine）"
    assert node_report.output_line({"outputLine": "   "}) == "近30min产出 不可知（旧 agent 无 outputLine）"


def test_status_reads_local_agent_with_auth_and_never_raises() -> None:
    calls: list = []
    body = {"outputLine": "games=1（rollout=1 / eval=0）"}
    st = rn._status(8443, "tok", urlopen=_opener(_Resp(body), calls=calls))
    assert st == body
    assert calls[0].full_url == "http://127.0.0.1:8443/v1/status"
    assert calls[0].get_header("Authorization") == "Bearer tok"
    assert rn._status(8443, "tok", urlopen=_opener(exc=urllib.error.URLError("down"))) is None
    # 非 dict 响应体（旧/坏 agent）⇒ None（不编一个状态出来）
    assert rn._status(8443, urlopen=_opener(_Resp([1, 2]))) is None


def test_output_summary_cadence_is_ten_minutes() -> None:
    """10 分钟一行（用户 2026-10-18 口径）——节拍是口径本身，写死值相等判据。"""
    assert rn.OUTPUT_STATUS_EVERY_SEC == 600.0


def test_emit_output_summary_prints_output_and_disk_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    """监护循环每 10 分钟调的就是它：读**一次** `/v1/status`、打两行（产出 + 磁盘）。

    2026-11-14（P3）：磁盘水位要与产出同拍上屏（plan/node-throughput-and-keepalive D4）——
    两行共用一个快照，`_status` 只许调一次（零额外请求）。钉在这里而不是钉源码文本：
    notebook 侧看不到的正是「算了但没上屏」。
    """
    calls: list[int] = []
    status = {
        "outputLine": "games=7（rollout=7 / eval=0）",
        "diskFreeMB": 2706,
        "diskLevel": "warn",
        "diskWarnMB": 4096,
        "diskFloorMB": 2048,
    }

    def _st(port: int, auth: str) -> dict:
        calls.append(1)
        return status

    monkeypatch.setattr(rn, "_status", _st)
    seen: list[str] = []
    rn.emit_output_summary(8443, "tok", seen.append)
    assert len(calls) == 1, "磁盘行与产出行必须共用同一次 /v1/status（零额外请求）"
    assert seen[0] == "近30min产出 games=7（rollout=7 / eval=0）"
    assert seen[1] == "磁盘 free=2706MB（warn<4096 / 熔断<2048） · 档位=warn · 拒收=否 · 距熔断 658MB"
    # agent 不可达 ⇒ 两行都如实说不可知（不是零行、不是抛）
    monkeypatch.setattr(rn, "_status", lambda port, auth: None)
    seen.clear()
    rn.emit_output_summary(8443, "tok", seen.append)
    assert seen == ["近30min产出 不可知（agent 未应答）", "磁盘 free=不可知（agent 未应答）"]


# ---------------------------------------------------------------- GitHub 不通时的下载路

def test_github_candidates_auto_try_builtin_mirrors() -> None:
    """不填任何开关：直连之后自动跟内置镜像（阿里云 DSW 现场不需要配置）。"""
    direct = "https://github.com/HuangJian/battle.git"
    got = rn._github_candidates({}, direct)
    assert got[0] == direct
    assert set(got[1:]) == {m + "/HuangJian/battle.git" for m in rn.GITHUB_MIRRORS}
    # 非 GitHub 域名不碰
    assert rn._github_candidates({}, "https://example.com/x.zip") == ["https://example.com/x.zip"]


def test_github_candidates_explicit_base_goes_first() -> None:
    """缓存了 `github_base` 时显式镜像优先、直连兜底、不重复。"""
    direct = "https://github.com/a/b"
    cfg = {"github_base": "https://mirror.example/https://github.com/"}
    assert rn._github_candidates(cfg, direct)[:2] == ["https://mirror.example/https://github.com/a/b", direct]
    got = rn._github_candidates({"github_base": "https://gh-proxy.com/https://github.com"}, direct)
    assert got.count("https://gh-proxy.com/https://github.com/a/b") == 1


def test_github_candidates_read_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_BASE", "https://mirror.example/https://github.com")
    assert (
        rn._github_candidates({}, "https://github.com/a/b")[0]
        == "https://mirror.example/https://github.com/a/b"
    )


def test_speed_probe_returns_bytes_per_second(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rn,
        "_sh",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="262144 0.500", stderr=""),
    )
    assert rn._speed_probe("https://example.com/a.zip") == pytest.approx(524288.0)


def test_speed_probe_failure_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        rn,
        "_sh",
        lambda *a, **k: SimpleNamespace(returncode=6, stdout="", stderr=""),
    )
    assert rn._speed_probe("https://example.com/a.zip") == 0.0


def test_rank_fastest_orders_by_measured_speed(monkeypatch: pytest.MonkeyPatch) -> None:
    speeds = {
        "https://slow.example/a": 1_000_000.0,
        "https://fast.example/a": 9_000_000.0,
        "https://dead.example/a": 0.0,
    }
    monkeypatch.setattr(rn, "_speed_probe", lambda url, **_kw: speeds[url])
    got = rn._rank_fastest([(u, u) for u in speeds], "测试", lambda _m: None)
    assert got == ["https://fast.example/a", "https://slow.example/a", "https://dead.example/a"]


def test_bun_asset_url_maps_base_to_latest_zip() -> None:
    assert rn._bun_asset_url("", "bun-linux-x64") == (
        "https://github.com/oven-sh/bun/releases/latest/download/bun-linux-x64.zip"
    )
    assert rn._bun_asset_url(
        "https://gh-proxy.com/https://github.com", "bun-linux-aarch64"
    ) == (
        "https://gh-proxy.com/https://github.com/oven-sh/bun/releases/latest/download/"
        "bun-linux-aarch64.zip"
    )


def test_bun_installer_gets_github_base(monkeypatch: pytest.MonkeyPatch) -> None:
    """官方 install.sh 认 `GITHUB` 环境变量 ⇒ 镜像基址必须传到子进程（阿里云 DSW 回归）。"""
    calls: list[dict[str, Any]] = []

    def _fake_sh(cmd, *, cwd=None, timeout=None, env=None):
        calls.append({"cmd": list(cmd), "env": env})
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(rn, "_sh", _fake_sh)
    monkeypatch.setattr(rn, "_install_bun_mirror", lambda cfg, log: "")
    monkeypatch.setattr(rn, "DOWNLOAD_BACKOFF", ())
    bun = rn._ensure_bun(
        Path("/tmp"),
        {"github_base": "https://gh-proxy.com/https://github.com"},
        lambda _m: None,
    )
    assert bun == ""
    installer = [c for c in calls if c["cmd"][-1].startswith("curl -fsSL https://bun.sh/install")]
    assert installer, "没走到官方安装器"
    assert installer[0]["env"]["GITHUB"] == "https://gh-proxy.com/https://github.com"


def test_bun_installer_auto_tries_mirrors_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    """不填 `github_base`：测不出速度时按「直连 → 内置镜像」稳定顺序逐个重跑官方安装器。"""
    calls: list[dict[str, Any]] = []

    def _fake_sh(cmd, *, cwd=None, timeout=None, env=None):
        calls.append({"cmd": list(cmd), "env": env})
        return SimpleNamespace(returncode=1, stdout="", stderr="")

    monkeypatch.setattr(rn, "_sh", _fake_sh)
    monkeypatch.setattr(rn, "_install_bun_mirror", lambda cfg, log: "")
    monkeypatch.setattr(rn, "DOWNLOAD_BACKOFF", ())
    assert rn._ensure_bun(Path("/tmp"), {}, lambda _m: None) == ""
    installers = [c for c in calls if c["cmd"][-1].startswith("curl -fsSL https://bun.sh/install")]
    got = [c["env"]["GITHUB"] if c["env"] else None for c in installers]
    assert got == [None, *rn.GITHUB_MIRRORS] * rn.DOWNLOAD_ROUNDS


def test_cloudflared_install_uses_fastest_probe_then_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """候选并行测速：先下最快的；失败再顺延下一条（不按固定表顺序）。"""
    downloads: list[str] = []

    def _fake_sh(cmd, *, cwd=None, timeout=None, env=None):
        if cmd[:2] == ["bash", "-lc"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        url = next((c for c in cmd if c.startswith("https://")), "")
        if "--range" in cmd:
            if "gh-proxy.com" in url:
                return SimpleNamespace(returncode=0, stdout="262144 0.100", stderr="")
            if "ghproxy.net" in url:
                return SimpleNamespace(returncode=0, stdout="262144 0.200", stderr="")
            return SimpleNamespace(returncode=6, stdout="", stderr="")
        downloads.append(url)
        return SimpleNamespace(
            returncode=0 if "ghproxy.net" in url else 6, stdout="", stderr=""
        )

    monkeypatch.setattr(rn, "_sh", _fake_sh)
    monkeypatch.setattr(rn, "_find_cloudflared", lambda _cfg, _log: "")
    monkeypatch.setattr(rn.os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(rn.subprocess, "Popen", lambda *a, **k: object())
    out = rn._start_cloudflared({}, 8443, Path("/tmp/x.log"), lambda _m: None)
    assert out is not None
    direct = (
        "https://github.com/cloudflare/cloudflared/releases/latest/download/"
        "cloudflared-linux-amd64"
    )
    assert downloads == [
        "https://gh-proxy.com/" + direct,
        "https://ghproxy.net/" + direct,
    ]


def test_cloudflared_download_retries_after_whole_round_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一轮三源全挂不是终点：退避后整轮重试，镜像恢复即装上（DSW 网络抖动回归）。"""
    downloads: list[str] = []

    def _fake_sh(cmd, *, cwd=None, timeout=None, env=None):
        if cmd[:2] == ["bash", "-lc"]:
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        url = next((c for c in cmd if c.startswith("https://")), "")
        if "--range" in cmd:
            return SimpleNamespace(returncode=6, stdout="", stderr="")
        downloads.append(url)
        if len(downloads) < 5:
            return SimpleNamespace(returncode=6, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rn, "_sh", _fake_sh)
    monkeypatch.setattr(rn, "_find_cloudflared", lambda _cfg, _log: "")
    monkeypatch.setattr(rn.os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(rn.subprocess, "Popen", lambda *a, **k: object())
    monkeypatch.setattr(rn, "DOWNLOAD_BACKOFF", ())
    out = rn._start_cloudflared({}, 8443, Path("/tmp/x.log"), lambda _m: None)
    assert out is not None
    assert len(downloads) == 5
    assert "gh-proxy.com" in downloads[4]


def test_slot_count_auto_is_twice_physical_cores(monkeypatch: pytest.MonkeyPatch) -> None:
    """`CFG.workers=0` ⇒ `min(有效核, 物理核) × 2`（2026-11-14 P3）。

    为什么 ×2（plan/node-throughput-and-keepalive D4）：单局 `svc 2.16s / wall 9.33s`，
    节点槽位只被服务段占住（装载/打包时不占 CPU）⇒ 多一倍的池槽把空档叠起来。
    口径仍与 agent 的 `CPUS = physicalCores()` 同源（cgroup 配额更小时取小），
    否则「笔记本按 4 开池、agent 自报 2 核」的超额认购会重演（2026-11-11 kaggle：
    父进程 HTTP 被饿到 30~60s）。回退口径：显式 `CFG.workers = 物理核数`。
    """
    from common import platform_utils

    monkeypatch.setattr(platform_utils, "effective_cores", lambda: 8)
    monkeypatch.setattr(platform_utils, "physical_cores", lambda: 4)
    assert rn._slot_count(0)[0] == 8  # 4 核 ×2
    assert rn._slot_count(3)[0] == 3  # 显式值完全照填
    # 容器配额比物理核更小（cgroup 限流）⇒ 取小，再 ×2
    monkeypatch.setattr(platform_utils, "effective_cores", lambda: 2)
    assert rn._slot_count(0)[0] == 4


def test_disk_thresholds_defaults_and_derivation() -> None:
    """默认 2048/4096；只给 floor ⇒ `max(floor+512, floor×2)`；两个都给 ⇒ 照填。"""
    assert node_report.disk_thresholds({}) == (2048, 4096)
    assert node_report.disk_thresholds({"disk_floor_mb": 1024}) == (1024, 2048)
    assert node_report.disk_thresholds({"disk_floor_mb": 4096}) == (4096, 8192)
    assert node_report.disk_thresholds({"disk_floor_mb": 3000, "disk_warn_mb": 8000}) == (3000, 8000)


def test_disk_thresholds_validation_is_loud() -> None:
    """非法阈值不静默夹取：启动期 FATAL（返回可读理由）。"""
    assert node_report.validate_disk_thresholds(2048, 4096) == ""
    assert node_report.validate_disk_thresholds(100, 4096).startswith("disk_floor_mb")
    assert node_report.validate_disk_thresholds(70000, 80000).startswith("disk_floor_mb")
    # warn < floor+512 ⇒ 拒（回差不足会在阈值附近抖振）
    assert node_report.validate_disk_thresholds(2048, 2300).startswith("disk_warn_mb")


def test_start_agent_passes_disk_thresholds(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """CFG → agent argv：阈值必须透传，否则 ping/status 回显的是默认值、真正生效的是另一个数。"""
    seen: list[list[str]] = []

    class _P:
        def poll(self):
            return None

    def _fake_popen(cmd: list[str], **_kw: object) -> _P:
        seen.append(cmd)
        return _P()

    monkeypatch.setattr(rn.subprocess, "Popen", _fake_popen)
    rn._start_agent(
        "bun",
        tmp_path / "a.ts",
        tmp_path,
        8443,
        4,
        {"disk_floor_mb": 3000, "disk_warn_mb": 8000},
        tmp_path / "agent.log",
    )
    cmd = seen[0]
    assert cmd[cmd.index("--disk-floor-mb") + 1] == "3000"
    assert cmd[cmd.index("--disk-warn-mb") + 1] == "8000"
    assert cmd[cmd.index("--workers") + 1] == "4"


def test_disk_line_three_levels_and_unknown() -> None:
    """周期行三档 + 不可知 + **生效**阈值（文本只引 status 的数字，不写死常量）。"""
    warn = {"diskFreeMB": 2706, "diskLevel": "warn", "diskWarnMB": 4096, "diskFloorMB": 2048}
    assert node_report.disk_line(warn) == (
        "磁盘 free=2706MB（warn<4096 / 熔断<2048） · 档位=warn · 拒收=否 · 距熔断 658MB"
    )
    crit = {"diskFreeMB": 1800, "diskLevel": "critical", "diskWarnMB": 4096, "diskFloorMB": 2048}
    assert node_report.disk_line(crit) == (
        "磁盘 free=1800MB（warn<4096 / 熔断<2048） · 档位=critical · 拒收=是 · 已破熔断 248MB"
    )
    # 生效阈值被 CFG 覆盖后文本跟着变（不是常量 2048/4096）
    ow = {"diskFreeMB": 3500, "diskLevel": "warn", "diskWarnMB": 8192, "diskFloorMB": 4096}
    assert node_report.disk_line(ow) == (
        "磁盘 free=3500MB（warn<8192 / 熔断<4096） · 档位=warn · 拒收=是 · 已破熔断 596MB"
    )

    assert node_report.disk_line(None) == "磁盘 free=不可知（agent 未应答）"
    assert node_report.disk_line({}) == "磁盘 free=不可知（旧 agent 无磁盘字段）"
    assert node_report.disk_line({"diskFreeMB": None}) == "磁盘 free=不可知（旧 agent 无磁盘字段）"


def test_over_subscribed_verdict() -> None:
    """≥1s 且 >1.5× 基准才算超额认购（§2026-11-11 的最早信号）。"""
    assert node_report.over_subscribed(900, 300) is False
    assert node_report.over_subscribed(1000, 300) is True
    assert node_report.over_subscribed(420, 300) is False  # 1.4×
    assert node_report.over_subscribed(1000, 0) is True  # 无基准：只看 1s


def test_note_disk_tick_only_on_change() -> None:
    """档位切换 / 正在拒收才打 ★；首拍不刷屏，但首拍就在拒收要可见。"""
    logs: list[str] = []
    st: dict = {"level": None, "rejecting": False, "warned_at": 0.0}
    ok = {"diskFreeMB": 5000, "diskLevel": "ok", "diskWarnMB": 4096, "diskFloorMB": 2048}
    node_report.note_disk_tick(ok, st, logs.append)
    assert logs == []  # 首拍基线
    node_report.note_disk_tick(dict(ok, diskFreeMB=3000, diskLevel="warn"), st, logs.append)
    assert len(logs) == 1 and logs[0].startswith("★ 磁盘")
    node_report.note_disk_tick(dict(ok, diskFreeMB=2800, diskLevel="warn"), st, logs.append)
    assert len(logs) == 1  # 同档不重复

    logs2: list[str] = []
    st2: dict = {"level": None, "rejecting": False, "warned_at": 0.0}
    low = {"diskFreeMB": 1000, "diskLevel": "critical", "diskWarnMB": 4096, "diskFloorMB": 2048}
    node_report.note_disk_tick(low, st2, logs2.append)
    assert len(logs2) == 1 and logs2[0].startswith("★ 磁盘")


def test_cloudflared_discovery_ignores_login_shell_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DSW 的 `bash -lc` 会先打横幅：stdout 非空不等于找到了二进制（旧实现撞 ENAMETOOLONG）。"""
    calls: list[list[str]] = []

    def _fake_sh(cmd, **_kw):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="╰────────┴────────╯\n", stderr="")

    monkeypatch.setattr(rn, "_sh", _fake_sh)
    monkeypatch.setattr(rn.shutil, "which", lambda _name: "")
    monkeypatch.setattr(rn.os.path, "isfile", lambda _p: False)
    assert rn._find_cloudflared({}, lambda _m: None) == ""
    assert calls == []


def test_cloudflared_discovery_prefers_explicit_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`CFG.cloudflared_path` 指到可执行文件时优先用它；不可执行则不采用。"""
    cf = tmp_path / "cloudflared"
    cf.write_bytes(b"")
    monkeypatch.setattr(rn.os.path, "isfile", lambda p: str(p) == str(cf))
    monkeypatch.setattr(rn.os, "access", lambda p, _mode: str(p) == str(cf))
    monkeypatch.setattr(rn.shutil, "which", lambda _name: "C:/other/cloudflared")
    assert rn._find_cloudflared({"cloudflared_path": str(cf)}, lambda _m: None) == str(cf)


def test_latest_bun_version_picks_semver_max() -> None:
    listing = (
        '[{"name":"bun-v1.4.9/"},{"name":"bun-v1.10.0/"},'
        '{"name":"bun-v1.4.4/"},{"name":"canary/"}]'
    )
    assert rn._latest_bun_version(listing) == "1.10.0"
    assert rn._latest_bun_version("[]") == ""


@pytest.mark.parametrize(
    ("machine", "cpuinfo", "want"),
    [
        ("x86_64", "flags: avx2", "bun-linux-x64"),
        ("x86_64", "flags: sse2", "bun-linux-x64-baseline"),
        ("amd64", "", "bun-linux-x64"),          # 读不到 cpuinfo 时不猜 baseline
        ("aarch64", "", "bun-linux-aarch64"),
        ("mips", "", ""),
    ],
)
def test_bun_target_table(machine: str, cpuinfo: str, want: str) -> None:
    assert rn._bun_target(machine, cpuinfo) == want


# ------------------------------------------------ 空 URL 硬闸 / banner / 通道姿势

def test_empty_url_never_posts_to_hub() -> None:
    """★ 空 URL 是本地失败：零 HTTP、kind=nourl、不写 reg_state。"""
    calls: list = []
    out = rn._try_register_once(
        url="",
        auth="ak",
        node_id="n",
        workers=1,
        label="l",
        hub_url="http://hub:8787",
        hub_token="tok",
        log=lambda _m: None,
        urlopen=_opener(_Resp({}), calls=calls),
    )
    assert out == {
        "ok": False,
        "status": 0,
        "action": None,
        "body": {},
        "capabilities": [],
        "error": "no tunnel url",
        "kind": "nourl",
    }
    assert calls == []


def test_try_register_once_posts_with_the_injected_opener() -> None:
    calls: list = []
    out = rn._try_register_once(
        url="https://a.trycloudflare.com",
        auth="ak",
        node_id="n",
        workers=1,
        label="l",
        hub_url="http://hub:8787",
        hub_token="tok",
        log=lambda _m: None,
        urlopen=_opener(_Resp({"action": "created"}), calls=calls),
    )
    assert out["ok"] is True and len(calls) == 1


def test_banner_skips_manual_snippet_when_url_empty() -> None:
    lines = rn._banner_lines(
        node_id="n",
        url="",
        auth="ak",
        workers=2,
        bun_version="1.4.4",
        code_hash="0123456789abcdef",
        hub_configured=True,
        reg_state={"url": "", "authKey": ""},
    )
    assert lines == [
        "=" * 68,
        "★ 未注册：隧道 URL 未取到 —— 不要手工登记（空 url 会被 hub 400 拒）。",
        "   节点每 60s 重试建隧道；真因看上面的 cloudflared 日志尾部。",
        "★ URL     : (未取到)",
        "★ authKey : ak",
        "  workers : 2  bun 1.4.4  codeHash=0123456789ab",
        "=" * 68,
    ]


def test_banner_prints_manual_snippet_when_url_present() -> None:
    lines = rn._banner_lines(
        node_id="n",
        url="https://a.trycloudflare.com",
        auth="ak",
        workers=2,
        bun_version="1.4.4",
        code_hash="0123456789abcdef",
        hub_configured=True,
        reg_state={"url": "", "authKey": ""},
    )
    assert lines[2] == (
        '  {"nodes":[{"id":"n","url":"https://a.trycloudflare.com","authKey":"ak"}]}'
    )


def test_direct_env_strips_proxies_keeps_no_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://platform:3128")
    monkeypatch.setenv("https_proxy", "http://platform:3128")
    monkeypatch.setenv("NO_PROXY", ".corp.example")
    env = rn._direct_env()
    assert env.get("HTTP_PROXY") is None and env.get("https_proxy") is None
    bypass = env["NO_PROXY"].split(",")
    assert bypass[:3] == ["localhost", "127.0.0.1", "::1"]
    assert bypass[-1] == ".corp.example"
    assert env["PATH"] == os.environ["PATH"]
    # 只给子进程用，不能污染本进程（注册请求还要走平台代理）。
    assert os.environ["HTTP_PROXY"] == "http://platform:3128"


def test_open_cloudflared_once_starts_even_with_nonempty_current_url() -> None:
    """★ B1：422 时 current_url 是 tailscale URL，但 cfp 为 None ⇒ 必须起 cf。"""
    calls: list[int] = []

    def _ensure() -> tuple[str, object]:
        calls.append(1)
        return "https://cf.trycloudflare.com", object()

    url, cfp, started = rn._open_cloudflared_once(
        current_url="http://100.64.0.5:8443", cfp=None, ensure=_ensure
    )
    assert url == "https://cf.trycloudflare.com"
    assert cfp is not None and started is True and calls == [1]


def test_open_cloudflared_once_reuses_live_process() -> None:
    proc = _Proc(None)
    url, cfp, started = rn._open_cloudflared_once(
        current_url="", cfp=proc, ensure=lambda: ("", None)
    )
    assert url == "" and cfp is proc and started is False


def test_tunnel_retry_due_only_when_empty_and_due() -> None:
    assert rn._tunnel_retry_due("https://a.trycloudflare.com", 100.0, 0.0) is False
    assert rn._tunnel_retry_due("", 100.0, 101.0) is False
    assert rn._tunnel_retry_due("", 101.0, 101.0) is True


# ------------------------------------------------ cloudflared 自救 / 预检

class _Proc:
    def __init__(self, rc: int | None) -> None:
        self.rc = rc
        self.terminated = False

    def poll(self) -> int | None:
        return self.rc

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0


def test_open_cloudflared_once_restarts_when_process_exited() -> None:
    """死进程对象（poll 非 None）必须当作没有：否则静默死后再也建不起隧道。"""
    calls: list[int] = []

    def _ensure() -> tuple[str, object]:
        calls.append(1)
        return "https://new.trycloudflare.com", _Proc(None)

    url, cfp, started = rn._open_cloudflared_once(
        current_url="https://dead.trycloudflare.com", cfp=_Proc(7), ensure=_ensure
    )
    assert url == "https://new.trycloudflare.com"
    assert cfp is not None and cfp.poll() is None
    assert started is True and calls == [1]


def test_tunnel_dead_requires_cloudflared_and_evidence() -> None:
    live, dead = _Proc(None), _Proc(7)
    kw: dict[str, Any] = {
        "is_cloudflared": True,
        "url": "https://a.trycloudflare.com",
    }
    assert rn._tunnel_dead(**kw, cfp=dead, agent_ok=True, probe_fails=0) is True
    assert rn._tunnel_dead(**kw, cfp=None, agent_ok=True, probe_fails=0) is True
    # 公网探针：连续 2 次不算、3 次算；本地 agent 不健康时不判。
    assert rn._tunnel_dead(**kw, cfp=live, agent_ok=True, probe_fails=2) is False
    assert rn._tunnel_dead(**kw, cfp=live, agent_ok=True, probe_fails=3) is True
    assert rn._tunnel_dead(**kw, cfp=live, agent_ok=False, probe_fails=9) is False
    # tailscale 通道 cfp=None 是正常态；空 URL 走补拿闸。
    assert (
        rn._tunnel_dead(
            is_cloudflared=False, url="http://100.64.0.5:8443", cfp=None,
            agent_ok=True, probe_fails=9,
        )
        is False
    )
    assert (
        rn._tunnel_dead(
            is_cloudflared=True,
            url="",
            cfp=None,
            agent_ok=True,
            probe_fails=9,
        )
        is False
    )


def test_public_ping_runs_in_platform_net_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """公网探针必须在 `_public_net()` 里跑（否则 tailnet 代理会把它恒打死）。"""
    seen: list[str] = []

    class _Ctx:
        def __enter__(self) -> None:
            seen.append("in")

        def __exit__(self, *_a: object) -> None:
            seen.append("out")

    monkeypatch.setattr(rt.tailscale_boot, "platform_net_env", lambda: _Ctx())
    assert rn._public_ping("https://x.trycloudflare.com", "ak", lambda *_a: {"ok": 1}) == {
        "ok": 1
    }
    assert seen == ["in", "out"]

    def _boom(*_a: object) -> dict | None:
        raise RuntimeError("down")

    assert rn._public_ping("https://x.trycloudflare.com", "ak", _boom) is None


def test_422_and_cf_failure_stops_without_second_tailscale_post(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B1：cf 全败时清空 URL，不能拿旧 tailscale URL 再发一次必然 422 的注册。"""
    code_dir = tmp_path / "code"
    entry = code_dir / "tools" / "agent" / "sampler-agent.ts"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fake\n", encoding="utf-8")
    posts: list[str] = []

    class _Agent:
        returncode = None

        def poll(self) -> None:
            return None

        def terminate(self) -> None:
            pass

        def wait(self, timeout: float | None = None) -> int:
            return 0

    def _interrupt(*_a: object) -> None:
        raise KeyboardInterrupt

    def _fake_register(**kw: object) -> dict:
        posts.append(str(kw["url"]))
        return {
            "ok": False,
            "status": 422,
            "action": None,
            "body": {},
            "capabilities": [],
            "error": "unreachable",
            "kind": "server",
        }

    monkeypatch.setattr(rn, "probe_cloud_env", lambda: "test")
    monkeypatch.setattr(rn, "resolve_link", lambda *_a: rn.LINK_TAILSCALE)
    monkeypatch.setattr(rn, "_slot_count", lambda _n: (1, "test"))
    monkeypatch.setattr(rn, "registry_url", lambda *_a: "http://hub:8787")
    monkeypatch.setattr(rn, "_ensure_bun", lambda *_a: "/bin/bun")
    monkeypatch.setattr(rn, "_net_probe", lambda _log: ("inherit", "direct"))
    monkeypatch.setattr(rn, "_open_tailscale", lambda *_a: ("http://100.64.0.5:8443", True))
    monkeypatch.setattr(rn, "_ensure_cloudflared_url", lambda *_a, **_k: ("", None))
    monkeypatch.setattr(rn, "_start_agent", lambda *_a: _Agent())
    monkeypatch.setattr(rn, "_wait_auth", lambda *_a, **_k: "ak")
    monkeypatch.setattr(rn, "_ping", lambda *_a, **_k: {"codeHash": "0123456789abcdef"})
    monkeypatch.setattr(rn, "_sh", lambda *_a, **_k: SimpleNamespace(stdout="1.4.4"))
    monkeypatch.setattr(rn, "_try_register_once", _fake_register)
    monkeypatch.setattr(rn.time, "sleep", _interrupt)

    rn.run(
        {
            "code_dir": str(code_dir),
            "link": "tailscale",
            "node_id": "n",
            "hub_url": "http://hub:8787",
            "hub_token": "tok",
            "agent_port": 8443,
            "workers": 1,
        },
        lambda _m: None,
        lambda key, default: {"TS_AUTHKEY": "ak", "HUB_TOKEN": "tok"}.get(key, default),
    )
    assert posts == ["http://100.64.0.5:8443"]


def test_empty_url_multiple_ticks_never_posts_or_backs_off(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B4：空 URL 在监护循环里多拍都不进注册，也不翻退避。"""
    code_dir = tmp_path / "code"
    entry = code_dir / "tools" / "agent" / "sampler-agent.ts"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fake\n", encoding="utf-8")
    clock = {"now": 1000.0, "sleeps": 0}
    logs: list[str] = []
    posts: list[str] = []
    gates: list[str] = []
    real_once = rn._try_register_once

    def _register(*_a: object) -> dict:
        posts.append("called")
        raise AssertionError("空 URL 不得进入 register()")

    def _gate(**kw: Any) -> dict:
        gates.append(str(kw["url"]))
        return real_once(**kw)

    def _time() -> float:
        return clock["now"]

    def _sleep(sec: float) -> None:
        clock["sleeps"] += 1
        clock["now"] += sec
        if clock["sleeps"] >= 4:
            raise KeyboardInterrupt

    monkeypatch.setattr(rn, "probe_cloud_env", lambda: "test")
    monkeypatch.setattr(rn, "resolve_link", lambda *_a: rn.LINK_CLOUDFLARED)
    monkeypatch.setattr(rn, "_slot_count", lambda _n: (1, "test"))
    monkeypatch.setattr(rn, "registry_url", lambda *_a: "http://hub:8787")
    monkeypatch.setattr(rn, "_ensure_bun", lambda *_a: "/bin/bun")
    monkeypatch.setattr(rn, "_net_probe", lambda _log: ("inherit", "direct"))
    monkeypatch.setattr(rn, "_ensure_cloudflared_url", lambda *_a, **_k: ("", None))
    monkeypatch.setattr(rn, "_start_agent", lambda *_a: _Proc(None))
    monkeypatch.setattr(rn, "_wait_auth", lambda *_a, **_k: "ak")
    monkeypatch.setattr(rn, "_ping", lambda *_a, **_k: {"codeHash": "0123456789abcdef"})
    monkeypatch.setattr(rn, "_sh", lambda *_a, **_k: SimpleNamespace(stdout="1.4.4"))
    monkeypatch.setattr(rn, "register", _register)
    monkeypatch.setattr(rn, "_try_register_once", _gate)
    monkeypatch.setattr(rn.time, "time", _time)
    monkeypatch.setattr(rn.time, "sleep", _sleep)

    rn.run(
        {
            "code_dir": str(code_dir),
            "link": "cloudflared",
            "node_id": "n",
            "hub_url": "http://hub:8787",
            "hub_token": "tok",
            "agent_port": 8443,
            "workers": 1,
            "max_session_hours": 1,
        },
        logs.append,
        lambda key, default: {"TS_AUTHKEY": "ak", "HUB_TOKEN": "tok"}.get(key, default),
    )
    assert posts == []
    assert gates == [""]  # 只有首拍一次的本地硬闸；监护多拍不再调用
    assert clock["sleeps"] == 4
    assert any(line.startswith("首拍未注册") for line in logs)
    assert not [line for line in logs if line.startswith("注册失败")]


def test_dead_tunnel_process_rebuilds_and_reregisters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """2026-11-08 现场：cloudflared 静默死、URL 字串不变 ⇒ 必须重建 + 重注册。"""
    code_dir = tmp_path / "code"
    entry = code_dir / "tools" / "agent" / "sampler-agent.ts"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fake\n", encoding="utf-8")
    clock = {"now": 1000.0, "sleeps": 0}
    logs: list[str] = []
    posts: list[str] = []
    built: list[str] = []
    tunnel = _Proc(None)  # 首拍拿到时还活着，注册成功后静默死掉（现场形态）
    ensured = iter(
        [
            ("https://dead.trycloudflare.com", tunnel),
            ("https://live.trycloudflare.com", _Proc(None)),
        ]
    )

    def _ensure(*_a: object, **_k: object) -> tuple[str, object]:
        url, proc = next(ensured)
        built.append(url)
        return url, proc

    def _register(**kw: object) -> dict:
        posts.append(str(kw["url"]))
        tunnel.rc = 7  # 注册成功后隧道进程死掉
        return {
            "ok": True,
            "status": 200,
            "action": "updated",
            "body": {},
            "capabilities": [],
            "error": None,
            "kind": "ok",
        }

    def _time() -> float:
        return clock["now"]

    def _sleep(sec: float) -> None:
        clock["sleeps"] += 1
        clock["now"] += sec
        if clock["sleeps"] >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(rn, "probe_cloud_env", lambda: "test")
    monkeypatch.setattr(rn, "resolve_link", lambda *_a: rn.LINK_CLOUDFLARED)
    monkeypatch.setattr(rn, "_slot_count", lambda _n: (1, "test"))
    monkeypatch.setattr(rn, "registry_url", lambda *_a: "http://hub:8787")
    monkeypatch.setattr(rn, "_ensure_bun", lambda *_a: "/bin/bun")
    monkeypatch.setattr(rn, "_net_probe", lambda _log: ("inherit", "direct"))
    monkeypatch.setattr(rn, "_ensure_cloudflared_url", _ensure)
    monkeypatch.setattr(rn, "_start_agent", lambda *_a: _Proc(None))
    monkeypatch.setattr(rn, "_wait_auth", lambda *_a, **_k: "ak")
    monkeypatch.setattr(rn, "_ping", lambda *_a, **_k: {"codeHash": "0123456789abcdef"})
    monkeypatch.setattr(rn, "_tunnel_url", lambda _p: "")
    monkeypatch.setattr(rn, "_sh", lambda *_a, **_k: SimpleNamespace(stdout="1.4.4"))
    monkeypatch.setattr(rn, "_try_register_once", _register)
    monkeypatch.setattr(rn, "unregister", lambda *_a, **_k: {"ok": True, "error": None})
    monkeypatch.setattr(rn.time, "time", _time)
    monkeypatch.setattr(rn.time, "sleep", _sleep)

    rn.run(
        {
            "code_dir": str(code_dir),
            "link": "cloudflared",
            "node_id": "n",
            "hub_url": "http://hub:8787",
            "hub_token": "tok",
            "agent_port": 8443,
            "workers": 1,
            "max_session_hours": 1,
        },
        logs.append,
        lambda key, default: {"HUB_TOKEN": "tok"}.get(key, default),
    )

    assert built == [
        "https://dead.trycloudflare.com",
        "https://live.trycloudflare.com",
    ]
    assert posts == [
        "https://dead.trycloudflare.com",
        "https://live.trycloudflare.com",
    ]


def test_cloudflared_dead_process_triggers_restart(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    procs = [_Proc(1), _Proc(None)]
    starts: list[object] = []

    def _start(*_a: object, **_k: object) -> _Proc:
        p = procs[len(starts)]
        starts.append(p)
        return p

    monkeypatch.setattr(rn, "_start_cloudflared", _start)
    monkeypatch.setattr(
        rn,
        "_wait_tunnel_url",
        lambda *_a, **_k: "" if len(starts) == 1 else "https://b.trycloudflare.com",
    )
    monkeypatch.setattr(rn, "_reset_cf_log", lambda *_a: None)
    monkeypatch.setattr(rn, "_dead_reason", lambda *_a: "x")
    monkeypatch.setattr(rn, "_dump_tail", lambda *_a, **_k: None)
    monkeypatch.setattr(rn.time, "sleep", lambda *_a: None)

    url, cfp = rn._ensure_cloudflared_url(
        {}, 8443, tmp_path / "cf.log", lambda _m: None, attempts=3
    )
    assert url == "https://b.trycloudflare.com"
    assert cfp is procs[1] and len(starts) == 2


def test_dead_url_is_not_accepted_even_when_log_has_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """B5：日志先写出 URL、进程随即退出 ⇒ 该 URL 不得采用，继续下一 attempt。"""
    procs = [_Proc(1), _Proc(None)]
    starts: list[object] = []
    urls = iter(["https://dead.trycloudflare.com", "https://live.trycloudflare.com"])

    def _start(*_a: object, **_k: object) -> _Proc:
        p = procs[len(starts)]
        starts.append(p)
        return p

    monkeypatch.setattr(rn, "_start_cloudflared", _start)
    monkeypatch.setattr(rn, "_wait_tunnel_url", lambda *_a, **_k: next(urls))
    monkeypatch.setattr(rn, "_reset_cf_log", lambda *_a: None)
    monkeypatch.setattr(rn, "_dead_reason", lambda *_a: "x")
    monkeypatch.setattr(rn, "_dump_tail", lambda *_a, **_k: None)
    monkeypatch.setattr(rn.time, "sleep", lambda *_a: None)

    url, cfp = rn._ensure_cloudflared_url(
        {}, 8443, tmp_path / "cf.log", lambda _m: None, attempts=2
    )
    assert url == "https://live.trycloudflare.com"
    assert cfp is procs[1] and len(starts) == 2


def test_cloudflared_retry_is_bounded_and_ladder_returns_to_preferred(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    starts: list[dict] = []
    sleeps: list[float] = []

    def _start(*_a: object, **kw: object) -> _Proc:
        starts.append(dict(kw))
        return _Proc(1)

    monkeypatch.setattr(rn, "_start_cloudflared", _start)
    monkeypatch.setattr(rn, "_wait_tunnel_url", lambda *_a, **_k: "")
    monkeypatch.setattr(rn, "_reset_cf_log", lambda *_a: None)
    monkeypatch.setattr(rn, "_dead_reason", lambda *_a: "x")
    monkeypatch.setattr(rn, "_dump_tail", lambda *_a, **_k: None)
    monkeypatch.setattr(rn.time, "sleep", lambda s: sleeps.append(s))

    url, cfp = rn._ensure_cloudflared_url(
        {},
        8443,
        tmp_path / "cf.log",
        lambda _m: None,
        attempts=3,
        postures=("inherit", "direct"),
    )
    assert url == "" and cfp is None and len(starts) == 3
    assert [s["env_posture"] for s in starts] == ["inherit", "direct", "inherit"]
    assert [s["protocol"] for s in starts] == ["http2", "http2", None]
    assert [s["debug"] for s in starts] == [False, False, True]
    assert sleeps == [5.0, 10.0]


def test_cf_log_is_reset_between_attempts(tmp_path: Path) -> None:
    cf = tmp_path / "cf.log"
    cf.write_text("https://a.trycloudflare.com", encoding="utf-8")
    rn._reset_cf_log(cf, 1)
    assert cf.read_text(encoding="utf-8") == ""
    assert (tmp_path / "cf.log.1").read_text(encoding="utf-8").startswith(
        "https://a.trycloudflare.com"
    )

    missing = tmp_path / "missing.log"
    rn._reset_cf_log(missing, 2)
    assert missing.read_text(encoding="utf-8") == ""


def test_dead_reason_extracted_from_log(tmp_path: Path) -> None:
    cf = tmp_path / "cf.log"
    cf.write_text(
        '{"level":"info","message":"Requesting new quick Tunnel..."}\n'
        '{"level":"error","message":"failed to dial: no such host"}\n',
        encoding="utf-8",
    )
    assert rn._dead_reason(cf) == (
        '{"level":"error","message":"failed to dial: no such host"}'
    )

    cf.write_text('{"level":"info","message":"still connecting"}\n', encoding="utf-8")
    assert rn._dead_reason(cf) == "未能从日志提取"


def test_net_probe_uses_proxy_aware_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rn, "_probe_https", lambda posture, **_k: posture == "inherit")
    monkeypatch.setattr(rn, "_probe_tcp", lambda *_a, **_k: True)
    logs: list[str] = []
    postures = rn._net_probe(logs.append)
    assert postures == ("inherit", "direct")
    assert logs == [
        "预检: api.trycloudflare.com:443 inherit=ok direct=fail ｜ 边缘7844 tcp=ok",
        "⇒ 采用 inherit 姿势",
    ]


def test_net_probe_falls_back_to_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rn, "_probe_https", lambda posture, **_k: posture == "direct")
    monkeypatch.setattr(rn, "_probe_tcp", lambda *_a, **_k: True)
    logs: list[str] = []
    postures = rn._net_probe(logs.append)
    assert postures == ("direct", "inherit")
    assert logs == [
        "预检: api.trycloudflare.com:443 inherit=fail direct=ok ｜ 边缘7844 tcp=ok",
        "⇒ inherit 不通、direct 可达 ⇒ 先试 direct 姿势",
    ]


def test_net_probe_reports_unreachable_but_still_returns_postures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rn, "_probe_https", lambda *_a, **_k: False)
    monkeypatch.setattr(rn, "_probe_tcp", lambda *_a, **_k: False)
    logs: list[str] = []
    postures = rn._net_probe(logs.append)
    assert postures == ("inherit", "direct")
    assert logs == [
        "预检: api.trycloudflare.com:443 inherit=fail direct=fail ｜ 边缘7844 tcp=fail",
        "⇒ 公网不可达：api.trycloudflare.com:443 两种姿势都不通（检查出网/DNS）；"
        "仍按 inherit 尝试",
    ]


def test_cf_cmd_carries_compat_flags(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict = {}

    def _popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return object()

    monkeypatch.setattr(rn, "_find_cloudflared", lambda *_a, **_k: "/bin/cloudflared")
    monkeypatch.setattr(rn.subprocess, "Popen", _popen)
    assert rn._start_cloudflared({}, 8443, tmp_path / "cf.log", lambda _m: None) is not None
    assert captured["cmd"] == [
        "/bin/cloudflared",
        "tunnel",
        "--url",
        "http://localhost:8443",
        "--no-autoupdate",
        "--protocol",
        "http2",
        "--edge-ip-version",
        "4",
        "--logfile",
        str(tmp_path / "cf.log"),
    ]
    assert captured["env"] is None


def test_cf_cmd_direct_posture_uses_child_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict = {}

    def _popen(cmd, **kw):
        captured["env"] = kw.get("env")
        return object()

    monkeypatch.setenv("HTTP_PROXY", "http://platform:3128")
    monkeypatch.setattr(rn, "_find_cloudflared", lambda *_a, **_k: "/bin/cloudflared")
    monkeypatch.setattr(rn.subprocess, "Popen", _popen)
    rn._start_cloudflared(
        {}, 8443, tmp_path / "cf.log", lambda _m: None, env_posture="direct"
    )
    env = captured["env"]
    assert env is not None
    assert env.get("HTTP_PROXY") is None
    assert env["NO_PROXY"].split(",")[0] == "localhost"
    assert os.environ["HTTP_PROXY"] == "http://platform:3128"


def test_open_tailscale_returns_url_and_never_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rn.tailscale_boot,
        "ensure",
        lambda _cfg, _log: {"ip": "100.64.0.5", "mode": "userspace"},
    )
    assert rn._open_tailscale({}, "ak", 8443, lambda _m: None) == (
        "http://100.64.0.5:8443",
        True,
    )

    def _boom(_cfg, _log):
        raise RuntimeError("no tailnet")

    monkeypatch.setattr(rn.tailscale_boot, "ensure", _boom)
    assert rn._open_tailscale({}, "ak", 8443, lambda _m: None) == ("", False)
# ---------------------------------------------------------------- 持久二进制缓存

def _fake_exe(path: Path) -> Path:
    path.write_bytes(b"fake-binary")
    path.chmod(0o755)
    return path


@pytest.mark.parametrize(
    ("root", "want"),
    [
        ("/mnt/workspace", "/mnt/workspace/.battle-bin"),
        ("/home/aistudio", "/home/aistudio/.battle-bin"),
        ("/kaggle/working", "/kaggle/working/.battle-bin"),
        ("/content", "/content/.battle-bin"),
    ],
)
def test_bin_cache_dir_autodetects_platform_root(root: str, want: str) -> None:
    """零配置：各平台探测自己的持久根（DSW/AI Studio/Kaggle/Colab），落 `.battle-bin/`。"""
    assert rn._bin_cache_dir({}, isdir=lambda p: p == root) == Path(want)


def test_bin_cache_dir_priority_dsw_first() -> None:
    """多个持久根并存时按 PERSISTENT_ROOTS 优先级（DSW 的 NAS 最持久，排第一）。"""
    got = rn._bin_cache_dir({}, isdir=lambda p: p in ("/content", "/mnt/workspace"))
    assert got == Path("/mnt/workspace/.battle-bin")


def test_bin_cache_dir_none_when_no_persistent_root() -> None:
    """没有持久根（本机/普通容器）⇒ None ⇒ 行为与无缓存时完全一致。"""
    assert rn._bin_cache_dir({}, isdir=lambda _p: False) is None


def test_bin_cache_dir_explicit_cfg_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATTLE_BIN_CACHE", "/env/cache")
    got = rn._bin_cache_dir({"bin_cache_dir": "/cfg/cache"}, isdir=lambda _p: True)
    assert got == Path("/cfg/cache")


def test_bin_cache_dir_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATTLE_BIN_CACHE", "/env/cache")
    assert rn._bin_cache_dir({}, isdir=lambda _p: False) == Path("/env/cache")


def test_cached_bin_reuses_valid_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    bun = _fake_exe(cache / "bun")
    monkeypatch.setattr(
        rn, "_sh", lambda *a, **k: SimpleNamespace(returncode=0, stdout="1.4.4", stderr="")
    )
    assert rn._cached_bin(cache, "bun", lambda _m: None) == str(bun)


def test_cached_bin_rejects_bad_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """坏档/架构不符（--version 非 0）⇒ 忽略缓存，让调用方重下。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    _fake_exe(cache / "bun")
    monkeypatch.setattr(
        rn, "_sh", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="")
    )
    logs: list[str] = []
    assert rn._cached_bin(cache, "bun", logs.append) == ""
    assert any("校验失败" in m for m in logs)


def test_cached_bin_skips_non_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "bun").write_bytes(b"x")
    monkeypatch.setattr(rn.os, "access", lambda *a: False)
    calls: list[Any] = []

    def _rec(cmd, **_kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rn, "_sh", _rec)
    assert rn._cached_bin(cache, "bun", lambda _m: None) == ""
    assert calls == [], "不可执行就不该跑 --version"


def test_cached_bin_none_cache_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a, **_k):
        pytest.fail("无缓存时不该跑子进程")

    monkeypatch.setattr(rn, "_sh", _boom)
    assert rn._cached_bin(None, "bun", lambda _m: None) == ""


def test_store_bin_copies_into_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    src = _fake_exe(tmp_path / "bun-src")
    rn._store_bin(cache, "bun", src, lambda _m: None)
    dest = cache / "bun"
    assert dest.is_file()
    assert dest.read_bytes() == src.read_bytes()


def test_store_bin_none_cache_noop(tmp_path: Path) -> None:
    src = _fake_exe(tmp_path / "bun-src")
    rn._store_bin(None, "bun", src, lambda _m: None)  # 不抛
    assert not (tmp_path / "bun").exists()


def test_store_bin_missing_source_noop(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    rn._store_bin(cache, "bun", tmp_path / "nope", lambda _m: None)
    assert not (cache / "bun").exists()


def test_store_bin_same_file_skips(tmp_path: Path) -> None:
    """命中复用后源就是缓存自身 ⇒ 跳过，SameFileError 绝不冒泡。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    dest = _fake_exe(cache / "bun")
    rn._store_bin(cache, "bun", dest, lambda _m: None)
    assert dest.read_bytes() == b"fake-binary"


def test_ensure_bun_reuses_cache_and_skips_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    bun = _fake_exe(cache / "bun")
    calls: list[list[str]] = []

    def _fake_sh(cmd, **_kw):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="1.4.4", stderr="")

    monkeypatch.setattr(rn, "_sh", _fake_sh)
    # `_ensure_bun` 命中缓存会把 cache 目录前插进 PATH（生产语义）；测试也必须
    # 让 monkeypatch 负责还原，否则后续用例的 `shutil.which("bun")` 会拿到 fake bun。
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    got = rn._ensure_bun(tmp_path, {"bin_cache_dir": str(cache)}, lambda _m: None)
    assert got == str(bun)
    assert not any("bun.sh/install" in " ".join(c) for c in calls), "命中缓存就不该再下载"


def test_ensure_bun_stores_into_cache_after_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """官方安装全挂 ⇒ 镜像装成功 ⇒ 二进制落进持久缓存（下次对话免重下）。"""
    cache = tmp_path / "cache"
    fake = _fake_exe(tmp_path / "fake-bun")
    monkeypatch.setattr(
        rn, "_sh", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="")
    )
    monkeypatch.setattr(rn, "_install_bun_mirror", lambda cfg, log: str(fake))
    monkeypatch.setattr(rn, "DOWNLOAD_BACKOFF", ())
    # 同 `test_ensure_bun_reuses_cache_and_skips_download`：`_ensure_bun` 会改 PATH。
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    got = rn._ensure_bun(tmp_path, {"bin_cache_dir": str(cache)}, lambda _m: None)
    assert got == str(fake)
    assert (cache / "bun").is_file()
    assert (cache / "bun").read_bytes() == fake.read_bytes()


def test_find_cloudflared_reuses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    cf = _fake_exe(cache / "cloudflared")
    monkeypatch.setattr(
        rn, "_sh", lambda *a, **k: SimpleNamespace(returncode=0, stdout="2025.1.0", stderr="")
    )
    monkeypatch.setattr(rn.shutil, "which", lambda _n: "")
    assert rn._find_cloudflared({"bin_cache_dir": str(cache)}, lambda _m: None) == str(cf)


def test_start_agent_marks_the_process_as_supervised(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """notebook 启动链必须让 agent 交给外层监护循环重启，而不是自己 spawn detached child。"""
    monkeypatch.delenv(rn.AGENT_SUPERVISED_ENV, raising=False)
    seen: dict[str, Any] = {}

    def _popen(cmd, **kw):
        seen["cmd"] = cmd
        seen["kw"] = kw
        return object()

    monkeypatch.setattr(rn.subprocess, "Popen", _popen)
    out = rn._start_agent(
        "/bin/bun",
        tmp_path / "sampler-agent.ts",
        tmp_path,
        8443,
        2,
        {},
        tmp_path / "agent.log",
    )
    assert out is not None
    assert seen["kw"]["env"][rn.AGENT_SUPERVISED_ENV] == "1"
    assert os.environ.get(rn.AGENT_SUPERVISED_ENV) is None, "不得把标记泄漏进训练机/本进程环境"


def test_planned_restart_exit_code_is_not_a_crash() -> None:
    """75 = 受监护模式下的 /v1/restart；监护循环只按这个码识别计划内重启。"""
    assert rn.AGENT_RESTART_EXIT_CODE == 75
    assert rn.AGENT_RESTART_EXIT_CODE != 0


def test_start_cloudflared_caches_after_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    stored: list[tuple[Any, str, Any]] = []
    monkeypatch.setattr(rn, "_find_cloudflared", lambda _cfg, _log: "")
    monkeypatch.setattr(rn, "_store_bin", lambda c, name, src, log: stored.append((c, name, src)))

    def _fake_sh(cmd, **_kw):
        if "--range" in cmd:
            return SimpleNamespace(returncode=6, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(rn, "_sh", _fake_sh)
    monkeypatch.setattr(rn.os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(rn.subprocess, "Popen", lambda *a, **k: object())
    out = rn._start_cloudflared(
        {"bin_cache_dir": str(cache)}, 8443, Path("/tmp/x.log"), lambda _m: None
    )
    assert out is not None
    assert stored and stored[0][0] == cache and stored[0][1] == "cloudflared"
