"""断点续跑：磁盘 shard 对账 + training_log.jsonl 锚点回读。"""
from __future__ import annotations

import json
from pathlib import Path


def completed_pairs(traj_dir: Path, wver: str,
                    extra_wver: str | None = None) -> set[tuple[int, int]]:
    """扫描 traj_dir 已完整落盘且 manifest.wver∈{wver, extra_wver} 的 (stage,seed)——rollout 断点。

    extra_wver：双缓冲预采快照的 wver（θ_{N,e3}）。下一轮对账时当前 args.out 已是
    θ_N（PPO 末写好），而预采首波 shard 的 wver = 快照指纹——必须双白名单，否则
    首波被当"未完成"重新派发/清场，预采白做。

    完整 shard 判定：write_shard 先写 12 npy 后写 manifest；存在 manifest.json ⇒ 目录完整。
    仅在 manifest 显式回显 stage/seed（agent 打包时回填）后才算数，否则不计入 done。
    """
    return {p for p, _m in _scan_shards(traj_dir, wver, extra_wver)}


def _scan_shards(traj_dir: Path, wver: str, extra_wver: str | None = None) -> list[tuple[tuple[int, int], Path]]:
    """扫描 traj_dir 内 manifest.wver∈{wver, extra_wver} 的完整 shard，产出 (pair, dir)。
    dir = shard 目录（含 manifest.json），stream 用它把在盘的预采首波 shard 注入训练。
    """
    res = []
    if not traj_dir.exists():
        return res
    for m in traj_dir.rglob("rl_s*_seed*/manifest.json"):
        try:
            mm = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        st, sd = mm.get("stage"), mm.get("seed")
        wv = mm.get("wver")
        if (wv != wver and wv != extra_wver) or not isinstance(st, int) or not isinstance(sd, int):
            continue
        res.append(((int(st), int(sd)), m.parent))
    return res


def resumed_manifests(traj_dir: Path, wver: str,
                      exclude: set[tuple[int, int]] | None = None,
                      only: set[tuple[int, int]] | None = None,
                      extra_wver: str | None = None) -> list[dict]:
    """收集本轮未采样（不在 exclude）且已 done（wver 匹配）shard 的单局摘要，
    重启续跑时并入聚合，使报告 games/outcomes 仍覆盖完整一轮。

    only 非空时只收计划内 (stage,seed)：跨配置断点（如 seeds_per_stage 4→3 后重启
    同一迭代）目录里会残留旧计划的同权重 shard，不滤则两个语料混进同一份报告、
    games 虚胖到 233/105（2026-08-25 it60 实测）。

    shard manifest 是单局 schema（stage/seed/nSamples/ticks/outcome/score，无
    games/totalSamples 顶层键），必须转换为 combine_reports 消费的聚合 schema；
    exclude=本轮 results 已覆盖的 (stage,seed)。此前不排除也不转换：整轮完成后
    本轮 shard 被原样并入 → combine_reports KeyError('games') 秒崩 → 主循环吞掉
    后 it+=1 静默跳轮（2026-08-24 it2/it3 根因）。
    """
    out: list[dict] = []
    if not traj_dir.exists():
        return out
    skip = exclude or set()
    for m in traj_dir.rglob("rl_s*_seed*/manifest.json"):
        try:
            mm = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        st, sd = mm.get("stage"), mm.get("seed")
        wv = mm.get("wver")
        if (wv != wver and wv != extra_wver) or not isinstance(st, int) or not isinstance(sd, int):
            continue
        if (int(st), int(sd)) in skip:
            continue
        if only is not None and (int(st), int(sd)) not in only:
            continue
        if isinstance(mm.get("totalSamples"), int) and "outcomes" in mm:
            # 远端 agent 写回的 manifest 即单局聚合报告（games=1 + outcomes/
            # totalSamples/scoreList/dimLists），combine_reports 直接可消费，透传。
            out.append(mm)
            continue
        score = mm.get("score")
        out.append({
            "games": 1,
            # stage/seed 回填：让转换产物自识别（combine_reports 忽略附加键，
            # 测试与排查按身份对账时不再依赖目录遍历顺序）。
            "stage": int(st),
            "seed": int(sd),
            "outcomes": {str(mm.get("outcome", "unknown")): 1},
            "totalSamples": int(mm.get("nSamples") or 0),
            "totalTicks": int(mm.get("ticks") or 0),
            "scoreList": [score] if isinstance(score, (int, float)) else [],
            # dims 细分维度暂不回填（单局 dims→dimLists 映射待统一 schema），只保
            # 证 games/outcomes/ticks/score 口径完整。
            "dimLists": {},
        })
    return out


def last_completed_iter(jsonl_path: Path) -> int:
    """回读日志最后一个 iteration 事件的迭代号（it 断点续跑），无则 0。"""
    last = 0
    if not jsonl_path.exists():
        return 0
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "iteration" and isinstance(e.get("iter"), int):
            last = max(last, e["iter"])
    return last


def last_rotate_seed(jsonl_path: Path) -> int | None:
    """回读日志最后一个 run_start 的 rotateSeed（课程连续性）。

    rotateSeed 决定 build_pairs 的 (stage,seed) 序列。若跨 relaunch 每次 re-roll（含时间戳），
    重启后下轮课程 seed 与已落盘局不交 ⇒ 断点续跑剔除失效 ⇒ 重跑已完成局（浪费）。续跑继承
    上一个 run_start 的 rotateSeed，使同一 traj 的训练流课程连续，断点续跑跨 relaunch 真正生效。
    """
    if not jsonl_path.exists():
        return None
    seed = None
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") == "run_start" and isinstance(e.get("rotateSeed"), int):
            seed = e["rotateSeed"]
    return seed
