/**
 * batch-eval-nn.ts — 批量仿真实用工具
 *
 * 从课程配置文件读取设置（含 stage 模板），生成 arena 场地，
 * 对多个敌人数量并行跑 rollout。使用 wasm 推理（nn/infer 加载权重），
 * bun 子进程并行。
 *
 * 课程配置文件格式（JSONC）：
 *   {
 *     "bc": "tmp/weights.json",
 *     "difficulty": "hard",
 *     "max_ticks": 8000,
 *     "player": { "lives": 3, "level": 1 },
 *     "stage": {           // 可选，不带则自动生成钢墙围场
 *       "grid": [[...], ...],  // 13×13 瓦格码
 *       "forces": "ababab",
 *       "count": 3,
 *       "player_spawn": {"col": 12, "row": 17},
 *       "enemy_spawns": [{"col": 6, "row": 6}, ...]
 *     }
 *   }
 *
 * Usage:
 *   bun tools/sim/batch-eval-nn.ts tools/sim/course/s2.jsonc --enemies 3,5,8
 *   bun tools/sim/batch-eval-nn.ts tools/sim/course/s5.jsonc
 *
 * Options:
 *   --enemies N1,N2,...   敌人数量列表（逗号分隔，默认读课程配置的 stage.count）
 *   --seeds N             每敌人数量仿真局数（默认 200）
 *   --lives N             玩家命数覆盖
 *   --level N             玩家星级覆盖
 *   --out DIR             输出目录（默认 tmp/batch-eval-{ts}）
 *   --parallel N          并行进程数（默认 8）
 *   --weights PATH        权重文件（默认读取课程配置的 bc）
 *   --difficulty DIFF     难度（默认 hard）
 *   --max-ticks N         Max ticks（默认读取课程配置）
 */

import { spawn } from 'child_process'
import { readFileSync, mkdirSync, writeFileSync, existsSync } from 'fs'
import { join, dirname } from 'path'

// 尺寸：GRID=26tiles=13cells，arena 内场 14tiles=7cells，偏移 3cells
// 钢墙 2tile 厚（cells 0-2 和 10-12 全钢），内场 open 7cells（cells 3-9）
function makeArenaGrid(enemyCount: number): any {
  const grid: number[][] = []
  for (let r = 0; r < 13; r++) {
    const row: number[] = []
    for (let c = 0; c < 13; c++) {
      const isRing = r < 3 || r >= 10 || c < 3 || c >= 10
      row.push(isRing ? 6 : 0)
    }
    grid.push(row)
  }
  return {
    grid,
    name: `arena-${enemyCount}`,
    forces: 'ab'.repeat(enemyCount),
    count: enemyCount,
    // 出生点坐标 = 瓦片格式（0-25），必须在内场 6-19 范围内且 2x2 完整
    player_spawn: { col: 12, row: 17 },
    enemy_spawns: [
      { col: 6, row: 6 },
      { col: 12, row: 6 },
      { col: 18, row: 6 },
    ],
  }
}

function parseJsonc(text: string): any {
  // 去掉注释 + 尾部逗号（JSONC 兼容）
  let cleaned = text.replace(/\/\/.*$/gm, '').replace(/\/\*[\s\S]*?\*\//g, '')
  cleaned = cleaned.replace(/,(\s*[}\]])/g, '$1') // 去掉数组/对象末尾逗号
  return JSON.parse(cleaned)
}

function parseSeedsSummary(lines: string[]): {
  games: number
  wins: number
  kills: number
  ticks: number
} {
  let games = 0,
    wins = 0,
    kills = 0,
    ticks = 0

  // 逐行解析单局结果
  for (const line of lines) {
    // 单局格式: [OK] s1 seed0 ... kills=5 enemyHits=13 ... win=true ... ticks=1720
    const km = line.match(/kills=(\d+)/)
    const tm = line.match(/ticks=(\d+)/)
    const isWin = line.includes('win=true')
    // 只统计 [OK] 行（单局结果）
    if (line.startsWith('[OK]') && km && tm) {
      games++
      kills += parseInt(km[1])
      ticks += parseInt(tm[1])
      if (isWin) wins++
    }
  }

  // 如果没找到单局行，尝试汇总行
  if (games === 0) {
    for (const line of lines) {
      const sm = line.match(/games=(\d+)\s+winRate=([\d.]+)/)
      if (sm && !line.startsWith('[OK]')) {
        games = parseInt(sm[1])
        wins = Math.round(games * parseFloat(sm[2]))
        // 从汇总行里找 kills 均值
        const km2 = line.match(/kills=([\d.]+)/)
        if (km2) kills = Math.round(parseFloat(km2[1]) * games)
        break
      }
    }
  }

  return { games, wins, kills, ticks }
}

