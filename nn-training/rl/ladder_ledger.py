"""ladder_ledger.py —— I5（roadmap §4-I5）：阶梯统一 identity 台账（Python 侧）。

一张表覆盖 20 级阶梯 + 经典 35 关（hy X3：不维护两套状态机）：
  nn-training/ladder/LEDGER.jsonc
    levels.<level_id> = {
      status: pending|bc|ppo|graduated|stuck,
      hypothesis: 开工前必写的训练侧假说（hy R2.3——无假说不开工）,
      teacherWR: 教师探针胜率（A5）,
      lastGate: { verdict, pooledPassRate, wilsonLB, report }（I4 runner 写）,
      disk_bytes / ckpt_keep_list（R5/E5）,
      graduate_weights / gate_report / tier_boundary_ack（D11 立案处人工置位）,
    }

写方：I4 gate runner（TS，verdict/lastGate）、本模块 CLI（runbook 第 2 步 hypothesis、
卡门 escalate、毕业 graduate_weights 登记）。多写方共用一个文件——写入一律
「读-合并-临时文件-原子 rename」，字段级 merge 互不覆盖。

CLI（runbook 消费）：
  python -m rl.ladder_ledger show [--level ladder-c04]
  python -m rl.ladder_ledger hypothesis --level ladder-c04 --text "wDmg=0 + wChip 0.03，预期 dmg/kill ↓"
  python -m rl.ladder_ledger escalate --level ladder-c04 --text "卡门 3 周期"
  python -m rl.ladder_ledger graduate --level ladder-c04 --weights <w> [--ack]
        （--ack 仅 tier 边界 c07/c14/c20 需要：缺 --ack 直接拒绝毕业 = D11 人工放行）
  python -m rl.ladder_ledger disk --level ladder-c04 --traj tmp/ladder-c04
"""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any

from platform_utils import force_utf8_stdio

NN_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LEDGER = NN_ROOT / "ladder" / "LEDGER.jsonc"

STATUSES = ("pending", "bc", "ppo", "graduated", "stuck")
#: tier 边界：晋级过线需人工 ack（D11 立案处，ms C2 自动晋级在此停）
TIER_BOUNDARIES = ("ladder-c07", "ladder-c14", "ladder-c20")


class LadderLedger:
    def __init__(self, path: str | Path = DEFAULT_LEDGER) -> None:
        self.path = Path(path)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "levels": {}}
        try:
            data: dict[str, Any] = json.loads(self.path.read_text(encoding="utf-8"))
            return data
        except (OSError, ValueError) as e:
            print(f"[{time.strftime('%H:%M:%S')}] [ladder-ledger] 读取失败（{e}）——返回空台账，写入前请先人工核对")
            return {"version": 1, "levels": {}}

    def save(self, data: dict[str, Any]) -> None:
        """读-合并-原子 rename：多写方（TS gate / 本 CLI / 训练循环）字段级安全。"""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, self.path)

    def mark(self, level: str, **patch: Any) -> dict[str, Any]:
        data = self.load()
        levels = data.setdefault("levels", {})
        entry: dict[str, Any] = levels.setdefault(level, {})
        entry.update(patch)
        entry["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        self.save(data)
        return dict(entry)

    def disk_bytes(self, level: str, traj: str | Path) -> int:
        """traj 目录体量（R5/E5 磁盘账）：walk 求和，目录不存在返回 0。"""
        total = 0
        root = Path(traj)
        if root.exists():
            for p in root.rglob("*"):
                try:
                    if p.is_file():
                        total += p.stat().st_size
                except OSError:
                    pass
        self.mark(level, disk_bytes=total)
        return total


def main() -> None:
    # CLI 入口钉 UTF-8（2026-09-15）：本模块的 SystemExit/日志含中文（如 tier 边界
    # 拒绝语），原先漏了这行 ⇒ 无 PYTHONIOENCODING 的环境下按 zh-CN 代码页 cp936
    # 输出，父进程 tests/subproc_util.run_utf8 严格按 UTF-8 解码即读线程死亡、
    # stdout/stderr 变 None（test_ladder_ledger 直接 TypeError）。契约见
    # tests/subproc_util.py docstring：**每个 CLI 入口**都要调，不只是被捕获的那些。
    force_utf8_stdio()
    ap = argparse.ArgumentParser(description="I5 阶梯台账 CLI（runbook 消费）")
    ap.add_argument("cmd", choices=("show", "hypothesis", "escalate", "graduate", "disk"))
    ap.add_argument("--level", required=True)
    ap.add_argument("--text", default="")
    ap.add_argument("--weights", default="")
    ap.add_argument("--traj", default="")
    ap.add_argument(
        "--ack",
        action="store_true",
        help="tier 边界（c07/c14/c20）毕业的人工放行位（D11 立案后由人置位）",
    )
    ap.add_argument(
        "--ledger",
        default=str(DEFAULT_LEDGER),
        help="台账路径（默认 nn-training/ladder/LEDGER.jsonc；测试/演练用临时路径）",
    )
    args = ap.parse_args()

    led = LadderLedger(args.ledger)
    if args.cmd == "show":
        data = led.load()
        levels = data.get("levels", {})
        if args.level:
            print(json.dumps(levels.get(args.level, {}), ensure_ascii=False, indent=2))
        else:
            for k in sorted(levels):
                lv = levels[k]
                print(f"{k}: {lv.get('status', 'pending')} lastGate={lv.get('lastGate', {}).get('verdict', '-')}")
        return
    if args.cmd == "hypothesis":
        if not args.text:
            raise SystemExit("hypothesis 需要 --text（无假说不开工，hy R2.3）")
        led.mark(args.level, hypothesis=args.text, status="ppo")
        print(f"[{time.strftime('%H:%M:%S')}] [ladder-ledger] {args.level}: hypothesis 已登记")
    elif args.cmd == "escalate":
        if not args.text:
            raise SystemExit("escalate 需要 --text（卡门原因，供用户复盘）")
        led.mark(args.level, status="stuck", escalate_reason=args.text)
        print(f"[{time.strftime('%H:%M:%S')}] [ladder-ledger] {args.level}: stuck（{args.text}）——上报用户")
    elif args.cmd == "graduate":
        # D11 人工放行护栏：tier 边界（c07/c14/c20）**必须**显式 --ack 才能毕业。
        # 旧实现按「level ∈ TIER_BOUNDARIES」自动置 True —— 恰好把唯一需要人工确认的
        # 那三级自动放行了，护栏形同虚设（ms C2 自动晋级在此停的意义被抹掉）。
        if args.level in TIER_BOUNDARIES and not args.ack:
            raise SystemExit(
                f"[ladder-ledger] {args.level} 是 tier 边界（D11 立案处）："
                "毕业需人工放行 —— 确认 DECISIONS 已立案后重跑并加 --ack"
            )
        led.mark(
            args.level,
            status="graduated",
            graduate_weights=args.weights,
            tier_boundary_ack=bool(args.ack),
        )
        print(
            f"[ladder-ledger] {args.level}: graduated（weights={args.weights or '见 lastGate'}"
            f"{'，tier 边界已人工放行' if args.ack else ''}）"
        )
    elif args.cmd == "disk":
        if not args.traj:
            raise SystemExit("disk 需要 --traj")
        total = led.disk_bytes(args.level, args.traj)
        print(f"[{time.strftime('%H:%M:%S')}] [ladder-ledger] {args.level}: disk {total / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
