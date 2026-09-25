/** verdict-cli.ts — 判决批入口（P2，2026-09-19「中方案」）。
 *
 * 判决 = 「语料（课程关卡文件 × 池外 seed 段）× N 个 ckpt 同批同种子配对」。
 * 本 CLI 只做**入队**：按 console 单写者纪律往 `requests.jsonl` 追加一条
 * `kind=verdict` 请求（runbook：谁都不直接写 batches.jsonl / games/ —— runner 单写）。
 * 消费与执行由 runner（`rl/batch_store.py::BatchStore.consume_requests` 在下窗拾取，或
 * `dashboard/src/evalboard/kick-once.py` 一次性 kick）完成。
 *
 * 用法（仓根）：
 *   bun dashboard/src/evalboard/verdict-cli.ts --list
 *   bun dashboard/src/evalboard/verdict-cli.ts --corpus v-ladder-c03-p400600 \
 *     --ckpt bc=nn-training/weights/x3-start.it333.json \
 *     --ckpt it30=nn-training/weights/x20-rebirth/x20-rebirth.it30.json \
 *     --requester cli [--policy nn] [--iter 30] [--dry] [--json]
 *
 * 退出码：0 入队成功（或 dry 打印）；1 用法/校验错误。
 */

import { existsSync } from 'fs'
import path from 'path'
import {
  corporaPath,
  corpusOf,
  loadCorpora,
  seedsOfCorpus,
  verdictKeyOf,
  type VerdictCorpus,
} from './corpora'
import { loadBatches } from './batches'
import { appendRequest, pendingRequests, readRequests, verdictQueued } from './requests'

const REPO_ROOT = path.resolve(import.meta.dir, '..', '..', '..')

function dataRoot(): string {
  return process.env.EVALBOARD_DATA ?? path.join(REPO_ROOT, 'dashboard', 'data', 'evalboard')
}

interface Args {
  list: boolean
  corpus: string
  ckpts: Array<{ label: string; path: string }>
  requester: string
  policy: string
  iter: number
  dry: boolean
  json: boolean
}

function parseArgs(argv: string[]): Args {
  const a: Args = {
    list: false,
    corpus: '',
    ckpts: [],
    requester: 'cli',
    policy: 'nn',
    iter: 0,
    dry: false,
    json: false,
  }
  for (let i = 0; i < argv.length; i++) {
    const v = argv[i]
    const next = (): string => argv[++i] ?? ''
    if (v === '--list') a.list = true
    else if (v === '--corpus') a.corpus = next()
    else if (v === '--requester') a.requester = next()
    else if (v === '--policy') a.policy = next()
    else if (v === '--iter') a.iter = Number(next()) || 0
    else if (v === '--dry') a.dry = true
    else if (v === '--json') a.json = true
    else if (v === '--ckpt') {
      const spec = next()
      const eq = spec.indexOf('=')
      // `label=path`；无 `=` 时 label 取文件名（与 eval-course-ckpt 的 --weights 同习惯）。
      if (eq > 0 && !spec.slice(0, eq).includes('/') && !spec.slice(0, eq).includes('\\')) {
        a.ckpts.push({ label: spec.slice(0, eq), path: spec.slice(eq + 1) })
      } else {
        a.ckpts.push({ label: path.basename(spec), path: spec })
      }
    } else {
      throw new Error(`未知参数: ${v}`)
    }
  }
  return a
}

function describe(c: VerdictCorpus): string {
  const seeds = seedsOfCorpus(c)
  return (
    `${c.id}\n  level=${c.level}  policy=${c.policy ?? 'nn'}  ` +
    `seeds=${seeds[0]}..${seeds[seeds.length - 1]}（${c.games_per_stage}/关）` +
    (c.note ? `\n  note: ${c.note}` : '')
  )
}

function main(): number {
  let args: Args
  try {
    args = parseArgs(process.argv.slice(2))
  } catch (e) {
    console.error(`[verdict] ${(e as Error).message}`)
    return 1
  }
  if (args.list || (!args.corpus && args.ckpts.length === 0)) {
    const doc = loadCorpora(REPO_ROOT)
    console.log(`[verdict] 语料注册表 ${corporaPath(REPO_ROOT)}（${doc.corpora.length} 条）`)
    for (const c of doc.corpora) console.log(describe(c))
    return args.list ? 0 : 1
  }
  try {
    const corpus = corpusOf(REPO_ROOT, args.corpus)
    if (args.ckpts.length === 0) throw new Error('--ckpt 至少一个（label=path）')
    for (const c of args.ckpts) {
      if (!c.path) throw new Error(`--ckpt ${c.label} 缺 path`)
      if (!existsSync(path.resolve(REPO_ROOT, c.path)) && !path.isAbsolute(c.path))
        throw new Error(`--ckpt ${c.label}: 权重文件不存在 ${c.path}`)
    }
    const root = dataRoot()
    const key = verdictKeyOf(corpus.id, args.ckpts)
    const queued = verdictQueued(loadBatches(root), readRequests(root), corpus.id, args.ckpts)
    const req = {
      kind: 'verdict' as const,
      requester: args.requester,
      corpus: corpus.id,
      ckpts: args.ckpts,
      policy: (args.policy === 'god' ? 'god' : 'nn') as 'nn' | 'god',
      iter: args.iter,
    }
    if (queued) {
      console.log(`[verdict] 同键判决已在队列/已物化（${key}）—— 不重复入队`)
      return 0
    }
    if (args.dry) {
      console.log(JSON.stringify({ dry: true, dataRoot: root, key, request: req }, null, 2))
      return 0
    }
    const appended = appendRequest(root, req)
    if (args.json) console.log(JSON.stringify({ req_id: appended.req_id, key, request: req }))
    else
      console.log(
        `[verdict] 入队 ${appended.req_id} · 语料 ${corpus.id} · ${args.ckpts.length} ckpt · ` +
          `待消费请求 ${pendingRequests(root).length} 条（runner 下窗或 kick-once 消费）`,
      )
    return 0
  } catch (e) {
    console.error(`[verdict] ${(e as Error).message}`)
    return 1
  }
}

if (import.meta.main) process.exit(main())
