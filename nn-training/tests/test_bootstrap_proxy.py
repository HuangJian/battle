"""引导期凭据 / 代理环境单测（2026-09-17 Kaggle 事故回归）。

为什么有这组用例：Kaggle 上「完整引导（userspace tailscale）之后」读平台 Secrets
（HUB_TOKEN 等）必失败——userspace 出站代理只转发 Tailscale IP，公网 HTTPS 走不通；
失败又被 `secret()` 吞成空串 ⇒ `/code` 401 ⇒ 会话终结。Colab 走的是
`google.colab.userdata`（localhost 通道，被 NO_PROXY 覆盖）所以从没暴露。

覆盖面（全部 hermetic：不碰真 tailscaled / 真网络 / 真平台 Secrets）：
  * `tailscale_boot.set_proxy_env`：NO_PROXY **合并**而非覆盖；大小写双写；
  * `tailscale_boot.platform_net_env`：临时还原平台代理 / 退出后恢复 tailnet 代理；
  * `tailscale_boot.start_daemon`：引擎顺序阀门（CFG `ts_engine`）；
  * `notebook_boot.run`：★ 三个凭据必须在 `ensure()` **之前**读完（本组核心回归）；
  * `notebook_boot.run`：HUB_TOKEN 读不到时**响亮报错**（不再落到误导性的 401）。
"""

from __future__ import annotations

import inspect
import os
from pathlib import Path

import pytest

import remote.notebook_boot as nb
import remote.tailscale_boot as ts

TAILNET_HTTP = f"http://{ts.PROXY}"
TAILNET_SOCKS = f"socks5://{ts.PROXY}"


def _env(key: str) -> str | None:
    """取某变量的大小写两份之一（ruff SIM112 禁字面量小写环境变量名 → 用 key.lower()）。"""
    return os.environ.get(key) or os.environ.get(key.lower())


@pytest.fixture(autouse=True)
def _clean_proxy_env(monkeypatch: pytest.MonkeyPatch):
    """每个用例从「没有任何代理环境」出发，并清掉模块级原值快照。"""
    for k in ts.PROXY_ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    ts._ORIG_PROXY_ENV.clear()
    yield
    ts._ORIG_PROXY_ENV.clear()


# ─────────────────────────── set_proxy_env / platform_net_env ───────────────────────────


def test_set_proxy_env_merges_no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """平台原有 NO_PROXY 条目必须保留（旧实现整条覆盖 ⇒ 平台内网服务被塞进 tailnet 代理）。"""
    monkeypatch.setenv("NO_PROXY", ".kaggle.com,169.254.169.254")
    monkeypatch.setenv("HTTP_PROXY", "http://platform-proxy:3128")

    ts.set_proxy_env({"no_proxy_extra": "100.64.0.0/10"})

    assert os.environ["HTTP_PROXY"] == TAILNET_HTTP
    assert os.environ["ALL_PROXY"] == TAILNET_SOCKS
    no_proxy = os.environ["NO_PROXY"]
    for entry in (
        "localhost",
        "127.0.0.1",
        "::1",
        "100.64.0.0/10",
        ".kaggle.com",
        "169.254.169.254",
    ):
        assert entry in no_proxy, f"NO_PROXY 丢了 {entry}: {no_proxy}"
    # 小写镜像（部分工具只认小写）
    assert _env("HTTP_PROXY") == TAILNET_HTTP
    assert _env("NO_PROXY") == no_proxy


def test_platform_net_env_restores_platform_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """with 内 = 平台自己的代理（缺省则「不存在」）；with 外 = tailnet 代理照旧。"""
    monkeypatch.setenv("HTTP_PROXY", "http://platform-proxy:3128")
    ts.set_proxy_env()

    with ts.platform_net_env():
        assert os.environ["HTTP_PROXY"] == "http://platform-proxy:3128"
        assert _env("HTTP_PROXY") == "http://platform-proxy:3128"
        assert _env("ALL_PROXY") is None  # 引导前本就没有 ⇒ 还原成「没有」
    assert os.environ["HTTP_PROXY"] == TAILNET_HTTP
    assert os.environ["ALL_PROXY"] == TAILNET_SOCKS


def test_platform_net_env_restores_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    ts.set_proxy_env()
    with pytest.raises(RuntimeError), ts.platform_net_env():
        assert "HTTP_PROXY" not in os.environ
        raise RuntimeError("boom")
    assert os.environ["HTTP_PROXY"] == TAILNET_HTTP


# ─────────────────────────── 引擎顺序阀门 ───────────────────────────


class _FakeProc:
    pid = 4242
    returncode = None

    def poll(self) -> None:
        return None

    def kill(self) -> None:
        return None


