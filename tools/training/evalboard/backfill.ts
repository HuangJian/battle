/** backfill.ts — P4 回填入队（plan/rl-eval-system.md §10.5）。
 *
 * 回填 p3-kb1 / c4-kb1 / c4-margin 三腿各 3–5 个关键 ckpt × 阶梯前 3 关 × 100 局
 * （≤4500 局，eval860k 空间）。k_seq 全 0 → 同 100 seed（段 0）→ ckpt 间配对可比。
 * only_rungs 精确到单关，避免门控前瞻多跑（预算内）。
 *
 * **写路径（2026-09-11 修正）**：此前直接 `enqueueBatch` 写 `batches.jsonl`，
 * 与 runner（唯一写者）并发时会撞 D-c 竞态。现改道 `requests.jsonl`（P1 协议，
 * console/CLI 只 append 请求，runner 消费后物化批次）—— 与
 * `POST /api/evalProbeRun` 同一条路径。
 * 副作用：本文件返回的不再是 batch_id，而是 req_id（批次要等 runner 下窗才建）。
 *
 * 用法：
 *   bun tools/training/evalboard/backfill.ts --course c4-margin \
 *     --ckpt tmp/c4-margin/weights.it30.json --ckpt tmp/c4-margin/weights.it60.json \
 *     --rungs c4l1,c6l1,c8l2 --init tmp/c4-margin/init.json [--dry]
 */

import { existsSync } from 'fs'
import { appendRequest } from './requests'
import { evalDataRoot, iterFromCkpt } from '../console/evalboard'
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
    // iter 从 ckpt 路径解析（`weights.it30.json` → 30），与 console 侧
    // `iterFromCkpt` 同口径 —— 此前硬编码 0，会让所有回填批挤在 iter=0。
    const iter = iterFromCkpt(ckpt) ?? 0
    for (const rung of rungs) {
      const pos = ladder.findIndex((r) => r.id === rung)
      if (pos < 0) {
        console.error(`[backfill] 未知 rung，跳过: ${rung}`)
        continue
      }
      if (dry) {
        console.log(`[backfill] dry: ${course} ${ckpt} ${rung} pos=${pos} k=0 it=${iter}`)
        n++
        continue
      }
      // units 不传：runner 物化时用默认，随后按 only_rungs 过滤由 _persist_of 回写真实 unit 数。
      const req = appendRequest(root, {
        kind: 'enqueue',
        course,
        rung_from: rung,
        ckpt,
        requester: 'backfill-p4',
        trigger: 'standalone',
        iter,
        policy: 'nn',
        ladder_pos: pos,
        ...(init ? { init_sha16: init } : {}),
        only_rungs: [rung],
      })
      console.log(`[backfill] requested ${req.req_id} ${course} ${ckpt} ${rung} it${iter}`)
      n++
    }
  }
  console.log(`[backfill] ${dry ? 'dry-run ' : ''}${n} requests（等 runner 下窗物化）`)
}
