"""test_run_rl_m1.py — run_rl.py 三模式整合 + m1-eval 评估管线回归（RL 入口整合，
plan/RL-Entry-Consolidation.md P5；DECISIONS §307）。迁移自 test_run_rl_intent.py
（parse_m1_eval_report / run_clean_eval_rerun）+ 新增三模式分派矩阵。无真实训练、
不碰节点。运行：start-training.sh --script test_run_rl_m1.py；退出码 0/1。
"""

from __future__ import annotations

import argparse
import sys
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

import run_rl
from rl.eval_m1 import CLEAN_EVAL_MAX_RETRY, parse_m1_eval_report, run_clean_eval

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    print(("  PASS " if cond else "  FAIL ") + msg, flush=True)
    if not cond:
        FAILS.append(msg)


def _report_text(total: int, error: int, cleared: int, win_pct: float) -> str:
    """m1-eval 输出夹具：横幅在 stderr（合并文本首行）+ JSON report（stdout）。
    只含顶层 outcomes（无嵌套 braces），parse_m1_eval_report 的 regex 可命中。"""
    return (
        f"[m1-eval] WIN RATE {win_pct}% (gate 60%) -> PASS\n"
        f'{{"total": {total}, "outcomes": {{"stage_clear": {cleared}, '
        f'"error": {error}}}, "cleared": {cleared}}}\n'
    )


def test_parse_m1_eval_report() -> None:
    print("[fast] parse_m1_eval_report 从合并文本（stdout+stderr）提取结果")
    r = parse_m1_eval_report(_report_text(350, 0, 260, 74.3))
    check(r["winRate"] == 0.743, f"winRate stderr 横幅 (got {r['winRate']})")
    check(
        r["total"] == 350 and r["cleared"] == 260 and r["error"] == 0,
        f"total/cleared/error JSON (got {r})",
    )
    r2 = parse_m1_eval_report(_report_text(350, 1830, 32, 9.1))
    check(r2["error"] == 1830 and r2["total"] == 350, "error 计数正确")
    r3 = parse_m1_eval_report(
        "[m1-eval] win rate 55.5% (gate 60%) -> FAIL\n" + _report_text(100, 0, 55, 55.5)
    )
    check(r3["winRate"] == 0.555, "横幅大小写不敏感")
    r4 = parse_m1_eval_report("some random output\nno report here")
    check(r4["total"] == 0 and r4["error"] == 0 and r4["winRate"] is None, "无报告文本安全降级")


def test_run_clean_eval_rerun() -> None:
    print("[fast] run_clean_eval 重跑决策（error>0 → 整批重跑，最多 3 次）")
    args = types.SimpleNamespace(eval_seeds=10, difficulty="hard", workers=8, goal=False)
    calls: list[str] = []

    def fake_clean(text: str):
        def runner(_cmd):
            calls.append(" ".join(_cmd))
            return parse_m1_eval_report(text)

        return runner

    calls.clear()
    res = run_clean_eval(
        "bun", "tmp/w.json", args, _runner=fake_clean(_report_text(350, 0, 260, 74.3))
    )
    check(
        len(calls) == 1 and res["retries"] == 0 and res["error"] == 0,
        f"干净报告一次通过 (calls={len(calls)} retries={res['retries']})",
    )
    seq = [_report_text(350, 1830, 32, 9.1), _report_text(350, 0, 262, 74.9)]
    n_calls = [0]

    def seq_runner(_cmd):
        n_calls[0] += 1
        return parse_m1_eval_report(seq.pop(0))

    res = run_clean_eval("bun", "tmp/w.json", args, _runner=seq_runner)
    check(
        n_calls[0] == 2 and res["error"] == 0 and res["retries"] == 1 and res["cleared"] == 262,
        f"error 后自动重跑至干净 (calls={n_calls[0]})",
    )
    calls.clear()
    res = run_clean_eval(
        "bun", "tmp/w.json", args, _runner=fake_clean(_report_text(350, 350, 0, 0.0))
    )
    check(
        len(calls) == CLEAN_EVAL_MAX_RETRY
        and res["error"] > 0
        and res["retries"] == CLEAN_EVAL_MAX_RETRY - 1,
        "超限后接受 error 标记",
    )
    check(res["games"] == 350, f"games 兼容字段 (got {res['games']})")