def test_start_daemon_engine_order(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """`ts_engine=userspace` 跳过 kernel 尝试；默认顺序仍是 kernel 优先。"""
    monkeypatch.setattr(ts, "SOCK", str(tmp_path / "tailscaled.sock"))
    monkeypatch.setattr(ts, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(ts, "DAEMON_LOG", str(tmp_path / "tailscaled.log"))
    monkeypatch.setattr(ts, "state", lambda: "Running")
    seen: list[list[str]] = []

    def _spawn(log, argv, fd):
        seen.append(list(argv))
        Path(ts.SOCK).write_text("", encoding="utf-8")  # _alive 要求 socket 在
        return _FakeProc()

    monkeypatch.setattr(ts, "_spawn", _spawn)

    assert ts.start_daemon(lambda _m: None, "userspace") == "userspace"
    assert seen == [
        [
            "tailscaled",
            "--tun=userspace-networking",
            f"--socks5-server={ts.PROXY}",
            f"--outbound-http-proxy-listen={ts.PROXY}",
        ]
    ]

    seen.clear()
    assert ts.start_daemon(lambda _m: None) == "kernel"
    assert seen[0] == ["tailscaled"], "默认顺序必须仍是 kernel 优先"


# ─────────────────────────── run()：凭据前置（核心回归） ───────────────────────────


def _rl_cfg() -> dict:
    return {
        "mode": "rl",
        "rl_mode": "pull",
        "hub_url": "http://100.64.0.9:8787",
        "ts_authkey": "",
        "ts_ephemeral": True,
        "push_port": 8790,
    }


def test_run_reads_credentials_before_installing_tailnet_proxy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """★ 回归：引导（userspace 代理装上）之后不得再读凭据。

    旧实现里 HUB_TOKEN 在 `ensure()` 之后才读 ⇒ Kaggle 上读失败被吞成空串 ⇒ /code 401。
    这里用假 secret + 假 ensure 复刻当时的时序：任何凭据在 tailnet 代理生效后被读，断言即红。
    """
    calls: list[str] = []

    def _fake_secret(key: str, cfg_val: str = "") -> str:
        assert os.environ.get("ALL_PROXY") != TAILNET_SOCKS, (
            f"凭据 {key} 是在 tailnet 代理装上**之后**才读的 —— "
            "Kaggle 上公网 HTTPS（平台 Secrets）此时已走不通（2026-09-17 事故）"
        )
        calls.append(key)
        return {"TS_AUTHKEY": "ak", "HUB_TOKEN": "ht", "PUSH_TOKEN": "pt"}[key]

    def _fake_ensure(cfg: dict, log) -> dict:
        assert cfg["ts_authkey"] == "ak", "TS_AUTHKEY 必须在引导前就绪"
        ts.set_proxy_env(cfg)  # 模拟 userspace 落地：改写进程代理环境
        log("Tailscale IP = 100.64.0.5 (mode=userspace)")
        return {"ip": "100.64.0.5", "mode": "userspace", "sock": ts.SOCK, "proxy": ts.PROXY}

    captured: dict = {}

    def _fake_pull(cfg, log, keepalive_stop, hub, hub_tok, push_tok):
        captured.update(hub=hub, hub_tok=hub_tok, push_tok=push_tok)
        return 0

    monkeypatch.setattr(nb.tailscale_boot, "ensure", _fake_ensure)
    monkeypatch.setattr(nb, "_pull", _fake_pull)

    assert nb.run(_rl_cfg(), lambda _m: None, _fake_secret, None) == 0
    assert calls == ["TS_AUTHKEY", "HUB_TOKEN", "PUSH_TOKEN"]
    assert captured == {"hub": "http://100.64.0.9:8787", "hub_tok": "ht", "push_tok": "pt"}


def test_pull_takes_no_secret_reader() -> None:
    """`_pull` 不再持有 secret 句柄——避免顺手在引导后读凭据（签名级防回归）。"""
    params = inspect.signature(nb._pull).parameters
    assert "secret" not in params
    assert list(params) == ["cfg", "log", "keepalive_stop", "hub", "hub_tok", "push_tok"]


def test_run_fails_loudly_when_hub_token_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """HUB_TOKEN 读不到时直接点名，不再以「/code 401 — HUB_TOKEN 不一致」误导排障。"""

    def _fake_secret(key: str, cfg_val: str = "") -> str:
        return "ak" if key == "TS_AUTHKEY" else ""

    monkeypatch.setattr(
        nb.tailscale_boot,
        "ensure",
        lambda cfg, log: {
            "ip": "100.64.0.5",
            "mode": "userspace",
            "sock": ts.SOCK,
            "proxy": ts.PROXY,
        },
    )
    with pytest.raises(SystemExit) as ei:
        nb.run(_rl_cfg(), lambda _m: None, _fake_secret, None)
    assert "HUB_TOKEN 未读到" in str(ei.value)
