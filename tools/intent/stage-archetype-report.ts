#!/usr/bin/env bun
/**
 * stage-archetype-report.ts — 地图原型分桶报表（Goal-Space 重建手册 §16.0.1）。
 *
 * 动机：逐关卡胜率样本少（60 局 → SE 5.5pp）、噪声大，且不可解释。
 * 按**地图结构原型**分桶能同时提高样本量与可读性，也让分原型验收门（§16.4）可落地。
 *
 * 两个原型都用**现成的**纯函数/度量，不新造轮子：
 *   原型一「中路无钢」 → detectCentralBreachRisk(world)（stage-adapt.ts:68）
 *   原型二「砖墙密集」 → brick/(brick+steel) 密度（stage-adapt.ts:124-143 同款扫描）
 *
 * 用法：
 *   bun tools/intent/stage-archetype-report.ts
 *   bun tools/intent/stage-archetype-report.ts --report reports/godai-baseline-hard-35x60.json
 *   bun tools/intent/stage-archetype-report.ts --difficulty hard --md reports/stage-archetypes.md
 *
 * `--report` 接受 m1-eval 的 JSON 报告（或其 stdout 日志，自动截取 JSON 块），
 * 用于把逐关胜率 join 进来做分桶均值。
 */
import { readFileSync, writeFileSync } from 'node:fs'
import { STAGES } from '../../src/config/stages'
import { World } from '../../src/game/World'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { detectCentralBreachRisk } from '../../src/ai/god/stage-adapt'
import { GRID } from '../../src/constants'

function arg(name: string, dflt?: string): string | undefined {
  const i = process.argv.indexOf('--' + name)
  return i > 0 ? process.argv[i + 1] : dflt
}

const difficulty = arg('difficulty', 'hard') ?? 'hard'

interface Row {
  name: string
  win: number | null
  centralBreach: boolean
  brickRatio: number
  steelRatio: number
}

function loadWinRates(path?: string): Map<string, number> {
  const m = new Map<string, number>()
  if (!path) return m
  const text = readFileSync(path, 'utf8')
  const start = text.indexOf('{')
  const banner = text.indexOf('[m1-eval] WIN RATE')
  const json = text.slice(start, banner > start ? banner : undefined).trimEnd()
  const rep = JSON.parse(json) as {
    perStage?: Array<{ stage?: string; name?: string; winRate: number }>
  }
  for (const s of rep.perStage ?? []) m.set((s.stage ?? s.name) as string, s.winRate)
  return m
}

const wins = loadWinRates(arg('report'))
const rows: Row[] = []

for (let i = 0; i < STAGES.length; i++) {
  const stage = STAGES[i]
  const world = new World()
  world.rng.reseed(1)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.loadStageData(stage, i)

  let brick = 0
  let steel = 0
  for (let r = 0; r < GRID; r++) {
    for (let c = 0; c < GRID; c++) {
      const t = world.tileMap.get(c, r)
      if (t === 'brick') brick++
      else if (t === 'steel') steel++
    }
  }
  const denom = brick + steel
  rows.push({
    name: stage.name,
    win: wins.has(stage.name) ? wins.get(stage.name)! : null,
    centralBreach: detectCentralBreachRisk(world),
    brickRatio: denom > 0 ? brick / denom : 0,
    steelRatio: denom > 0 ? steel / denom : 0,
  })
}

rows.sort((a, b) => (a.win ?? 9) - (b.win ?? 9))

const pct = (v: number | null) => (v === null ? '  n/a' : (v * 100).toFixed(1).padStart(5) + '%')

const lines: string[] = []
lines.push('# 地图原型分桶报表')
lines.push('')
lines.push(
  `难度 **${difficulty}** · 共 ${rows.length} 关 · 胜率来源：${arg('report') ?? '（未提供，仅结构分桶）'}`,
)
lines.push('')
lines.push('| 关卡 | 胜率 | 中路无钢 | 砖占比 | 钢占比 |')
lines.push('|---|---|---|---|---|')
for (const r of rows) {
  lines.push(
    `| ${r.name} | ${pct(r.win)} | ${r.centralBreach ? '**YES**' : 'no'} | ` +
      `${(r.brickRatio * 100).toFixed(1)}% | ${(r.steelRatio * 100).toFixed(1)}% |`,
  )
}

// 砖密集阈值：取前 1/3 分位
const byBrick = [...rows].sort((a, b) => b.brickRatio - a.brickRatio)
const brickCut = byBrick[Math.floor(byBrick.length / 3)].brickRatio

function bucket(f: (r: Row) => boolean, label: string, hasWin = true): string {
  const g = rows.filter(f)
  if (g.length === 0) return `| ${label} | 0 | n/a | n/a |`
  if (!hasWin || g.some((r) => r.win === null)) {
    return `| ${label} | ${g.length} | n/a | n/a |`
  }
  const mean = g.reduce((a, b) => a + (b.win as number), 0) / g.length
  return `| ${label} | ${g.length} | ${(mean * 100).toFixed(1)}% | ${g.map((r) => r.name).join(', ')} |`
}

lines.push('')
lines.push('## 分桶汇总')
lines.push('')
lines.push(`砖密集阈值（砖占比前 1/3）= **${(brickCut * 100).toFixed(1)}%**`)
lines.push('')
lines.push('| 桶 | n（关） | 平均胜率 | 关卡 |')
lines.push('|---|---|---|---|')
lines.push(bucket((r) => r.centralBreach, '原型一：中路无钢'))
lines.push(bucket((r) => !r.centralBreach, '对照：中路有钢护卫'))
lines.push(bucket((r) => r.brickRatio >= brickCut, '原型二：砖墙密集（前 1/3）'))
lines.push(bucket((r) => r.brickRatio < brickCut, '对照：非砖密集'))
lines.push(bucket((r) => r.centralBreach && r.brickRatio >= brickCut, '两原型叠加'))
lines.push(bucket((r) => !r.centralBreach && r.brickRatio < brickCut, '两原型皆无（最简单桶）'))

const out = lines.join('\n')
console.log(out)

const mdPath = arg('md')
if (mdPath) {
  writeFileSync(mdPath, out + '\n')
  console.log('\nwrote markdown -> ' + mdPath)
}