def test_run_clean_eval_goal_cmd() -> None:
    print("[fast] run_clean_eval 命令组装按 mode 分派（--policy goal/intent-exec）")
    args = types.SimpleNamespace(eval_seeds=10, difficulty="hard", workers=8, goal=False)
    captured: list[str] = []

    def runner(cmd):
        captured.append(" ".join(cmd))
        return {"winRate": 0.5, "total": 350, "cleared": 175, "error": 0}

    run_clean_eval("bun", "tmp/g.json", args, _runner=runner)
    check(
        "--policy intent-exec" in captured[-1] and "--intent-weights" in captured[-1],
        "intent 用 intent-exec + --intent-weights",
    )
    captured.clear()
    args.goal = True
    run_clean_eval("bun", "tmp/g.json", args, _runner=runner)
    check(
        "--policy goal" in captured[-1] and "--goal-weights" in captured[-1],
        "goal 用 goal + --goal-weights",
    )


def test_resolve_mode() -> None:
    print("[fast] resolve_mode：--mode / --goal 预解析")
    check(run_rl.resolve_mode(["--iters", "2"]) == "per-tick", "默认 per-tick")
    check(run_rl.resolve_mode(["--mode", "intent"]) == "intent", "--mode intent")
    check(run_rl.resolve_mode(["--mode=goal"]) == "goal", "--mode=goal")
    check(run_rl.resolve_mode(["--goal"]) == "goal", "--goal 别名 → goal")
    check(run_rl.resolve_mode(["--goal", "--mode", "intent"]) == "intent", "--mode 后出现者胜")


def test_apply_mode_flags() -> None:
    print("[fast] apply_mode_flags：置位 intent_rollout/goal_rollout/goal")
    args = argparse.Namespace(mode="intent", goal=False)
    run_rl.apply_mode_flags(args)
    check(
        args.intent_rollout and not args.goal_rollout and not args.goal,
        "intent：intent_rollout=True 其余 False",
    )
    args = argparse.Namespace(mode="per-tick", goal=False)
    run_rl.apply_mode_flags(args)
    check(
        not args.intent_rollout and not args.goal_rollout and not args.goal,
        "per-tick：三 flag 全 False",
    )
    args = argparse.Namespace(mode="per-tick", goal=True)
    run_rl.apply_mode_flags(args)
    check(
        args.mode == "goal" and args.goal_rollout and args.goal,
        "--goal 强制 mode=goal + goal_rollout/goal",
    )


def test_merged_mode_args() -> None:
    print("[fast] merged_mode_args：rl.<mode> → intent_rl 遗留块 → rl 优先级")
    cfg = {"rl": {"a": 1, "b": 2}, "intent_rl": {"b": 3, "c": 4}}
    merged, src = run_rl.merged_mode_args(cfg, "intent")
    check(
        merged["a"] == 1 and merged["b"] == 3 and merged["c"] == 4, "intent_rl 遗留块覆盖 rl 同名键"
    )
    check(src["b"] == "intent_rl(legacy)", "来源标注 intent_rl(legacy)")
    cfg2 = {"rl": {"a": 1, "intent": {"b": 9}}, "intent_rl": {"b": 3}}
    merged2, src2 = run_rl.merged_mode_args(cfg2, "intent")
    check(merged2["b"] == 9 and src2["b"] == "rl.intent", "rl.intent 嵌套块最优先")
    merged3, _ = run_rl.merged_mode_args(cfg2, "per-tick")
    check("b" not in merged3, "per-tick 忽略嵌套块与 intent_rl 遗留块")
    merged4, _ = run_rl.merged_mode_args({"rl": {}, "intent_rl": {"b": 3}}, "goal")
    check(merged4["b"] == 3, "goal 回退 intent_rl 遗留块")


