"""rl/plan.py —— 「半离线整段训练」的计划（kind=run 的 `plan.json`）。

**问题**（用户 2026-09-17 需求）：云机从 hub 领到任务（课程 + 初始权重 + 代码）后，
即使本机 hub **一直失联**，也要能自己把剩下的轮次跑完，并在本机（Kaggle /kaggle/working、
Colab Drive）留下可打包下载的逐轮产物。现状是 hub 拥有迭代循环：每轮 publish 一个
job、云机跑一轮就回等——hub 一断，云机除了等没有任何事可做。

**计划 = 把「hub 本来会自己逐轮做的决定」一次性写下来**，而不是把 hub 的逻辑抄一份到云上：

  * `pair_args`：`build_pairs` 的输入子集（纯函数的入参）。云机用**同一个** `build_pairs`
    （同 commit 的代码随 job 下发）重放，逐字节一致是构造性质；`pairs_fp` 是两侧对账的
    凭据——发布时 hub 用真 `args` 算一遍比对，交接时节点再算一遍比对，**任一侧漂移都响亮失败**。
  * `argv_template`：目标轮的 argv（**由 hub 用 `rl/cmd.build_rollout_cmd` 原样拼**，
    不在云上复制这份知识）。后续轮只做「四个动态 flag 的重定向」（`--stages/--seeds/
    --out/--wver` + 自定义关的 `--stage-json`）——`retarget_argv` 是通用替换、不认识任何
    导出器细节。
  * `end_it`：本轮之后还要自己跑几轮（课程的 iters 预算与 `max_iters` 的交集）。

产物侧（逐轮权重/opt/指标 + 续跑点）在 `remote/artifacts.py`，执行在 `remote/run_loop.py`。

术语：**半离线** = hub 只在交接时必需；此后云机自主，产物是权威记录。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from common.protocol import (
    PLAN_PROTO,
    RUN_MAX_ITERS_HARD_CAP,
    RUN_NODE_LABEL,
    ProtocolError,
)
from rl.cmd import build_rollout_cmd
from rl.course import build_pairs

#: `build_pairs` 实际读的 args 字段（纯函数入参 = 计划必须携带的全部信息）。
#: 少一个字段 ⇒ 云机重放出的对集与 hub 不同（`pairs_fp` 会在发布期就抓住）。
PAIR_ARG_FIELDS: tuple[str, ...] = (
    "curriculum_stages",
    "curriculum_start",
    "curriculum_every",
    "curriculum_grow",
    "seeds_per_stage",
    "rotate_stages",
    "stages",
    "seed_rotate",
    "seeds",
    "total_stages",
)

#: 逐轮重定向的 flag → 占位符名。值一律按当轮/当局实值写入（模板里是目标轮的值）。
_RETARGET_FLAGS: dict[str, str] = {
    "--stages": "stage",
    "--seeds": "seed",
    "--out": "out",
    "--wver": "wver",
}


# ------------------------------------------------------------------ 对集（纯函数重放）


def pair_args(args: Any) -> dict:
    """从 args 抽出 `build_pairs` 的入参子集（缺失字段用 None，让重放侧 fail loud）。"""
    return {k: getattr(args, k, None) for k in PAIR_ARG_FIELDS}


def pairs_for(plan: dict, it: int) -> list[tuple[int, int]]:
    """重放第 `it` 轮的对集（计划是唯一的输入）。

    两条口径（同一个计划里互斥，由 `volume` 块的有无决定）：

      * **无 `volume`**（老课程）：`build_pairs` 纯函数重放 —— 与 hub 侧逐字节一致；
      * **有 `volume`**（`target_transitions > 0`）：`initial_wave_pairs` = 每关 G0 局的
        **初波**对集，与本地集群 `TrainingLoop._iteration_pairs` 的 volume 分支**同函数**
        —— 云机采的这批种子与本地集群该轮初波逐位相同（2026-09-22，见 `rl/volume_waves`
        的「计划里的动态采集块」节）。
    """
    vol = plan.get("volume")
    if isinstance(vol, dict):
        from rl.volume_waves import initial_wave_pairs

        return initial_wave_pairs(
            int(plan["rotate_seed"]),
            int(it),
            stages=[int(s) for s in vol["stages"]],
            games_per_stage=int(vol["games_per_stage"]),
        )
    ns = SimpleNamespace(**plan["pair_args"])
    return build_pairs(ns, int(it), int(plan["rotate_seed"]))


def pairs_fp(pairs: list[tuple[int, int]]) -> str:
    """单轮对集指纹（规范化 JSON 的 sha256；两侧同函数 ⇒ 可直接对账）。"""
    payload = json.dumps([[int(s), int(d)] for s, d in pairs], separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def plan_pairs_fp(plan: dict) -> str:
    """计划覆盖的**全部轮次**对集指纹（逐轮 fp 顺序拼接后再 hash）。

    只覆盖计划的 it 区间（`start_it+1 .. end_it`），顺序敏感。交接时节点先一次性重放
    所有轮次再比对：**任一**轮的对集漂移都在跑第一局之前暴露，而不是跑到半途才发现
    语料与计划不符（那时已经产生了一批不可信 shard）。
    """
    h = hashlib.sha256()
    for it in planned_iters(plan):
        h.update(pairs_fp(pairs_for(plan, it)).encode("ascii"))
    return h.hexdigest()


def planned_iters(plan: dict) -> list[int]:
    """计划覆盖的轮次（`start_it` 之后到 `end_it`，含端点）。"""
    return list(range(int(plan["start_it"]) + 1, int(plan["end_it"]) + 1))


# ------------------------------------------------------------------ argv 重定向


def retarget_argv(
    template: list[str],
    *,
    stage: int,
    seed: int,
    out: str,
    wver: str,
    stage_json: str | None = None,
) -> list[str]:
    """把模板 argv 的四个动态 flag 换成当局/当轮实值；`--stage-json` 按关卡增删改。

    为什么是「按 flag 替换」而不是「在云上重新拼命令」：拼命令的知识（三导出器 + 课程
    覆盖 + D14 血缘）只有 `rl/cmd.build_rollout_cmd` 一份，复制到协议/云侧就会漂移。
    这里只认识**四个必然逐局变化的 flag** + 一个「只有自定义关才有」的可选 flag，
    导出器细节一概不知。

    行为：
      * `--stages/--seeds/--out/--wver` 一律替换其后的值；模板里没有则**追加**
        （`--wver` 在模板里可能是占位空串）。
      * `--stage-json`：`stage_json` 非空 → 替换/追加；为空 → **整对删除**（新关卡不是
        自定义关时留着旧值 = 拿错关卡的 JSON 跑，比缺失更危险）。
    """
    values = {"stage": int(stage), "seed": int(seed), "out": str(out), "wver": str(wver)}
    res: list[str] = []
    seen: set[str] = set()
    i = 0
    n = len(template)
    while i < n:
        tok = template[i]
        if tok in _RETARGET_FLAGS:
            seen.add(tok)
            res += [tok, str(values[_RETARGET_FLAGS[tok]])]
            i += 2 if (i + 1 < n and not template[i + 1].startswith("--")) else 1
            continue
        if tok == "--stage-json":
            seen.add(tok)
            if i + 1 < n and not template[i + 1].startswith("--"):
                if stage_json:
                    res += [tok, str(stage_json)]
                i += 2
                continue
            if stage_json:
                res += [tok, str(stage_json)]
            i += 1
            continue
        res.append(tok)
        i += 1
    for flag, name in _RETARGET_FLAGS.items():
        if flag not in seen:
            res += [flag, str(values[name])]
    if stage_json and "--stage-json" not in seen:
        res += ["--stage-json", str(stage_json)]
    return res


def argv_fp(argv: list[list[str]]) -> str:
    """argv 模板指纹（规范化 JSON 的 sha256）。"""
    payload = json.dumps([[str(x) for x in a] for a in argv], separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def template_argv(
    args: Any,
    pairs: list[tuple[int, int]],
    *,
    weights: str,
    wver: str,
    node_label: str = RUN_NODE_LABEL,
) -> list[list[str]]:
    """用 `build_rollout_cmd` 拼目标轮的 argv（hub 侧唯一拼装点，掷掉 argv[0]）。

    与 `rl/iter_job.build_iter_spec` 同规矩：路径一律 job 目录内相对路径，argv[0]
    （本机 bun 绝对路径）丢掉——节点用自己的 bun。
    """
    argv: list[list[str]] = []
    for idx, (stage, seed) in enumerate(pairs):
        cmd = build_rollout_cmd(
            "bun",
            args,
            weights=weights,
            out_dir=f"w{idx}",
            stage=int(stage),
            seed=int(seed),
            wver=str(wver or ""),
            node_label=node_label,
        )
        argv.append([str(x) for x in cmd[1:]])
    return argv


# ------------------------------------------------------------------ 计划组装 / 校验


def build_plan(
    args: Any,
    *,
    it: int,
    iters_total: int,
    rotate_seed: int,
    max_iters: int = 0,
    workers: int = 0,
    game_timeout_sec: float = 0.0,
    budget_sec: float = 0.0,
    node_label: str = RUN_NODE_LABEL,
    template_wver: str = "",
    volume: dict | None = None,
    log=lambda msg: None,
) -> dict:
    """组装计划（hub 侧）。`it` = 本 job 自己那一轮（其权重已在 payload 里）。

    `max_iters`：本次最多自主跑几轮（0 = 跑到课程预算 `iters_total`）。`end_it` 取二者
    交集，并受 `RUN_MAX_ITERS_HARD_CAP` 钳制（手写/损坏的计划不许把节点按在机上一整天）。

    `volume`：动态采集块（`rl/volume_waves.volume_block(args)` 的产出；None = 老口径）。
    带上它以后，节点重放出的对集不再是 `build_pairs` 的固定局数，而是**按
    `target_transitions` 反解的初波**（与本地集群同函数、同种子）——这是「云端采样量与
    本地集群 rollout 一致」的那一半；另一半（训练侧只收达标样本量）由 manifest 里的
    `per_stage_quota` 承担（`remote/run_loop._run_iteration` 会把它从计划带进逐轮 manifest）。

    自检（发布期，失败即拒发）：① 计划重放的对集必须 == 真 `args` 的对集
    （`PAIR_ARG_FIELDS` 少字段会在这里暴露；volume 路线对的是 `volume_block` 重解）；
    ② `retarget_argv` 对模板本身必须是恒等（模板就是目标轮的 argv——重定向逻辑的回归在
    **发出去之前**就被抓住）。
    """
    it = int(it)
    iters_total = int(iters_total)
    end_it = iters_total if int(max_iters or 0) <= 0 else min(iters_total, it + int(max_iters))
    if end_it <= it:
        raise ProtocolError(
            f"build_plan: it={it} 之后没有可自主跑的轮次（iters_total={iters_total}，"
            f"max_iters={max_iters}）——这种情况应发 kind=iter（单轮）而不是 kind=run"
        )
    if end_it - it > RUN_MAX_ITERS_HARD_CAP:
        raise ProtocolError(
            f"build_plan: 计划覆盖 {end_it - it} 轮 > 硬上界 {RUN_MAX_ITERS_HARD_CAP}——拒发"
        )
    plan = {
        "proto": PLAN_PROTO,
        "start_it": it,
        "end_it": end_it,
        "iters_total": iters_total,
        "rotate_seed": int(rotate_seed),
        "pair_args": pair_args(args),
        "node_label": str(node_label),
        "workers": int(workers) if int(workers) > 0 else 1,
        "game_timeout_sec": float(game_timeout_sec or 0.0),
        "budget_sec": float(budget_sec or 0.0),
    }
    if volume is not None:
        from rl.volume_waves import validate_volume_block

        try:
            plan["volume"] = validate_volume_block(volume)
        except ValueError as e:
            raise ProtocolError(f"build_plan: 动态采集块非法（拒发）：{e}") from e
    # 目标轮（= 本 job 之后那一轮）：argv 模板的目标就是它。
    tmpl_it = it + 1
    tmpl_pairs = pairs_for(plan, tmpl_it)
    # 局部变量带类型（plan 是 dict[str, object]）：下面几行要拿它做 argv_fp/len，
    # 直接从 plan 里取回来 mypy 只看到 object。
    argv_template: list[list[str]] = template_argv(
        args, tmpl_pairs, weights="init_weights.json", wver=template_wver, node_label=node_label
    )
    plan["argv_template"] = argv_template
    plan["argv_fp"] = argv_fp(argv_template)
    plan["pairs_fp"] = plan_pairs_fp(plan)
    check_plan_against_args(args, plan)
    log(
        f"[plan] kind=run it{it} → 自主段 it{it + 1}..it{end_it}"
        f"（{len(planned_iters(plan))} 轮 × {len(tmpl_pairs)} 局，模板 argv {len(argv_template)} 条）"
    )
    return plan


def plan_fp(plan: dict) -> str:
    """计划的身份（规范化 JSON 的 sha256；payload 里 `plan.json` 的 sha 就是它）。

    排除 `argv_fp`/`pairs_fp` 之外的易变字段？不排除——计划是**只读契约**，任何字段变化
    都是另一个计划（sha 变 ⇒ 另一个 job 身份）。规范化 `sort_keys=True` 保证两侧同字节。
    """
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return hashlib.sha256(payload).hexdigest()


def dump_plan(plan: dict) -> bytes:
    """计划 → `plan.json` 字节（**唯一**序列化口径：写盘与算 sha 必须同一份字节）。"""
    return json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")


def load_plan(path: str | Path) -> dict:
    """读计划文件并校验（形状 + 区间 + 硬上界）。畸形计划一律 ProtocolError。"""
    p = Path(path)
    if not p.exists():
        raise ProtocolError(f"计划文件不存在: {p}")
    try:
        plan = json.loads(p.read_text(encoding="utf-8"))
    except ValueError as e:
        raise ProtocolError(f"计划文件不是合法 JSON: {p}: {e}") from e
    return validate_plan(plan)


def validate_plan(plan: object) -> dict:
    """计划的形状校验（kind=run 交接时的第一道门；畸形 ⇒ 拒收，绝不半信半疑地跑）。"""
    if not isinstance(plan, dict):
        raise ProtocolError(f"计划必须是对象，收到 {type(plan).__name__}")
    if int(plan.get("proto", -1)) != PLAN_PROTO:
        raise ProtocolError(f"计划 proto={plan.get('proto')!r} != {PLAN_PROTO}")
    missing = [
        k
        for k in ("start_it", "end_it", "iters_total", "rotate_seed", "pair_args", "argv_template")
        if k not in plan
    ]
    if missing:
        raise ProtocolError(f"计划缺字段: {missing}")
    for k in ("start_it", "end_it", "iters_total", "rotate_seed", "workers"):
        v = plan.get(k, 0)
        if isinstance(v, bool) or not isinstance(v, int):
            raise ProtocolError(f"计划 {k} 必须是整数，收到 {v!r}")
    if plan["end_it"] <= plan["start_it"]:
        raise ProtocolError(
            f"计划区间非法: start_it={plan['start_it']} end_it={plan['end_it']}（无可跑的轮次）"
        )
    if plan["iters_total"] < plan["end_it"]:
        raise ProtocolError(
            f"计划 end_it={plan['end_it']} 超出课程预算 iters_total={plan['iters_total']}"
        )
    if plan["end_it"] - plan["start_it"] > RUN_MAX_ITERS_HARD_CAP:
        raise ProtocolError(
            f"计划覆盖 {plan['end_it'] - plan['start_it']} 轮 > 硬上界 {RUN_MAX_ITERS_HARD_CAP}"
        )
    if not isinstance(plan["pair_args"], dict):
        raise ProtocolError("计划 pair_args 必须是对象")
    vol = plan.get("volume")
    if vol is not None:
        from rl.volume_waves import validate_volume_block

        try:
            plan["volume"] = validate_volume_block(vol)
        except ValueError as e:
            raise ProtocolError(f"计划 volume 块非法：{e}") from e
    argv = plan["argv_template"]
    if not isinstance(argv, list) or not argv or not all(isinstance(a, list) for a in argv):
        raise ProtocolError("计划 argv_template 必须是非空二维数组")
    if int(plan.get("game_timeout_sec", 0) or 0) < 0 or int(plan.get("budget_sec", 0) or 0) < 0:
        raise ProtocolError("计划的 game_timeout_sec / budget_sec 不得为负")
    return plan


def check_plan_against_args(args: Any, plan: dict) -> None:
    """发布期自检：计划重放 == 真 args（的对集）；重定向对模板恒等。

    这是「云上是重放而不是重新发明」的守卫：`PAIR_ARG_FIELDS` 漏了字段、`build_pairs`
    新增了随机源、`retarget_argv` 改坏了替换语义——三种漂移都在**发出去之前**失败，
    而不是等节点跑完一半、拿一堆与 hub 不同的语料回来。

    对集口径按计划有没有 `volume` 块分两条（与 `pairs_for` 同源）：volume 路线的对照物是
    `volume_pairs_from_args`（按计划里钉住的 est 重解一遍）——它抓的是「块里写的
    stages/target/G0 与 args 说的不一致」这类错（例如导出时用了别的课程的 --stages）。
    """
    vol = plan.get("volume")
    for it in planned_iters(plan):
        mine = pairs_for(plan, it)
        if isinstance(vol, dict):
            from rl.volume_waves import volume_pairs_from_args

            real = volume_pairs_from_args(
                args, it, int(plan["rotate_seed"]), est_samples_per_game=int(vol["est_samples_per_game"])
            )
            if real is None:
                raise ProtocolError(
                    "计划自检失败：计划带 volume 块，但 args 的 target_transitions ≤ 0"
                    "（导出时用的课程与计划不是同一门？）"
                )
        else:
            real = build_pairs(args, it, int(plan["rotate_seed"]))
        if mine != real:
            raise ProtocolError(
                f"计划自检失败：it{it} 重放对集与 args 重解不一致"
                f"（计划 {len(mine)} 局 / 实际 {len(real)} 局；"
                + (
                    f"volume={{stages={vol['stages']}, G0={vol['games_per_stage']}, "
                    f"target={vol['target_transitions']}, est={vol['est_samples_per_game']}}}"
                    if isinstance(vol, dict)
                    else f"pair_args={sorted(plan['pair_args'])}）——PAIR_ARG_FIELDS 是否漏字段？"
                )
            )
    tmpl_pairs = pairs_for(plan, int(plan["start_it"]) + 1)
    tmpl = plan["argv_template"]
    if len(tmpl) != len(tmpl_pairs):
        raise ProtocolError(
            f"计划自检失败：argv_template {len(tmpl)} 条 != 目标轮对集 {len(tmpl_pairs)} 局"
        )
    for i, ((stage, seed), row) in enumerate(zip(tmpl_pairs, tmpl, strict=True)):
        # 用模板**自己的** wver 值做恒等检查（运行时会被换成当轮实值）
        own_wver = _flag_value(row, "--wver") or ""
        rt = retarget_argv(
            list(row),
            stage=stage,
            seed=seed,
            out=_flag_value(row, "--out") or f"w{i}",
            wver=own_wver,
            stage_json=_flag_value(row, "--stage-json"),
        )
        if rt != list(row):
            raise ProtocolError(
                f"计划自检失败：argv_template[{i}] 经 retarget_argv 后不再等于自身"
                f"（重定向语义改了？{' '.join(row[:6])}…）"
            )


def _flag_value(argv: list[str], flag: str) -> str | None:
    """取 argv 里 `flag` 的值（无该 flag/值 → None）。"""
    if flag not in argv:
        return None
    i = argv.index(flag) + 1
    return argv[i] if i < len(argv) else None


def stage_json_of(course: Any, stage: int) -> str:
    """关卡的自定义 stageJson（非自定义关 → 空串；与 `rl.config.stage_json_for_args` 同源）。"""
    if course is None:
        return ""
    try:
        return str(course.stage_json(int(stage)) or "")
    except Exception:  # 课程/关卡越界等——按「无自定义关」处理（与本地一致）
        return ""


def argv_for_iteration(
    plan: dict,
    it: int,
    pairs: list[tuple[int, int]],
    *,
    wver: str,
    course: Any = None,
) -> list[list[str]]:
    """第 `it` 轮的 argv：逐局把模板重定向到 (stage, seed, out, wver) + 该关的 stageJson。"""
    tmpl = plan["argv_template"]
    rows: list[list[str]] = []
    for idx, (stage, seed) in enumerate(pairs):
        base = list(tmpl[idx]) if idx < len(tmpl) else list(tmpl[0])
        rows.append(
            retarget_argv(
                base,
                stage=int(stage),
                seed=int(seed),
                out=f"w{idx}",
                wver=str(wver or ""),
                stage_json=stage_json_of(course, int(stage)),
            )
        )
    return rows


def iter_spec(plan: dict, it: int, pairs: list[tuple[int, int]], *, wver: str, course: Any = None) -> dict:
    """第 `it` 轮的 rollout 规格（形状与 `rl/iter_job.build_iter_spec` 一致）。

    交付 `remote/iter_rollout.run_iter_rollout` 前仍会过 `validate_rollout_spec`
    （协议白名单 + 相对路径 + 逐局 stage/seed）——模板损坏/被篡改在这里被抓住。
    """
    from common.protocol import validate_rollout_spec

    return validate_rollout_spec(
        {
            "argv": argv_for_iteration(plan, it, pairs, wver=wver, course=course),
            "wver": str(wver or ""),
            "workers": int(plan.get("workers", 1) or 1),
            "game_timeout_sec": float(plan.get("game_timeout_sec", 0.0) or 0.0),
            "bun": "bun",
        }
    )
