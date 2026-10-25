// god/muzzle-threat.ts — A14: dodge the MUZZLE, not the shell.
//
// Hit autopsy (hard, 350 games, 1386 hits): 96% of the shells that connect had
// been in flight for >20 ticks, yet the tank still cannot escape them — leaving
// a shell's lane needs ≈19px of lateral travel = 27 ticks at 0.7px/tick, plus
// the 12-tick turn cooldown ⇒ ~39 ticks of lead time. In other words: by the
// time a shell EXISTS, a large share of hits is already unavoidable.
//
// A muzzle gives unlimited lead time: an enemy whose barrel is trained down a
// clear lane at us will put a shell there at an unknown tick, so the lane must
// be vacated BEFORE the shot. This module answers "is a barrel aimed at me from
// a lane I cannot afford to stay in, and which way is out".
import { BULLET, CELL, TANK } from '../../constants'
import { TileMap } from '../../game/TileMap'
import type { Direction } from '../../utils/direction'
import { ALL_DIRS, DIR_VECTORS } from '../../utils/direction'
import type { GodAIInput } from '../GodAIInput'
import { ticksUntilFire } from './ThreatBudget'

const BAND = TANK / 2 + BULLET / 2

/** Is every cell strictly between the two tanks passable for a shell? */
function laneClear(self: GodAIInput, c0: number, r0: number, c1: number, r1: number): boolean {
  const tm = self.world.tileMap
  if (c0 === c1) {
    const step = r0 < r1 ? 1 : -1
    for (let r = r0 + step; r !== r1; r += step) {
      if (TileMap.blocksBullet(tm.get(c0, r))) return false
    }
    return true
  }
  const step = c0 < c1 ? 1 : -1
  for (let c = c0 + step; c !== c1; c += step) {
    if (TileMap.blocksBullet(tm.get(c, r0))) return false
  }
  return true
}

/**
 * Return the step that leaves the most dangerous aimed muzzle, or null when no
 * muzzle is aimed at us inside the warning window.
 */
export function muzzleEscapeDirImpl(self: GodAIInput, pcx: number, pcy: number): Direction | null {
  const w = self.world
  const p = self.controlledTank(w)
  if (!p) return null
  const pc = self.playerCell()
  const pcol = pc.col
  const prow = pc.row
  const tanks = w.allTanks
  let worst = Infinity
  let worstDir: Direction | null = null
  for (let i = 0; i < tanks.length; i++) {
    const t = tanks[i]
    if (!t.alive || t.allegiance !== 'enemy' || t.spawnTimer > 0) continue
    const tx = t.x + t.w / 2
    const ty = t.y + t.h / 2
    const vertical = t.dir === 'up' || t.dir === 'down'
    // Barrel must already point at us (same lane, within the hit band).
    const lateral = vertical ? Math.abs(tx - pcx) : Math.abs(ty - pcy)
    if (lateral > BAND) continue
    // …and we must be ahead of the muzzle, not behind it.
    const v = DIR_VECTORS[t.dir]
    const ahead = (pcx - tx) * v.dx + (pcy - ty) * v.dy
    if (ahead <= 0) continue
    const cells = ahead / CELL
    if (cells > self.params.muzzleWarnCells) continue
    // A14b (2026-10-25): only an ARMED muzzle is a threat. Without this the
    // escape fires on almost every combat tick ("someone is aiming at me" is
    // the normal state), which measured −3.00pp / kills −234 — the tank kept
    // abandoning its position. `ticksUntilFire === 0` means the shell can leave
    // the barrel THIS tick, which is exactly when the lane must already be clear
    // (a shell that appears while we are in the lane cannot be outrun: 19px of
    // travel = 27 ticks + 12-tick turn cooldown).
    // A14c (2026-10-25): the shell must be PRE-EMPTED, not raced. "Gun ready
    // RIGHT NOW" was exactly neutral (0.00pp, +75/−75): by then the lane cannot
    // be left any more (19px of travel = 27 ticks + 12-tick turn cooldown).
    // What matters is the gun that is ABOUT to be ready: start the lateral move
    // `muzzleLeadTicks` before readiness so we are already out of the lane when
    // the shot exists.
    if (self.params.muzzleRequireReady > 0) {
      const untilReady = ticksUntilFire(w, t)
      if (untilReady > self.params.muzzleLeadTicks) continue
    }
    const tc = self.tankCell(t)
    if (!laneClear(self, tc.col, tc.row, pcol, prow)) continue
    const speed = p.bulletSpeed > 0 ? p.bulletSpeed : 4
    const tti = ahead / speed
    if (tti < worst) {
      worst = tti
      // Step across the barrel's axis (vertical barrel ⇒ move sideways).
      worstDir = vertical ? (tx < pcx ? 'right' : 'left') : ty < pcy ? 'down' : 'up'
    }
  }
  if (worstDir === null) return null
  // Prefer the natural escape; if it is blocked, take any perpendicular step.
  if (self.canMoveDir(p, worstDir)) return worstDir
  const vertical = worstDir === 'up' || worstDir === 'down'
  for (let i = 0; i < ALL_DIRS.length; i++) {
    const d = ALL_DIRS[i]
    const dv = d === 'up' || d === 'down'
    if (dv === vertical) continue
    if (self.canMoveDir(p, d)) return d
  }
  return null
}
