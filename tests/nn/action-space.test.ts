import { describe, it, expect } from 'bun:test'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { decodeMove, encodeMove, MOVE_DECODE, MOVE_DIM } from '../../src/nn/action-space'
import { World } from '../../src/game/World'
import { Simulation } from '../../src/game/Simulation'
import { makeArena } from '../../src/nn/arena-ladder'
import type { InputLike } from '../../src/game/Input'

// ============================================================
// action-space（plan/new-era-stop.plan.md Phase 0 #1/#10, B案）
// ============================================================

describe('decodeMove / encodeMove (index 0 = STOP)', () => {
  it('0 decodes to STOP (null)', () => {
    expect(decodeMove(0)).toBeNull()
  })

  it('1..4 decode to the four directions', () => {
    expect(decodeMove(1)).toBe('up')
    expect(decodeMove(2)).toBe('down')
    expect(decodeMove(3)).toBe('left')
    expect(decodeMove(4)).toBe('right')
  })

  it('out-of-range degrades to STOP, never an undefined direction', () => {
    expect(decodeMove(5)).toBeNull()
    expect(decodeMove(-1)).toBeNull()
  })

  it('round-trips every direction through encode', () => {
    for (const dir of MOVE_DECODE) expect(decodeMove(encodeMove(dir))).toBe(dir)
    expect(encodeMove(null)).toBe(0)
  })

  it('MOVE_DIM stays 5 (dims unchanged by B案)', () => {
    expect(MOVE_DIM).toBe(5)
    expect(MOVE_DECODE.length).toBe(4)
  })
})

// ============================================================
// Physical contract: STOP ⇒ moving=false, through the real Simulation.
// ============================================================

describe('STOP reaches the physics (0 ⇒ moving=false)', () => {
  it('a null movement command stops the tank', () => {
    const world = new World()
    world.rng.reseed(1)
    world.loadStageData(makeArena({ size: 12, enemyCount: 1 }), 0)
    world.player!.spawnTimer = 0
    const stub: InputLike = {
      getMoveDirection: () => null,
      isFiring: () => false,
      wasItemPressed: () => false,
      endFrame: () => {},
      reset: () => {},
    }
    const sim = new Simulation(world, stub)
    sim.tick()
    expect(world.player!.moving).toBe(false)
  })

  it('a decoded direction (decodeMove(1) === up) makes the tank move', () => {
    const world = new World()
    world.rng.reseed(1)
    world.loadStageData(makeArena({ size: 12, enemyCount: 1 }), 0)
    world.player!.spawnTimer = 0
    const stub: InputLike = {
      getMoveDirection: () => decodeMove(1),
      isFiring: () => false,
      wasItemPressed: () => false,
      endFrame: () => {},
      reset: () => {},
    }
    const sim = new Simulation(world, stub)
    sim.tick()
    expect(world.player!.moving).toBe(true)
  })
})

// ============================================================
// Manifest gate (#10): the "0 = keep" idiom must never grow back.
//
// The old semantics leaked through several private copies (policy-input,
// export-rl-rollout, export-eval-game, export-nn-replays, record-games-video)
// each holding its own `lastDir`. A grep-by-hand audit caught it once; this
// test catches the NEXT copy at CI time. Every mover must decode the sampled
// index through `decodeMove`, and MOVE_DIM/MOVE_DECODE must have a single home.
// ============================================================

const SINGLE_SOURCE = 'src/nn/action-space.ts'
const SCAN_ROOTS = ['src', 'tools']

function walkTs(root: string): string[] {
  const out: string[] = []
  const rec = (p: string): void => {
    let st
    try {
      st = statSync(p)
    } catch {
      return
    }
    if (st.isDirectory()) {
      for (const name of readdirSync(p)) rec(join(p, name))
    } else if (p.endsWith('.ts') || p.endsWith('.mjs')) {
      out.push(p)
    }
  }
  rec(root)
  return out
}

interface Leak {
  line: number
  text: string
}

function findLeaks(pattern: RegExp, roots = SCAN_ROOTS): Array<{ file: string } & Leak> {
  const hits: Array<{ file: string } & Leak> = []
  for (const root of roots) {
    for (const file of walkTs(root)) {
      if (file.replace(/\\/g, '/').endsWith(SINGLE_SOURCE)) continue
      const lines = readFileSync(file, 'utf8').split(/\r?\n/)
      lines.forEach((text, i) => {
        if (pattern.test(text)) hits.push({ file, line: i + 1, text: text.trim() })
      })
    }
  }
  return hits
}

describe('manifest gate: no "0 = keep" mover survives', () => {
  it('no `lastDir` held-direction state anywhere in src/ or tools/', () => {
    const leaks = findLeaks(/\blastDir\b/)
    expect(leaks.map((l) => `${l.file}:${l.line} ${l.text}`)).toEqual([])
  })

  it('no local `MOVE_DIM = 5` / `MOVE_DECODE =` copies outside action-space.ts', () => {
    expect(findLeaks(/\bMOVE_DIM\s*=\s*5\b/)).toEqual([])
    expect(findLeaks(/\bMOVE_DECODE\b\s*[:=]/)).toEqual([])
  })

  it('the in-sim policy decodes through decodeMove (source guard)', () => {
    const src = readFileSync('src/nn/policy-input.ts', 'utf8')
    expect(src).toContain('decodeMove(bestMove)')
    expect(src).not.toContain('lastDir')
  })

  it('every mover that sets a movement action imports decodeMove', () => {
    const movers = [
      'src/nn/policy-input.ts',
      'tools/sim/export-rl-rollout.ts',
      'tools/sim/export-eval-game.ts',
      'tools/sim/export-nn-replays.ts',
      'tools/sim/record-games-video.ts',
      'tools/sim/nn-trace.ts',
    ]
    for (const f of movers) {
      const src = readFileSync(f, 'utf8')
      expect(`${f}: ${src.includes('decodeMove')}`).toBe(`${f}: true`)
    }
  })
})
