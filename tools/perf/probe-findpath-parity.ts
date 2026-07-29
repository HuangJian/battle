/**
 * probe-findpath-parity.ts — prove the optimized findPath returns
 * byte-identical Direction[] sequences to the ORIGINAL implementation.
 *
 * The original is loaded from git (the committed version) at runtime so we
 * compare against exactly what calibration was built on. Any mismatch would
 * mean the God-AI navigation decisions changed — invalidating tuning.
 */
import { execSync } from 'node:child_process'
import { writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { TileMap } from '../../src/game/TileMap'
import { STAGES } from '../../src/config/stages'
import { GRID } from '../../src/constants'
import { findPath as newFindPath, type Cell } from '../../src/utils/pathfind'

// 1. Extract the ORIGINAL findPath from git (committed version) into
//    src/utils/ so its relative imports (`../game/TileMap`) resolve, then
//    load it via a shim. We delete the temp file at the end.
const origPath = join(process.cwd(), 'src/utils/pathfind.orig.ts')
const src = execSync('git show HEAD:src/utils/pathfind.ts', { encoding: 'utf8' })
writeFileSync(origPath, src)

const shim = `
import { TileMap as TM } from '../game/TileMap'
import { findPath as fp } from './pathfind.orig'
export function findPathOrig(tm: any, from: any, to: any, c?: any) { return fp(tm as TM, from, to, c) }
`
const shimPath = join(process.cwd(), 'src/utils/__fp_parity_shim.ts')
writeFileSync(shimPath, shim)
const { findPathOrig } = await import(shimPath)

// 2. Build a list of (tilemap, from, to) cases spanning empty, classic
// stages, and breakBrick mode.
const cases: Array<{ tm: TileMap; from: Cell; to: Cell; breakBrick: boolean }> = []
const empty = new TileMap()
cases.push({ tm: empty, from: { col: 0, row: 0 }, to: { col: 24, row: 24 }, breakBrick: false })
cases.push({ tm: empty, from: { col: 0, row: 0 }, to: { col: 0, row: 0 }, breakBrick: false })
for (const st of STAGES.slice(0, 8)) {
  const tm = new TileMap()
  tm.loadStage(st)
  for (let r = 1; r < GRID - 2; r += 5) {
    for (let c = 1; c < GRID - 2; c += 5) {
      cases.push({
        tm,
        from: { col: c, row: r },
        to: { col: GRID - 1 - c, row: GRID - 1 - r },
        breakBrick: false,
      })
      cases.push({
        tm,
        from: { col: c, row: r },
        to: { col: GRID - 1 - c, row: GRID - 1 - r },
        breakBrick: true,
      })
    }
  }
}

// 3. Compare.
let mismatches = 0
let compared = 0
for (const cs of cases) {
  const a = newFindPath(cs.tm, cs.from, cs.to, cs.breakBrick ? { breakBrick: true } : undefined)
  const b = findPathOrig(cs.tm, cs.from, cs.to, cs.breakBrick ? { breakBrick: true } : undefined)
  compared++
  const sa = a ? a.join(',') : 'NULL'
  const sb = b ? b.join(',') : 'NULL'
  if (sa !== sb) {
    mismatches++
    if (mismatches <= 5)
      console.log(
        `MISMATCH from=${JSON.stringify(cs.from)} to=${JSON.stringify(cs.to)} bb=${cs.breakBrick}\n  new=${sa}\n  old=${sb}`,
      )
  }
}
console.log(`\ncompared=${compared} mismatches=${mismatches}`)
console.log(
  mismatches === 0
    ? 'PARITY OK: optimized findPath is byte-identical to original'
    : 'PARITY FAILED',
)
process.exit(mismatches === 0 ? 0 : 1)