async function main() {
  const args = process.argv.slice(2)
  if (args.length < 1 || args[0] === '--help') {
    console.log(`
Usage: bun tools/sim/batch-eval-nn.ts <course.jsonc> [options]

Options:
  --enemies N1,N2,...    Enemy counts (comma-separated, default: from course config)
  --seeds N              Seeds per enemy count (default: 200)
  --lives N              Player lives override
  --level N              Player level override
  --out DIR              Output directory (default: tmp/batch-eval-{ts})
  --parallel N           Parallel processes (default: 8)
  --weights PATH         Weights file (default: course config's bc)
  --difficulty DIFF      Difficulty (default: hard)
  --max-ticks N          Max ticks (default: course config)
    `)
    process.exit(0)
  }

  const coursePath = args[0]
  const repoRoot = join(dirname(process.argv[1]), '../..')
  const course = parseJsonc(readFileSync(join(repoRoot, coursePath), 'utf8'))

  const getArg = (flag: string, def: string) => {
    const i = args.indexOf(flag)
    return i >= 0 && i + 1 < args.length ? args[i + 1] : def
  }
  const hasArg = (flag: string) => args.includes(flag)

  // 读取课程配置的 stage 模板
  const courseStage = course.stage || null
  const defaultEnemies = courseStage?.count ?? 3

  // --enemies 未指定时使用课程配置默认敌人数量
  const enemiesStr = hasArg('--enemies') ? getArg('--enemies', '') : String(defaultEnemies)
  const enemies = enemiesStr.split(',').map(Number)
  const seeds = parseInt(getArg('--seeds', '200'), 10)
  const livesOverride = hasArg('--lives') ? getArg('--lives', '3') : null
  const levelOverride = hasArg('--level') ? getArg('--level', '0') : null
  const weightsPath = getArg('--weights', course.bc || 'tmp/s2-cap/weights.json')
  const difficulty = getArg('--difficulty', course.difficulty || 'hard')
  const maxTicks = parseInt(getArg('--max-ticks', String(course.max_ticks || 8000)), 10)
  const parallel = parseInt(getArg('--parallel', '8'), 10)
  const outDir = getArg('--out', `tmp/batch-eval-${Date.now()}`)

  if (!existsSync(weightsPath)) {
    console.error(`ERROR: weights file not found: ${weightsPath}`)
    process.exit(1)
  }

  const playerLives = livesOverride ?? course.player?.lives ?? 3
  const playerLevel = levelOverride ?? course.player?.level ?? 0
  mkdirSync(outDir, { recursive: true })

  console.log(`╔══════════════════════════════════════════════════╗`)
  console.log(`║ Batch Eval`)
  console.log(`║ weights: ${weightsPath}`)
  console.log(`║ difficulty: ${difficulty}  lives: ${playerLives}  level: ${playerLevel}`)
  console.log(`║ enemies: ${enemies.join(', ')}  seeds: ${seeds}  parallel: ${parallel}`)
  console.log(`║ max-ticks: ${maxTicks}  out: ${outDir}`)
  console.log(`╚══════════════════════════════════════════════════╝`)

  interface Result {
    enemy: number
    wins: number
    games: number
    killsTotal: number
    ticksTotal: number
  }
  const allResults: Result[] = []
  const totalStart = Date.now()

  for (const enemyCount of enemies) {
    // 使用课程配置 stage 模板，用目标敌人数量覆盖 forces/count
    const base = courseStage || makeArenaGrid(enemyCount)
    const stageJson = JSON.stringify({
      ...base,
      forces: 'ab'.repeat(enemyCount),
      count: enemyCount,
    })

    const batchSize = Math.max(1, Math.ceil(seeds / parallel))
    const batches: { start: number; end: number }[] = []
    for (let s = 0; s < seeds; s += batchSize)
      batches.push({ start: s, end: Math.min(s + batchSize - 1, seeds - 1) })

    console.log(`\n  ${enemyCount} enemies: ${batches.length} batch(es), ${seeds} seeds`)

    // 并行启动批次
    const batchPromises = batches.map(
      (b) =>
        new Promise<{ games: number; wins: number; kills: number; ticks: number }>((resolve) => {
          const out = join(outDir, `e${enemyCount}`, `s1_${b.start}-${b.end}`)
          mkdirSync(dirname(out), { recursive: true })

          const cli = [
            'tools/sim/export-rl-rollout.ts',
            '--stage-json',
            stageJson,
            '--stages',
            '1',
            '--seeds',
            `${b.start}-${b.end}`,
            '--out',
            out,
            '--weights',
            weightsPath,
            '--max-ticks',
            String(maxTicks),
            '--difficulty',
            difficulty,
            '--lives-override',
            String(playerLives),
            '--player-level',
            String(playerLevel),
          ]

          const proc = spawn(process.argv[0], cli, {
            cwd: repoRoot,
            stdio: ['ignore', 'pipe', 'pipe'],
          })
          let stdout = '',
            stderr = ''
          proc.stdout.on('data', (d: Buffer) => (stdout += d.toString()))
          proc.stderr.on('data', (d: Buffer) => (stderr += d.toString()))
          proc.on('close', () => {
            const all = (stdout + stderr).split('\n')
            try {
              writeFileSync(join(outDir, `e${enemyCount}_${b.start}-${b.end}.log`), stdout + stderr)
            } catch {}
            resolve(parseSeedsSummary(all))
          })
          proc.on('error', () => resolve({ games: 0, wins: 0, kills: 0, ticks: 0 }))
        }),
    )

    const batchResults = await Promise.all(batchPromises)
    let totalGames = 0,
      totalWins = 0,
      totalKills = 0,
      totalTicks = 0
    for (const r of batchResults) {
      totalGames += r.games
      totalWins += r.wins
      totalKills += r.kills
      totalTicks += r.ticks
    }

    if (totalGames > 0) {
      const wr = ((totalWins / totalGames) * 100).toFixed(1)
      const ak = (totalKills / totalGames).toFixed(2)
      const at = (totalTicks / totalGames).toFixed(0)
      console.log(`  → ${totalGames} games: ${wr}% win, ${ak} avg kills, ${at} avg ticks`)
      allResults.push({
        enemy: enemyCount,
        wins: totalWins,
        games: totalGames,
        killsTotal: totalKills,
        ticksTotal: totalTicks,
      })
    } else {
      console.log(`  → WARN: no games parsed`)
    }
  }

  const totalSec = ((Date.now() - totalStart) / 1000).toFixed(0)
  console.log(`\n${'='.repeat(60)}`)
  console.log(`SUMMARY (${totalSec}s)`)
  console.log(`${'='.repeat(60)}`)
  console.log(`Enemies  Games  Win%    AvgKills  AvgTicks`)
  for (const r of allResults) {
    const wr = r.games > 0 ? ((r.wins / r.games) * 100).toFixed(1) : '-'
    const ak = r.games > 0 ? (r.killsTotal / r.games).toFixed(2) : '-'
    const at = r.games > 0 ? (r.ticksTotal / r.games).toFixed(0) : '-'
    console.log(
      `  ${String(r.enemy).padStart(3)}     ${String(r.games).padStart(4)}  ${wr.padStart(5)}%  ${ak.padStart(8)}  ${at.padStart(8)}`,
    )
  }
  console.log(`${'='.repeat(60)}`)
  console.log(`Results: ${outDir}`)
}

main().catch((e) => {
  console.error(e)
  process.exit(1)
})
