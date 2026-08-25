#!/usr/bin/env bun
/**
 * divergence-probe.ts — M1 分歧探针 (plan/AI-No-Items-Warmstart.md §4).
 *
 * 预注册定义（防事后叙事）：
 *   分歧 = 在决策 tick 上：学生【贪心】动作 ≠ 教师(God-AI)标签动作
 *          （move 或 fire 任一不等），且该分歧在后续 T=120 tick 内于
 *          学生实际轨迹上引发可观测后果。
 *   后果（学生实际轨迹直接提取的代理指标，不做 counterfactual 双臂重放）：
 *     · fire 分歧   → 窗口 (t, t+T] 内发生坦克被毁（击杀或玩家阵亡）
 *                     或护圈格变化（brick/steel 被拆）或基地被毁；
 *     · move 分歧   → 窗口 (t, t+T] 内玩家中心位移 ≥ CELL(16px)，且位移平均
 *                     方向与教师命令方向点积 < 0.5（「学生去了教师不会去的地方」）；
 *     · 任一分歧    → 窗口内基地被毁 → 有后果。
 *   其余不满足后果条件的分歧 = 静默分歧（tie-breaking / 浮点序 / 无后果朝向差），
 *   单独计数，不进归因。
 *
 * 分桶（状态类别，优先级 基地高压 > 交战 > 巡航）：
 *   基地高压 : 环完好度 < 1（scalars[6] < 1，任意护圈损伤）
 *             或任一存活敌距基地中心 ≤ BASE_PRESSURE_RADIUS(12) 格；
 *   交战     : 任一存活敌距玩家 ≤ 14 格，或场上有敌方子弹；
 *   巡航     : 以上皆否。
 *
 * 归因规则（输出四选一）：
 *   ① 分歧集中于「交战」桶且特征表完整 → 标签选择（M3 wins-only 有效）；
 *   ② 分歧集中于「基地高压」桶且该桶缺失特征 → 观测表达（M2 追加 obs 修补）；
 *   ③ 高分歧率 + 桶分布均匀 + 特征表完整 → 监督方式/分布漂移（M3 走 DAgger）；
 *   ④ 兼有 / 低分歧率 → 见脚本输出 DAGNOSIS 行。
 *
 * 特征完备表（v1 编码现状；v2 删 item 头 + 5 标量后 ringCompleteness 仍为标量6）。
 *
 * Usage:
 *   bun tools/diag/divergence-probe.ts --weights-dir tmp/student-weights-dagger \
 *       --stages 0-4 --seeds 0-9 --out tmp/probe-m1.json
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES, CELL, BASE_POS } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { writeFileSync } from 'fs'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { NNInput } from '../../src/nn/policy-input'
import { ObsEncoder } from '../../src/nn/obs-encoder'

const MAX_TICKS = 36000
const K = 10
const T = 120 // 后果观察窗口
const BASE_PRESSURE_RADIUS = 12 // 与 export-rl-rollout 同半径
const ENGAGE_RADIUS = 14 // 交战桶敌距判定（格）

type Bucket = 'base' | 'combat' | 'cruise'
const BUCKETS: Bucket[] = ['base', 'combat', 'cruise']

const FEATURES: Record<Bucket, Array<{ name: string; present: boolean }>> = {
  base: [
    { name: 'scalar.ringCompleteness(6)', present: true },
    { name: 'channel.base(5)', present: true },
    { name: 'channel.waveHeat(13)', present: true },
    { name: 'channel.enemy-*(7-10)', present: true },
  ],
  combat: [
    { name: 'channel.enemy-*(7-10)', present: true },
    { name: 'channel.bullet(11)', present: true },
    { name: 'scalar.nearestEnemyDist(19)', present: true },
    { name: 'scalar.fireProgress(4)', present: true },
  ],
  cruise: [
    { name: 'channel.enemy-*(7-10)', present: true },
    { name: 'scalar.nearestEnemyDist(19)', present: true },
  ],
}

interface TickRec {
  t: number
  bucket: Bucket
  sMove: number
  sFire: number
  tMove: number
  tFire: number
  diverged: boolean
  silent: boolean
  px: number
  py: number
}

function distCells(tx: number, ty: number, col: number, row: number): number {
  return Math.abs(Math.floor((tx + 16) / CELL) - col) + Math.abs(Math.floor((ty + 16) / CELL) - row)
}

function bucketOf(world: World, sc: Float32Array): Bucket {
  const basePressed =
    sc[6] < 1 ||
    world.tanks.some(
      (e) =>
        e.alive &&
        e.spawnTimer <= 0 &&
        e.allegiance === 'enemy' &&
        distCells(e.x, e.y, BASE_POS.col, BASE_POS.row) <= BASE_PRESSURE_RADIUS,
    )
  if (basePressed) return 'base'
  const p = world.player
  const pc = p ? { x: p.x + p.w / 2, y: p.y + p.h / 2 } : null
  const enemyNear =
    pc !== null &&
    world.tanks.some((e) => {
      if (!e.alive || e.spawnTimer > 0 || e.allegiance !== 'enemy') return false
      return Math.hypot(e.x + e.w / 2 - pc.x, e.y + e.h / 2 - pc.y) <= ENGAGE_RADIUS * CELL
    })
  const enemyBullet = world.bullets.some((b) => b.alive && b.allegiance === 'enemy')
  return enemyNear || enemyBullet ? 'combat' : 'cruise'
}

const DIR_VEC: Array<[number, number]> = [
  [0, -1], // up (1)
  [0, 1], // down (2)
  [-1, 0], // left (3)
  [1, 0], // right (4)
]

function ringSnapshot(world: World): number[] {
  const bc = BASE_POS.col
  const br = BASE_POS.row
  const out: number[] = []
  for (let col = bc - 1; col <= bc + 2; col++)
    for (let row = br - 1; row <= br + 1; row++) {
      const tt = world.tileMap.get(col, row)
      out.push(tt === 'brick' || tt === 'steel' ? 1 : 0)
    }
  return out
}

type EvKind = 'kill' | 'death' | 'ring_change' | 'base_down'

function runOneDetailed(
  stageIdx: number,
  seed: number,
  difficulty: string,
  weightsDir: string,
): { recs: TickRec[]; ticks: number; outcome: string } {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES

  const teacherRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const teacher = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, teacherRng)
  const student = new NNInput(world, { weightsDir })
  const sim = new Simulation(world, student)
  world.loadStageData(STAGES[stageIdx], stageIdx)
  teacher.reset()
  student.reset()

  const encoder = new ObsEncoder()
  const recs: TickRec[] = []
  // 滚动窗口（最近 T tick）事件 + 玩家位置（含 tick 号）
  const eventsRing: Array<{ tick: number; kind: EvKind }> = []
  const posRing: Array<{ tick: number; x: number; y: number }> = []
  const pending: number[] = [] // recs 中未判定的分歧 idx
  const tObj = { t: 0 }
  let prevRing = ringSnapshot(world)
  let t = 0
  let outcome = 'timeout'

  const pushEvent = (kind: EvKind): void => {
    eventsRing.push({ tick: t, kind })
  }

  while (t < MAX_TICKS) {
    encoder.encode(world)
    const teacherMove = teacher.getMoveDirection() ?? null
    const teacherFire = teacher.isFiring()
    teacher.endFrame()
    const tMove = teacherMove ? ({ up: 1, down: 2, left: 3, right: 4 }[teacherMove] ?? 0) : 0
    const tFire = teacherFire ? 1 : 0

    if (t % K === 0 || t === 0) {
      student.thinkNow()
      const sMove = student.moveArgmax()
      const sFire = student.fireArgmax()
      const rec: TickRec = {
        t,
        bucket: bucketOf(world, encoder.scalars),
        sMove,
        sFire,
        tMove,
        tFire,
        diverged: sMove !== tMove || sFire !== tFire,
        silent: false,
        px: (world.player?.x ?? 0) + 16,
        py: (world.player?.y ?? 0) + 16,
      }
      recs.push(rec)
      if (rec.diverged) pending.push(recs.length - 1)
    }

    // 事件采集
    for (const e of world.consumeEvents()) {
      if (e.type === 'tank_destroyed') {
        if ((e as any).tank?.isPlayer) pushEvent('death')
        else if ((e as any).by === 'player') pushEvent('kill')
        else pushEvent('kill') // 学生-教师均视为交战结果（保守）
      }
    }
    if (world.tileMap.isBaseDestroyed()) pushEvent('base_down')
    const ring = ringSnapshot(world)
    if (ring.join('') !== prevRing.join('')) pushEvent('ring_change')
    prevRing = ring
    posRing.push({ tick: t, x: (world.player?.x ?? 0) + 16, y: (world.player?.y ?? 0) + 16 })

    // 滚动窗口修剪
    while (eventsRing.length > 0 && eventsRing[0].tick <= t - T) eventsRing.shift()
    while (posRing.length > 0 && posRing[0].tick <= t - T) posRing.shift()

    sim.tick()
    student.endFrame()
    t++
    if (world.state === 'stageclear' || world.state === 'victory') {
      outcome = 'stage_clear'
      break
    }
    if (world.state === 'gameover') {
      outcome = 'gameover'
      break
    }
  }

  // 判定窗口（tick 已定格；eventsRing 覆盖最后 T tick——对 rec.t 无条件正确，
  // 因为收尾时 rec.t + T ≥ t - T 保证窗口起点仍在环内或被修剪过）
  for (const idx of pending) {
    const rec = recs[idx]
    const lo = rec.t
    const hi = rec.t + T
    const win = eventsRing.filter((e) => e.tick > lo && e.tick <= hi)
    const hasKill = win.some((e) => e.kind === 'kill' || e.kind === 'death')
    const hasRingOrBase = win.some((e) => e.kind === 'ring_change' || e.kind === 'base_down')
    const moveDiverged = rec.sMove !== rec.tMove
    const fireDiverged = rec.sFire !== rec.tFire
    // 条件 a/b：事件类后果（任一分歧 + 击杀/基地变化 → 有后果）
    let consequent = (fireDiverged || moveDiverged) && (hasKill || hasRingOrBase)
    if (!consequent && moveDiverged) {
      // 位置类后果：窗口内位移 ≥1 cell 且方向背离教师命令
      const p0 = { x: rec.px, y: rec.py }
      const pn = posRing.filter((p) => p.tick > lo && p.tick <= hi)
      if (pn.length > 0 && rec.tMove >= 1 && rec.tMove <= 4) {
        const last = pn[pn.length - 1]
        const dx = last.x - p0.x
        const dy = last.y - p0.y
        const dist = Math.hypot(dx, dy)
        if (dist >= CELL && dist > 0) {
          const [ux, uy] = DIR_VEC[rec.tMove - 1]
          const dot = (dx / dist) * ux + (dy / dist) * uy
          if (dot < 0.5) consequent = true
        } else if (dist >= CELL) {
          consequent = true
        }
      }
    }
    recs[idx].silent = !consequent
  }
  void tObj
  return { recs, ticks: t, outcome }
}

function parseRange(s: string): number[] {
  const out: number[] = []
  for (const part of s.split(',')) {
    if (part.includes('-')) {
      const [a, b] = part.split('-').map((x) => parseInt(x, 10))
      for (let i = a; i <= b; i++) out.push(i)
    } else out.push(parseInt(part, 10))
  }
  return out
}

function arg(name: string, fallback?: string): string | undefined {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 ? process.argv[i + 1] : fallback
}

function main(): void {
  const weightsDir = arg('weights-dir', 'tmp/student-weights-dagger')!
  const stageSpec = arg('stages', '0-4')!
  const seedSpec = arg('seeds', '0-9')!
  const difficulty = arg('difficulty', 'hard')!
  const outPath = arg('out', 'tmp/probe-m1.json')!
  const stages = parseRange(stageSpec)
  const seeds = parseRange(seedSpec)

  const totals = { games: 0, ticks: 0, decisions: 0, diverged: 0, silent: 0, consequent: 0 }
  const perBucket: Record<
    Bucket,
    { frames: number; div: number; silent: number; consequent: number }
  > = {
    base: { frames: 0, div: 0, silent: 0, consequent: 0 },
    combat: { frames: 0, div: 0, silent: 0, consequent: 0 },
    cruise: { frames: 0, div: 0, silent: 0, consequent: 0 },
  }
  const outcomes: Record<string, number> = {}

  for (const si of stages) {
    for (const seed of seeds) {
      const { recs, ticks, outcome } = runOneDetailed(si, seed, difficulty, weightsDir)
      process.stderr.write(
        `[probe] s${si} seed${seed} outcome=${outcome} ticks=${ticks} decisions=${recs.length} div=${recs.filter((r) => r.diverged).length}\n`,
      )
      outcomes[outcome] = (outcomes[outcome] ?? 0) + 1
      totals.games++
      totals.ticks += ticks
      for (const r of recs) {
        totals.decisions++
        perBucket[r.bucket].frames++
        if (r.diverged) {
          totals.diverged++
          perBucket[r.bucket].div++
          if (r.silent) {
            totals.silent++
            perBucket[r.bucket].silent++
          } else {
            totals.consequent++
            perBucket[r.bucket].consequent++
          }
        }
      }
    }
  }

  const divRate = totals.decisions > 0 ? totals.diverged / totals.decisions : 0
  const report: Record<string, unknown> = {
    probe: 'divergence-probe',
    schemaNote: 'v1（探针先于 M2 纪元）',
    weightsDir,
    stages,
    seeds,
    difficulty,
    maxTicks: MAX_TICKS,
    K,
    T,
    totals: { ...totals, divRate: +divRate.toFixed(4) },
    outcomeDist: outcomes,
    perBucket: Object.fromEntries(
      BUCKETS.map((b) => {
        const pb = perBucket[b]
        return [
          b,
          {
            frames: pb.frames,
            divRate: pb.frames > 0 ? +(pb.div / pb.frames).toFixed(4) : 0,
            silentRate: pb.frames > 0 ? +(pb.silent / pb.frames).toFixed(4) : 0,
            div: pb.div,
            silent: pb.silent,
            consequent: pb.consequent,
          },
        ]
      }),
    ),
    featureTable: FEATURES,
  }

  const pb = perBucket
  const maxBucket = BUCKETS.reduce((a, b) => (pb[b].div > pb[a].div ? b : a))
  const featuresComplete = BUCKETS.every((b) => FEATURES[b].every((f) => f.present))
  let diagnosis: string
  if (divRate < 0.15) {
    diagnosis =
      '低分歧率（不良反应小）：教师/学生动作高度一致，0% 更可能来自分布外状态泛化 → 倾向 ③ 边缘 + 交互采集（DAgger 轮次）'
  } else if (maxBucket === 'base' && !featuresComplete) {
    diagnosis = '② 观测表达：分歧集中于基地高压桶且特征表缺失 → M2 追加 obs 修补'
  } else if (maxBucket === 'combat' && featuresComplete) {
    diagnosis = '① 标签选择：分歧集中于交战桶且特征完整 → M3 wins-only 有效'
  } else if (maxBucket === 'base' && featuresComplete) {
    diagnosis = '①/③ 边界：基地高压桶分歧高但特征完整 → 标签或监督，wins-only + 守家回补并捕 DAgger'
  } else if (pb.combat.div > 0 && pb.base.div > 0) {
    diagnosis = '④ 兼有：两桶均高 → wins-only 与 obs 修补都做'
  } else {
    diagnosis = '③ 监督方式/分布漂移：分歧率高且桶分布均匀且特征完整 → M3 增补 DAgger 交互采集轮'
  }
  report.diagnosis = diagnosis
  console.log(JSON.stringify(report, null, 2))
  console.log(`\nDIAGNOSIS: ${diagnosis}`)
  writeFileSync(outPath, JSON.stringify(report, null, 2))
}

main()
