"""ts_code.zip 白名单门禁（2026-09-21）：native 共享库必须随包走。

为什么值得一条单测：离线训练任务的 rollout 跑在 PPO 云机上，云机**既没有 clang、也不持仓库**
（worker 只拿到 ts_code.zip 解出来的一棵树，见 remote/worker.py::_ensure_ts_code）。native
features 后端是 `src/nn/native-conv.ts` 用**模块相对路径** `native/prebuilt/<平台>/…` 找库的，
所以只要打包白名单漏了 `.dll/.so/.dylib`，云机上就是「无库可加载 ⇒ 首用 attestation 跳过 ⇒
静默回落 wasm」——不报错、只是每局回到 1338ms。这条测试就是那个静默的哨兵。

覆盖三件：
  ① 产物齐全：`manifest.json` 里登记的每个目标都在包里（且是 0755，便于 dlopen）；
  ② 白名单没有漏/溢：`.wasm` 与代表性子目录仍在；`.md/.map/node_modules` 之类不进包；
  ③ 可复现：同内容两次打包 sha 相同（云机按 sha 做内容寻址缓存，漂了就永远命中不了）；
  ④ 缺目录时**响亮**报错（带重建命令），而不是静默打出一个小包。
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest

from remote.hub_client import (
    TS_CODE_BINARY_DIRS,
    TS_CODE_BINARY_SUFFIXES,
    HubClientError,
    pack_ts_code_zip,
)

ROOT = Path(__file__).resolve().parents[2]
PREBUILT_REL = TS_CODE_BINARY_DIRS[0]


def _pack(tmp_path: Path) -> tuple[Path, str, list[str]]:
    out = tmp_path / "ts_code.zip"
    sha = pack_ts_code_zip(ROOT, out)
    with zipfile.ZipFile(out) as z:
        return out, sha, z.namelist()


def test_native_prebuilt_libs_are_packed(tmp_path: Path) -> None:
    """每个登记目标都进包，且可执行位保留（dlopen 只需要读权限，但 0755 更符合预期）。"""
    manifest = json.loads((ROOT / PREBUILT_REL / "manifest.json").read_text(encoding="utf-8"))
    targets = [t for t in manifest["targets"]]
    assert targets, "prebuilt manifest 里一个目标都没有——先跑 native-build.ts --cross"

    out, _sha, names = _pack(tmp_path)
    with zipfile.ZipFile(out) as z:
        for t in targets:
            arc = f"{PREBUILT_REL}/{t['id']}/{t['lib']}"
            assert arc in names, f"ts_code 缺 native 库：{arc}（云机上会静默回落 wasm）"
            info = z.getinfo(arc)
            assert info.file_size == t["bytes"], f"{arc} 字节数不符（被打包/传输改过？）"
            assert info.external_attr >> 16 & 0o111, f"{arc} 没有可执行位"
    # 库里只有共享库后缀，不该有别的杂物
    stray = [
        n
        for n in names
        if n.startswith(PREBUILT_REL + "/") and not n.endswith(TS_CODE_BINARY_SUFFIXES)
    ]
    assert stray == [], f"prebuilt 目录里混进了非共享库文件：{stray}"


def test_whitelist_still_covers_ts_runtime(tmp_path: Path) -> None:
    """漏一个 .ts/.wasm 云机上就炸——这条与 real_bun 的真跑互为上下半场。"""
    _out, _sha, names = _pack(tmp_path)
    for arc in (
        "tools/sim/export-rl-rollout.ts",
        "src/nn/wasm/conv_feats.wasm",
        "src/nn/native-conv.ts",  # native 后端的加载器本身也得在
        "src/nn/native-prebuilt.ts",  # 平台→目录名映射（漏了它云机上找不到库）
    ):
        assert arc in names, f"ts_code 缺 {arc}"
    # 不该进包的（噪声 / 依赖树 / 临时物）
    assert not [n for n in names if n.endswith(".md")]
    assert not [n for n in names if n.endswith(".map")]
    assert not [n for n in names if n.startswith("tools/node_modules")]
    assert not [n for n in names if "/tmp/" in n]


def test_pack_is_reproducible(tmp_path: Path) -> None:
    """内容寻址缓存的前提：同内容 → 同 sha（固定时间戳打包）。"""
    a = tmp_path / "a.zip"
    b = tmp_path / "b.zip"
    sha_a = pack_ts_code_zip(ROOT, a)
    sha_b = pack_ts_code_zip(ROOT, b)
    assert sha_a == sha_b
    assert a.read_bytes() == b.read_bytes()


def test_missing_prebuilt_dir_fails_loudly(tmp_path: Path) -> None:
    """仓里没有 prebuilt 目录（没跑 --cross）⇒ 响亮报错并给出重建命令，不打一个小包蒙混。"""
    fake = tmp_path / "repo"
    (fake / "src").mkdir(parents=True)
    (fake / "tools").mkdir(parents=True)
    with pytest.raises(HubClientError) as e:
        pack_ts_code_zip(fake, tmp_path / "out.zip")
    assert "native-build.ts --cross" in str(e.value)
