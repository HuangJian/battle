import { World } from '../src/game/World'
import { Simulation } from '../src/game/Simulation'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../src/ai/GodAIInput'
import { DIFFICULTIES } from '../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../src/config/rules'
import { STAGES } from '../src/config/stages'
import { GRID } from '../src/constants'

// Trace S9 (or any stage) to understand why the AI is paralyzed
const stageIdx = parseInt(process.argv[2] ?? '9', 10)
const seed = parseInt(process.argv[3] ?? '1', 10)
const maxTicks = parseInt(process.argv[4] ?? '3000', 10)
const traceInterval = parseInt(process.argv[5] ?? '100', 10)

const world = new World()
world.rng.reseed(seed)
world.difficultyKey = 'classic'
world.difficulty = DIFFICULTIES['classic']
world.rules = RULES['classic'] ?? DEFAULT_RULES

const input = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS)
const sim = new Simulation(world, input)
world.loadStageData(STAGES[stageIdx], 0)
input.reset()

// Print the stage layout
console.log(`=== Stage ${stageIdx} (${STAGES[stageIdx].name}) seed=${seed} ===`)
console.log('Map (26x26):')
for (let r = 0; r < GRID; r++) {
  let line = ''
  for (let c = 0; c < GRID; c++) {
    const t = world.tileMap.get(c, r)
    line += t === 'empty' ? '.' : t[0]
  }
  console.log(line)
}
console.log()

// Trace
let lastKillCount = 0
const branchCounts = { dodge: 0, t8: 0, aggressive: 0, t2a: 0, powerup: 0, navigate: 0, dead: 0 }

for (let tick = 0; tick < maxTicks; tick++) {
  sim.tick()
  input.endFrame()

  // Accumulate branch counts
  const bc = input.branchCounts
  for (const k of Object.keys(branchCounts) as Array<keyof typeof branchCounts>) {
    branchCounts[k] = bc[k]
  }

  if (tick % traceInterval === 0 || world.killCount !== lastKillCount) {
    const p = world.player
    if (!p) continue
    const pc = input.playerCell()
    const _bc2 = input.branchCounts
    const branchThisTick = `dodge=${_bc2.dodge} t8=${_bc2.t8} aggr=${_bc2.aggressive} t2a=${_bc2.t2a} pu=${_bc2.powerup} nav=${_bc2.navigate} dead=${_bc2.dead}`

    // Show target
    const enemies = world.tanks.filter((t) => t.alive && t.spawnTimer <= 0)
    const nearestEnemy = enemies[0]
    let targetInfo = 'no enemies'
    if (nearestEnemy) {
      const tc = input.tankCell(nearestEnemy)
      targetInfo = `nearest enemy at (${tc.col},${tc.row}) dist=${Math.abs(tc.col - pc.col) + Math.abs(tc.row - pc.row)}`
    }

    console.log(
      `t${tick}: pos=(${p.x.toFixed(0)},${p.y.toFixed(0)}) cell=(${pc.col},${pc.row}) ` +
        `kills=${world.killCount} state=${world.state} dir=${p.dir} ` +
        `${branchThisTick} | ${targetInfo}`,
    )

    if (world.killCount !== lastKillCount) {
      console.log(`  *** KILL at t${tick}! kill count=${world.killCount}`)
      lastKillCount = world.killCount
    }
  }

  if (world.state === 'stageclear' || world.state === 'gameover') {
    console.log(`  END: ${world.state} at t${tick} kills=${world.killCount}`)
    break
  }
}

console.log(`\nFinal: state=${world.state} kills=${world.killCount} ticks=${maxTicks}`)
console.log(
  `Branch totals: dodge=${branchCounts.dodge} t8=${branchCounts.t8} aggr=${branchCounts.aggressive} t2a=${branchCounts.t2a} pu=${branchCounts.powerup} nav=${branchCounts.navigate} dead=${branchCounts.dead}`,
)
