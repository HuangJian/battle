/**
 * ab-arm-compare.ts — 新臂 vs 旧臂 A/B 对照（plan/dodge-item-reward-v2.md Step 10）。
 *
 * 用法（并行）：
 *   bun tools/diag/ab-arm-compare.ts --weights tmp/s-dodge/weights-collect-50.json \
 *       --out tmp/ab-compare --stages 1050-1052 --seeds 0-9
 *
 * 内部将 (stage,seed) 对拆成 N 个单局 bun 子进程并行执行，各自写独立 manifest，
 * 然后聚合汇总并计算新旧两臂的奖励分布。
 */
import { spawn } from 'node:child_process'
import { readFileSync, writeFileSync, mkdirSync, existsSync } from 'node:fs'
import { join } from 'node:path'
import { toyPotential, toyTerminal, TOY_REWARD_ARMS } from '../../src/nn/rl-reward-toy'

// ---- 旧臂参数（2026-08-31 dodge-mix 初值，改动前）----
const OLD_ARM = {
  name: 'dodge-mix-old',
  wKill: 1.0,
  p: 1.15,
  wDmg: 1.0,
  wDmg2: 0.003,
  wAlive: 0,
  wClear: 2.0,
  wDeath: 1.5,
  wLoot: 0.15,
}

// ---- 新臂参数（当前代码 dodge-mix，含标定后 wMiss=0.063）----
const NEW_ARM = TOY_REWARD_ARMS['dodge-mix']

function parseRange(s: string): number[] {
  const m = s.match(/^(\d+)-(\d+)$/)
  if (m) {
    const a = parseInt(m[1], 10),
      b = parseInt(m[2], 10)
    return Array.from({ length: b - a + 1 }, (_, i) => a + i)
  }
  return [parseInt(s, 10)]
}

function parseArgs(): { weights: string; outDir: string; stages: number[]; seeds: number[] } {
  const argv = process.argv.slice(2)
  let weights = '',
    outDir = 'tmp/ab-compare',
    stages: number[] = [],
    seeds: number[] = []
  for (let i = 0; i < argv.length; i++) {
    if (argv[i] === '--weights') weights = argv[++i]
    else if (argv[i] === '--out') outDir = argv[++i]
    else if (argv[i] === '--stages') stages = parseRange(argv[++i])
    else if (argv[i] === '--seeds') seeds = parseRange(argv[++i])
  }
  if (!weights || stages.length === 0 || seeds.length === 0) {
    console.error(
      'Usage: bun tools/diag/ab-arm-compare.ts --weights <path> --out <dir> --stages N-M --seeds N-M',
    )
    process.exit(1)
  }
  return { weights, outDir, stages, seeds }
}

