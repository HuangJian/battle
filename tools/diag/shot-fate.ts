/**
 * shot-fate.ts — 每一发玩家子弹的归宿统计（命中 / 打墙 / 开阔地消失）。
 *
 * 口径（第三次修正，已自检）：命中 = 该发子弹消失的那一拍**有敌人掉血或消失**
 * （消失 = 致命一击，必须计入，否则 hits < kills 会露出马脚）；否则检查子弹
 * **下一格**是否阻弹地形 → 打墙；再否则 = 开阔地消失（敌人横向躲开/目标已死）。
 *
 * 自检不变量：`hits >= kills`，脚本会打印 OK/BROKEN。
 *
 * 参考值（2026-10-25，hard 35×10 = 350 局）：shots 51/局 · **命中 60.7%** ·
 * 打墙 2.1% · 开阔地消失 37.1% · 1.66 命中/击杀 ⇒ **未命中主因是敌人躲开，
 * 不是遮挡**。
 *
 * 用法：bun tools/diag/shot-fate.ts [seeds]
 */
/** Shot-fate accounting with a self-check: hits must be >= kills. */
import { STAGES } from '../../src/config/stages'
import { GodAIInput, DEFAULT_GOD_AI_PARAMS } from '../../src/ai/GodAIInput'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { RNG } from '../../src/utils/RNG'
import { DIFFICULTIES } from '../../src/config/difficulty'
import { RULES, DEFAULT_RULES } from '../../src/config/rules'
import { CELL } from '../../src/constants'
import { TileMap } from '../../src/game/TileMap'

const SEEDS = Number(process.argv[2] ?? 10)
type C = { shots: number; hit: number; wall: number; off: number }
const mk = (): C => ({ shots: 0, hit: 0, wall: 0, off: 0 })
const per: Record<number, C> = {}
const T = mk()
let killsTotal = 0

for (let s = 0; s < STAGES.length; s++) {
  const c = (per[s] = mk())
  for (let seed = 0; seed < SEEDS; seed++) {
    const world = new World()
    world.rng.reseed(seed)
    world.difficultyKey = 'hard'
    world.difficulty = DIFFICULTIES['hard']
    world.rules = RULES['hard'] ?? DEFAULT_RULES
    world.playerLevel = world.difficulty?.playerStartLevel ?? 0
    world.lives = world.difficulty?.startLives ?? 3
    const input = new GodAIInput(world, DEFAULT_GOD_AI_PARAMS, new RNG((seed ^ 0x9e3779b9) >>> 0))
    const sim = new Simulation(world, input)
    world.loadStageData(STAGES[s], s)
    input.reset()
    // bulletId -> {dir,x,y} of its LAST live tick
    const live = new Map<number, { dir: string; x: number; y: number }>()
    for (let t = 0; t < 18000; t++) {
      if (world.state !== 'playing') break
      // snapshot enemies BEFORE the tick
      const before = new Map<number, number>()
      for (const e of world.tanks) if (e.allegiance !== 'player' && e.alive) before.set(e.id, e.hp)
      sim.tick()
      const after = new Map<number, number>()
      for (const e of world.tanks) if (e.allegiance !== 'player' && e.alive) after.set(e.id, e.hp)
      // an enemy took damage this tick if it lost hp OR vanished (killed)
      let enemyDamaged = false
      for (const [id, h] of after) {
        const old = before.get(id)
        if (old !== undefined && h < old) enemyDamaged = true
      }
      for (const id of before.keys()) if (!after.has(id)) enemyDamaged = true
      // resolve vanished player bullets
      const nowAlive = new Map<number, { dir: string; x: number; y: number }>()
      for (const b of world.bullets) {
        if (b.alive && b.isPlayer)
          nowAlive.set(b.id, { dir: b.dir, x: b.x + b.w / 2, y: b.y + b.h / 2 })
      }
      for (const [id, prev] of live) {
        if (nowAlive.has(id)) continue
        c.shots++
        T.shots++
        if (enemyDamaged) {
          c.hit++
          T.hit++
          continue
        }
        // nothing was damaged: did the muzzle point into a solid cell next?
        const dx = prev.dir === 'left' ? -1 : prev.dir === 'right' ? 1 : 0
        const dy = prev.dir === 'up' ? -1 : prev.dir === 'down' ? 1 : 0
        const col = Math.floor(prev.x / CELL) + dx
        const row = Math.floor(prev.y / CELL) + dy
        if (col < 0 || col >= 26 || row < 0 || row >= 26) {
          c.off++
          T.off++
        } else if (TileMap.blocksBullet(world.tileMap.get(col, row))) {
          c.wall++
          T.wall++
        } else {
          c.off++
          T.off++
        }
      }
      live.clear()
      for (const [id, v] of nowAlive) live.set(id, v)
      input.endFrame()
    }
    killsTotal += world.killCount
  }
}
const pct = (a: number, b: number): string => (b ? ((100 * a) / b).toFixed(1) + '%' : '-')
console.log(
  `TOTAL shots=${T.shots} hit=${T.hit}(${pct(T.hit, T.shots)}) wall=${T.wall}(${pct(T.wall, T.shots)}) openOrOff=${T.off}(${pct(T.off, T.shots)}) kills=${killsTotal}`,
)
console.log(
  `INVARIANT hits>=kills: ${T.hit >= killsTotal ? 'OK' : 'BROKEN'} (${T.hit} vs ${killsTotal})`,
)
const worst = Object.entries(per)
  .map(([s, x]) => ({ s: Number(s) + 1, x, r: x.shots ? x.hit / x.shots : 0 }))
  .sort((a, b) => a.r - b.r)
for (const w of worst.slice(0, 6))
  console.log(
    `  s${w.s}: hit=${pct(w.x.hit, w.x.shots)} wall=${pct(w.x.wall, w.x.shots)} open=${pct(w.x.off, w.x.shots)}`,
  )
