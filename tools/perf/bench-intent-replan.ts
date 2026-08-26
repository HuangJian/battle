/**
 * bench-intent-replan.ts — 拆解 intent-exec 每次 replan 的耗时构成（encode vs forward）。
 * 回答：为什么 intent-exec 2100 局 ≈ 52min，而 God-AI ≈ 90s。
 */
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { STAGES } from '../../src/config/stages'
import { START_LIVES } from '../../src/constants'
import { RNG } from '../../src/utils/RNG'
import { readFileSync } from 'node:fs'
import { IntentExecutor } from '../../src/nn/intent-executor'
import { ObsEncoder } from '../../src/nn/obs-encoder'

const weightsText = readFileSync('tmp/intent-weights-Bp.json', 'utf8')

function run(seed: number, ticks: number) {
  const world = new World()
  world.rng.reseed(seed)
  world.difficultyKey = 'hard'
  world.difficulty = DIFFICULTIES['hard']
  world.rules = RULES['hard'] ?? DEFAULT_RULES
  world.playerLevel = world.difficulty?.playerStartLevel ?? 0
  world.lives = world.difficulty?.startLives ?? START_LIVES
  const exec = new IntentExecutor(world, { weightsText, rng: new RNG((seed ^ 0x9e3779b9) >>> 0) })
  const sim = new Simulation(world, exec)
  world.loadStageData(STAGES[9], 9)
  exec.reset()

  const enc = new ObsEncoder()
  // 1) encode 单次耗时（冷启动后取 50 次平均）
  let t0 = performance.now()
  for (let i = 0; i < 50; i++) enc.encode(world)
  const encMs = (performance.now() - t0) / 50

  // 2) forward 单次耗时（模型 internal intentForward）
  const model = exec['model']
  const obs = new Uint8Array(14 * 26 * 26)
  const scal = new Float32Array(19)
  const inj = new Float32Array(9)
  t0 = performance.now()
  for (let i = 0; i < 50; i++) model.intentForward(obs, scal, inj)
  const fwdMs = (performance.now() - t0) / 50

  let replans = 0
  for (let t = 0; t < ticks; t++) {
    if (world.state !== 'playing') break
    sim.tick()
    exec.endFrame()
  }
  replans = exec['replanEvery'] ? Math.ceil(ticks / exec['replanEvery']) : 0
  return { encMs, fwdMs, replans, ticks: Math.min(ticks, 2000) }
}

const r = run(11, 2000)
const perReplan = r.encMs + r.fwdMs
console.log(`encode 单次: ${r.encMs.toFixed(1)}ms`)
console.log(`forward 单次: ${r.fwdMs.toFixed(1)}ms`)
console.log(`合计/ replan: ${perReplan.toFixed(1)}ms`)
console.log(
  `2000 tick 内 replan 数: ${r.replans} (≈ ${(r.replans / (r.ticks / 2000 / 100)).toFixed(1)}/100tick)`,
)
const replansPerGame = Math.round((r.replans / r.ticks) * 4761)
const secPerGame = (replansPerGame * perReplan) / 1000
console.log(`外推: 一局 ~4761 tick ≈ ${replansPerGame} replan → ${secPerGame.toFixed(1)}s/局`)
console.log(
  `外推: 2100 局 / 16 核 = ${(2100 / 16).toFixed(0)} 局/核 × ${secPerGame.toFixed(1)}s = ${(((2100 / 16) * secPerGame) / 60).toFixed(0)}min`,
)
