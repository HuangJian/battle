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
        ("local", "relay", False, "relay"),  # 强压：节点长轮询 hub（无入站）
        ("modelscope", "RELAY", True, "relay"),  # 大小写不敏感；有 TS key 也照强压
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
    assert (
        rn.registry_url("", 0, "https://hub.example.ts.net:8443/")
        == "https://hub.example.ts.net:8443"
    )


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

    out = rn.register(
        _opener(exc=urllib.error.URLError("down")), "http://h", "tok", {}, lambda m: None
    )
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
    assert (
        node_report.output_line({"gamesDoneTotal": 1240})
        == "近30min产出 不可知（旧 agent 无 outputLine）"
    )
    assert (
        node_report.output_line({"outputLine": "   "})
        == "近30min产出 不可知（旧 agent 无 outputLine）"
    )


# ---------------------------------------------------------------- 准入计数（2026-11-15）


def test_node_online_carries_admission_counters() -> None:
    """503 归因：每轮那行 online 带 agent 的准入计数（零新增探测，ping 顺带）。

    `active` 要对照 workers 才读得懂（零余量 = `8/8`）；peakWaiting/queueFull/timedOut
    是进程累计值，相邻轮相减 = 本轮新增。用**全等**钉形状（文本断言预算只许降）。
    """
    ping = {
        "cpus": 8,
        "workers": 8,
        "admission": {
            "active": 8,
            "waiting": 3,
            "admitted": 120,
            "rejected": 0,
            "queueFull": 1,
            "timedOut": 97,
            "peakWaiting": 5,
        },
    }
    c_n, line = node_report.node_online("self", {"concurrency": 8}, ping)
    note = node_report.node_slots_note(8, 8, 8)  # 槽位注释另有它自己的用例，这里只管准入段
    assert (c_n, line) == (
        8,
        f"[dist] node self: online, concurrency=8{note}"
        " · 准入 active=8/8 waiting=3 peakWaiting=5 queueFull=1 timedOut=97",
    )


def test_node_online_omits_admission_for_old_agent() -> None:
    """旧 agent 的 ping 没有 admission 字段 ⇒ 不追加（不编数字，也不每轮刷「不可知」）。"""
    c_n, line = node_report.node_online("a97", {"concurrency": 2}, {"cpus": 4, "workers": 4})
    note = node_report.node_slots_note(2, 4, 4)
    assert (c_n, line) == (2, f"[dist] node a97: online, concurrency=2{note}")


def test_status_reads_local_agent_with_auth_and_never_raises() -> None:
    calls: list = []
    body = {"outputLine": "games=1（rollout=1 / eval=0）"}
    st = rn._status(8443, "tok", urlopen=_opener(_Resp(body), calls=calls))
    assert st == body
    assert calls[0].full_url == "http://127.0.0.1:8443/v1/status"
    assert calls[0].get_header("Authorization") == "Bearer tok"
    down = rn._status(8443, "tok", urlopen=_opener(exc=urllib.error.URLError("down")))
    assert down is None
    # 非 dict 响应体（旧/坏 agent）⇒ None（不编一个状态出来）
    odd = rn._status(8443, urlopen=_opener(_Resp([1, 2])))
    assert odd is None


