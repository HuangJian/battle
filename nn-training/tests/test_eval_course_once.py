"""test_eval_course_once — 一次性课程评估入口的**纯逻辑**单测（2026-09-19）。

覆盖三个易漂移的约定：
  1. `_stage_plan` 的种子段映射必须与 TS 侧 `buildCourseJobs` 逐值相同
     （stageLocal = g % n，seed = seed0 + g // n）——两端同语料才谈得上对拍；
  2. `_row_id` 是 `buildCourseJobs` 的 `wi * games + g` 的反解（行序对齐）；
  3. `_to_tool_row` 的 B 层行 → 工具 JSONL 契约映射（字段名/布尔化/缺列不伪造）。

节点通信与重试**不在此测**：那是 `BatchEvalRunner` + `dist_common` 的既有测试面。
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "nn-training"))


def _load():
    spec = importlib.util.spec_from_file_location(
        "eval_course_once", str(ROOT / "nn-training" / "eval_course_once.py")
    )
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


eco = _load()


def test_stage_plan_matches_ts_build_course_jobs() -> None:
    # TS: stageLocal = g % nStages ; seed = seed0 + floor(g / nStages)
    # g=0..6：s0 → 100,101,102；s1 → 100,101；s2 → 100,101（每关同一 seed 段内各取自己的那份）
    plan = eco._stage_plan(games=7, n_stages=3, seed0=100)
    assert plan == [[100, 101, 102], [100, 101], [100, 101]]
    # 局数 < 关卡数：前面的关各 1 局，后面的关为空
    assert eco._stage_plan(games=2, n_stages=4, seed0=9) == [[9], [9], [], []]


def test_row_id_is_inverse_of_game_index() -> None:
    # games=6, n=3 ⇒ g = (seed - seed0) * n + stageLocal 必须落回 0..5
    seen = set()
    for g in range(6):
        stage_local, seed = g % 3, 405000 + g // 3
        seen.add(eco._row_id(405000, 3, 6, 0, stage_local, seed))
    assert seen == set(range(6))
    # 第二个权重的 id 段偏移 = games
    assert eco._row_id(405000, 3, 6, 1, 0, 405000) == 6


def test_to_tool_row_maps_b_layer_row() -> None:
    raw = {
        "stage": 2001,
        "seed": 405002,
        "outcome": "stage_clear",
        "win": 1,
        "cleared": 1,
        "ticks": 1234,
        "kills": 7,
        "enemyHits": 21,
        "playerHits": 2,
        "playerDamageTaken": 400,
        "playerShots": 30,
        "powerUpsCollected": 3,
        "score": 0.5,
        "hitsByKind": [3, 2, 1, 1],
        "killsByKind": [3, 2, 1, 1],
        "exposureByKind": [100, 200, 300, 400],
        "firstHitKind": "basic",
        "firstKillKind": "fast",
        "killOrder": ["basic", "fast"],
        "killerKinds": [None, "basic"],
        "node": "a95",
        "ckpt_sha16": "abc123",
    }
    meta = {
        ("abc123", 2001, 405002): {
            "label": "it30",
            "weightIdx": 0,
            "stageName": "ladder-c06",
        }
    }
    row = eco._to_tool_row(raw, meta, 3, 405000, 6)
    assert row["label"] == "it30"
    assert row["stageId"] == 2001
    assert row["stageName"] == "ladder-c06"
    assert row["id"] == 7  # g = (405002-405000)*3 + 1（buildCourseJobs 的 id 反解）
    assert row["win"] is True and row["cleared"] is True  # B 层 1 → 工具契约 bool
    assert row["hitsByKind"] == [3, 2, 1, 1]
    assert row["killOrder"] == ["basic", "fast"]
    assert row["node"] == "a95"  # 来源列保留（判读「这批局谁跑的」不再靠 TS 自报）


def test_to_tool_row_does_not_fabricate_missing_columns() -> None:
    raw = {"stage": 2000, "seed": 5, "outcome": "max_ticks", "win": 0, "cleared": 0}
    meta = {("", 2000, 5): {"label": "god", "weightIdx": 0, "stageName": "s"}}
    row = eco._to_tool_row(raw, meta, 1, 5, 1)
    assert row["win"] is False and row["cleared"] is False
    assert row["hitsByKind"] == [] and row["killerKinds"] == []  # 缺列 → 空表，不伪造零值
    assert row["firstHitKind"] is None


def test_reset_run_ledgers_clears_both_ledgers(tmp_path) -> None:
    """runDir 复用时两个账本都要清（否则 provenance 会被上一轮的记录污染）。

    2026-09-19 实测：同一个 `--out` 重跑 200 局，`dist-agent-meta.jsonl` 积了 343 条
    （114 条属于上一轮的远端局），照它判读「是否降级本地」会得出反的结论。
    """
    (tmp_path / "eval_log.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "dist-agent-meta.jsonl").write_text('{"node":"mac"}\n', encoding="utf-8")
    (tmp_path / "spec.json").write_text("{}", encoding="utf-8")

    removed = eco._reset_run_ledgers(tmp_path)

    assert sorted(removed) == ["dist-agent-meta.jsonl", "eval_log.jsonl"]
    assert not (tmp_path / "eval_log.jsonl").exists()
    assert not (tmp_path / "dist-agent-meta.jsonl").exists()
    assert (tmp_path / "spec.json").exists()  # 非账本文件不动
    assert eco._reset_run_ledgers(tmp_path) == []  # 幂等：干净目录不报错


def test_oneshot_runner_uses_dedicated_weight_kind() -> None:
    """一次性评估必须走专用权重桶（`kind='eval'`），不蹭训练作业的 'rollout'。

    2026-09-19 实测事故：训练作业每轮向 'rollout' 桶 POST 新权重，节点侧按
    `WEIGHT_FILES_KEEP = 4` 收敛同 kind 文件 ⇒ 评估那份固定权重几秒内被扫掉；
    而 agent 内存桶仍说 cached ⇒ 任务子进程 ENOENT 退出、client 只见 10054，
    重试耗尽 ⇒ 单元 0/50 settled（`local_slots: 0` 时整批 0 行、exit 1）。
    独立 kind 让该 kind 下只有它一份 ⇒ 训练 churn 扫不到（真集群实测 16/16 远端）。
    """
    import inspect
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    sys.path.insert(0, str(ROOT / "nn-training"))
    from rl.batch_eval import ONESHOT_EVAL_KIND, BatchEvalRunner

    def mk(**kw) -> BatchEvalRunner:
        return BatchEvalRunner(
            "bun",
            None,
            Path("tmp/kind-probe.log"),
            SimpleNamespace(mode="per-tick", out=""),
            {},
            {"iter": 0, "batch_id": "b"},
            {"rung": "x"},
            0,
            1,
            "run",
            "epoch",
            "nn",
            None,
            "",
            **kw,
        )

    assert ONESHOT_EVAL_KIND == "eval"
    assert mk(kind=ONESHOT_EVAL_KIND).kind == "eval"
    assert mk().kind == "rollout"  # 训练循环路径的缺省不变

    # 源码契约：kind 必须**按关键字**传（它前面还有 init_sha16，位置写错会静默回落
    # 'rollout' —— 本守卫的第一版真就踩了这个坑，写进 14 号位置后行为与修复前一模一样）。
    for entry in ("eval_course_once.py", "eval_m1_once.py"):
        src = (ROOT / "nn-training" / entry).read_text(encoding="utf-8")
        assert "kind=ONESHOT_EVAL_KIND" in src, f"{entry} 未把专用 kind 交给 BatchEvalRunner"
        assert inspect.cleandoc(src).count("BatchEvalRunner(") == 1

def test_build_course_units_one_continuous_unit_per_weight(tmp_path) -> None:
    """用户 2026-09-19 第 2 条：**每个权重一个单元**、跨该权重全部关卡（不再 u0/u1/u2）。

    单元边界在 B 层是串行屏障（权重下发 → 派发 → 等全结算），4 关 = 4 段串行、每段还会
    重新 ping + 重传一次权重 ⇒ 实测日志出现「CPU 满一阵掉一阵」的锯齿。这里钉死新契约：
      * units 数 == 权重数（不是权重 × 关卡数）；
      * unit["pairs"] 覆盖全部 (关, 种子)，顺序 stage-major（与 buildCourseJobs 同序）；
      * unit["stageParams"] 逐关带 stageJson/lives/level/maxTicks/difficulty；
      * 元数据按**权重身份**分表（多权重调用下 label/weightIdx 不再互相覆盖）。
    """
    w1 = tmp_path / "w1.json"
    w1.write_text('{"a":1}', encoding="utf-8")
    w2 = tmp_path / "w2.json"
    w2.write_text('{"a":2}', encoding="utf-8")

    stages = [
        {"name": "a", "grid": ["..", ".."]},
        {"name": "b", "grid": ["..", ".."]},
        {"name": "c", "grid": ["..", ".."]},
    ]
    plan = eco._stage_plan(games=6, n_stages=3, seed0=100)
    weights = [{"label": "w1", "path": str(w1)}, {"label": "w2", "path": str(w2)}]
    units, meta = eco.build_course_units(
        weights,
        stages,
        plan,
        max_ticks=500,
        difficulty="hard",
        lives=1,
        level=2,
    )
    assert len(units) == 2, f"每权重一个单元：{len(units)}"
    assert [u["rung"] for u in units] == ["w1·all3", "w2·all3"]
    u = units[0]
    assert [p[0] for p in u["pairs"]] == [2000, 2000, 2001, 2001, 2002, 2002]  # stage-major
    assert [p[1] for p in u["pairs"]] == [100, 101, 100, 101, 100, 101]
    for si in range(3):
        sp = u["stageParams"][str(2000 + si)]
        assert sp["lives"] == 1 and sp["level"] == 2 and sp["maxTicks"] == 500
        assert sp["difficulty"] == "hard"
        assert f'"name": "{chr(ord("a") + si)}"' in sp["stageJson"]
    # 元数据按权重身份分表：两个权重对同一 (关, 种子) 各有自己的 label/weightIdx。
    k1 = eco.weight_key16({"path": str(w1)})
    k2 = eco.weight_key16({"path": str(w2)})
    assert k1 != k2 and len(k1) == 16
    assert meta[(k1, 2001, 100)] == {"label": "w1", "weightIdx": 0, "stageName": "b"}
    assert meta[(k2, 2001, 100)] == {"label": "w2", "weightIdx": 1, "stageName": "b"}
    assert len(meta) == 2 * 6  # 两权重 × (3 关 × 2 种子)，无覆盖丢失
    assert all(len(x["pairs"]) == 6 for x in units)  # 末个权重也不漏
    # god（无权重文件）：key16 与 B 层 god 分支同式，元数据同样可用
    god = eco.weight_key16({"path": ""})
    assert god.startswith("god-") and len(god) == 4 + 12
