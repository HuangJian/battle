#!/usr/bin/env bun
/**
 * diag-resim.ts — 诊断重仿驱动（plan/diag-lane-instrument.plan.md §1）。
 * 同权重同种子本地重跑 verdict 对局，逐局记录危险遥测（encl/threat/onLane/back/Edhit），
 * 与终判行逐种子对账（kills/win 全字段一致才算数）。
 * 只读调用 runEvalOne；不改任何现有文件/语义；不在 dist 哈希集内（本地诊断专用）。
 *
 * Usage:
 *   bun tools/sim/diag-resim.ts --course nn-training/levels/ladder-c20-lives1.jsonc \
 *     --weights <w>.json --seed0 415000 --games 800 --decision-k 10 --out tmp/diag.jsonl
 *   seed 映射与 trainer/eval_course_once._stage_plan 同式：si = g % 4，seed = seed0 + g // 4。
 */
import { readFileSync, writeFileSync } from 'fs'
import { runEvalOne } from './export-eval-game'
import { stripTrailingCommas } from './eval-course-ckpt'
import { decodeStageGrid } from '../../src/nn/config-stage'

function arg(name: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : undefined
}

function req(name: string): string {
  const v = arg(name)
  if (v === undefined) {
    console.error(`[diag-resim] --${name} required`)
    process.exit(2)
  }
  return v
}

/** 去 // 行注释（关卡文件无 URL，调用前已断言无 ://；字符串内 // 不处理——关卡无此用例）。 */
function parseCourseJsonc(text: string): any {
  if (text.includes('://')) throw new Error('[diag-resim] course contains ://, refuse naive strip')
  const body = stripTrailingCommas(
    text
      .split('\n')
      .map((l) => {
        const i = l.indexOf('//')
        return i >= 0 ? l.slice(0, i) : l
      })
      .join('\n'),
  )
  return JSON.parse(body)
}

const coursePath = req('course')
const weightsPath = req('weights')
const seed0 = parseInt(req('seed0'), 10)
const games = parseInt(req('games'), 10)
const skip = parseInt(arg('skip') ?? '0', 10)
const decisionK = parseInt(arg('decision-k') ?? '10', 10)
const out = req('out')
if (
  ![seed0, games, decisionK].every((n) => Number.isInteger(n) && n > 0) ||
  !Number.isInteger(skip) ||
  skip < 0
) {
  console.error('[diag-resim] seed0/games/decision-k must be positive ints, skip >= 0')
  process.exit(2)
}

const course = parseCourseJsonc(readFileSync(coursePath, 'utf8'))
const rawStages: any[] = course.stages
if (!Array.isArray(rawStages) || rawStages.length < 4)
  throw new Error('[diag-resim] need ≥4 stages')
// 与 main() 同式：课程 13×13 瓦格经 decodeStageGrid 解码后传 runEvalOne（loadIndex 0）。
const stages: any[] = [0, 1, 2, 3].map((si) => decodeStageGrid(rawStages[si], si))
const difficulty: string = course.difficulty ?? 'hard'
const maxTicks: number = course.max_ticks ?? 12900
// 与 eval-course-ckpt.ts:713-714 同式：命数/星级在 course.player 下（顶层无此键，误读即 3 命神仙局）。
const lives: number | null = typeof course.player?.lives === 'number' ? course.player.lives : null
const level: number | null = typeof course.player?.level === 'number' ? course.player.level : null
const weightsText = readFileSync(weightsPath, 'utf8')

const pick = (r: any, k: string) => (r as any)[k] ?? null
const t0 = Date.now()
const lines: string[] = []
// 分片语义：g 跑 [skip, skip+games)，seed/关映射只看 g ⇒ 分片拼接与串行逐字节一致。
for (let g0 = 0; g0 < games; g0++) {
  const g = skip + g0
  const si = g % 4
  const seed = seed0 + Math.floor(g / 4)
  const r: any = runEvalOne(
    0,
    stages[si],
    seed,
    difficulty,
    maxTicks,
    weightsText,
    'nn',
    '',
    '',
    0,
    0,
    lives,
    level,
    '',
    null,
    false,
    decisionK,
  )
  lines.push(
    JSON.stringify({
      seed,
      stageId: 2000 + si,
      win: r.win ? 1 : 0,
      kills: r.kills,
      ticks: r.ticks,
      encl1: pick(r, 'encl1Ticks'),
      encl2: pick(r, 'encl2Ticks'),
      encl3p: pick(r, 'encl3pTicks'),
      enclMax: pick(r, 'enclMax'),
      threat: pick(r, 'threatTicks'),
      lane: pick(r, 'onLaneTicks'),
      back: pick(r, 'backHits'),
      side: pick(r, 'sideHits'),
      aimHits: pick(r, 'aimHits'),
      aimDist: pick(r, 'aimHitDistSum'),
      danger: pick(r, 'dangerTicks'),
      near4: pick(r, 'nearEnemy4Ticks'),
      dmgTaken: pick(r, 'playerDamageTaken'),
      shots: pick(r, 'playerShots'),
    }),
  )
  if ((g0 + 1) % 50 === 0 || g0 + 1 === games)
    console.error(`[diag-resim] ${g0 + 1}/${games} (skip=${skip})`)
}
writeFileSync(out, lines.join('\n') + '\n')
console.error(
  `[diag-resim] wrote ${games} rows -> ${out} (${((Date.now() - t0) / 1000).toFixed(1)}s)`,
)
