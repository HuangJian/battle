# God AI P0 — Verification Record (2026-07-29)

Independent reproduction of the P0 T2a deadlock fix results reported for the
`god-ai` branch (uncommitted, on top of `fd126ae`). Method: `runSimulation`
headless harness, classic difficulty, 36000-tick budget, seeds 1..40,
Stage 0 + Stage 1 from `src/config/stages`.

## Reproduced numbers (exact match)

| Stage | Claimed | Measured | base survival | gameovers |
|-------|---------|----------|---------------|-----------|
| 0 Classic | 28/40 (70%) | **28/40 (70.0%)** ✓ | 90.0% (36/40) | 8 |
| 1 Classic | 35/40 (87.5%) | **35/40 (87.5%)** ✓ | 97.5% (39/40) | 2 |

### Stage 1 failure breakdown (matches claimed 3 timeout + 2 gameover)
- Timeouts (max_ticks): seed 8 (18 kills), 20 (17), 24 (18)
- Gameovers: seed 16 (14 kills, lives exhausted — base alive), 26 (16 kills, base destroyed)

### Regression gate (`tests/god-ai-regression-gate.test.ts`, seeds 1..30, 18000t)
- winRate = **66.7%** (20/30) — floor is 6 → PASS
- base survival = 90.0% — floor is 25 → PASS
- avgKills = 16.9 — floor is 9 → PASS

## Verdict
**Report is HONEST and reproducible** (contrast: earlier CMA-ES v3 report was
not — floats dropped on write-back). tsc `--noEmit` clean; oxlint 0 warnings /
0 errors.

## What was checked
- `src/ai/GodAIInput.ts`: P0.2 `scan.enemy` gate (L498), P0.1 camp tracking
  (L500–535), P0.3 nav-stuck escape (L580–606) all present and match the
  described mechanism. The old T2a-holds-at-wall branch is removed; wall
  breaking now falls through to `navigate`/`directMove`/`canMoveOrBreak`.
- `tests/godai-split-parity.test.ts`: baseline re-locked — 4 seeds
  timeout→stage_clear (1/100/999/55555), 2 timeout→gameover (2/12345).
- `tools/curriculum.ts`: stage 3 seed restored 7→42 (seed 42 clears after P0).
- `DECISIONS.md §39`: complete and consistent with the implementation.

## Open items before P2
1. **Stale regression gate**: comment still says "v3 tuning floor" with floors
   6/25/9 and a 2026-07-28 date. Now we measure 20/27/16.9 on seeds 1..30 —
   bump floors (e.g. 18/25/12) and refresh the date so the gate stays meaningful.
2. **Optional**: extend the gate to Stage 1 (P0 lifted it to 87.5%) so a future
   regression in the harder stage is caught.
3. **`tools/decision-trace.ts`** still carries the `world.rules` correctness fix
   from the diagnostic sweep (uncommitted) — keep it.

## Next step
P2: re-run CMA-ES on the fixed architecture. The T2a deadlock no longer
dominates the search space, so the optimizer can now reach a higher win rate
than the old 20% ceiling. Recommended prep: refresh the regression-gate floors
(item 1) so the new strength is locked before tuning.
