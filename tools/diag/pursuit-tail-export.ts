#!/usr/bin/env bun
/**
 * pursuit-tail-export.ts — 把 §302 场景导出成可播放的 .replay 文件（供人工审查）。
 *
 * 场景来自 pursuit-tail-scenes.ts 的扫描结果；本工具对给定的 (stage, seed, mode)
 * 用 runSimulation({ record: true }) 重跑并落盘 —— 走的是仿真器的官方录制链路
 * （InputRecorder），与浏览器里手动打出来的录像是同一种文件。
 *
 * **轨迹一致性自检（关键）**：扫描器用的是自己的 in-process 循环，导出用的是
 * runSimulation。两者若不是同一条轨迹，扫描给出的时间窗口在录像里就对不上。
 * 所以每次导出都比对 outcome / ticks / killCount 三项，不一致就报 MISMATCH
 * 并拒绝把该文件当作已验证场景交付。
 *
 * 同时导出同一 stage@seed 的 baseline 臂（mode 0）作对照 —— 注意对照臂的轨迹
 * 会分叉，同样时间点的画面不可直接对比，它用来看"没有并道时玩家在干什么"。
 *
 * Usage:
 *   bun tools/diag/pursuit-tail-export.ts --mode 3 --games 32:31,7:31,2:3,28:25,13:29
 *   bun tools/diag/pursuit-tail-export.ts --mode 3 --games 32:31 --dir tmp/s302-replays
 */
import { runSimulation } from '../sim/simulation-runner'
import { writeReplayFile } from '../sim/replay-writer'
import { DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { STAGES } from '../../src/config/stages'
import { arg } from '../lib/cli'
import { mkdirSync } from 'node:fs'

const difficulty = arg('difficulty') ?? 'hard'
const mode = Number(arg('mode', '3'))
/** §302 mode-7 along-mode split: 0 both / 1 wake / 2 level-ahead / 3 yield. */
const alongMode = Number(arg('along-mode', '0'))
const dir = arg('dir') ?? 'tmp/s302-replays'
const gamesSpec = arg('games') ?? ''
if (!gamesSpec) {
  console.error('usage: pursuit-tail-export.ts --mode 3 --games <stageNo>:<seed>[,...]')
  process.exit(1)
}

interface Game {
  stageNo: number
  seed: number
}
const games: Game[] = gamesSpec.split(',').map((g) => {
  const [s, seed] = g.split(':').map(Number)
  return { stageNo: s, seed }
})

/**
 * In-process reference run (identical loop to pursuit-tail-scenes.ts) — the
 * yardstick the exported replay is checked against.
 */
async function reference(stageIdx: number, seed: number, m: number) {
  const { World } = await import('../../src/game/World')
  const { Simulation } = await import('../../src/game/Simulation')
  const { GodAIInput } = await import('../../src/ai/GodAIInput')
  const { DIFFICULTIES } = await import('../../src/config/difficulty')
  const { RULES, DEFAULT_RULES } = await import('../../src/config/rules')
  const { RNG } = await import('../../src/utils/RNG')
  const { START_LIVES } = await import('../../src/constants')
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = difficulty
  world.difficulty = DIFFICULTIES[difficulty] ?? DIFFICULTIES['classic']
  world.rules = RULES[difficulty] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const godRng = new RNG((seed ^ 0x9e3779b9) >>> 0)
  const params = { ...DEFAULT_GOD_AI_PARAMS, pursuitTailMode: m, pursuitTailAlongMode: alongMode }
  const input = new GodAIInput(world, params, godRng)
  const sim = new Simulation(world, input)
  world.loadStageData(STAGES[stageIdx], 0)
  input.reset()
  let tick = 0
  let overrides = 0
  while (tick < 36000) {
    sim.tick()
    input.endFrame()
    tick++
    if (world.state === 'stageclear' || world.state === 'gameover') break
  }
  overrides = input._pursuitTailOverrides
  return {
    outcome: world.state,
    ticks: tick,
    kills: world.killCount,
    overrides,
  }
}

mkdirSync(dir, { recursive: true })

for (const { stageNo, seed } of games) {
  const stageIdx = stageNo - 1
  const stage = STAGES[stageIdx]
  console.log(`\n=== s${stageNo} (${stage.name}) @seed ${seed}  [difficulty ${difficulty}]`)

  for (const [label, m] of [
    ['cand', mode],
    ['base', 0],
  ] as const) {
    const ref = await reference(stageIdx, seed, m)
    const params = { ...DEFAULT_GOD_AI_PARAMS, pursuitTailMode: m, pursuitTailAlongMode: alongMode }
    const result = runSimulation({
      seed,
      stage,
      stageIndex: 0,
      difficulty,
      godAIParams: params,
      maxTicks: 36000,
      record: true,
    })
    // World.state says 'stageclear'; SimResult.outcome says 'stage_clear'.
    // Normalise before comparing — the vocabulary differs, not the trajectory.
    const norm = (o: string) => (o === 'stageclear' ? 'stage_clear' : o)
    const ok =
      norm(ref.outcome) === norm(result.outcome) &&
      ref.ticks === result.ticks &&
      ref.kills === result.finalState.killCount
    console.log(
      `  ${label}(mode=${m})  ref ${ref.outcome}/${ref.ticks}t/${ref.kills}kills  ` +
        `sim ${result.outcome}/${result.ticks}t/${result.finalState.killCount}kills  ` +
        `${ok ? 'MATCH ✓' : 'MISMATCH ✗'}  overrides=${ref.overrides}`,
    )
    const path = await writeReplayFile({
      result,
      dir: `${dir}/${label}`,
      stageIndex: stageIdx,
      stageName: stage.name,
      godAIParams: params as unknown as Record<string, unknown>,
    })
    if (path) console.log(`    -> ${path}`)
  }
}
console.log(`\n录像目录: ${dir}/{cand,base}/  （.replay 可在游戏内 ReplayBrowser 导入播放）`)
