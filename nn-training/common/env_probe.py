"""common/env_probe.py —— worker 身份命名的环境/连接判据（plan/worker-name-readable，2026-10-04）。

**名 = `{env}-{link}`；本机 = `local`（不带 link 段）**。两个判据都是纯函数、可注入、
零副作用——单测不真 import 平台包、不碰文件系统。

环境判据（用户口径 2026-10-03 第 ④ 条；2026-10-04 评审修订 R2）：**平台包可导入性**。

* `kaggle`  ⇐ `import kaggle_secrets` 成功；
* `colab`   ⇐ `import google.colab.userdata` 成功；
* `aistudio`⇐ `/home/aistudio` 存在（该平台没有 secret 机制）；
* `local`   ⇐ 三者都不命中。

三条纪律（都是判据能不能用的前提，改这里之前先想清楚）：

1. **只 import，不调用 getter**——`get_secret` / `userdata.get` 一次都不碰。命中与「key 有没有配」
   无关；不碰 secret 值 ⇒ 也不需要 notebook 那套绕代理上下文（`_platform_net_env` 是为**取值**
   的请求存在的，import 不发网络）。
2. **只有 ImportError 是「不在该平台」的语义来源**；任何 import 异常（坏包/半装/缺依赖）一律
   否定并吞掉——诚实未知，绝不抛。worker 的身份计算在任何环境下都必须有值。
3. **顺序写死** `kaggle → colab → aistudio → local`，先命中先算（用例钉住；Kaggle 镜像里
   `google.colab` 通常不存在，Colab 里 `kaggle_secrets` 也不存在，顺序主要是给「都装了的
   异常镜像」一个确定答案）。

连接方式（三态，运行时可判）：

* `o` offline —— `offline=True`（worker 的 `role=ROLE_OFFLINE`）；
* `t` tailscale —— hub URL host ∈ `100.64.0.0/10`（RFC 6598 CGNAT，tailnet 实际地址段）
  或域名以 `.ts.net` 结尾；
* `c` cloudflared —— 其余（含空/不可解析 URL；保守兜底）。
"""

from __future__ import annotations

import importlib
import ipaddress
import os
from collections.abc import Callable
from urllib.parse import urlsplit

#: 环境名（= 名字前缀；`local` 无 link 段）。
ENV_KAGGLE = "kaggle"
ENV_COLAB = "colab"
ENV_AISTUDIO = "aistudio"
ENV_LOCAL = "local"

#: 连接方式简写（用户裁决 2026-10-03：c/t/o）。
LINK_CLOUDFLARED = "c"
LINK_TAILSCALE = "t"
LINK_OFFLINE = "o"

#: 平台包 → 环境（顺序 = 命中优先级，先命中先算）。
_PLATFORM_MODULES: tuple[tuple[str, str], ...] = (
    (ENV_KAGGLE, "kaggle_secrets"),
    (ENV_COLAB, "google.colab.userdata"),
)

#: tailnet 地址段（RFC 6598 CGNAT）。
_TAILNET_V4 = ipaddress.ip_network("100.64.0.0/10")
#: tailnet MagicDNS 域名后缀。
_TAILNET_SUFFIX = ".ts.net"

#: AI Studio 固定家目录（无 secret 机制时的路径判据）。
_AISTUDIO_HOME = "/home/aistudio"


def _default_importer(name: str) -> object:
    return importlib.import_module(name)


def _importable(name: str, importer: Callable[[str], object]) -> bool:
    """能 import ⇒ True。ImportError 与非 ImportError（坏包）一律 False——绝不抛出。"""
    try:
        importer(name)
    except Exception:  # 判据要的是「能不能 import 到」，任何失败都不算命中
        return False
    return True


def probe_cloud_env(
    *,
    importer: Callable[[str], object] | None = None,
    exists: Callable[[str], bool] | None = None,
) -> str:
    """环境探测：`kaggle → colab → aistudio → local`（先命中先算，顺序写死 + 用例钉住）。

    `importer` / `exists` 是单测注入点：喂假 importer 的用例**不真 import 平台包**、
    不碰文件系统。默认实现 = `importlib.import_module` / `os.path.isdir`。
    """
    imp = _default_importer if importer is None else importer
    isdir = os.path.isdir if exists is None else exists
    for env, module in _PLATFORM_MODULES:
        if _importable(module, imp):
            return env
    if isdir(_AISTUDIO_HOME):
        return ENV_AISTUDIO
    return ENV_LOCAL


def _hostname_of(url: str) -> str:
    """URL → host（小写；容忍没写 scheme 的 `100.64.0.1:8787` 形态；解析不了 ⇒ 空串）。"""
    raw = (url or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "//" + raw
    try:
        return (urlsplit(raw).hostname or "").lower()
    except ValueError:
        return ""


def resolve_link_kind(offline: bool, hub_url: str) -> str:
    """连接方式：`o` offline 优先；`t` tailnet（`100.64/10` 或 `.ts.net`）；其余 `c`。"""
    if offline:
        return LINK_OFFLINE
    host = _hostname_of(hub_url)
    if host.endswith(_TAILNET_SUFFIX):
        return LINK_TAILSCALE
    try:
        if ipaddress.ip_address(host) in _TAILNET_V4:
            return LINK_TAILSCALE
    except ValueError:
        pass
    return LINK_CLOUDFLARED


def worker_name(env: str, link: str) -> str:
    """`env` + `link` → 名字。**本机 = `local`（用户裁决 ⑥：不带 link 段）**。"""
    return ENV_LOCAL if env == ENV_LOCAL else f"{env}-{link}"