def test_probe_errors_are_transport_only_not_our_own_shape_bug() -> None:
    """`_NODE_PROBE_ERRORS` 的边界（P0-7 第二十一批）：「探不活」只许是传输面的结论。

    `_ping` 的结论是「节点死了 ⇒ 重启 agent」——把 `AttributeError`（回执形状变了）也读成
    不健康就是一个重启环。`_status` 同理，后果轻些（摘要变「不可知」）。
    反向判据：把两个 `except` 改回 `Exception` ⇒ 下面两条 DID NOT RAISE。
    """
    # 判据全在**返回值**上（G5：断言表达式不出现私有名）
    for exc in (ConnectionResetError("tunnel dropped"), TimeoutError("idle"), ValueError("bad")):
        empty_status = rn._status(8443, urlopen=_opener(exc=exc))
        empty_ping = rn._ping(8443, urlopen=_opener(exc=exc))
        assert empty_status is None and empty_ping is None, exc
    not_json = rn._status(8443, urlopen=_opener(_Resp("not json")))
    assert not_json is None, "200 但 body 不是 JSON 也只能是「不可知」"

    # 我们自己的 bug：直接穿出去（两处都得抛，`_ping` 那条最贵）
    with pytest.raises(AttributeError):
        rn._status(8443, urlopen=_opener(exc=AttributeError("resp lost .read")))
    with pytest.raises(AttributeError):
        rn._ping(8443, urlopen=_opener(exc=AttributeError("resp lost .read")))


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
    assert (
        seen[1] == "磁盘 free=2706MB（warn<4096 / 熔断<2048） · 档位=warn · 拒收=否 · 距熔断 658MB"
    )
    # agent 不可达 ⇒ 两行都如实说不可知（不是零行、不是抛）
    monkeypatch.setattr(rn, "_status", lambda port, auth: None)
    seen.clear()
    rn.emit_output_summary(8443, "tok", seen.append)
    assert seen == ["近30min产出 不可知（agent 未应答）", "磁盘 free=不可知（agent 未应答）"]


# ---------------------------------------------------------------- GitHub 不通时的下载路


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
    assert node_report.disk_thresholds({"disk_floor_mb": 3000, "disk_warn_mb": 8000}) == (
        3000,
        8000,
    )


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


