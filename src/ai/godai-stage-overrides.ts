import type { GodAIParams } from './GodAIInput'

/**
 * Per-stage GOD AI parameter overrides (P4, plan/God-AI-Next-Round).
 *
 * §58 (2026-07-31): THE OVERRIDE TABLE IS NOW EMPTY. Stage-specific tuning
 * was generalized into data-driven stage-level adaptation in
 * `computeStageAdaptedParams()` (GodAIInput.ts), triggered by stage
 * characteristics instead of stage name:
 *
 *   - S32 Diamond's close-combat camp/nav (50/50/90) → armorAdaptRatio:
 *     any stage with ≥35% armor in the queue now gets the same timing.
 *   - S26 Brick Maze's fast replan + path noise (30 / 0.05) →
 *     brickDenseAdaptRatio: any stage with ≥45% brick density now gets the
 *     same replanning.
 *
 * This removes the last hardcoded stage names from the AI. The adaptation is
 * computed in `GodAIInput.reset()` from `world.spawnQueue` (armor ratio) and
 * `world.tileMap` (brick density) — both pure World-state reads, fully
 * deterministic. The threshold and adapted values are `GodAIParams` fields
 * (§58), so CMA-ES can tune them directly.
 *
 * Why the table is kept (empty): `applyStageOverrides` is still called by
 * `simulation-runner.ts`. Keeping the function (returning the base unchanged)
 * avoids touching every call site. The `skipStageOverrides` flag in the
 * simulation runner is now a no-op for stage-specific behavior — the
 * adaptation lives inside GodAIInput and is controlled by the
 * `armorAdaptRatio` / `brickDenseAdaptRatio` params (set to 0 to disable).
 *
 * Historical override audit (2026-07-30, DECISIONS.md §54-§56):
 *   S6  Iron Curtain  — REMOVED (§54): override 59.2% < naked 62.5%.
 *   S18 Frozen Field  — REMOVED (§55): override 56.7% < naked 60.8%.
 *   S25 Ice Palace    — REMOVED (§55): override 77.5% = naked 77.5%.
 *   S32 Diamond       — REPLACED by §58 armorAdaptRatio (generalized).
 *   S26 Brick Maze    — REPLACED by §58 brickDenseAdaptRatio (generalized).
 *
 * LESSON: overrides are data, and data goes stale. After RNG split, §47
 * collision fix, and v7 evaluation, the global default improved dramatically.
 * Every override must be re-validated after major baseline shifts. When an
 * override's core mechanism can be abstracted into a universal parameter (as
 * with S32's close-combat → t2aHighHpMaxRange / armorAdaptRatio, and S26's
 * fast replan → brickDenseAdaptRatio), prefer the generalization over the
 * per-stage override.
 */
export const GOD_AI_STAGE_OVERRIDES: Record<string, Partial<GodAIParams>> = {}

/**
 * Merge per-stage overrides (if any) into a base parameter set.
 * Returns the base unchanged when the stage has no overrides.
 *
 * §58: the override table is empty — all stage-specific tuning is now handled
 * by `computeStageAdaptedParams()` in GodAIInput.reset(). This function is
 * kept for call-site compatibility (simulation-runner.ts).
 */
export function applyStageOverrides(stageName: string, base: GodAIParams): GodAIParams {
  const o = GOD_AI_STAGE_OVERRIDES[stageName]
  return o ? { ...base, ...o } : base
}
