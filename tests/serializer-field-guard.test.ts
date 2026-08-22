import { describe, it, expect } from 'bun:test'
import { World } from '../src/game/World'
import { cloneWorld } from '../src/snapshot/WorldSerializer'

/**
 * §1.5 (plan/refactor.agy.md) — WorldSerializer field-coverage guard.
 *
 * cloneWorld enumerates World fields by hand. The classic failure mode: an
 * agent adds `world.newTimer` to World.ts but forgets the serializer — the
 * game still runs, yet every snapshot/rewind silently drops the new field.
 *
 * This guard makes that drift LOUD:
 *  1. every World instance field must either appear in a fresh clone or be
 *     listed in EXCLUDED below (with the reason it is safe to drop);
 *  2. every snapshot field must map back to a live World field (catches
 *     stale serialization after a rename).
 *
 * When this test fails after adding a World field: serialize it in
 * WorldSerializer.cloneWorld + restoreWorld, or — only if it is genuinely
 * transient — add it to EXCLUDED with a one-line justification.
 */

/** snapshot key → world key for the two renamed fields. */
const KEY_MAP: Record<string, string> = {
  tileGrid: 'tileMap',
  rngState: 'rng',
}

/**
 * Fields intentionally not serialized. Each entry is a deliberate decision,
 * not an oversight — keep justifications current.
 */
const EXCLUDED: Record<string, string> = {
  // Serialized under different names / mechanisms.
  tileMap: 'serialized as snap.tileGrid (row-cloned)',
  rng: 'restored via snap.rngState → rng.reseed()',

  // Re-derived on restore from serialized keys (restoreWorld re-derives).
  rules: 're-derived from snap.difficultyKey via RULES[]',
  difficulty: 're-derived from snap.difficultyKey via DIFFICULTIES[]',
  theme: 're-derived from snap.themeKey via THEMES[]',

  // Transient presentation/event data — cleared on restore, rebuilt by
  // PresentationLayer (AGENTS §2.5 presentation-is-disposable).
  explosions: 'transient visual state; restoreWorld clears to []',
  popups: 'transient visual state; restoreWorld clears to []',
  events: 'event bus double-buffer; consumed per frame, never persisted',

  // Menu / recovery UI navigation state — outside gameplay snapshot scope
  // (plan/refactor.agy.md §1.3 Phase A target for extraction).
  menuCursor: 'menu UI state, not gameplay',
  selectedStage: 'menu UI state, not gameplay',
  recoveryCursor: 'recovery overlay UI state',
  recoveryCountdown: 'recovery overlay UI state',
  recoveryFading: 'recovery overlay UI state',
  state: "lifecycle flag; restoreWorld forces 'playing'",

  // Internal caches / perf flags — recomputed from serialized data.
  _allTanksBuf: 'getter cache rebuilt on demand',
  _hasActiveMines: 'derived from mines array contents',
  _needsCleanup: 'per-tick entity-compaction signal',
  eventsSpare: 'event bus double-buffer back store',

  // Session-scoped signal & identity.
  rewindPending: 'one-tick signal consumed by Game.ts within the same session',
  seed:
    'session identity for RNG stream derivation; preserved by object identity across restoreWorld',
}

describe('WorldSerializer field coverage (§1.5 Option C guard)', () => {
  it('cloneWorld covers every World field unless explicitly excluded', () => {
    const world = new World()
    const snap = cloneWorld(world) as unknown as Record<string, unknown>
    const clonedKeys = new Set(Object.keys(snap).map((k) => KEY_MAP[k] ?? k))

    const missing = Object.keys(world).filter(
      (k) => !clonedKeys.has(k) && !(k in EXCLUDED),
    )
    expect(missing).toEqual([])
  })

  it('every excluded field has a non-empty justification', () => {
    for (const [key, reason] of Object.entries(EXCLUDED)) {
      expect(`${key}: ${reason.length > 0}`).toBe(`${key}: true`)
    }
  })

  it('no stale snapshot fields — every key maps back to a live World field', () => {
    const world = new World()
    const snap = cloneWorld(world) as unknown as Record<string, unknown>
    const worldKeys = new Set(Object.keys(world))

    const stale = Object.keys(snap)
      .map((k) => KEY_MAP[k] ?? k)
      .filter((k) => !worldKeys.has(k))
    expect(stale).toEqual([])
  })
})
