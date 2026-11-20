"""tests/remote/test_node_toolchain.py —— 节点工具链的公开契约。

从 `tests/remote/test_rollout_node.py` 拆出（2026-11-18，G5「建接缝」）：这些用例
断言的是 `remote/node_toolchain.py` 的**公开**函数（`ensure_bun` / `ensure_cloudflared` /
持久缓存 / 镜像候选），而不是 `rollout_node` 的私有别名。拆开之后
「工具从哪来」与「节点怎么监护」可以各自改，互不背对方的实现细节。
"""

from __future__ import annotations

import os
import platform
import shutil
import urllib.request
import zipfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from remote import node_toolchain as nt


class _Listing:
    """npmmirror 目录 JSON 的假响应（只被 `read()` 一次）。"""

    def __init__(self, text: str) -> None:
        self._b = text.encode("utf-8")

    def read(self) -> bytes:
        return self._b

    def __enter__(self) -> _Listing:
        return self

    def __exit__(self, *a: object) -> None:
        return None


def test_github_candidates_auto_try_builtin_mirrors() -> None:
    """不填任何开关：直连之后自动跟内置镜像（阿里云 DSW 现场不需要配置）。"""
    direct = "https://github.com/HuangJian/battle.git"
    got = nt.github_candidates({}, direct)
    assert got[0] == direct
    assert set(got[1:]) == {m + "/HuangJian/battle.git" for m in nt.GITHUB_MIRRORS}
    # 非 GitHub 域名不碰
    assert nt.github_candidates({}, "https://example.com/x.zip") == ["https://example.com/x.zip"]


def test_github_candidates_explicit_base_goes_first() -> None:
    """缓存了 `github_base` 时显式镜像优先、直连兜底、不重复。"""
    direct = "https://github.com/a/b"
    cfg = {"github_base": "https://mirror.example/https://github.com/"}
    assert nt.github_candidates(cfg, direct)[:2] == ["https://mirror.example/https://github.com/a/b", direct]
    got = nt.github_candidates({"github_base": "https://gh-proxy.com/https://github.com"}, direct)
    assert got.count("https://gh-proxy.com/https://github.com/a/b") == 1


def test_github_candidates_read_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_BASE", "https://mirror.example/https://github.com")
    assert (
        nt.github_candidates({}, "https://github.com/a/b")[0]
        == "https://mirror.example/https://github.com/a/b"
    )


def test_speed_probe_returns_bytes_per_second(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nt,
        "sh",
        lambda *a, **k: SimpleNamespace(returncode=0, stdout="262144 0.500", stderr=""),
    )
    assert nt.speed_probe("https://example.com/a.zip") == pytest.approx(524288.0)


def test_speed_probe_failure_is_zero(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        nt,
        "sh",
        lambda *a, **k: SimpleNamespace(returncode=6, stdout="", stderr=""),
    )
    assert nt.speed_probe("https://example.com/a.zip") == 0.0


def test_rank_fastest_orders_by_measured_speed(monkeypatch: pytest.MonkeyPatch) -> None:
    speeds = {
        "https://slow.example/a": 1_000_000.0,
        "https://fast.example/a": 9_000_000.0,
        "https://dead.example/a": 0.0,
    }
    monkeypatch.setattr(nt, "speed_probe", lambda url, **_kw: speeds[url])
    got = nt.rank_fastest([(u, u) for u in speeds], "测试", lambda _m: None)
    assert got == ["https://fast.example/a", "https://slow.example/a", "https://dead.example/a"]