def test_start_agent_work_dir_env_decoupled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CFG.work_dir → 子进程 SAMPLER_WORK_DIR：显式值透传；空/auto 不设（零行为变化）。"""
    seen: list[dict[str, str]] = []

    class _P:
        def poll(self):
            return None

    def _fake_popen(_cmd: list[str], **kw: Any) -> _P:
        seen.append(kw["env"])
        return _P()

    monkeypatch.setattr(rn.subprocess, "Popen", _fake_popen)
    monkeypatch.delenv("SAMPLER_WORK_DIR", raising=False)
    for cfg, want in (
        ({"work_dir": "/dev/shm/battle-agent"}, "/dev/shm/battle-agent"),
        ({"work_dir": "  /dev/shm/battle-agent  "}, "/dev/shm/battle-agent"),
        ({"work_dir": ""}, None),
        ({"work_dir": "auto"}, None),
        ({}, None),
    ):
        rn._start_agent("bun", tmp_path / "a.ts", tmp_path, 8443, 4, cfg, tmp_path / "agent.log")
        env = seen[-1]
        assert env.get("SAMPLER_WORK_DIR") == want, cfg

    # 父进程已带 SAMPLER_WORK_DIR 时，空 CFG 不该抹掉继承值（单测重定向通道不破坏）
    monkeypatch.setenv("SAMPLER_WORK_DIR", str(tmp_path / "inherited"))
    rn._start_agent("bun", tmp_path / "a.ts", tmp_path, 8443, 4, {}, tmp_path / "agent.log")
    assert seen[-1].get("SAMPLER_WORK_DIR") == str(tmp_path / "inherited")


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
    monkeypatch.setattr(rn, "ensure_bun", lambda *_a: "/bin/bun")
    monkeypatch.setattr(rn, "_net_probe", lambda _log: ("inherit", "direct"))
    monkeypatch.setattr(rn, "_open_tailscale", lambda *_a: ("http://100.64.0.5:8443", True))
    monkeypatch.setattr(rn, "_ensure_cloudflared_url", lambda *_a, **_k: ("", None))
    monkeypatch.setattr(rn, "_start_agent", lambda *_a: _Agent())
    monkeypatch.setattr(rn, "_wait_auth", lambda *_a, **_k: "ak")
    monkeypatch.setattr(rn, "_ping", lambda *_a, **_k: {"codeHash": "0123456789abcdef"})
    monkeypatch.setattr(rn, "_proc_bun_version", lambda *_a, **_k: "1.4.4")
    monkeypatch.setattr(rn, "_try_register_once", _fake_register)
    monkeypatch.setattr(rn.time, "sleep", _interrupt)
    # 清场（`reclaim_node_id`）与注册地址无关：它会写共享 pidfile，跑完留下的记录会被
    # 下一次运行当成「上一个节点还活着」而白等（同 `test_register_uses_the_port_...`）。
    monkeypatch.setattr(rn.node_report, "reclaim_node_id", lambda *_a: 0)

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
    monkeypatch.setattr(rn, "ensure_bun", lambda *_a: "/bin/bun")
    monkeypatch.setattr(rn, "_net_probe", lambda _log: ("inherit", "direct"))
    monkeypatch.setattr(rn, "_ensure_cloudflared_url", lambda *_a, **_k: ("", None))
    monkeypatch.setattr(rn, "_start_agent", lambda *_a: _Proc(None))
    monkeypatch.setattr(rn, "_wait_auth", lambda *_a, **_k: "ak")
    monkeypatch.setattr(rn, "_ping", lambda *_a, **_k: {"codeHash": "0123456789abcdef"})
    monkeypatch.setattr(rn, "_proc_bun_version", lambda *_a, **_k: "1.4.4")
    monkeypatch.setattr(rn, "register", _register)
    monkeypatch.setattr(rn, "_try_register_once", _gate)
    monkeypatch.setattr(rn.node_report, "reclaim_node_id", lambda *_a: 0)
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
    monkeypatch.setattr(rn, "ensure_bun", lambda *_a: "/bin/bun")
    monkeypatch.setattr(rn, "_net_probe", lambda _log: ("inherit", "direct"))
    monkeypatch.setattr(rn, "_ensure_cloudflared_url", _ensure)
    monkeypatch.setattr(rn, "_start_agent", lambda *_a: _Proc(None))
    monkeypatch.setattr(rn, "_wait_auth", lambda *_a, **_k: "ak")
    monkeypatch.setattr(rn, "_ping", lambda *_a, **_k: {"codeHash": "0123456789abcdef"})
    monkeypatch.setattr(rn, "_tunnel_url", lambda _p: "")
    monkeypatch.setattr(rn, "_proc_bun_version", lambda *_a, **_k: "1.4.4")
    monkeypatch.setattr(rn, "_try_register_once", _register)
    monkeypatch.setattr(rn, "unregister", lambda *_a, **_k: {"ok": True, "error": None})
    monkeypatch.setattr(rn.node_report, "reclaim_node_id", lambda *_a: 0)
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


def test_net_probe_uses_proxy_aware_https(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 预检已下沉到 `remote/rollout_tunnel.py`（rollout_node 只 re-export）⇒ 插件目标在新所有者上，
    # 迁到之的都是两条真相。
    monkeypatch.setattr(
        "remote.rollout_tunnel.probe_https", lambda posture, **_k: posture == "inherit"
    )
    monkeypatch.setattr("remote.rollout_tunnel.probe_tcp", lambda *_a, **_k: True)
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
    monkeypatch.setattr(
        "remote.rollout_tunnel.probe_https", lambda posture, **_k: posture == "direct"
    )
    monkeypatch.setattr("remote.rollout_tunnel.probe_tcp", lambda *_a, **_k: True)
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
    monkeypatch.setattr("remote.rollout_tunnel.probe_https", lambda *_a, **_k: False)
    monkeypatch.setattr("remote.rollout_tunnel.probe_tcp", lambda *_a, **_k: False)
    logs: list[str] = []
    postures = rn._net_probe(logs.append)
    assert postures == ("inherit", "direct")
    assert logs == [
        "预检: api.trycloudflare.com:443 inherit=fail direct=fail ｜ 边缘7844 tcp=fail",
        "⇒ 公网不可达：api.trycloudflare.com:443 两种姿势都不通（检查出网/DNS）；仍按 inherit 尝试",
    ]


def test_cf_cmd_carries_compat_flags(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict = {}

    def _popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env"] = kw.get("env")
        return object()

    monkeypatch.setattr(rn, "ensure_cloudflared", lambda *_a, **_k: "/bin/cloudflared")
    monkeypatch.setattr(rn.subprocess, "Popen", _popen)
    proc = rn._start_cloudflared({}, 8443, tmp_path / "cf.log", lambda _m: None)
    assert proc is not None
    assert captured["cmd"] == [
        "/bin/cloudflared",
        "tunnel",
        "--url",
        # ★ 必须是 IPv4 回环字面量：`localhost` 会被 cloudflared（Go）解析成 `::1` 后只拨
        #   IPv6 回环，而 agent（`Bun.serve` 缺省绑 0.0.0.0）只在 IPv4 上 ⇒ origin 恒
        #   `connection refused` ⇒ CF 回 502（2026-11-19 现场：公网 10 发 8 个 502）。
        "http://127.0.0.1:8443",
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
    monkeypatch.setattr(rn, "ensure_cloudflared", lambda *_a, **_k: "/bin/cloudflared")
    monkeypatch.setattr(rn.subprocess, "Popen", _popen)
    rn._start_cloudflared({}, 8443, tmp_path / "cf.log", lambda _m: None, env_posture="direct")
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
    opened = rn._open_tailscale({}, "ak", 8443, lambda _m: None)
    assert opened == ("http://100.64.0.5:8443", True)

    def _boom(_cfg, _log):
        raise RuntimeError("no tailnet")

    monkeypatch.setattr(rn.tailscale_boot, "ensure", _boom)
    opened = rn._open_tailscale({}, "ak", 8443, lambda _m: None)
    assert opened == ("", False)


def test_start_agent_marks_the_process_as_supervised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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


def test_banner_blames_relay_not_cloudflared_when_channel_is_relay() -> None:
    """relay 模式没有 cloudflared 进程 —— 空 URL 时不能再指「看 cloudflared 日志尾部」。

    2026-11-18 DSW 现场：日志最后一段把真因指向了根本不存在的 cloudflared 日志，
    而真正的死因（hub 地址不可达）一行没提。
    """
    lines = rn._banner_lines(
        node_id="dsw",
        url="",
        auth="ak",
        workers=8,
        bun_version="1.4.4",
        code_hash="",
        hub_configured=True,
        reg_state={"url": "", "authKey": ""},
        channel=rn.LINK_RELAY,
        hub_url="https://x.trycloudflare.com",
    )
    joined = "\n".join(lines)
    assert "relay 预检" in joined and "cloudflared 日志" not in joined, joined
    assert any("x.trycloudflare.com" in ln for ln in lines), lines


# ---------------------------------------------------------------- 同一 node_id 重复进程（2026-11-18 DSW）


class _ReclaimRig:
    """清场用的小夹具：pidfile 在 tmp、信号记账、存活状态可控（不碰真进程）。"""

    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, alive: bool = True) -> None:
        monkeypatch.setattr(node_report.tempfile, "gettempdir", lambda: str(tmp_path))
        self.path = tmp_path / "battle-rollout-dsw.pid"
        self.alive = {"v": alive}
        self.signals: list[bool] = []
        self.ident = {"v": "stamp:777"}
        monkeypatch.setattr(node_report, "pid_alive", lambda _p: self.alive["v"])
        monkeypatch.setattr(node_report, "proc_identity", lambda _p: self.ident["v"])
        monkeypatch.setattr(node_report, "reclaim_signal", self._signal)
        # 别真睡 8s：这两条常量是「等对方走完 finally」的预算，用例里压成 0。
        monkeypatch.setattr(node_report, "RECLAIM_GRACE_SEC", 0.0)
        monkeypatch.setattr(node_report, "RECLAIM_POLL_SEC", 0.0)

    def _signal(self, _pid: int, *, interrupt: bool) -> bool:
        self.signals.append(interrupt)
        if interrupt:
            self.alive["v"] = False  # 收到 SIGINT 就走 finally 收线（真机行为）
        return True

    def write(self, text: str) -> None:
        self.path.write_text(text, encoding="utf-8")

    def owner_pid(self) -> int:
        pid_txt, _, _stamp = self.path.read_text(encoding="utf-8").partition("|")
        return int(pid_txt)


def test_reclaims_previous_owner_before_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """cell 重跑必须先杀干净上一个节点进程（2026-11-18 用户口径），且**先 SIGINT**：

    对方的 `except KeyboardInterrupt` 会走 finally —— unregister + 收 bun agent/cloudflared；
    SIGTERM/SIGKILL 没有这个待遇，会留下占着 agent_port 的孤儿，新实例起来就用不了。
    实现在 `common/node_report`（`rollout_node.py` 已顶在 1000 行预算上）。
    """
    rig = _ReclaimRig(tmp_path, monkeypatch)
    rig.write("4242|stamp:777")
    logs: list[str] = []
    assert node_report.reclaim_node_id("dsw", logs.append) == 4242
    assert rig.signals == [True], "只许先礼（SIGINT）：不许一上来就 SIGKILL"
    assert rig.owner_pid() == os.getpid(), "杀掉之后本进程才是 owner"
    assert len(logs) == 2, logs  # 「发现/收线」两行，不多不少


def test_hard_kills_when_the_old_node_ignores_sigint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SIGINT 被无视（卡在不可中断的 IO 里）⇒ 到点升级 SIGKILL，并如实说「已收掉」。"""
    rig = _ReclaimRig(tmp_path, monkeypatch)

    def stubborn(_pid: int, *, interrupt: bool) -> bool:
        rig.signals.append(interrupt)
        if not interrupt:
            rig.alive["v"] = False
        return True

    monkeypatch.setattr(node_report, "reclaim_signal", stubborn)
    rig.write("4242|stamp:777")
    assert node_report.reclaim_node_id("dsw", lambda _m: None) == 4242
    assert rig.signals == [True, False]


