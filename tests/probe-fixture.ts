import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { flatten } from '../tools/probe/flatten-manifest'
import { parseProbeManifestText, type ProbeManifest } from '../src/probe/manifest'

// ============================================================
// Human-opening probe — test fixture
//
// A probe course is a SESSION, not a repo artifact: the game list is scratch
// (`tmp/probe/*.games.json`) and the served manifest is generated
// (`public/probe/`, gitignored). So the tests BUILD their corpus from the one
// committed input — the training level file — plus the canonical rows below.
//
// That is why there is no golden manifest and no drift gate here: `flatten()`
// is pure and deterministic, so the same inputs give the same bytes by
// construction, and a committed copy could only ever be a second source of
// truth to keep in sync (it was one, and it went red on every regeneration).
// ============================================================

export const ROOT = join(import.meta.dir, '..')
/** The one committed input: the training level file this course plays. */
export const PROBE_LEVEL_PATH = join(ROOT, 'nn-training/levels/ladder-c20-lives1.jsonc')
export const PROBE_COURSE = 'x20-opening'
/** Must equal the level file's own `name` (the generator checks this). */
export const PROBE_LEVEL_NAME = 'ladder-c20-lives1'

/**
 * The canonical 7-row set — game 0 is the §0 calibration game. Kept as test
 * data (not a served artifact) so the probe's schema/boot/kit tests exercise a
 * realistic course without depending on anything generated.
 */
export const PROBE_GAMES: ReadonlyArray<{
  game: number
  stage: number
  seed: number
  tag: string
}> = [
  { game: 0, stage: 2000, seed: 414009, tag: 'calibration' },
  { game: 1, stage: 2000, seed: 414013, tag: 'S1' },
  { game: 2, stage: 2001, seed: 414085, tag: 'S1' },
  { game: 3, stage: 2002, seed: 414021, tag: 'S1' },
  { game: 4, stage: 2003, seed: 414001, tag: 'S1' },
  { game: 5, stage: 2001, seed: 414050, tag: 'S2-positive' },
  { game: 6, stage: 2002, seed: 414065, tag: 'S3-godweak' },
]

/** The scratch-list payload a course is defined by (shape = the games file). */
export const PROBE_GAMES_JSON = JSON.stringify({
  course: PROBE_COURSE,
  level: PROBE_LEVEL_NAME,
  games: PROBE_GAMES,
})

/** Generated on import — nothing on disk is read except the level file. */
export const PROBE_MANIFEST_TEXT: string = flatten(
  readFileSync(PROBE_LEVEL_PATH, 'utf8'),
  PROBE_GAMES_JSON,
).json
export const PROBE_MANIFEST: ProbeManifest = parseProbeManifestText(PROBE_MANIFEST_TEXT)
