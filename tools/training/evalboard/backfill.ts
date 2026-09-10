/** backfill.ts — P4 回填入队（plan/rl-eval-system.md §10.5）。
 *
 * 回填 p3-kb1 / c4-kb1 / c4-margin 三腿各 3–5 个关键 ckpt × 阶梯前 3 关 × 100 局
 * （≤4500 局，eval860k 空间）。k_seq 全 0 → 同 100 seed（段 0）→ ckpt 间配对可比。
 * only_rungs 精确到单关，避免门控前瞻多跑（预算内）。
 *
 * 用法：
 *   bun tools/training/evalboard/backfill.ts --course c4-margin \
 *     --ckpt tmp/c4-margin/weights.it30.json --ckpt tmp/c4-margin/weights.it60.json \
 *     --rungs c4l1,c6l1,c8l2 --init tmp/c4-margin/init.json [--dry]
 */

import { existsSync } from 'fs'
import { enqueueBatch } from './batches'
import { evalDataRoot } from '../console/evalboard'
import { buildLadder } from './ladder'

function arg(argv: string[], k: string, dft = ''): string {
  const i = argv.indexOf(k)
  return i >= 0 ? (argv[i + 1] ?? dft) : dft
}

function args(argv: string[], k: string): string[] {
  const out: string[] = []
  for (let i = 0; i < argv.length; i++) if (argv[i] === k && argv[i + 1]) out.push(argv[++i])
  return out
}

if (import.meta.main) {
  const argv = process.argv.slice(2)
  const course = arg(argv, '--course')
  const ckpts = args(argv, '--ckpt')
  const rungs = arg(argv, '--rungs', 'c4l1,c6l1,c8l2')
    .split(',')
    .map((s) => s.trim())
  const init = arg(argv, '--init')
  const dry = argv.includes('--dry')
  if (!course || ckpts.length === 0) {
    console.error('[backfill] --course + --ckpt（可重复） 必需')
    process.exit(2)
  }
  const ladder = buildLadder()
  const root = evalDataRoot()
  let n = 0
  for (const ckpt of ckpts) {
    if (!existsSync(ckpt)) {
      console.error(`[backfill] ckpt 不存在，跳过: ${ckpt}`)
      continue
    }
    for (const rung of rungs) {
      const pos = ladder.findIndex((r) => r.id === rung)
      if (pos < 0) {
        console.error(`[backfill] 未知 rung，跳过: ${rung}`)
        continue
      }
      if (dry) {
        console.log(`[backfill] dry: ${course} ${ckpt} ${rung} pos=${pos} k=0`)
        n++
        continue
      }
      const b = enqueueBatch(root, {
        course,
        rung_from: rung,
        ckpt,
        requester: 'backfill-p4',
        trigger: 'standalone',
        iter: 0,
        units: { of: 1, done: [] },
        k_seq: 0,
        window_seq: 0,
        policy: 'nn',
        ladder_pos: pos,
        ...(init ? { init_sha16: init } : {}),
        only_rungs: [rung],
      })
      console.log(`[backfill] queued ${b.batch_id} ${course} ${ckpt} ${rung}`)
      n++
    }
  }
  console.log(`[backfill] ${dry ? 'dry-run ' : ''}${n} batches`)
}