async function main() {
  const { weights, outDir, stages, seeds } = parseArgs()
  mkdirSync(outDir, { recursive: true })

  // 生成所有 (stage, seed) 对
  const tasks: { stage: number; seed: number }[] = []
  for (const stage of stages) {
    for (const seed of seeds) {
      tasks.push({ stage, seed })
    }
  }
  console.log(`[ab-arm] tasks=${tasks.length} stages=${stages.join(',')} seeds=${seeds.join(',')}`)

  // 并行执行：同时起 N 个单局 bun 子进程
  const CONCURRENCY = 6
  const results: { stage: number; seed: number; manifest: any }[] = []
  let done = 0

  async function runTask(task: { stage: number; seed: number }): Promise<void> {
    const taskDir = join(outDir, `s${task.stage}_seed${task.seed}`)
    mkdirSync(taskDir, { recursive: true })
    return new Promise((resolve, reject) => {
      const proc = spawn(
        'bun',
        [
          'tools/sim/export-rl-rollout.ts',
          '--weights',
          weights,
          '--out',
          taskDir,
          '--stages',
          String(task.stage),
          '--seeds',
          String(task.seed),
          '--max-ticks',
          '12000',
        ],
        {
          stdio: ['ignore', 'pipe', 'pipe'],
          shell: true,
        },
      )
      let out = ''
      proc.stdout.on('data', (d: Buffer) => {
        out += d.toString()
      })
      proc.stderr.on('data', (_d: Buffer) => {
        /* ignore */
      })
      proc.on('close', (code) => {
        if (code !== 0) {
          console.error(`[ab-arm] FAIL s${task.stage} seed${task.seed} exit=${code}`)
          reject(new Error(`exit ${code}`))
          return
        }
        // 读取 manifest
        const mf = join(taskDir, `rl_s${task.stage}_seed${task.seed}`, 'manifest.json')
        if (!existsSync(mf)) {
          reject(new Error(`manifest not found: ${mf}`))
          return
        }
        const manifest = JSON.parse(readFileSync(mf, 'utf8'))
        results.push({ stage: task.stage, seed: task.seed, manifest })
        done++
        if (done % 10 === 0 || done === tasks.length) {
          console.log(`[ab-arm] progress: ${done}/${tasks.length}`)
        }
        resolve()
      })
    })
  }

  // 并发池
  const pool: Promise<void>[] = []
  for (const task of tasks) {
    const p = runTask(task)
    pool.push(p)
    if (pool.length >= CONCURRENCY) {
      await Promise.race(pool)
      // 移除已完成的
      await Promise.allSettled(pool)
      // 过滤掉已完成的
      for (let i = pool.length - 1; i >= 0; i--) {
        try {
          // 检查是否已完成
          await Promise.race([pool[i], new Promise((_, r) => setTimeout(() => r('pending'), 0))])
          pool.splice(i, 1)
        } catch {
          /* still pending */
        }
      }
    }
  }
  await Promise.allSettled(pool)

  // ---- 聚合 ----
  console.log(`\n[ab-arm] === A/B 对照 ===`)
  const newRewards: number[] = []
  const oldRewards: number[] = []
  const allHits: number[] = []
  const allKills: number[] = []
  const allLoot: number[] = []
  const allDeaths: number[] = []
  const allShots: number[] = []

  for (const { manifest: m } of results) {
    const counters = {
      kills: m.kills ?? 0,
      playerHits: m.playerHits ?? 0,
      playerDamageTaken: m.playerDamageTaken ?? 0,
      powerUpsCollected: m.powerUpsCollected ?? 0,
      hits: m.enemyHits ?? 0,
      shots: m.playerShots ?? 0,
      stuckTicks: m.stuckTicks ?? 0,
    }
    const ticks = m.ticks ?? 0
    const outcome = m.outcome ?? 'timeout'

    // 计算新旧两臂的势函数值 + 终局奖励
    const phiOld = toyPotential(counters, ticks, OLD_ARM as any)
    const phiNew = toyPotential(counters, ticks, NEW_ARM)
    const termOld = toyTerminal(outcome, OLD_ARM as any)
    const termNew = toyTerminal(outcome, NEW_ARM)
    // 总奖励 ≈ Φ_end + terminal（Φ_0 = 0，因为初始状态 counters 全 0）
    const rewardOld = phiOld + termOld
    const rewardNew = phiNew + termNew

    oldRewards.push(rewardOld)
    newRewards.push(rewardNew)
    allHits.push(counters.hits)
    allKills.push(counters.kills)
    allLoot.push(counters.powerUpsCollected)
    allDeaths.push(m.playerDeaths ?? 0)
    allShots.push(counters.shots)
  }

  const stat = (xs: number[]) => {
    const n = xs.length
    const mean = xs.reduce((a, b) => a + b, 0) / n
    const std = Math.sqrt(xs.reduce((a, x) => a + (x - mean) ** 2, 0) / n)
    return {
      mean: +mean.toFixed(4),
      std: +std.toFixed(4),
      min: +Math.min(...xs).toFixed(4),
      max: +Math.max(...xs).toFixed(4),
    }
  }

  const report = {
    games: results.length,
    oldArm: { wKill: 1.0, p: 1.15, wDmg: 1.0, wDmg2: 0.003, wLoot: 0.15 },
    newArm: {
      wKill: 1.0,
      p: 1.15,
      wHit: 0.2,
      wMiss: 0.063,
      wDmg: 1.0,
      wDmg2: 0.01,
      wLoot: 0.4,
      wStuck: 0.002,
    },
    reward: {
      old: stat(oldRewards),
      new: stat(newRewards),
    },
    behavior: {
      hitRate: +(
        allHits.reduce((a, b) => a + b, 0) /
        Math.max(
          1,
          allShots.reduce((a, b) => a + b, 0),
        )
      ).toFixed(4),
      killsPerGame: stat(allKills),
      hitsPerGame: stat(allHits),
      lootPerGame: stat(allLoot),
      deathsPerGame: stat(allDeaths),
    },
  }

  console.log(JSON.stringify(report, null, 2))
  writeFileSync(join(outDir, 'ab-report.json'), JSON.stringify(report, null, 2))
  console.log(`\n[ab-arm] report written to ${join(outDir, 'ab-report.json')}`)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
