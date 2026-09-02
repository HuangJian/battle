/**
 * v7-phi-oracle.ts — v7 势的 TS oracle（plan/rl-training-config.md §4.6-2）。
 *
 * 读 `counters.jsonl`（每行一个 `PhiCounters`），逐行算 `phiNow`，把 `[phi...]`
 * 写到 stdout（JSON）。供 `nn-training/scripts/regen_v7_ts_oracle.py` 一次性生成
 * `nn-training/tests/golden/v7_phi_ts_oracle.json` —— pytest 断言 Python 的 v7
 * 公式/内置对该 golden ≤1e-9。bun 侧不留调 Python 子进程的脆测试（评审 P1-8）。
 *
 * ⚠️ basePressureMean 字段命名坑：rl-reward.ts 的 lossPartialQ 算
 * `1 − basePressureMean/basePressureSamples`。rollout 口径（export-rl-rollout.ts）
 * 是 `1 − basePressureSum/basePressureSamples`——生成侧把 **sum** 放进
 * `basePressureMean` 字段即可精确复现 rollout 口径（两次相除变成一次）。
 *
 * 用法：bun tools/diag/v7-phi-oracle.ts < counters.jsonl > phi.json
 */

import { readFileSync } from 'fs'
import { phiNow, type PhiCounters } from '../sim/rl-reward'

let text: string
try {
  text = readFileSync(0, 'utf8')
} catch {
  console.error('[v7-phi-oracle] 用法: bun tools/diag/v7-phi-oracle.ts < counters.jsonl > phi.json')
  process.exit(2)
}
const rows = text
  .split('\n')
  .filter((l) => l.trim())
  .map((l) => JSON.parse(l) as PhiCounters)
const out: number[] = []
for (const r of rows) {
  // JSON 无 undefined：firstKillTick:null 语义 = 无首杀（TS undefined）
  const c: PhiCounters = {
    ...r,
    baseAlive: !!r.baseAlive,
    firstKillTick:
      r.firstKillTick === null || r.firstKillTick === undefined ? undefined : r.firstKillTick,
  }
  out.push(phiNow(c))
}
process.stdout.write(JSON.stringify(out))
