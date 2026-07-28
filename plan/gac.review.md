## Review: God-AI-Curriculum.md

### Overall Assessment

The plan is **well-researched, architecturally sound, and correctly identifies the real bottlenecks**. The core thesis — "toy stages are for per-subsystem verification, not CMA-ES optimization" — is the right call and aligns with AGENTS §2.3 (determinism) and the MANIFEST's "data over code" principle. However, several claims about the current codebase are **already stale** — bugs that the plan lists as "to fix" have already been fixed. The plan needs a freshness pass before execution.

---

### §3 — Gap Analysis: Mostly Accurate, One Already Done

**Gap A (enemyCount not controllable)** — ✅ **Confirmed accurate.**
- `ENEMIES_PER_STAGE = 20` is hardcoded at `src/constants.ts:43`.
- `World.ts:337` sets `enemiesRemaining = ENEMIES_PER_STAGE`, `World.ts:365` loops `for i < ENEMIES_PER_STAGE`.
- `Simulation.ts:245` uses `ENEMIES_PER_STAGE - w.enemiesSpawned`.
- `StageData` (`src/types.ts:334`) has no `enemyCount` field.
- `WorldSerializer.ts:56-57,130-131` persists `enemiesSpawned`/`enemiesRemaining` but there's no `enemiesTotal` — the plan's warning about snapshot safety is valid and important.

**Gap B (GodAIInput guards ghost base)** — ✅ **Confirmed accurate.**
- `GodAIInput.reset()` (line 200) does **not** cache `hasBase`.
- `selectTarget` (line 1198) hardcodes `BASE_POS.col` / `BASE_POS.row` unconditionally.
- `getDefaultDefensePosition()` (line 1157) returns `BASE_POS` unconditionally.
- `perception.ts:170` already has `hasBase: !!base` — the plan's "mirror this" precedent is correct.
- `TileMap` has `getBasePos()` (returns `null` when no base) and `isBaseDestroyed()`, but no `hasBase()` method. Adding one is trivial.