def test_never_signals_a_recycled_pid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """pid 复用：身份指纹对不上就**不动手**（给陌生进程递 SIGINT 最坏能打断无关的 kernel/训练）。"""
    rig = _ReclaimRig(tmp_path, monkeypatch)
    rig.ident["v"] = "stamp:999"  # 同一个 pid，但已是另一个进程
    rig.write("4242|stamp:777")
    logs: list[str] = []
    assert node_report.reclaim_node_id("dsw", logs.append) == 0
    assert rig.signals == []
    assert len(logs) == 1, logs
    assert rig.owner_pid() == os.getpid(), "核验不过也不妨碍本进程接管记账"


def test_legacy_bare_pid_pidfile_is_reported_not_signalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """旧格式（裸 pid、无启动戳）无从核验 ⇒ 只告警；本次启动把它重写成新格式，下一次生效。"""
    rig = _ReclaimRig(tmp_path, monkeypatch)
    rig.write("4242")
    logs: list[str] = []
    assert node_report.reclaim_node_id("dsw", logs.append) == 0
    assert rig.signals == []
    assert len(logs) == 1, logs
    pid_txt, sep, stamp = rig.path.read_text(encoding="utf-8").partition("|")
    assert sep and int(pid_txt) == os.getpid() and stamp == "stamp:777", "要迁移成 pid|身份"


