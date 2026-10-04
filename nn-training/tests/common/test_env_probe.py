"""tests/common/test_env_probe.py —— 环境/连接判据的纯函数契约（plan/worker-name-readable W0）。

钉住的东西（评审修订 R2/R4 后）：

  * 环境 = **平台包可导入性**，顺序 `kaggle → colab → aistudio → local` 写死；
  * **只 import、不调用 getter**（不碰 `get_secret` / `userdata.get`）；
  * ImportError 与非 ImportError（坏包）一律否定且**绝不抛**；
  * 链接三态：offline ⇒ `o`；`100.64/10` / `.ts.net` ⇒ `t`；其余（含空/垃圾）⇒ `c`；
  * `local` 不带 link 段。

全部用例走注入（假 importer / 假 exists），不真 import 平台包、不碰文件系统。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pytest

from common.env_probe import (
    ENV_AISTUDIO,
    ENV_COLAB,
    ENV_KAGGLE,
    ENV_LOCAL,
    LINK_CLOUDFLARED,
    LINK_OFFLINE,
    LINK_TAILSCALE,
    probe_cloud_env,
    resolve_link_kind,
    worker_name,
)


class _BombModule:
    """假模块：任何属性访问都炸——证明探测**没有**调用 getter。"""

    def __getattr__(self, name: str) -> object:
        raise AssertionError(f"探测不得访问平台包属性（收到 {name!r}）")


def _importer(*available: str, broken: tuple[str, ...] = ()):
    """假 importer：`available` 可导入；`broken` 抛非 ImportError；其余 ModuleNotFoundError。"""

    def imp(name: str) -> object:
        if name in broken:
            raise RuntimeError(f"半装/坏包：{name}")
        if name in available:
            return _BombModule()
        raise ModuleNotFoundError(name)

    return imp


def _no_dirs(*_a, **_k) -> bool:
    return False


# ────────────────────────── ① 环境判据 ──────────────────────────


def test_kaggle_from_secret_client() -> None:
    assert probe_cloud_env(importer=_importer("kaggle_secrets"), exists=_no_dirs) == ENV_KAGGLE


def test_colab_from_userdata() -> None:
    assert (
        probe_cloud_env(importer=_importer("google.colab.userdata"), exists=_no_dirs) == ENV_COLAB
    )


def test_import_only_never_touches_getters() -> None:
    """平台包在 ⇒ 命中；且**从不访问包属性**（getter 一次都不调，key 存不存在无关）。"""
    assert probe_cloud_env(importer=_importer("kaggle_secrets"), exists=_no_dirs) == ENV_KAGGLE


def test_import_error_is_negative() -> None:
    assert probe_cloud_env(importer=_importer(), exists=_no_dirs) == ENV_LOCAL


def test_aistudio_when_no_platform_secret() -> None:
    def exists(path: str) -> bool:
        return path == "/home/aistudio"

    assert probe_cloud_env(importer=_importer(), exists=exists) == ENV_AISTUDIO


def test_kaggle_wins_over_colab_when_both_importable() -> None:
    """顺序写死：两者都可导入（异常镜像）⇒ `kaggle`。"""
    assert (
        probe_cloud_env(importer=_importer("kaggle_secrets", "google.colab.userdata"), exists=_no_dirs)
        == ENV_KAGGLE
    )


def test_colab_wins_over_aistudio() -> None:
    """顺序写死：colab 可导入 + `/home/aistudio` 存在 ⇒ `colab`（评审 R4）。"""
    assert (
        probe_cloud_env(
            importer=_importer("google.colab.userdata"), exists=lambda _p: True
        )
        == ENV_COLAB
    )


def test_broken_import_is_negative_and_never_raises() -> None:
    """坏包（非 ImportError）⇒ 否定、继续走下一档；绝不把异常抛给 worker。"""
    assert (
        probe_cloud_env(importer=_importer(broken=("kaggle_secrets",)), exists=_no_dirs)
        == ENV_LOCAL
    )


# ────────────────────────── ② 连接方式 ──────────────────────────


@pytest.mark.parametrize(
    ("offline", "hub_url", "want"),
    (
        (True, "http://100.64.0.1:8787", LINK_OFFLINE),
        (True, "", LINK_OFFLINE),
        (False, "http://100.64.0.1:8787", LINK_TAILSCALE),
        (False, "http://hub.tailnet.ts.net:8787/", LINK_TAILSCALE),
        (False, "100.64.0.1:8787", LINK_TAILSCALE),  # 没写 scheme 的形态
        (False, "https://abc.trycloudflare.com", LINK_CLOUDFLARED),
        (False, "http://127.0.0.1:8787", LINK_CLOUDFLARED),
        (False, "http://203.0.113.9:8787", LINK_CLOUDFLARED),
        (False, "", LINK_CLOUDFLARED),
        (False, "::garbage::", LINK_CLOUDFLARED),
    ),
)
def test_link_kinds(offline: bool, hub_url: str, want: str) -> None:
    assert resolve_link_kind(offline=offline, hub_url=hub_url) == want


# ────────────────────────── ③ 名字 ──────────────────────────


def test_name_is_env_dash_link() -> None:
    assert worker_name(ENV_KAGGLE, LINK_CLOUDFLARED) == "kaggle-c"
    assert worker_name(ENV_COLAB, LINK_TAILSCALE) == "colab-t"
    assert worker_name(ENV_AISTUDIO, LINK_OFFLINE) == "aistudio-o"


def test_local_has_no_link_suffix() -> None:
    """用户裁决 ⑥：本机就是 `local`，不带 link 段。"""
    assert worker_name(ENV_LOCAL, LINK_CLOUDFLARED) == "local"
    assert worker_name(ENV_LOCAL, LINK_TAILSCALE) == "local"
    assert worker_name(ENV_LOCAL, LINK_OFFLINE) == "local"