**Gap C (8×8 can't be expressed natively)** — ✅ **Confirmed accurate.** `GRID=26` is fixed. The steel-walled arena approach is pragmatic and requires zero code changes.

---

### §5.2 — Bug Fixes: **Already Done** ⚠️

This is the biggest issue with the plan. All three bugs listed as "to fix" have **already been fixed in the codebase**:

1. **`urgencyBonus` reversal (Bug 1)** — The plan says "change to `(tc.row - defenseRow + 1) * 100`". The code at line 1291 already reads:
   ```typescript
   const urgencyBonus = tc.row >= defenseRow ? (tc.row - defenseRow + 1) * 100 : 0
   ```
   With a comment: *"Fix Bug 1: urgencyBonus was reversed..."*. **Already fixed.**

2. **`findPowerUpTarget` score reversal (Bug 2/3)** — The plan says "change to `(6 - priority) * 1000`". The code at line 960 already reads:
   ```typescript
   let score = (6 - priority) * 1000 - dist * 10 - dangerLevel * 500
   ```
   With a comment: *"Fix Bug 2: Score formula was reversed..."*. And the `maxDist` logic at line 977:
   ```typescript
   const maxDist = priority <= 1 ? 8 : this.params.powerupMaxDivertDistance
   ```
   With: *"Fix Bug 3: maxDist logic was reversed..."*. **Already fixed.**

3. **`killerKind` in simulation-runner (Bug 5)** — The plan says "已修 (log Round 2); confirm". The code at `tools/simulation-runner.ts:171-178` already populates `failure.killerKind` by walking back through events. **Already fixed.**

4. **`failure!` empty assertion (Bug 6)** — The plan says "add `stage_clear` guard". The test at `tests/god-ai-gates.test.ts:23-26` already has:
   ```typescript
   if (result.outcome === 'stage_clear') {
     expect(result.failure).toBeUndefined()
   ```
   **Already fixed.**

> **Recommendation:** Remove §5.2 entirely or mark it as "✅ Done — verify only". These items waste implementation time and create false confidence about what's left.

---

### §5.3 — S6 Attack-Defense Switching: **Partially Implemented** ⚠️

The plan proposes adding S6 as a new feature (`huntEnemyThreshold` param). But the code **already has an S6 implementation**:

- `selectTarget` (line 1210+) has a `canHunt` check: `enemies.length <= 2 && w.enemiesRemaining <= 3 && !baseUnderThreat`.
- When `canHunt` is true, it chases the nearest enemy directly (line 1257+).
- The `endgameEnemyThreshold` param exists (line 87, default `1`) but is **not used** in the current `canHunt` logic — the thresholds are hardcoded to `2` and `3`.

> **Recommendation:** The plan should acknowledge the existing S6 implementation and focus on:
> 1. Making the hardcoded thresholds (`<=2`, `<=3`) configurable via `GodAIParams` (the `endgameEnemyThreshold` param is already declared but unused — this is a latent bug).
> 2. Tuning whether the current thresholds are too conservative (the plan's thesis is that 0% win rate stems from not hunting aggressively enough).
> 3. The `hasBase` guard (Gap B) — without it, S6's `baseUnderThreat` check references `BASE_POS` even when no base exists, making the "no-base" curriculum stages (1-3) test the wrong thing.

---

### §4 — The Five-Stage Ladder: Sound but Needs Adjustment

| Stage | Assessment |
|-------|-----------|
| **1** (1 enemy, no base) | Good. But without Gap B fix, the AI will still try to defend `(12,24)`. The `canHunt` check requires `enemiesRemaining <= 3` — with `enemyCount=1` this is satisfied, so hunting *should* activate. But `baseUnderThreat` checks `tc.row >= 20` near `BASE_POS` — with no base, this is meaningless. **Gap B is a hard prerequisite.** |
| **2** (3-5 enemies, no base) | Good. Tests multi-target fire discipline. Same Gap B dependency. |
| **3** (20 enemies, no base) | **This is the key stage.** It directly tests whether S6 can clear without base-defense distraction. The `canHunt` threshold (`enemiesRemaining <= 3`) means hunting only activates at the *end* — the AI will spend most of the game in defense mode against a ghost base. **This is likely the actual root cause of 0% win rate**, and the plan correctly identifies it. |
| **4** (maze, no base) | Good. But the plan's assertion that `branchCounts.navigate` should be `followPath`-dominant is **wrong** — the current code uses `directMove` (line 427) as the primary navigation, not `followPath`. `followPath` is only used in dodge-reaction and aggressive modes. The assertion needs to check `directMove` usage or the plan needs to explain why A* should replace `directMove` (the code comments explicitly say A* was "too slow to catch wandering enemies"). |
| **5** (full classic) | Good. Standard regression. |

> **Key concern with Stage 4:** The plan says "A* navigation is really used (not `directMove`)" but the codebase **intentionally abandoned A* for target pursuit** in favor of `directMove` (see the extensive comment at line 414-426). The plan needs to either:
> - Accept `directMove` as the navigation strategy and drop the A* assertion, or
> - Argue why A* should be reinstated (which contradicts the existing code's rationale and would need a DECISIONS.md entry).

---

### §5.4 — `tools/curriculum.ts`: Good Design

The proposed `CurriculumStage` type and `makeArena()` helper are clean. A few notes:

- `tools/curriculum.ts` doesn't exist yet — confirmed.
- optimize-godai.ts exists — confirmed. The plan correctly says they should coexist.
- The `makeArena()` helper should live in tools not src — it's a test scaffold, not engine code. The plan gets this right.
- The `--only N` CLI flag is good for iterative development.

---

### §7 — Risks: Accurate

All four risks are real:
- **Toy stages as CMA-ES fitness** — the plan correctly forbids this.
- **`enemiesTotal` missing from `WorldSerializer`** — confirmed: WorldSerializer.ts only has `enemiesSpawned`/`enemiesRemaining`. This **must** be fixed in the same PR as Gap A.
- **Ghost base guarding** — confirmed: no `hasBase` guard exists in `GodAIInput`.
- **A* not actually called** — as noted above, this is more nuanced than the plan suggests. The codebase deliberately uses `directMove` for pursuit.
