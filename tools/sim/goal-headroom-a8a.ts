/**
 * goal-headroom-a8a.ts — 卡 A8a（plan/goal-nn-action.md）：目标轴 headroom 轻量探针。
 *
 * 三档臂（goal-god 执行器 = followGodNav 诊断模式，零网络）：
 *   orig           原口径 God-AI 导航目标
 *   random-legal   每 240 tick 换一个确定性随机合法格（破坏臂）
 *   static-corner  固定合法格（退化臂）
 *
 * headroom = winRate(orig) − winRate(random-legal)（同 (field,variant,seed) 配对）。
 * 判读（§3.6，三态，2026-08-30 修正）：headroom ≥ +3pp ⇒ provisional 拓扑 1a；
 *   0 ≤ headroom < +3pp ⇒ 方案 3（headroom 不足）；headroom < 0（倒挂）⇒ 方案 3
 *   但理由记"当前执行器下测不出正 headroom（探针非真上界）"——倒挂与 §1.1
 *   goal-god 0% 同源（教师目标选择对执行器失配），不是"教师没价值"的证明；
 *   A9b 出口由 A8b 用在训 RL 执行器复核，可撤销（须显式记录原因）。
 *
 * 保真度声明（纪律 2，DS-9②）——本探针**不是真上界**，失真项：
 *   ① 执行器是 T8.5 goal-god 执行器，不是本方案正在训练的 RL 执行器；
 *   ② 测的是"目标敏感度"（破坏目标掉多少），不是"好目标能加多少"；
 *   ③ God-AI 逐 tick 重选 vs 探针目标持有 240 tick（承诺窗口）——代理差异；
 *   ④ 场为 arena（S2/S3），真实 hard 上执行器本底 0%（§1.1），无测量空间。
 *
 * 可区分性冒烟（纪律 1）：orig 与 random-legal 两臂同 seed 结果必须不完全相同。
 *
 * Usage:
 *   bun tools/sim/goal-headroom-a8a.ts [--seeds 20] [--out reports/goal-headroom-a8a]
 */
import { writeFileSync } from 'node:fs'
import { arg } from '../lib/cli'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { START_LIVES, type Direction } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { GoalExecutor } from '../../src/nn/goal-executor'
import { resolveArenaStage, ARENA_LADDER, type ArenaLevel } from '../../src/nn/arena-ladder'

const FIELDS: Array<{ level: ArenaLevel; maxTicks: number }> = [
  { level: 'S2', maxTicks: 6000 },
  { level: 'S3', maxTicks: 20000 },
]

/** 工具侧 InputLike 包装（God-AI 之外的第二个玩家驱动器——goal-god 执行器）。 */
class ExecInput {
  constructor(private exec: GoalExecutor) {}
  getMoveDirection(): Direction | null {
    return this.exec.getMoveDirection()
  }
  isFiring(): boolean {
    return this.exec.isFiring()
  }
  wasItemPressed(): false {
    return false
  }
  endFrame(): void {
    this.exec.endFrame()
  }
  reset(): void {
    this.exec.reset()
  }
}

type Arm = 'orig' | 'random-legal' | 'static-corner'

interface GameRow {
  field: string
  variant: number
  seed: number
  arm: Arm
  outcome: string
  ticks: number
  kills: number
}

function runGame(
  field: ArenaLevel,
  variant: number,
  seed: number,
  arm: Arm,
  maxTicks: number,
): GameRow {
  const stage = resolveArenaStage(ARENA_ID(field, variant))!
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const exec = new GoalExecutor(world, {
    followGodNav: true,
    rng: new RNG((seed ^ 0x9e3779b9) >>> 0),
    navCorrupt: arm === 'orig' ? 'none' : arm,
    navCorruptSeed: seed * 7919 + 17,
  })
  const input = new ExecInput(exec)
  const sim = new Simulation(world, input as never)
  world.loadStageData(stage, 0)
  input.reset()
  let t = 0
  let outcome = 'timeout'
  while (t < maxTicks) {
    sim.tick()
    input.endFrame()
    t++
    if (world.state === 'stageclear' || world.state === 'victory') {
      outcome = 'stage_clear'
      break
    }
    if (world.state === 'gameover') {
      outcome = world.tileMap.isBaseDestroyed() ? 'base_destroyed' : 'lives_exhausted'
      break
    }
  }
  return { field, variant, seed, arm, outcome, ticks: t, kills: world.killCount }
}

/** arena id = ARENA_ID_BASE + levelSeedBase*10 + variant（与 arena-ladder 同构）。 */
function ARENA_ID(level: ArenaLevel, variant: number): number {
  for (const [id, spec] of ARENA_LADDER) {
    if (spec.level === level && spec.variant === variant) return id
  }
  throw new Error(`no arena for ${level}-v${variant}`)
}