def test_bun_asset_url_maps_base_to_latest_zip() -> None:
    assert nt.bun_asset_url("", "bun-linux-x64") == (
        "https://github.com/oven-sh/bun/releases/latest/download/bun-linux-x64.zip"
    )
    assert nt.bun_asset_url(
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

    monkeypatch.setattr(nt, "sh", _fake_sh)
    monkeypatch.setattr(nt, "install_bun_mirror", lambda cfg, log: "")
    monkeypatch.setattr(nt, "DOWNLOAD_BACKOFF", ())
    bun = nt.ensure_bun(
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

    monkeypatch.setattr(nt, "sh", _fake_sh)
    monkeypatch.setattr(nt, "install_bun_mirror", lambda cfg, log: "")
    monkeypatch.setattr(nt, "DOWNLOAD_BACKOFF", ())
    assert nt.ensure_bun(Path("/tmp"), {}, lambda _m: None) == ""
    installers = [c for c in calls if c["cmd"][-1].startswith("curl -fsSL https://bun.sh/install")]
    got = [c["env"]["GITHUB"] if c["env"] else None for c in installers]
    assert got == [None, *nt.GITHUB_MIRRORS] * nt.DOWNLOAD_ROUNDS


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

    monkeypatch.setattr(nt, "sh", _fake_sh)
    monkeypatch.setattr(nt, "find_cloudflared", lambda _cfg, _log: "")
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    out = nt.ensure_cloudflared({}, lambda _m: None)
    assert out == "/usr/local/bin/cloudflared"
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

    monkeypatch.setattr(nt, "sh", _fake_sh)
    monkeypatch.setattr(nt, "find_cloudflared", lambda _cfg, _log: "")
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    monkeypatch.setattr(nt, "DOWNLOAD_BACKOFF", ())
    out = nt.ensure_cloudflared({}, lambda _m: None)
    assert out == "/usr/local/bin/cloudflared"
    assert len(downloads) == 5
    assert "gh-proxy.com" in downloads[4]


def test_cloudflared_discovery_ignores_login_shell_banner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """DSW 的 `bash -lc` 会先打横幅：stdout 非空不等于找到了二进制（旧实现撞 ENAMETOOLONG）。"""
    calls: list[list[str]] = []

    def _fake_sh(cmd, **_kw):
        calls.append(list(cmd))
        return SimpleNamespace(returncode=0, stdout="╰────────┴────────╯\n", stderr="")

    monkeypatch.setattr(nt, "sh", _fake_sh)
    monkeypatch.setattr(shutil, "which", lambda _name: "")
    monkeypatch.setattr(os.path, "isfile", lambda _p: False)
    assert nt.find_cloudflared({}, lambda _m: None) == ""
    assert calls == []


def test_cloudflared_discovery_prefers_explicit_executable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`CFG.cloudflared_path` 指到可执行文件时优先用它；不可执行则不采用。"""
    cf = tmp_path / "cloudflared"
    cf.write_bytes(b"")
    monkeypatch.setattr(os.path, "isfile", lambda p: str(p) == str(cf))
    monkeypatch.setattr(os, "access", lambda p, _mode: str(p) == str(cf))
    monkeypatch.setattr(shutil, "which", lambda _name: "C:/other/cloudflared")
    assert nt.find_cloudflared({"cloudflared_path": str(cf)}, lambda _m: None) == str(cf)


def test_latest_bun_version_picks_semver_max() -> None:
    listing = (
        '[{"name":"bun-v1.4.9/"},{"name":"bun-v1.10.0/"},'
        '{"name":"bun-v1.4.4/"},{"name":"canary/"}]'
    )
    assert nt.latest_bun_version(listing) == "1.10.0"
    assert nt.latest_bun_version("[]") == ""


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
    assert nt.bun_target(machine, cpuinfo) == want


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
    assert nt.bin_cache_dir({}, isdir=lambda p: p == root) == Path(want)


def test_bin_cache_dir_priority_dsw_first() -> None:
    """多个持久根并存时按 PERSISTENT_ROOTS 优先级（DSW 的 NAS 最持久，排第一）。"""
    got = nt.bin_cache_dir({}, isdir=lambda p: p in ("/content", "/mnt/workspace"))
    assert got == Path("/mnt/workspace/.battle-bin")


def test_bin_cache_dir_none_when_no_persistent_root() -> None:
    """没有持久根（本机/普通容器）⇒ None ⇒ 行为与无缓存时完全一致。"""
    assert nt.bin_cache_dir({}, isdir=lambda _p: False) is None


def test_bin_cache_dir_explicit_cfg_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATTLE_BIN_CACHE", "/env/cache")
    got = nt.bin_cache_dir({"bin_cache_dir": "/cfg/cache"}, isdir=lambda _p: True)
    assert got == Path("/cfg/cache")


def test_bin_cache_dir_env_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BATTLE_BIN_CACHE", "/env/cache")
    assert nt.bin_cache_dir({}, isdir=lambda _p: False) == Path("/env/cache")


def test_cached_bin_reuses_valid_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    bun = _fake_exe(cache / "bun")
    monkeypatch.setattr(
        nt, "sh", lambda *a, **k: SimpleNamespace(returncode=0, stdout="1.4.4", stderr="")
    )
    assert nt.cached_bin(cache, "bun", lambda _m: None) == str(bun)


def test_cached_bin_rejects_bad_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """坏档/架构不符（--version 非 0）⇒ 忽略缓存，让调用方重下。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    _fake_exe(cache / "bun")
    monkeypatch.setattr(
        nt, "sh", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="")
    )
    logs: list[str] = []
    assert nt.cached_bin(cache, "bun", logs.append) == ""
    assert any("校验失败" in m for m in logs)


def test_cached_bin_skips_non_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    (cache / "bun").write_bytes(b"x")
    monkeypatch.setattr(os, "access", lambda *a: False)
    calls: list[Any] = []

    def _rec(cmd, **_kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(nt, "sh", _rec)
    assert nt.cached_bin(cache, "bun", lambda _m: None) == ""
    assert calls == [], "不可执行就不该跑 --version"


def test_cached_bin_none_cache_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a, **_k):
        pytest.fail("无缓存时不该跑子进程")

    monkeypatch.setattr(nt, "sh", _boom)
    assert nt.cached_bin(None, "bun", lambda _m: None) == ""


def test_store_bin_copies_into_cache(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    src = _fake_exe(tmp_path / "bun-src")
    nt.store_bin(cache, "bun", src, lambda _m: None)
    dest = cache / "bun"
    assert dest.is_file()
    assert dest.read_bytes() == src.read_bytes()


def test_store_bin_none_cache_noop(tmp_path: Path) -> None:
    src = _fake_exe(tmp_path / "bun-src")
    nt.store_bin(None, "bun", src, lambda _m: None)  # 不抛
    assert not (tmp_path / "bun").exists()


def test_store_bin_missing_source_noop(tmp_path: Path) -> None:
    cache = tmp_path / "cache"
    nt.store_bin(cache, "bun", tmp_path / "nope", lambda _m: None)
    assert not (cache / "bun").exists()


def test_store_bin_same_file_skips(tmp_path: Path) -> None:
    """命中复用后源就是缓存自身 ⇒ 跳过，SameFileError 绝不冒泡。"""
    cache = tmp_path / "cache"
    cache.mkdir()
    dest = _fake_exe(cache / "bun")
    nt.store_bin(cache, "bun", dest, lambda _m: None)
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

    monkeypatch.setattr(nt, "sh", _fake_sh)
    # `ensure_bun` 命中缓存会把 cache 目录前插进 PATH（生产语义）；测试也必须
    # 让 monkeypatch 负责还原，否则后续用例的 `shutil.which("bun")` 会拿到 fake bun。
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    got = nt.ensure_bun(tmp_path, {"bin_cache_dir": str(cache)}, lambda _m: None)
    assert got == str(bun)
    assert not any("bun.sh/install" in " ".join(c) for c in calls), "命中缓存就不该再下载"


def test_ensure_bun_stores_into_cache_after_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """官方安装全挂 ⇒ 镜像装成功 ⇒ 二进制落进持久缓存（下次对话免重下）。"""
    cache = tmp_path / "cache"
    fake = _fake_exe(tmp_path / "fake-bun")
    monkeypatch.setattr(
        nt, "sh", lambda *a, **k: SimpleNamespace(returncode=1, stdout="", stderr="")
    )
    monkeypatch.setattr(nt, "install_bun_mirror", lambda cfg, log: str(fake))
    monkeypatch.setattr(nt, "DOWNLOAD_BACKOFF", ())
    # 同 `test_ensure_bun_reuses_cache_and_skips_download`：`ensure_bun` 会改 PATH。
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    got = nt.ensure_bun(tmp_path, {"bin_cache_dir": str(cache)}, lambda _m: None)
    assert got == str(fake)
    assert (cache / "bun").is_file()
    assert (cache / "bun").read_bytes() == fake.read_bytes()


def test_find_cloudflared_reuses_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = tmp_path / "cache"
    cache.mkdir()
    cf = _fake_exe(cache / "cloudflared")
    monkeypatch.setattr(
        nt, "sh", lambda *a, **k: SimpleNamespace(returncode=0, stdout="2025.1.0", stderr="")
    )
    monkeypatch.setattr(shutil, "which", lambda _n: "")
    assert nt.find_cloudflared({"bin_cache_dir": str(cache)}, lambda _m: None) == str(cf)


def test_start_cloudflared_caches_after_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = tmp_path / "cache"
    stored: list[tuple[Any, str, Any]] = []
    monkeypatch.setattr(nt, "find_cloudflared", lambda _cfg, _log: "")
    monkeypatch.setattr(nt, "store_bin", lambda c, name, src, log: stored.append((c, name, src)))

    def _fake_sh(cmd, **_kw):
        if "--range" in cmd:
            return SimpleNamespace(returncode=6, stdout="", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(nt, "sh", _fake_sh)
    monkeypatch.setattr(os, "chmod", lambda *a, **k: None)
    out = nt.ensure_cloudflared({"bin_cache_dir": str(cache)}, lambda _m: None)
    assert out == "/usr/local/bin/cloudflared"
    assert stored and stored[0][0] == cache and stored[0][1] == "cloudflared"


def test_bun_mirror_install_swallows_transport_and_bad_zip_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_BUN_INSTALL_ERRORS` 的边界（P0-7 第二十一批）：装不上 ⇒ `""`（回落直连），但只对
    「网/盘/坏 zip」这一族；`zip` 布局假设变了（`AttributeError`）要穿出去。

    为什么值得分开：这个函数的返回值是「镜像装没装上」，调用方据此回落直连。把我们的 bug
    记成「镜像不可用」= 一个永久静默失效的优化路径（现场只会看到「镜像总是不行」）。
    """
    monkeypatch.setattr(platform, "machine", lambda: "x86_64")
    monkeypatch.setattr(nt, "bun_target", lambda *_a: "bun-linux-x64")
    logs: list[str] = []

    def fake_download(_urls, dest, _log, **_kw):
        Path(dest).write_bytes(b"not a zip")
        return True

    monkeypatch.setattr(nt, "_download_retry", fake_download)
    monkeypatch.setattr(nt, "latest_bun_version", lambda _txt: "1.2.3")
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: _Listing("bun-v1.2.3"),
    )
    installed = nt.install_bun_mirror({}, logs.append)
    assert installed == "", "坏 zip ⇒ 回落直连，不抛"
    assert len(logs) >= 1, "失败要留痕（否则现场只看到「镜像总是不行」而不知道是坏包）"

    class _BadZip:
        def __init__(self, *_a, **_k):
            raise AttributeError("zipfile API changed shape")

    monkeypatch.setattr(zipfile, "ZipFile", _BadZip)
    with pytest.raises(AttributeError):
        nt.install_bun_mirror({}, logs.append)
