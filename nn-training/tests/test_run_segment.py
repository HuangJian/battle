"""test_run_segment.py —— 半离线整段（kind=run）的 **hub 侧**：段长解析 + 发布 + 计划随 payload。

用户需求（2026-09-17）：「云机从 hub 领到训练任务（课程、初始权重、代码）后，即使本机 hub
一直失联，也能全程自主完成训练，并以 kaggle/colab 官方方式提供产物（每轮权重和指标）打包
下载。」节点侧的执行器/产物/续跑在 `tests/test_run_loop.py`（+ `tests/test_plan.py`）；本文件
钉的是**交接的那一半**——hub 必须真的能把「整段」发出去，否则节点再能干也无从领起：

  * 开关解析：`--run-iters` > `courses.<课>.run_iters` > `rl.run_iters` > 0（缺省**关**，
    历史行为逐字节不变）；等待预算同规（缺省 8h）；
  * 发布：`publish_job(kind="run", plan_bytes=…)` 把 `plan.json` 放进 **payload**（节点解包
    即得），manifest 记 `plan_sha256`；缺计划/带本地 shard 一律**拒发**（不是静默降级）；
  * 段长语义：`max_iters=n-1` ⇒ 计划覆盖 it+1..it+n-1（模板 argv 是给**下一轮**的）；
  * 幂等：同一份计划重发 ⇒ 同一 job_id（逐轮重试/进程重启后重发走的正是这条路）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.protocol import PLAN_NAME, TS_CODE_NAME, unpack_payload
from remote.hub_client import HubClientError, publish_job
from rl.cli import build_argparser
from rl.iter_job import build_iter_spec
from rl.loop_steps import (
    ROLLOUT_SRCS,
    RUN_WAIT_DEFAULT_SEC,
    _rollout_source,
    _run_segment_iters,
    _run_wait_sec,
)
from rl.plan import build_plan, dump_plan, planned_iters


def _args(**over: object) -> SimpleNamespace:
    """最小 args（与 tests/test_plan.py 同源）：够 build_rollout_cmd + 段长解析。"""
    base: dict = {
        "curriculum_stages": "",
        "curriculum_start": 4,
        "curriculum_every": 8,
        "curriculum_grow": 4,
        "seeds_per_stage": 2,
        "rotate_stages": 2,
        "stages": "0-3",
        "seed_rotate": 0,
        "seeds": "1-2",
        "total_stages": 4,
        "max_ticks": 700,
        "difficulty": "hard",
        "goal_rollout": False,
        "intent_rollout": False,
        "dodge": "",
        "course_obj": None,
        "course_frozen_bytes": None,
        "course_path": "",
        "run_iters": 0,
        "run_wait_sec": 0.0,
    }
    base.update(over)
    return SimpleNamespace(**base)


# ────────────────────────── 开关解析 ──────────────────────────


def test_segment_iters_defaults_to_off() -> None:
    """缺省 0 = 关（历史行为逐字节不变）；无课程也无配置 → 0。"""
    assert _run_segment_iters(_args()) == 0


def test_segment_iters_cli_wins_over_config() -> None:
    """CLI > courses.<课> > rl.*：显式给的值不许被 rl-config 覆盖。"""
    with patch("rl.loop_transport.dist_common") as dc:
        dc.load_dist_config.return_value = {"courses": {"x1": {"run_iters": 9}}, "rl": {"run_iters": 5}}
        assert _run_segment_iters(_args(run_iters=2, course_path="curricula/x1.jsonc")) == 2


def test_segment_iters_reads_course_then_rl() -> None:
    """课程键优先于 rl.*（与 rollout_src 同口径）；配置读不到 → 关（不炸训练）。"""
    from train.loop_util import course_key_from_path

    stem = course_key_from_path("curricula/x1.jsonc")
    with patch("rl.loop_transport.dist_common") as dc:
        dc.load_dist_config.return_value = {"courses": {stem: {"run_iters": 6}}, "rl": {"run_iters": 3}}
        assert _run_segment_iters(_args(course_path="curricula/x1.jsonc")) == 6
        dc.load_dist_config.return_value = {"courses": {}, "rl": {"run_iters": 3}}
        assert _run_segment_iters(_args(course_path="curricula/x1.jsonc")) == 3
        dc.load_dist_config.side_effect = OSError("no cfg")
        assert _run_segment_iters(_args(course_path="curricula/x1.jsonc")) == 0


def test_rollout_src_run_is_a_declared_source() -> None:
    """`run`（离线模式的机器侧写法）必须在来源枚举里——否则配置被静默读成 `local`。

    这正是「云机在跑」与「本机在跑」看起来一样的那类静默分叉：`_rollout_source` 对**未知**
    值一律回落 local（历史容忍），所以枚举少一个值 = 配置项静默失效。
    """
    assert "run" in ROLLOUT_SRCS
    assert _rollout_source(_args(rollout_src="run")) == "run"
    assert _rollout_source(_args(rollout_src="node")) == "node"
    # 显式 CLI 给了垃圾值 ⇒ 响亮拒跑（不许静默退化）
    with pytest.raises(SystemExit, match="未知 --rollout-src"):
        _rollout_source(_args(rollout_src="cloud"))
    # 配置里给了垃圾值 ⇒ 容忍成 local（旧行为逐字节不变：配置写错不该炸训练）
    with patch("rl.loop_transport.dist_common") as dc:
        dc.load_dist_config.return_value = {"courses": {"x1": {"rollout_src": "cloud"}}}
        assert _rollout_source(_args(rollout_src="auto", course_path="curricula/x1.jsonc")) == "local"


def test_cli_accepts_rollout_src_run() -> None:
    """命令行也必须收 `run`（argparse choices 与 ROLLOUT_SRCS 同源；否则控制台写了就拒启）。"""
    ns = build_argparser("rl", {}).parse_args(["--rollout-src", "run"])
    assert ns.rollout_src == "run"
    with pytest.raises(SystemExit):
        build_argparser("rl", {}).parse_args(["--rollout-src", "cloud"])


def test_segment_wait_sec_default_and_override() -> None:
    """等待上限：CLI > rl.run_wait_sec > 缺省 8h（整段墙钟量级，不是 30min）。"""
    assert _run_wait_sec(_args()) == RUN_WAIT_DEFAULT_SEC
    assert _run_wait_sec(_args(run_wait_sec=60.0)) == 60.0
    with patch("rl.loop_transport.dist_common") as dc:
        dc.load_dist_config.return_value = {"rl": {"run_wait_sec": 7200}}
        assert _run_wait_sec(_args()) == 7200.0
        # CLI 仍压过配置
        assert _run_wait_sec(_args(run_wait_sec=11.0)) == 11.0


def test_segment_plan_range_matches_declared_length() -> None:
    """`max_iters=n-1` ⇒ 覆盖 it+1..it+n-1：段长 n 的语义必须与 `planned_iters` 对得上。"""
    plan = build_plan(_args(), it=3, iters_total=20, rotate_seed=7, max_iters=4, log=lambda _m: None)
    assert plan["end_it"] == 7
    assert planned_iters(plan) == [4, 5, 6, 7]
    # n<0（跑到课程末尾）：max_iters=0 → end_it = iters_total
    whole = build_plan(_args(), it=3, iters_total=9, rotate_seed=7, log=lambda _m: None)
    assert whole["end_it"] == 9


def test_build_iter_spec_node_label_marks_autonomous_shards() -> None:
    """半离线段的 shard 打 `run` 标签（逐轮上云是 `node`）——事后按 shard 可溯源。"""
    pairs = [(0, 1), (2, 3)]
    run_spec = build_iter_spec(_args(), pairs, wver="w" * 64, node_label="run")
    iter_spec = build_iter_spec(_args(), pairs, wver="w" * 64)
    for spec, label in ((run_spec, "run"), (iter_spec, "node")):
        for argv in spec["argv"]:
            assert argv[argv.index("--node-label") + 1] == label
        assert spec["wver"] == "w" * 64


# ────────────────────────── 发布：计划随 payload 走 ──────────────────────────


def _publish(tmp_path: Path, *, n: int = 3, it: int = 1, **over: object) -> tuple[dict, bytes]:
    """发布一个 kind=run job，返回 (manifest, plan 字节)。"""
    tmp_path.mkdir(parents=True, exist_ok=True)  # 子目录（拒发用例用 tmp_path/"a" 之类）
    args = _args()
    plan = build_plan(args, it=it, iters_total=it + n, rotate_seed=99, max_iters=n - 1, log=lambda _m: None)
    plan_bytes = dump_plan(plan)
    w = tmp_path / "init_weights.json"
    w.write_text('{"format":"nn-weights-json","params":{}}', encoding="utf-8")
    ts = tmp_path / TS_CODE_NAME
    ts.write_bytes(b"PK\x03\x04" + b"ts-bytes" * 10)
    spec = build_iter_spec(args, [(0, 1)], wver="w" * 64, node_label="run")
    kw: dict = dict(
        job_root=tmp_path / "jobs",
        jsonl_path=tmp_path / "run.jsonl",
        run_id="r",
        it=it,
        traj_dir=tmp_path / "traj",
        shard_dirs=[],
        commit="c" * 40,
        code_sha256="z" * 64,
        course='{"reward":{"formula":"score"}}',
        course_fp="f" * 64,
        corpus_fp="",
        reward_formula="score",
        formula_hash="h" * 40,
        metrics_version=1,
        gamma=0.995,
        lam=0.95,
        mode="per-tick",
        epochs=1,
        mb=512,
        lr=3e-4,
        init_weights_path=str(w),
        kind="run",
        rollout_spec=spec,
        ts_code_sha256="t" * 64,
        ts_code_zip_path=ts,
        plan_bytes=plan_bytes,
        log=lambda _m: None,
    )
    kw.update(over)
    (tmp_path / "run.jsonl").write_text("", encoding="utf-8")
    m = publish_job(**kw)
    return m, plan_bytes


def test_publish_run_carries_plan_in_payload(tmp_path: Path) -> None:
    """kind=run：manifest 记 kind/plan_sha256/rollout/ts_code，**payload 带 plan.json 原件**。"""
    import hashlib

    m, plan_bytes = _publish(tmp_path)
    assert m["kind"] == "run"
    assert m["plan_sha256"] == hashlib.sha256(plan_bytes).hexdigest()
    assert m["ts_code_sha256"] == "t" * 64
    assert m["rollout"]["wver"] == "w" * 64
    # 节点侧要的就是「job 目录里的 plan.json」，所以验到字节
    dest = tmp_path / "unpacked"
    dest.mkdir()
    payload = tmp_path / "jobs" / m["job_id"] / "payload.tar.xz"
    unpack_payload(payload, dest)
    assert (dest / PLAN_NAME).read_bytes() == plan_bytes
    assert (dest / "init_weights.json").exists()  # 起点权重也必须在（节点要它跑 rollout）


def test_publish_run_is_idempotent_on_same_plan(tmp_path: Path) -> None:
    """同一份计划重发 ⇒ 同一 job_id（逐轮重试 / 进程重启重发走的正是这条路）。"""
    m1, _ = _publish(tmp_path)
    m2, _ = _publish(tmp_path)
    assert m1["job_id"] == m2["job_id"]


def test_publish_run_requires_plan_and_rollout_spec(tmp_path: Path) -> None:
    """缺计划 / 缺本轮采集规格 = 拒发（节点要么不知道自己该跑什么，要么跑不了本轮的局）。"""
    with pytest.raises(HubClientError) as e1:
        _publish(tmp_path / "a", plan_bytes=None)
    assert "必须带 plan_bytes" in str(e1.value)
    with pytest.raises(HubClientError) as e2:
        _publish(tmp_path / "b", rollout_spec=None)
    assert "必须带 rollout_spec" in str(e2.value)


def test_publish_run_rejects_local_shards(tmp_path: Path) -> None:
    """带本地 shard = 双份采集（节点自己会让 rollout 现产）——拒发。"""
    sd = tmp_path / "shards" / "rl_s0_seed1"
    sd.mkdir(parents=True)
    (sd / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(HubClientError) as e:
        _publish(tmp_path / "c", shard_dirs=[sd])
    assert "不接受本地 shard" in str(e.value)


def test_publish_run_needs_ts_code_sha(tmp_path: Path) -> None:
    """TS 运行时 sha 缺失 → 节点定位不到运行时（与 kind=iter 同门）。"""
    with pytest.raises(HubClientError) as e:
        _publish(tmp_path / "d", ts_code_sha256="")
    assert "ts_code_sha256" in str(e.value)


def test_published_manifest_accepted_by_protocol(tmp_path: Path) -> None:
    """发布的 manifest 自己过得了 `normalize_manifest`（kind=run 的必填三件套齐）。"""
    from common.protocol import normalize_manifest

    m, _ = _publish(tmp_path)
    again = normalize_manifest(json.loads(json.dumps(m)))
    assert again["kind"] == "run" and again["plan_sha256"] == m["plan_sha256"]