async function main(): Promise<void> {
  const seedsN = parseInt(arg('seeds') ?? '20', 10)
  const outPrefix = arg('out') ?? 'reports/goal-headroom-a8a'
  const seeds = Array.from({ length: seedsN }, (_, i) => i + 1)
  const arms: Arm[] = ['orig', 'random-legal', 'static-corner']

  const rows: GameRow[] = []
  const t0 = Date.now()
  for (const { level, maxTicks } of FIELDS) {
    for (const spec of ARENA_LADDER.values()) {
      if (spec.level !== level) continue
      for (const seed of seeds) {
        for (const arm of arms) {
          rows.push(runGame(level, spec.variant, seed, arm, maxTicks))
        }
      }
    }
  }

  // 聚合
  const win = (r: GameRow): number => (r.outcome === 'stage_clear' ? 1 : 0)
  const byArm: Record<Arm, { winRate: number; kills: number; ticks: number }> = {} as never
  for (const arm of arms) {
    const rs = rows.filter((r) => r.arm === arm)
    byArm[arm] = {
      winRate: +((rs.reduce((a, r) => a + win(r), 0) / rs.length) * 100).toFixed(2),
      kills: +(rs.reduce((a, r) => a + r.kills, 0) / rs.length).toFixed(2),
      ticks: +(rs.reduce((a, r) => a + r.ticks, 0) / rs.length).toFixed(0),
    }
  }
  // 配对差（orig − random-legal）
  const key = (r: GameRow): string => `${r.field}:${r.variant}:${r.seed}`
  const origMap = new Map(rows.filter((r) => r.arm === 'orig').map((r) => [key(r), r]))
  const diffs: number[] = []
  let distinct = 0
  for (const r of rows.filter((r) => r.arm === 'random-legal')) {
    const o = origMap.get(key(r))
    if (!o) continue
    const d = win(o) - win(r)
    diffs.push(d)
    if (o.outcome !== r.outcome || o.kills !== r.kills || o.ticks !== r.ticks) distinct++
  }
  const n = diffs.length
  const mean = diffs.reduce((a, b) => a + b, 0) / n
  const sd = Math.sqrt(diffs.reduce((a, d) => a + (d - mean) ** 2, 0) / Math.max(1, n - 1))
  const se = sd / Math.sqrt(n)
  const headroom = {
    meanPp: +(mean * 100).toFixed(2),
    sdPp: +(sd * 100).toFixed(2),
    sePp: +(se * 100).toFixed(2),
    lo95Pp: +((mean - 1.96 * se) * 100).toFixed(2),
    n,
  }

  const smoke = { distinctGames: distinct, total: n, pass: distinct > 0 }
  const report = {
    tool: 'goal-headroom-a8a',
    generated: new Date().toISOString(),
    fields: FIELDS.map((f) => f.level),
    seeds: `1-${seedsN}`,
    executor: 'goal-god (followGodNav, 零网络)',
    fidelityNotes: [
      '① 执行器是 T8.5 goal-god 执行器，不是本方案在训的 RL 执行器',
      '② 测的是目标敏感度（破坏目标掉多少），不是好目标能加多少 —— 非真上界',
      '③ God-AI 逐 tick 重选 vs 探针目标持有 240 tick 承诺窗口（代理差异，DS-9②）',
      '④ arena 场口径；真实 hard 上 goal-god 本底 0%，无测量空间（§1.1）',
    ],
    byArm,
    headroom,
    distinguishabilitySmoke: smoke,
    wallSec: +((Date.now() - t0) / 1000).toFixed(1),
  }
  writeFileSync(`${outPrefix}.json`, JSON.stringify(report, null, 2) + '\n')
  const md = [
    '# goal-headroom-a8a（卡 A8a）',
    '',
    `生成 ${report.generated} · 场 ${FIELDS.map((f) => f.level).join('/')} × 3 变异 × ${seedsN} seed · hard`,
    '',
    '| 臂 | 通关率 | 击杀 | 平均 tick |',
    '|---|---|---|---|',
    ...arms.map((a) => `| ${a} | ${byArm[a].winRate}% | ${byArm[a].kills} | ${byArm[a].ticks} |`),
    '',
    `**headroom (orig − random-legal)**: ${headroom.meanPp}pp ± ${headroom.sdPp}pp, SE ${headroom.sePp}pp, 95%CI 下界 ${headroom.lo95Pp}pp (n=${n})`,
    '',
    `可区分性冒烟: ${smoke.pass ? 'PASS ✅' : 'FAIL ❌'}（${smoke.distinctGames}/${smoke.total} 局结果不同；0 差异 = 静默回退指纹）`,
    '',
    '保真度声明（纪律 2）:',
    ...report.fidelityNotes.map((s) => `- ${s}`),
    '',
    headroom.meanPp >= 3
      ? `**判读**: headroom ${headroom.meanPp}pp ≥ +3pp ⇒ A4 登记 provisional 拓扑 1a（硬 mask 禁止）`
      : headroom.meanPp >= 0
        ? `**判读**: headroom ${headroom.meanPp}pp ≥ 0 但 < +3pp ⇒ A4 登记方案 3（不开目标头；headroom 不足）`
        : `**判读**: headroom ${headroom.meanPp}pp **倒挂**（教师目标 ≤ 随机目标）——当前执行器下测不出正 headroom，` +
          `探针非真上界（教师目标选择可能本身失配，与 §1.1 goal-god 0% 同源，见保真度①）。` +
          `A4 仍登记方案 3（不开目标头），但理由是"测不出正 headroom"而非"headroom 不足"；` +
          `A9b 出口由 A8b 用在训 RL 执行器复核，可撤销本登记（须显式记录原因）`,
  ]
  writeFileSync(`${outPrefix}.md`, md.join('\n') + '\n')
  process.stderr.write(`[goal-headroom-a8a] done ${report.wallSec}s → ${outPrefix}.{json,md}\n`)
  process.stderr.write(JSON.stringify({ byArm, headroom, smoke }, null, 2) + '\n')
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