def test_no_reclaim_for_own_pid_or_dead_owner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """自己的 pid（同一 kernel 重跑）/ 已死的 owner 都不该递信号，也不该刷日志。"""
    rig = _ReclaimRig(tmp_path, monkeypatch)
    logs: list[str] = []
    rig.write(f"{os.getpid()}|stamp:777")
    assert node_report.reclaim_node_id("dsw", logs.append) == 0
    rig.alive["v"] = False
    rig.write("4242|stamp:777")
    assert node_report.reclaim_node_id("dsw", logs.append) == 0
    assert rig.signals == []
    assert logs == [], logs


# ---------------------------------------------------------------- 注册地址跟随通道


def test_register_uses_the_port_the_relay_actually_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """relay 换端口之后，**注册必须打通道实际在用的那个口**（不是 `CFG.hub_url`）。

    2026-11-19 DSW 现场：443 的 TLS 被中间层按 SNI 掐，`probe_hub` 判 `port-blocked` 并把
    通道改到 `:2096`（长轮询真的在跑），而注册仍打 `CFG.hub_url` 的 `:443` ⇒ 恒
    `URLError: handshake operation timed out`，节点连续多轮「★ 未注册」。
    两件事同屏，看起来像两个故障，其实只有一个根因：注册没吃到通道的改写。

    这里走**整条 `run()`**（不是纯函数）：唯一真相是 `_open_relay` 里那一句
    `hub_url = relay.hub_url or hub_url`，它的消费方是首注册 / 240s 重注册 / 横幅 / 注销
    四处 —— 只有真跑一遍才能证明「改一处、四处都跟随」。
    """
    code_dir = tmp_path / "code"
    entry = code_dir / "tools" / "agent" / "sampler-agent.ts"
    entry.parent.mkdir(parents=True)
    entry.write_text("// fake\n", encoding="utf-8")
    posts: list[str] = []
    logs: list[str] = []

    class _Relay:
        """假通道：`supervise` 第一拍就把端口改到备用口（真实现里是 `probe_hub` 干的）。"""

        def __init__(self, **kw: Any) -> None:
            self.hub_url = str(kw["hub_url"])

        def supervise(self, _reason: str = "") -> str:
            self.hub_url = "https://hub.example.com:2096"
            return "http://127.0.0.1:8787"

        def alive(self) -> bool:
            return True

        def stop(self) -> None:
            pass

        def stats_line(self) -> str:
            return "relay fake"

    def _register(**kw: object) -> dict:
        posts.append(str(kw["hub_url"]))
        return {
            "ok": True,
            "status": 200,
            "action": "created",
            "body": {},
            "capabilities": [],
            "error": None,
            "kind": "",
        }

    unregs: list[str] = []

    def _unregister(_urlopen: object, hub_url: str, *_a: object, **_k: object) -> dict:
        unregs.append(str(hub_url))
        return {"ok": True, "error": ""}

    monkeypatch.setattr(rn, "unregister", _unregister)

    monkeypatch.setattr(rn, "probe_cloud_env", lambda: "test")
    monkeypatch.setattr(rn, "resolve_link", lambda *_a: rn.LINK_RELAY)
    monkeypatch.setattr(rn, "RelayChannel", _Relay)
    monkeypatch.setattr(rn, "_slot_count", lambda _n: (1, "test"))
    monkeypatch.setattr(rn, "registry_url", lambda *_a: "https://hub.example.com")
    monkeypatch.setattr(rn, "_ensure_bun", lambda *_a: "/bin/bun")
    monkeypatch.setattr(rn, "_start_agent", lambda *_a: _Proc(None))
    monkeypatch.setattr(rn, "_wait_auth", lambda *_a, **_k: "ak")
    monkeypatch.setattr(rn, "_ping", lambda *_a, **_k: {"codeHash": "0123456789abcdef"})
    monkeypatch.setattr(rn, "_try_register_once", _register)
    # 清场（`reclaim_node_id`）与注册地址无关：它会写共享 pidfile，跑完留下的记录会被
    # 下一次运行当成「上一个节点还活着」而白等 8s（本用例曾因此固定耗时 10.5s）。
    monkeypatch.setattr(rn.node_report, "reclaim_node_id", lambda *_a: 0)
    # 假时钟：`max_session_hours` 缺省 0 ⇒ deadline=当前；`time.time()` 的粒度（Windows 15.6ms）
    # 会让「第一拍就超时」偶尔判假。让 sleep 把时钟推过去，第二拍必 break（零真实等待）。
    clock = {"now": 1_000.0}
    monkeypatch.setattr(rn.time, "time", lambda: clock["now"])
    monkeypatch.setattr(
        rn.time, "sleep", lambda sec: clock.__setitem__("now", clock["now"] + sec + 1.0)
    )

    rn.run(
        {
            "code_dir": str(code_dir),
            "link": "relay",
            "node_id": "n",
            "hub_url": "https://hub.example.com",
            "hub_token": "tok",
            "agent_port": 8443,
            "workers": 1,
        },
        logs.append,
        lambda key, default: {"HUB_TOKEN": "tok"}.get(key, default),
    )
    assert posts == ["https://hub.example.com:2096"], posts
    # 注销（`finally`）是同一个 `hub_url` 的另一个消费方：它必须也打 2096，
    # 否则 hub 侧会按 443 的旧地址留一条僵尸节点。
    assert unregs == ["https://hub.example.com:2096"], unregs
