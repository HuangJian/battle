"""test_local_rollout_pool.py — 本机腿的长驻池：与逐局 spawn **逐字节同产物**（真 bun）。

为什么值得（2026-09-25）：`rl/queue_local.py` 的两条本机腿（`run_rollout` / dispatcher 的本机槽）
原先**每局** `Popen` 一个 bun——进程启动 + 模块加载 + wasm 编译 + 权重解析每局重付一次（节点侧
同款池实测 1.59×，见 `docs/nn/runtime-opt.md` §20/§21）。现在默认交给 `--serve` 长驻池。

池化的全部风险都落在「等价性」上：一个 worker 连跑多局，任何跨局残留（世界没重建、argv 送错、
stdout 串局、`_rl_report.json` 落错目录）都会表现为**跑起来了但样本不是那一局**。本文件用真 bun
+ 真导出器把这条钉死：同一轮，两条腿各跑一遍同一批 (stage, seed)——
  A `run_rollout`（池开，默认）
  B `run_rollout`（`NN_SERVE_POOL=0` ⇒ 原来的逐局 spawn）
然后逐文件 diff（shard 目录里的每一个文件，逐字节）。

⚠ 与 `test_remote_iter_real_bun.py` 同规：需要真 bun + `tests/fixtures/student-golden.json`
（零填充权重，入库）；缺任一个就**跳过**（CI 上不会红），而不是去依赖 `weights/**`（gitignore）。
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPO_ROOT = ROOT.parent
GOLDEN = REPO_ROOT / "tests" / "fixtures" / "student-golden.json"
#: `shutil.which` 的 `str | None` 不进被调函数的签名（mypy：arg-type）；空串 = 没 bun。
BUN: str = shutil.which("bun") or ""

pytestmark = pytest.mark.skipif(
    not BUN or not GOLDEN.exists(),
    reason="需要真 bun 与 tests/fixtures/student-golden.json（缺失即跳过）",
)

#: 瘦身局：本文件钉的是「池 ≡ 一次性」，不是玩法（60 tick ⇒ 6 个样本，秒级）。
MAX_TICKS = 60
PAIRS = [(0, 11), (0, 12)]


def _weights(dir_path: Path) -> str:
    """把 golden 夹具写成导出器能吃的最小权重 JSON（形状同 `zeroStudentWeights`）。"""
    g = json.loads(GOLDEN.read_text(encoding="utf-8"))
    p = dir_path / "w.json"
    p.write_text(
        json.dumps({"arch": {"kind": "student", "h": g["h"], "d": g["d"]}, "params": g["params"]}),
        encoding="utf-8",
    )
    return str(p)


def _args(workers: int = 2) -> SimpleNamespace:
    """近似训练进程的 args（只带 `build_rollout_cmd` / 池探针真正读的字段）。"""
    return SimpleNamespace(
        goal_rollout=False,
        intent_rollout=False,
        workers=workers,
        max_ticks=MAX_TICKS,
        difficulty="classic",
        dodge="",
        lives_override=1,
        player_level=None,
        course_obj=None,
        course_path="",
        course_frozen_bytes=None,
    )


def _shard_files(d: Path) -> dict[str, bytes]:
    return {f.name: f.read_bytes() for f in sorted(d.iterdir()) if f.is_file()}


@pytest.mark.time_budget(60)  # 真 bun：两条腿 × 两局 + 池冷启动
def test_pooled_run_rollout_is_byte_identical_to_per_game_spawn(tmp_path, monkeypatch) -> None:
    """池化那轮与本改动前的逐局 spawn 那轮：shard **逐文件逐字节**相同。"""
    import rl.queue_local as ql

    weights = _weights(tmp_path)

    logs: list[str] = []
    monkeypatch.setattr(ql, "log", logs.append)
    a = tmp_path / "pooled"
    a.mkdir()
    rep_a = ql.run_rollout(BUN, weights, a, list(PAIRS), _args())

    joined = "\n".join(logs)
    assert "长驻 worker 池" in joined, logs
    assert "serve_pool: served=2" in joined, logs  # 两局都真的由长驻 worker 服务

    # 回退路径（本改动前的行为）：`NN_SERVE_POOL=0` ⇒ 整轮逐局 spawn
    monkeypatch.setenv("NN_SERVE_POOL", "0")
    b = tmp_path / "oneshot"
    b.mkdir()
    rep_b = ql.run_rollout(BUN, weights, b, list(PAIRS), _args())

    for i, (stage, seed) in enumerate(PAIRS):
        shard = f"w{i}/rl_s{stage}_seed{seed}"  # 一局一个 w{idx}/（与本地 traj 布局同形）
        fa = _shard_files(a / shard)
        fb = _shard_files(b / shard)
        assert fa, f"池化那轮没产出 shard（{shard}）"
        assert set(fa) == set(fb), f"{shard} 文件集不同：{sorted(fa)} vs {sorted(fb)}"
        for name in fa:
            assert fa[name] == fb[name], f"{shard}/{name} 逐字节不一致（池化引入了差异）"

    # 报告口径也一致（池化只该省启动成本，不该动读数）
    for key in ("games", "totalSamples", "totalTicks"):
        assert rep_a[key] == rep_b[key], (key, rep_a.get(key), rep_b.get(key))


@pytest.mark.time_budget(60)
def test_pool_probe_skips_scripts_that_cannot_serve(tmp_path, monkeypatch) -> None:
    """池只按**真 argv 的脚本**建：不在 `--serve` 白名单的模式（goal/intent）连池都不起。"""
    import rl.queue_local as ql
    from remote import serve_pool

    weights = _weights(tmp_path)
    args = _args()
    args.intent_rollout = True  # → export-intent-rollout.ts（不在 SERVE_CAPABLE_SCRIPTS）
    built: list[str] = []
    real_make = serve_pool.make_pool

    def spy_make(bun, script, ts_dir, workers, log_fn, **kw):
        built.append(str(script))
        return real_make(bun, script, ts_dir, workers, log_fn, **kw)

    monkeypatch.setattr(serve_pool, "make_pool", spy_make)
    # 真跑一局太贵（intent 模式要 replan 参数）——只钉「探针按真 argv 选脚本」这一步：
    # `make_local_pool` 的返回值就是判据，脚本不在白名单时必须是 None。
    assert ql.make_local_pool(BUN, weights, tmp_path, PAIRS[0], args, "wver", 2) is None
    assert built == ["tools/sim/export-intent-rollout.ts"], built