def test_stop_loss_hit() -> None:
    print("[fast] stop_loss_hit：止损判门分模式（原 iter15 Δ≤0 泛化 + P1-9 统计化）")
    check(
        run_rl.stop_loss_hit("per-tick", 15, 0.0, 20, {"delta": -0.1}) is False, "per-tick 永不触发"
    )
    check(
        run_rl.stop_loss_hit("intent", 0, 0.0, 20, {"delta": -0.1}) is False, "stop_loss_at=0 关闭"
    )
    check(
        run_rl.stop_loss_hit("intent", 15, 0.0, 10, {"delta": -0.1}) is False,
        "未到 stop-loss-at 不触发",
    )
    # 旧调用方（eval_rec 无 games/winRate）：保持原语义 Δ≤bar 即停（无 σ 可推）。
    check(run_rl.stop_loss_hit("intent", 15, 0.0, 20, {"delta": -0.05}) is True, "Δ<=0 触发(legacy)")
    check(run_rl.stop_loss_hit("intent", 15, 0.0, 20, {"delta": 0.1}) is False, "Δ>0 不触发")
    check(run_rl.stop_loss_hit("goal", 15, 0.0, 20, {"delta": 0.0}) is True, "goal Δ==0 触发(legacy)")
    check(run_rl.stop_loss_hit("intent", 15, 0.0, 20, None) is False, "无 eval 记录不触发")
    # P1-9 统计化：带 games/winRate 时要求 Δ ≤ −2σ（350 局 p≈0.72 → σ≈0.024 → −2σ≈−0.048）。
    check(
        run_rl.stop_loss_hit("intent", 15, 0.0, 20, {"delta": -0.05, "games": 350, "winRate": 0.72})
        is True,
        "Δ=−0.05 ≤ −2σ(−0.048) 统计显著 → 触发",
    )
    check(
        run_rl.stop_loss_hit("intent", 15, 0.0, 20, {"delta": 0.0, "games": 350, "winRate": 0.72})
        is False,
        "Δ=0 不显著（旧逻辑会误停）——P1-9 修复核心断言",
    )
    check(
        run_rl.stop_loss_hit("intent", 15, 0.0, 20, {"delta": -0.02, "games": 350, "winRate": 0.72})
        is False,
        "Δ=−0.02 在噪声带内（−0.048, 0）→ 不触发",
    )
    check(
        run_rl.stop_loss_hit("intent", 15, 0.0, 20, {"delta": -0.02, "games": 350, "winRate": 0.72}, z_score=0.5)
        is True,
        "z 收紧（0.5σ）时 −0.02 也算显著——z 参数可调",
    )


def test_update_kwargs() -> None:
    print("[fast] update_kwargs：value 预热 + kickstarting KL 系数衰减")
    args = types.SimpleNamespace(
        epochs=4, warmup_iters=2, kickstart_kl=1.0, kickstart_decay=0.5, seed=7
    )
    kw = run_rl.update_kwargs(args, 1, 1, object())
    check(
        kw["value_warmup_epochs"] == 4 and kw["seed"] == 7 and kw["kl_coef"] == 0.0,
        "warmup：value-only + kl=0",
    )
    kw = run_rl.update_kwargs(args, 3, 1, object())
    check(
        kw["value_warmup_epochs"] == 0 and abs(kw["kl_coef"] - 1.0) < 1e-9,
        "策略迭代 kl=基础系数 1.0",
    )
    kw = run_rl.update_kwargs(args, 4, 1, object())
    check(abs(kw["kl_coef"] - 0.5) < 1e-9, "kl 每策略迭代衰减 0.5")
    args2 = types.SimpleNamespace(
        epochs=4, warmup_iters=1, kickstart_kl=0.0, kickstart_decay=0.5, seed=7
    )
    check(run_rl.update_kwargs(args2, 3, 1, object())["kl_coef"] == 0.0, "kickstart_kl=0 关闭")


def main() -> None:
    test_parse_m1_eval_report()
    test_run_clean_eval_rerun()
    test_run_clean_eval_goal_cmd()
    test_resolve_mode()
    test_apply_mode_flags()
    test_merged_mode_args()
    test_stop_loss_hit()
    test_update_kwargs()
    print()
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        raise SystemExit(1)
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
