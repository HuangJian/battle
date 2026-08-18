/**
 * verify-demos.ts — 验收人类演示 NDJSON 语料（训练数据质量门，NN-M0）。
 *
 * 逐局：parseReplayFile → restoreWorld → ReplayInput → 真实 Simulation 重放
 * （复用 ./verify-replay.ts 的 verifyReplayText），另加：
 *   - 动作分布（fire/move/方向直方图）— BC 数据健全性
 *   - 同关多局的 rngState 对比 — seed 会话级共享时的多样性检查
 *   - initialSnapshot 完整性抽查（difficultyKey / playerLevel / lives）
 *   - yield-rate 汇总（OK / DESYNC 计数）— 语料可用性指标
 *
 * 用法：
 *   bun tools/replay/verify-demos.ts <demos1.ndjson> [demos2.ndjson ...] [--json] [--out report.json]
 *
 * 退出码：有任一 DESYNC 局返回 1，否则 0。
 */
import { verifyReplayText } from './verify-replay'
import { unpackFrames } from '../../src/replay/pack'
import { parseReplayFile } from '../../src/replay/file'
import type { Direction } from '../../src/constants'
import type { InputFrame } from '../../src/replay/types'
import { writeFileSync } from 'node:fs'

// ---- arg parsing ----
const args = process.argv.slice(2)
const files: string[] = []
let json = false
let outPath: string | null = null
for (let i = 0; i < args.length; i++) {
  const a = args[i]
  if (a === '--json') json = true
  else if (a === '--out') outPath = args[++i]
  else files.push(a)
}
if (files.length === 0) {
  console.error('usage: bun tools/replay/verify-demos.ts <demos1.ndjson> [..] [--json] [--out report.json]')
  process.exit(2)
}

interface Row {
  i: number
  file: string
  stage: number
  type: string
  finalState: string
  endedAtTick: number
  totalTicks: number
  verdict: string
  score: number
  kills: number
  lives: number
  baseAlive: boolean
  fireTicks: number
  moveTicks: number
  firstFire: number
  dirHist: Record<string, number>
  rngState: string
  difficultyKey: string
  playerLevel: number
  hash: string
}

const rows: Row[] = []
let desync = 0

for (const file of files) {
  const text = await Bun.file(file).text()
  const lines = text.split('\n').filter((l) => l.trim().length > 0)
  if (!json) console.log(`file=${file} replays=${lines.length}`)

  for (let i = 0; i < lines.length; i++) {
    const parsed = parseReplayFile(lines[i])
    if ('error' in parsed) throw new Error(`file=${file} line ${i}: parse failed: ${parsed.error}`)
    const replay = parsed.replay
    const r = verifyReplayText(lines[i], `demo-${i}`)
    const un = unpackFrames(replay.frames)
    const p1: InputFrame[] = un?.p1 ?? []
    const dirHist: Record<string, number> = {}
    let fireTicks = 0
    let moveTicks = 0
    for (const f of p1) {
      if (f.firing) fireTicks++
      if (f.direction !== null) {
        moveTicks++
        dirHist[f.direction as Direction] = (dirHist[f.direction as Direction] ?? 0) + 1
      }
    }
    const firstFire = p1.findIndex((f) => f.firing)
    const snap = replay.initialSnapshot
    const hash =
      r.hashVerified === null
        ? 'n/a'
        : r.hashVerified === true
          ? 'ok'
          : `mismatch@t${r.firstHashMismatch?.checkpoint ?? '?'}`
    rows.push({
      i,
      file,
      stage: replay.metadata.stage,
      type: (replay as { type?: string }).type ?? '(none)',
      finalState: r.finalState,
      endedAtTick: r.endedAtTick,
      totalTicks: r.totalTicks,
      verdict: r.verdict,
      score: r.score,
      kills: r.killCount,
      lives: r.lives,
      baseAlive: r.baseAlive,
      fireTicks,
      moveTicks,
      firstFire,
      dirHist,
      rngState: JSON.stringify(snap.rngState),
      difficultyKey: snap.difficultyKey ?? '(none)',
      playerLevel: snap.playerLevel ?? -1,
      hash,
    })
    if (r.verdict !== 'OK') desync++
  }
}

// ---- per-replay detail ----
if (!json) {
  console.log('\n== per-replay verify (real Simulation replay) ==')
  for (const r of rows) {
    console.log(
      `[${r.verdict}] ${r.file} #${r.i} stage=${r.stage} type=${r.type} -> final='${r.finalState}' @${r.endedAtTick}/${r.totalTicks}` +
        ` score=${r.score} kills=${r.kills} lives=${r.lives} baseAlive=${r.baseAlive} diff=${r.difficultyKey} lv=${r.playerLevel} hash=${r.hash}`,
    )
    console.log(
      `       actions: fire=${r.fireTicks} (${((r.fireTicks / r.totalTicks) * 100).toFixed(1)}%) move=${r.moveTicks} (${((r.moveTicks / r.totalTicks) * 100).toFixed(1)}%) firstFire@${r.firstFire} dirs=${JSON.stringify(r.dirHist)}`,
    )
  }

  console.log('\n== same-stage rngState diversity ==')
  const byStage = new Map<number, string[]>()
  for (const r of rows) {
    const arr = byStage.get(r.stage) ?? []
    arr.push(r.rngState)
    byStage.set(r.stage, arr)
  }
  for (const [stage, states] of byStage) {
    const unique = new Set(states)
    console.log(`stage=${stage}: runs=${states.length} unique-initial-rngState=${unique.size}`)
  }
}

const clears = rows.filter((r) => r.finalState === 'stageclear' || r.finalState === 'victory').length
const summary = {
  files: files.length,
  replays: rows.length,
  ok: rows.length - desync,
  desync,
  clears,
  nonClear: rows.length - clears,
  yieldRate: rows.length === 0 ? 0 : (rows.length - desync) / rows.length,
}

if (json) {
  const out = { rows, summary }
  if (outPath) {
    writeFileSync(outPath, JSON.stringify(out, null, 2))
    console.log(`wrote ${outPath}`)
  } else {
    process.stdout.write(JSON.stringify(out) + '\n')
  }
} else {
  console.log(
    `\nsummary: ${rows.length} replays across ${files.length} file(s), ${clears} clear, ${rows.length - clears} non-clear, desync=${desync} (yield-rate=${((summary.yieldRate) * 100).toFixed(1)}%)`,
  )
}

process.exit(desync > 0 ? 1 : 0)
