#!/usr/bin/env bun
/**
 * probe-tick0.ts — headless tick-0 twin for the human-opening probe
 * (human-opening-probe.plan v7 §T5a).
 *
 *   bun tools/probe/probe-tick0.ts [--manifest public/probe/x20-opening.json]
 *
 * Re-derives, for every game in the manifest:
 *   • `stageLayoutHash` — the layout identity (tiles + spawns + kinds + count)
 *   • `worldTickHash`   — the world state right after the run is set up
 * and compares both against the values archived at generation time.
 *
 * Both sides call the SAME `applyProbeRun` (src/probe/setup.ts), so this is a
 * structural equivalence check, not a transcription of "the five steps". It is
 * also the guard that the browser's tick 0 equals the archived one: the browser
 * differs only in having constructed a `Game` first, and `applyProbeRun`
 * RESEEDS the RNG, so prior boot/menu RNG consumption cannot leak in.
 *
 * Exit code is non-zero on any mismatch — a drift means the browser and the
 * archive no longer agree on the starting world.
 */

import { readFileSync } from 'node:fs'
import { World } from '../../src/game/World'
import { stageLayoutHash } from '../../src/nn/arena-ladder'
import { worldTickHash } from '../../src/replay/tickHash'
import { parseProbeManifestText, stageOfGame } from '../../src/probe/manifest'
import { applyProbeRun } from '../../src/probe/setup'

const DEFAULT_MANIFEST = 'public/probe/x20-opening.json'

function manifestPath(): string {
  const args = process.argv.slice(2)
  const i = args.indexOf('--manifest')
  return i >= 0 ? (args[i + 1] ?? DEFAULT_MANIFEST) : DEFAULT_MANIFEST
}

function main(): number {
  const path = manifestPath()
  const manifest = parseProbeManifestText(readFileSync(path, 'utf8'))
  const options = {
    difficulty: manifest.difficulty,
    lives: manifest.lives,
    level: manifest.level,
  }

  let failures = 0
  for (const game of manifest.games) {
    const stage = stageOfGame(manifest, game.game)
    const world = new World()
    applyProbeRun(world, { stage, game }, options)
    const tick0 = worldTickHash(world)
    const layout = stageLayoutHash(stage)
    const ok = tick0 === game.tick0Hash && layout === stage.layoutHash
    if (!ok) failures++
    console.log(
      `${ok ? 'OK  ' : 'FAIL'} game ${game.game} stage ${stage.id} seed ${game.seed} ` +
        `tick0=${tick0}${tick0 === game.tick0Hash ? '' : ` (manifest ${game.tick0Hash})`} ` +
        `layout=${layout}${layout === stage.layoutHash ? '' : ` (manifest ${stage.layoutHash})`}`,
    )
  }

  if (failures > 0) {
    console.error(`probe-tick0: ${failures}/${manifest.games.length} games drifted`)
    return 1
  }
  console.log(`probe-tick0: ${manifest.games.length} games OK (${path})`)
  return 0
}

if (import.meta.main) process.exit(main())
