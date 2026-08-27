"""test_run_rl_intent.py — run_rl_intent.py 干净评估的常驻回归测试（无真实训练、不碰节点）。

覆盖（用户指令 2026-08-28：评估结果校验 + error 自动重跑，写入 eval 脚本本身并加单测）：
  - parse_m1_eval_report：纯函数解析（横幅 WIN RATE 在 stderr + JSON report 在 stdout
    的合并文本；outcomes.error 提取；perStage 的 "total" 不误匹配顶层）。
  - run_clean_eval 重跑决策：error>0 整批重跑（最多 CLEAN_EVAL_MAX_RETRY）；干净即一次
    通过；超限接受带 error 标记的结果。注入 _runner fake，不执行真实 m1-eval。

运行（经统一启动器，venv/torch 由它保证）：
  powershell -ExecutionPolicy Bypass -File nn-training/start-training.ps1 -Script test_run_rl_intent.py
退出码：全部通过 0，否则 1。
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_rl_intent  # noqa: E402

FAILS: list[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  PASS {msg}")
    else:
        print(f"  FAIL {msg}")
        FAILS.append(msg)


def _report_text(total: int, error: int, cleared: int, win_pct: float = 70.0,
                 banner_stream: str = "stderr") -> str:
    """构造一段 m1-eval 合并输出：JSON report 在 stdout，WIN RATE 横幅在 stderr。"""
    outcomes = {"gameover": max(0, total - cleared - error), "stage_clear": cleared}
    if error:
        outcomes["error"] = error
    report = {
        "policy": "intent-exec", "difficulty": "hard", "stages": 35, "seeds": 10,
        "total": total, "outcomes": outcomes, "winRate": cleared / total,
        "perStage": [{"stage": "Outpost", "total": 60, "cleared": 50}],
    }
    stdout = re.sub(r"^", "", __import__("json").dumps(report, indent=2))
    banner = f"[m1-eval] WIN RATE {win_pct}% (gate 60%) -> PASS"
    if banner_stream == "stderr":
        return stdout + "\n" + banner + "\n"
    return banner + "\n" + stdout + "\n"


def test_parse_m1_eval_report() -> None:
    print("[fast] parse_m1_eval_report (横幅 + outcomes 提取)")
    # 干净报告：横幅在 stderr，JSON 在 stdout → 合并文本必须都解析到。
    t = _report_text(total=350, error=0, cleared=260, win_pct=74.3)
    r = run_rl_intent.parse_m1_eval_report(t)
    check(r["winRate"] == 0.743, f"winRate 从 stderr 横幅提取 (got {r['winRate']})")
    check(r["total"] == 350 and r["cleared"] == 260 and r["error"] == 0,
          f"total/cleared/error 从 JSON report 提取 (got {r})")
    # 含 error 的报告。
    r2 = run_rl_intent.parse_m1_eval_report(_report_text(total=350, error=1830, cleared=32))
    check(r2["error"] == 1830 and r2["total"] == 350,
          f"error 计数正确 (got error={r2['error']})")
    # 横幅大小写不敏感（§27 回归：'WIN RATE' vs 'winRate'）。
    r3 = run_rl_intent.parse_m1_eval_report(
        "[m1-eval] win rate 55.5% (gate 60%) -> FAIL\n" + _report_text(100, 0, 55))
    check(r3["winRate"] == 0.555, f"横幅大小写不敏感 (got {r3['winRate']})")
    # 无 outcomes 块（如评估被中断）→ 安全返回默认值。
    r4 = run_rl_intent.parse_m1_eval_report("some random output\nno report here")
    check(r4["total"] == 0 and r4["error"] == 0 and r4["winRate"] is None,
          f"无报告文本安全降级 (got {r4})")


def test_run_clean_eval_rerun() -> None:
    print("[fast] run_clean_eval 重跑决策（error>0 → 整批重跑，最多 3 次）")
    args = types.SimpleNamespace(eval_seeds=10, difficulty="hard", workers=8)

    calls: list[str] = []

    def fake_clean(text: str):
        def runner(_cmd):
            calls.append(" ".join(_cmd))
            return run_rl_intent.parse_m1_eval_report(text)
        return runner

    # 1) 干净 → 一次通过，不重跑。
    calls.clear()
    res = run_rl_intent.run_clean_eval("bun", "tmp/w.json", args,
                                       _runner=fake_clean(_report_text(350, 0, 260, 74.3)))
    check(len(calls) == 1 and res["retries"] == 0 and res["error"] == 0,
          f"干净报告一次通过 (calls={len(calls)} retries={res['retries']})")

    # 2) 首跑 error → 自动重跑 → 第二次干净 → 返回干净结果 + retries=1。
    seq = [
        _report_text(350, 1830, 32, 9.1),  # 首跑 87% error（it33 实证场景）
        _report_text(350, 0, 262, 74.9),
    ]
    n_calls = [0]

    def seq_runner(_cmd):
        n_calls[0] += 1
        return run_rl_intent.parse_m1_eval_report(seq.pop(0))

    res = run_rl_intent.run_clean_eval("bun", "tmp/w.json", args, _runner=seq_runner)
    check(n_calls[0] == 2 and res["error"] == 0 and res["retries"] == 1
          and res["cleared"] == 262,
          f"error 后自动重跑至干净 (calls={n_calls[0]} error={res['error']} "
          f"retries={res['retries']})")

    # 3) 一直 error → 3 次后接受带 error 标记结果，不无限重试。
    calls.clear()
    res = run_rl_intent.run_clean_eval(
        "bun", "tmp/w.json", args,
        _runner=fake_clean(_report_text(350, 350, 0, 0.0)))
    check(len(calls) == run_rl_intent.CLEAN_EVAL_MAX_RETRY and res["error"] > 0
          and res["retries"] == run_rl_intent.CLEAN_EVAL_MAX_RETRY - 1,
          f"超限后接受 error 标记 (calls={len(calls)} retries={res['retries']} error={res['error']})")

    # 4) games 字段兼容 dispatch_eval_bg_intent（= 35 × eval_seeds）。
    check(res["games"] == 350, f"games 兼容字段 (got {res['games']})")


def main() -> None:
    test_parse_m1_eval_report()
    test_run_clean_eval_rerun()
    print()
    if FAILS:
        print(f"RESULT: {len(FAILS)} FAILURE(S)")
        for f in FAILS:
            print("  - " + f)
        raise SystemExit(1)
    print("RESULT: ALL PASS")


if __name__ == "__main__":
    main()
