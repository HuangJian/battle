/** ingest-cli.ts — EvalStore 入账 CLI（§10.1 T0.3/T0.5）。
 *
 * 训练进程的 A 层自动入账钩子 + 现存活腿 eval_log.jsonl 顺手 ingest（T0.5）。
 * 唯一写入路径是 `ingestRows`（ingest.ts）；本文件只做 argv/文件读取。
 *
 * 用法：
 *   bun tools/training/evalboard/ingest-cli.ts --eval-log <eval_log.jsonl> \
 *     --data tools/training/data/evalboard --run <run_id> --course <course> \
 *     --batch <batch_id> --ckpt <weights.json> --init <init.json> \
 *     --difficulty hard --max-ticks 2400 [--source A] [--rung <rung>] [--map-hash <h>]
 */

import { existsSync, readFileSync } from 'fs'
import { createHash } from 'node:crypto'
import { ingestRows, type IngestCtx, type RawEvalRow } from './ingest'

function arg(argv: string[], k: string, dft = ''): string {
  const i = argv.indexOf(k)
  return i >= 0 ? (argv[i + 1] ?? dft) : dft
}

function sha16OfFile(p: string): string {
  try {
    return createHash('sha1').update(readFileSync(p)).digest('hex').slice(0, 16)
  } catch {
    return 'missing0000000000'.slice(0, 16)
  }
}

export function runIngestCli(argv: string[]): { appended: number; duplicate: number } {
  const evalLog = arg(argv, '--eval-log')
  const data = arg(argv, '--data', 'tools/training/data/evalboard')
  if (!evalLog || !existsSync(evalLog)) throw new Error(`--eval-log 缺失: ${evalLog}`)
  const ckpt = arg(argv, '--ckpt')
  const fixedRung = arg(argv, '--rung')
  const fixedMapHash = arg(argv, '--map-hash', 'unknown')
  const ctx: IngestCtx = {
    run_id: arg(argv, '--run', 'manual'),
    course: arg(argv, '--course', 'unknown'),
    batch_id: arg(argv, '--batch', `manual-${Date.now()}`),
    batch_of: Number(arg(argv, '--batch-of', '1')) || 1,
    rungOfStage: (s) => fixedRung || String(s),
    engine: {
      git_commit: arg(argv, '--git-commit', 'unknown'),
      dist_codehash: arg(argv, '--dist-codehash', 'unknown'),
      engine_epoch: arg(argv, '--engine-epoch', 'unknown'),
    },
    source: (arg(argv, '--source', 'A') as IngestCtx['source']) || 'A',
    ckpt_path: ckpt,
    ckpt_sha16: sha16OfFile(ckpt),
    init_sha16: sha16OfFile(arg(argv, '--init')),
    difficulty: arg(argv, '--difficulty', 'hard'),
    maxTicks: Number(arg(argv, '--max-ticks', '2400')) || 2400,
    mapHashOfStage: () => fixedMapHash,
  }
  const raws: RawEvalRow[] = []
  for (const line of readFileSync(evalLog, 'utf-8').split('\n')) {
    if (!line.trim()) continue
    try {
      const r = JSON.parse(line) as RawEvalRow
      if (r.event === 'eval') raws.push(r)
    } catch {
      /* 坏行跳过 */
    }
  }
  return ingestRows(data, raws, ctx)
}

if (import.meta.main) {
  try {
    const r = runIngestCli(process.argv.slice(2))
    console.log(`[ingest] appended=${r.appended} duplicate=${r.duplicate}`)
  } catch (e) {
    console.error(`[ingest] FAIL ${e instanceof Error ? e.message : String(e)}`)
    process.exit(1)
  }
}
