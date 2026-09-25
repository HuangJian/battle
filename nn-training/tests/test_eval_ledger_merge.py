"""tests/test_eval_ledger_merge.py —— 云机 A 层评估读数并进课程账本的合并口径。

背景（2026-09-23 用户实测）：`x20-demo-mix` 云腿 it50–110、每 5 轮 400 局、`node=cloud`
的逐局读数**全在**账本里，控制台的 eval 列 / eval 弹窗 / 开课回执与门判据却一栏都不显示。
根因不在读方，在写方：两条腿（回传 / 导入）合并时都只并 `event:"eval"` 逐局行，把
`eval_summary` 丢掉了，而上面那些读方**只认 summary 行**（`dashboard/src/server/iters.ts`
的 `readEvalSummaries`、`rl/gate_check.py` 的 `read_trend_rows`）。原注释里那句「summary 由
课程侧按合并后的台账重算」对纯云腿不成立——没有本地循环，没人替它算。

本文件钉住合并的三条性质：
  * **单调**：同一 `(iter, wver)` 已有的 summary 不会被 `games` 更少的覆盖（重投/重导天然会重发）；
  * **幂等**：同一份行并两次，第二次一行不写；
  * **不猜**：坏行/形状不合法的行跳过，绝不抛。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from rl.eval_local import (
    append_eval_summaries,
    eval_summary_key,
    merge_eval_rows,
    read_eval_summary_rows,
)


def _game(it: int, seed: int, *, wver: str = "a" * 16, win: bool = True) -> dict:
    return {
        "event": "eval",
        "iter": it,
        "wver": wver,
        "stage": 0,
        "seed": seed,
        "node": "cloud",
        "win": win,
        "cleared": win,
    }


def _summary(it: int, *, wver: str = "a" * 16, games: int, wins: int) -> dict:
    return {
        "event": "eval_summary",
        "iter": it,
        "wver": wver,
        "time": "2026-09-23 10:00:00",
        "games": games,
        "wins": wins,
        "winRate": round(wins / games, 4) if games else None,
        "clears": wins,
        "outcomes": {"loss": games - wins, "stage_clear": wins},
        "nodes": {"cloud": games},
        "course_fp": "",
    }


def _write(path: Path, rows: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def _read(path: Path) -> list[dict]:
    return [json.loads(ln) for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_summary_key_rejects_shapes_it_cannot_identify() -> None:
    """身份键只认「事件对 + iter 是整数 + wver 非空」；其余一律 None（不猜）。"""
    assert eval_summary_key(_summary(3, games=4, wins=1)) == (3, "a" * 16)
    assert eval_summary_key(_summary(0, games=4, wins=1)) == (0, "a" * 16)  # it0 基线合法
    assert eval_summary_key({"event": "eval_summary", "iter": 3}) is None  # 缺 wver
    assert eval_summary_key({"event": "eval_summary", "wver": "x" * 16}) is None  # 缺 iter
    assert eval_summary_key({"event": "eval_summary", "iter": -1, "wver": "x" * 16}) is None
    assert eval_summary_key({"event": "eval", "iter": 3, "wver": "x" * 16}) is None  # 事件不对


def test_merge_carries_games_and_summary_together(tmp_path: Path) -> None:
    """一条腿的读数 = 逐局行 + summary 行；返回值把两类分开报（日志要能区分）。"""
    src = tmp_path / "src-eval_log.jsonl"
    dst = tmp_path / "eval_log.jsonl"
    _write(src, [_game(5, 1), _game(5, 2, win=False), _summary(5, games=2, wins=1)])

    assert merge_eval_rows(src, dst) == (2, 1)
    rows = _read(dst)
    assert [r["event"] for r in rows] == ["eval", "eval", "eval_summary"]
    s = rows[-1]
    assert (s["iter"], s["games"], s["wins"]) == (5, 2, 1) and s["nodes"] == {"cloud": 2}

    # 幂等：同一份再并一次，两类都不再写
    assert merge_eval_rows(src, dst) == (0, 0)
    assert len(_read(dst)) == 3


def test_summary_merge_is_monotone_never_downgrades(tmp_path: Path) -> None:
    """断点续跑：先落部分（200 局）、后补齐（400 局）⇒ 前者被后者升级；倒过来不覆盖。"""
    ledger = tmp_path / "eval_log.jsonl"
    assert append_eval_summaries(ledger, [_summary(7, games=200, wins=20)]) == 1
    assert append_eval_summaries(ledger, [_summary(7, games=400, wins=44)]) == 1
    # 后到的旧 summary（更少局）不许把新的覆盖掉——重投/重导天然会重发
    assert append_eval_summaries(ledger, [_summary(7, games=200, wins=20)]) == 0
    assert append_eval_summaries(ledger, [_summary(7, games=400, wins=44)]) == 0
    rows = [r for r in _read(ledger) if r["event"] == "eval_summary"]
    assert [r["games"] for r in rows] == [200, 400]
    # 读方（TS/Python 同口径）取最后一条 ⇒ 有效读数是补齐后的那一份
    assert read_eval_summary_rows(ledger)[-1]["winRate"] == round(44 / 400, 4)


def test_summary_without_games_is_allowed_but_never_upgrades(tmp_path: Path) -> None:
    """缺 `games` 的 summary（旧行/手写）按 0 算：没有它才写，有了就不再写第二行。"""
    ledger = tmp_path / "eval_log.jsonl"
    stub = {"event": "eval_summary", "iter": 2, "wver": "b" * 16}
    assert append_eval_summaries(ledger, [stub]) == 1
    assert append_eval_summaries(ledger, [stub]) == 0
    assert append_eval_summaries(ledger, [_summary(2, wver="b" * 16, games=400, wins=10)]) == 1
    assert len([r for r in _read(ledger) if r["event"] == "eval_summary"]) == 2


def test_bad_lines_and_unidentifiable_rows_are_skipped(tmp_path: Path) -> None:
    """坏 JSON / 非字典 / 形状不合法的 summary 都只跳过——合并绝不抛。"""
    src = tmp_path / "src.jsonl"
    dst = tmp_path / "eval_log.jsonl"
    src.write_text(
        "\n".join(
            [
                json.dumps(_game(1, 1)),
                "{ 这不是 JSON",
                json.dumps(["不是字典"]),
                json.dumps({"event": "eval_summary", "iter": 1}),  # 缺 wver
                json.dumps({"event": "iteration", "iter": 1}),  # 别的账本事件
                json.dumps(_summary(1, games=100, wins=9)),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    assert merge_eval_rows(src, dst) == (1, 1)
    assert [r["event"] for r in _read(dst)] == ["eval", "eval_summary"]


def test_missing_source_ledger_is_a_no_op(tmp_path: Path) -> None:
    """源账本不存在（云机没评过 / 旧包）：一行不写，也不抛。"""
    assert merge_eval_rows(tmp_path / "absent.jsonl", tmp_path / "eval_log.jsonl") == (0, 0)
    assert not (tmp_path / "eval_log.jsonl").exists()
