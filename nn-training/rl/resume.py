"""断点续跑：磁盘 shard 对账 + training_log.jsonl 锚点回读。"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path

# P2-2（2026-09-02）：_scan_shards 目录签名缓存。run_rl 的轮询热路径每 2 秒调一次
# completed_pairs——旧实现每次都 rglob 全树 + 逐个 JSON 解析（140 局 ≈ 25 万次
# 文件读）。目录签名 = 全部 rl_s*_seed* 目录的 (相对路径, mtime) 元组：新增 shard
# 目录、或 shard 内写完 manifest（目录 mtime 更新）都会改变签名 → 缓存失效重扫；
# 签名未变时零 IO 直接复用。mtime 秒级精度足够（轮询间隔 2s）。
_SCAN_CACHE: dict[tuple, list[tuple[tuple[int, int], Path]]] = {}
_SCAN_CACHE_MAX = 512


def _dir_signature(traj_dir: Path) -> tuple:
    """traj_dir 下全部 shard 目录的 (相对路径, mtime)——检测新增/完成的 shard。"""
    sig = []
    for p in traj_dir.rglob("rl_s*_seed*"):
        if p.is_dir():
            try:
                sig.append((str(p.relative_to(traj_dir)), p.stat().st_mtime))
            except OSError:
                continue
    return tuple(sorted(sig))


def completed_pairs(
    traj_dir: Path,
    wver: str,
    extra_wver: str | None = None,
    course_fp: str | None = None,
) -> set[tuple[int, int]]:
    """扫描 traj_dir 已完整落盘且 manifest.wver∈{wver, extra_wver} 的 (stage,seed)——rollout 断点。

    extra_wver：双缓冲预采快照的 wver（θ_{N,e3}）。下一轮对账时当前 args.out 已是
    θ_N（PPO 末写好），而预采首波 shard 的 wver = 快照指纹——必须双白名单，否则
    首波被当"未完成"重新派发/清场，预采白做。

    course_fp（D14，2026-09-05）：课程文件 sha256（语料血缘）。非空时只认
    manifest.course_fp == 该值的 shard——跨课程语料绝不混入本轮（课程切换后旧课程
    shard 不参与断点对账，被当"未完成"重新派发）。None = 不过滤（旧行为逐字节不变）。

    完整 shard 判定：write_shard 先写 12 npy 后写 manifest；存在 manifest.json ⇒ 目录完整。
    仅在 manifest 显式回显 stage/seed（agent 打包时回填）后才算数，否则不计入 done。
    """
    return {p for p, _m in _scan_shards(traj_dir, wver, extra_wver, course_fp)}


def _scan_shards(
    traj_dir: Path,
    wver: str,
    extra_wver: str | None = None,
    course_fp: str | None = None,
) -> list[tuple[tuple[int, int], Path]]:
    """扫描 traj_dir 内 manifest.wver∈{wver, extra_wver} 的完整 shard，产出 (pair, dir)。
    dir = shard 目录（含 manifest.json），stream 用它把在盘的预采首波 shard 注入训练。

    course_fp：非空时额外要求 manifest.course_fp 匹配（D14 语料血缘）。

    目录签名缓存（P2-2）：签名未变（无新 shard / 无 shard 内容更新）时零 IO 复用。
    """
    if not traj_dir.exists():
        return []
    sig = _dir_signature(traj_dir)
    key = (str(traj_dir), wver, extra_wver, course_fp, sig)
    cached = _SCAN_CACHE.get(key)
    if cached is not None:
        return cached
    res: list[tuple[tuple[int, int], Path]] = []
    for m in traj_dir.rglob("rl_s*_seed*/manifest.json"):
        try:
            mm = json.loads(m.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        st, sd = mm.get("stage"), mm.get("seed")
        wv = mm.get("wver")
        if (wv != wver and wv != extra_wver) or not isinstance(st, int) or not isinstance(sd, int):
            continue
        if course_fp is not None and mm.get("course_fp") != course_fp:
            continue  # D14：跨课程语料不参与对账
        res.append(((int(st), int(sd)), m.parent))
    if len(_SCAN_CACHE) >= _SCAN_CACHE_MAX:
        _SCAN_CACHE.clear()
    _SCAN_CACHE[key] = res
    return res


def settled_stage_totals(
    traj_dir: Path,
    wver: str,
    extra_wver: str | None = None,
    course_fp: str | None = None,
) -> dict[int, tuple[int, int]]:
    """stage → (games, transitions)：已结算 shard 的局数与 `nSamples` 之和。

    动态采集配额（plan/dynamic-rollout-volume §2.2.2）的**唯一**账本口径：只数已结算
    （manifest 落盘）的 shard。掉局零样本 ⇒ transitions 不涨（天然触发补采，这是特性），
    但它仍占一个派发名额（games 计入硬顶——硬顶管「派了多少局」，不是「落了多少样本」）。

    复用 `_scan_shards` 的目录签名缓存 ⇒ 补波循环内反复调用零额外扫描（只有每个
    shard 一份小的 manifest.json 读，且仅补波时发生）。

    transitions 字段**两种落盘 schema 都要认**（`dist_common.write_shard` 原样写 agent
    返回的 manifest，所以盘上两种都可能有）：单局 schema `nSamples`（TS exporter
    `export-rl-rollout.ts` 生产的正规形）与远端/队列 path 的聚合单局 schema
    `totalSamples`（一局一份 shard ⇒ 它就是这局的 transitions）。只认前者会让后者
    的关永远“零样本” ⇒ 补波永不达标（白烧到波次上限）。
    """
    out: dict[int, tuple[int, int]] = {}
    for (stage, _seed), d in _scan_shards(traj_dir, wver, extra_wver, course_fp):
        try:
            mm = json.loads((d / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        n = mm.get("nSamples")
        if not isinstance(n, int):
            n = mm.get("totalSamples")
        games, trans = out.get(stage, (0, 0))
        out[stage] = (games + 1, trans + (int(n) if isinstance(n, int) else 0))
    return out


def trailing_samples_per_game(jsonl_path: Path, window: int = 5, fallback: int = 0) -> int:
    """最近 `window` 轮 iteration 的局均 **samples**（Σsamples / Σgames）——动态采集的 est。

    ★ 量纲（2026-09-15 T9）：读 jsonl 的 **`samples`**（= `report.totalSamples` = 已结算
    shard 的 `nSamples` 之和），**绝不读 `ticks`**。两者差 K 倍（x3 实测 samples/ticks
    ≈ 0.1007）；读 ticks 会让「首轮按声明值采对、第二轮起采量偏离 10×」——与旧键名
    `est_ticks_per_game` 是同一条 bug 的两半（见 rl/volume_waves.py 文件头量纲节）。

    决策可 replay 的基础（plan §2.3.1）：est 是 jsonl 历史的**纯函数**（同一份 jsonl
    ⇒ 同一 est），重启后重算一致，不靠 WAL 重放。games 口径 = Σ outcomes（掉局也
    占一局——它确实花了墙钟）；无可用历史 → `fallback`（课程声明的首轮估计）；
    两者都缺 → 响亮 ValueError，绝不静默拿一个假值去反解局数。
    """
    rows: list[tuple[int, int]] = []  # (games, samples)
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if e.get("event") != "iteration":
                    continue
                # 量纲红线：samples（不是 ticks）。旧行无 samples 键则跳过（不用 ticks 顶）。
                samples = e.get("samples")
                outs = e.get("outcomes")
                if not isinstance(samples, int) or not isinstance(outs, dict):
                    continue
                games = sum(v for v in outs.values() if isinstance(v, int))
                if games > 0 and samples > 0:
                    rows.append((games, samples))
    except OSError:
        rows = []
    last = rows[-window:] if window > 0 else []
    total_games = sum(g for g, _ in last)
    total_samples = sum(s for _, s in last)
    if total_games > 0 and total_samples > 0:
        return max(1, round(total_samples / total_games))
    if fallback > 0:
        return int(fallback)
    raise ValueError(
        "trailing_samples_per_game: 无 iteration 历史且未给 est_samples_per_game 兜底——"
        "动态采集无法反解局数（检查课程 target_transitions/est_samples_per_game 配对）"
    )


def trailing_stage_samples_per_game(
    traj_root: Path,
    stages: Sequence[int],
    *,
    window_iters: int = 3,
    fallback: int = 1,
) -> dict[int, int]:
    """近几轮盘上 shard 的 **分关** 局均 nSamples（连续配额 est_s）。

    扫 `traj_root/it*` 下最近 `window_iters` 个仍有 manifest 的轮目录，
    对每个 stage 聚合 `ΣnSamples/Σgames`；无数据的关回退 `fallback`（通常=全局 est）。
    量纲与 `settled_stage_totals` 同：只认 nSamples/totalSamples，不读 ticks。
    """
    from collections import defaultdict

    stage_set = {int(s) for s in stages}
    it_dirs = sorted(
        (p for p in Path(traj_root).glob("it*") if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[: max(1, int(window_iters))]
    games: dict[int, int] = defaultdict(int)
    samples: dict[int, int] = defaultdict(int)
    for it_dir in it_dirs:
        for mp in it_dir.glob("**/rl_s*_seed*/manifest.json"):
            try:
                mm = json.loads(mp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            st = mm.get("stage")
            if st not in stage_set:
                continue
            n = mm.get("nSamples")
            if not isinstance(n, int):
                n = mm.get("totalSamples")
            if not isinstance(n, int) or n <= 0:
                continue
            games[int(st)] += 1
            samples[int(st)] += int(n)
    out: dict[int, int] = {}
    fb = max(1, int(fallback))
    for st in stage_set:
        g = games.get(st, 0)
        s = samples.get(st, 0)
        out[st] = max(1, round(s / g)) if g > 0 and s > 0 else fb
    return out


def resumed_manifests(
    traj_dir: Path,
    wver: str,
    exclude: set[tuple[int, int]] | None = None,
    only: set[tuple[int, int]] | None = None,
    extra_wver: str | None = None,
    course_fp: str | None = None,
) -> list[dict]:
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
        if course_fp is not None and mm.get("course_fp") != course_fp:
            continue  # D14：跨课程语料绝不并入本轮报告
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
        out.append(
            {
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
            }
        )
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


def peak_entropy(jsonl_path: Path) -> float | None:
    """回读日志中历史最大 entropy（F4 ENT 相对崩塌基线，DECISIONS §339）。

    续跑时 breaker 的 ent_peak 不能从 None 重新开始，否则重启首轮被当冷启动、
    按绝对电平判定白记连击。无 iteration 事件或无 entropy 字段 → None（真冷启动）。
    """
    if not jsonl_path.exists():
        return None
    peak: float | None = None
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if e.get("event") != "iteration":
            continue
        ent = e.get("entropy")
        if isinstance(ent, (int, float)):
            peak = ent if peak is None else max(peak, float(ent))
    return peak


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
