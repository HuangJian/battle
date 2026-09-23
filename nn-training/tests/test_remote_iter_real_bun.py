"""test_remote_iter_real_bun.py — M3 验收：节点侧 rollout 与本机直跑**逐位一致**。

plan/remote-wire-remediation.plan.md §5.5①：「同 (stage, seed)、同权重，节点产出的
shard 与本地 bun rollout 逐字节一致（data_fp 相等是必要不充分，要直接 diff 文件）」。

本文件的做法比「节点 vs 本机」更严格也更便宜：**同一个 argv**（由
`rl.iter_job.build_iter_spec` 生成，与上云时发给节点的完全一样）跑两遍——
  A 直跑（subprocess + cwd=dirA）
  B 过 `run_iter_rollout`（cwd=job_dir）
然后逐文件 diff。命令都一样还一致，说明节点路径没有引入任何差异（剩下的只是导出器
自身的确定性，那由本仓既有的 determinism 纪律守着）。

⚠ 为什么不在 `e2e/` 下：那一层的契约是 **hermetic**（`e2e/conftest.py` 明写「不需要
bun / 真节点 / weights fixture」）——本文件需要真 bun + 真权重，放进 e2e 会让
「e2e 是自足层」这个说法失真。故留在 tests/ 层并显式 skip（缺 bun / 缺权重即跳过，
CI 上不会红）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import remote.iter_rollout as iter_rollout
from remote.hub_client import pack_ts_code_zip
from remote.iter_rollout import run_iter_rollout
from remote.protocol import (
    TS_CODE_NAME,
    data_fp,
    iter_expected_data_fp,
    validate_rollout_spec,
)
from rl.iter_job import build_iter_spec
from tests.subproc_util import run_utf8

REPO_ROOT = ROOT.parent
#: 每局 tick 上限——验收只要「采到 shard 并逐位可比」，不要长局（本文件要秒级跑完）。
MAX_TICKS = 120
#: 优先用的权重（形状最新、最可能被 exporter 直接吃下）；找不到就跳过本文件。
_WEIGHT_CANDIDATES = (
    "weights/bc-c4-v3/bc-c4-v3.it1.20260914-103830.json",
    "weights/bc-c4/bc-c4.it1.20260913-161311.json",
)


def _find_weights() -> Path | None:
    for rel in _WEIGHT_CANDIDATES:
        p = ROOT / rel
        if p.exists():
            return p
    # 兜底：weights/ 下任意一个 json（版本不明也无妨——两边用的是同一份）
    for p in sorted((ROOT / "weights").glob("*/*.json")):
        return p
    return None


BUN = shutil.which("bun")
WEIGHTS = _find_weights()

pytestmark = pytest.mark.skipif(
    BUN is None or WEIGHTS is None,
    reason="需要真 bun 与一份真实权重（本仓 weights/*/；缺失即跳过）",
)


def _host_native_lib_rel() -> str | None:
    """本机在 native 矩阵里的目标库（相对仓根的 posix 路径）；不在矩阵里返回 None。

    口径必须与 TS 侧一致（`process.platform` / `process.arch`）：python 的 machine()
    给的是 `AMD64`/`x86_64`/`aarch64`/`arm64`，要映射成 TS 的 `x64`/`arm64`；
    `darwin`/`linux`/`win32` 两边同名。
    """
    import platform as _pl

    machine = _pl.machine().lower()
    arch = "x64" if machine in ("amd64", "x86_64") else "arm64" if machine in ("aarch64", "arm64") else None
    if arch is None:
        return None
    plat = sys.platform  # 'win32' / 'linux' / 'darwin'（与 process.platform 同名）
    lib = {"win32": "conv_native.dll", "darwin": "conv_native.dylib"}.get(
        plat, "conv_native.so"
    )
    rel = f"src/nn/conv/prebuilt/{plat}-{arch}/{lib}"
    return rel if (REPO_ROOT / rel).exists() else None


def _rollout_args() -> SimpleNamespace:
    return SimpleNamespace(
        goal_rollout=False,
        intent_rollout=False,
        max_ticks=MAX_TICKS,
        difficulty="hard",
        dodge="",
        course_obj=None,
        course_path="",
        course_frozen_bytes=None,
        # 2026-09-19：export-rl-rollout 无 --lives-override 即响亮失败（x20 命数事故
        # 根因修复）。训练侧由课程合并恒传；本测试无课程，须显式给出。
        lives_override=1,
        player_level=0,
    )


def _shard_files(d: Path) -> dict[str, bytes]:
    return {
        f.name: f.read_bytes()
        for f in sorted(d.iterdir())
        if f.is_file()
    }


def test_node_runner_shards_byte_identical_to_direct_run(tmp_path: Path) -> None:
    assert WEIGHTS is not None
    spec = validate_rollout_spec(
        build_iter_spec(_rollout_args(), [(0, 0)], wver="WVER" * 16, workers=1, hub_bun=str(BUN))
    )
    argv = spec["argv"][0]

    # 0) TS 运行时 = **真的** 走 pack_ts_code_zip（这一跑顺带验证白名单够用：
    #    缺一个 .ts/.wasm 都会在这里炸，而不是等上云那一刻）。
    ts_zip = tmp_path / TS_CODE_NAME
    pack_ts_code_zip(REPO_ROOT, ts_zip)
    ts_root = tmp_path / "tsroot"
    with zipfile.ZipFile(ts_zip) as zf:
        zf.extractall(ts_root)
    assert (ts_root / "tools/sim/export-rl-rollout.ts").exists()
    assert (ts_root / "src/nn/conv/prebuilt/wasm/conv.wasm").exists(), \
        "wasm 内核没进 ts_code —— 云机上卷积会直接炸"
    # native 共享库同理：云机没有 clang、也不持仓库，只能靠 ts_code 带过去；
    # 漏了它不会报错，只会**静默**回落 wasm（每局 1338ms）。
    host_lib = _host_native_lib_rel()
    if host_lib is not None:
        assert (ts_root / host_lib).exists(), \
            f"native 库没进 ts_code（{host_lib}）——云机上会静默回落 wasm"

    # 两个目录各放一份同名权重（argv 里是 `--weights init_weights.json`，job 目录相对）
    dir_a = tmp_path / "direct"
    dir_b = tmp_path / "viarunner"
    for d in (dir_a, dir_b):
        d.mkdir(parents=True)
        shutil.copyfile(WEIGHTS, d / "init_weights.json")

    # A：直跑（等价于「本机 rollout」）——cwd = TS 根，job 侧路径绝化（与节点侧同一规则）
    # run_utf8：裸 text=True 在 zh-CN Windows 按 cp936 解码 bun 的 UTF-8 stdout，
    # 读线程 UnicodeDecodeError → stdout=None → assert 消息再 TypeError（§30）。
    exec_argv = iter_rollout._exec_argv(argv, dir_a)
    p = run_utf8(
        [str(BUN), *exec_argv],
        cwd=str(ts_root),
        timeout=180,
    )
    assert p.returncode == 0, (
        f"直跑失败：{(p.stdout or '')[-2000:]}\n{(p.stderr or '')[-2000:]}"
    )

    # B：过节点侧执行器（真 bun + 真 TS 树 + 真权重）
    out = run_iter_rollout(dir_b, spec, ts_dir=ts_root, log=lambda _m: None)

    # 两侧同形（`w0/rl_s0_seed0`，与本机 rollout 一致）
    shard = Path("w0") / "rl_s0_seed0"
    fa = _shard_files(dir_a / shard)
    fb = _shard_files(dir_b / shard)
    assert set(fa) == set(fb), f"文件集不同：{sorted(fa)} vs {sorted(fb)}"
    assert fa, "shard 目录为空——导出器没写盘？"
    for name in fa:
        assert fa[name] == fb[name], f"{name} 逐字节不一致（节点路径引入了差异）"

    # 声明集 = 实产集（同一个 data_fp 函数两侧各算一次）
    assert data_fp([dir_b / shard]) == iter_expected_data_fp(spec)
    assert out["report"]["shards"] == 1
    assert out["report"]["games"] == 1

    # 长驻池**真的接上了**（这是全仓唯一「真 bun + 真导出器」走池的路径）：
    # 协议漂移（serve-loop.ts 改了标记/argv 约定）会让池静默回落一次性 —— 只慢不错，
    # 所以必须在这里响亮地钉住「服务过」这个事实，否则 1.59× 会在无人察觉时消失。
    stats = out["serve_pool"]
    assert stats is not None and stats["served"] == 1, (
        f"真 bun 路径没走长驻池：{stats}（--serve 协议漂移？）"
    )

    # 报告口径与直跑一致（elapsedSec 是唯一允许不同的字段）
    rep_a = json.loads((dir_a / "w0" / "_rl_report.json").read_text(encoding="utf-8"))
    rep_b = json.loads((dir_b / "w0" / "_rl_report.json").read_text(encoding="utf-8"))
    assert rep_a.get("wver") == "WVER" * 16  # argv 里的 wver 真的进了 shard manifest
    # 记账字段 `feat`：库在包里的前提下，云机布局（cwd=解包树）必须真的用上 native ——
    # 这条同时证明「模块相对路径解析」在**搬过家的树**里成立（正是云机的情形）。
    man_a = json.loads((dir_a / shard / "manifest.json").read_text(encoding="utf-8"))
    man_b = json.loads((dir_b / shard / "manifest.json").read_text(encoding="utf-8"))
    assert man_a.get("feat") == man_b.get("feat"), "两条路径的 features 后端不一致"
    if host_lib is not None and os.environ.get("NN_NATIVE") != "0":
        assert man_b.get("feat") == "native", (
            f"云机布局下没走 native（feat={man_b.get('feat')}）—— 库在包里但加载失败？"
        )
    rep_a.pop("elapsedSec", None)
    rep_b.pop("elapsedSec", None)
    assert rep_a == rep_b
    # 节点侧聚合报告（combine_reports 的输出）必须落在单局口径上
    assert out["report"]["totalTicks"] == rep_b["totalTicks"]
    assert out["report"]["totalSamples"] == rep_b["totalSamples"]
    assert out["report"]["outcomes"] == rep_b["outcomes"]
    assert out["report"]["winRate"] == rep_b["winRate"]


def test_bun_version_reported_for_selfcheck(tmp_path: Path) -> None:
    """§5.3 启动自检：节点必须能报出 bun 版本（对账同 major.minor 的前提）。"""
    assert BUN is not None
    # 直接跑被测函数（不依赖 resolve_bun 的环境）——空串也允许（旧 bun），但必须不抛。
    assert iter_rollout.bun_version(str(BUN)) == "" or iter_rollout.bun_version(str(BUN))[0].isdigit()
