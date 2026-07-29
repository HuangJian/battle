# God AI P1 — Verification Record (2026-07-29)

Independent reproduction of the P1 survival/defense fix results reported for
the `god-ai` branch (uncommitted, on top of `aec21f4`). Method: `runSimulation`
headless harness, classic difficulty, 36000-tick budget, seeds 1..40,
Stage 0 + Stage 1 from `src/config/stages`.

## Reproduced numbers (exact match)

| Stage | After P0 (claimed) | After P1 (claimed) | Measured | base survival | gameovers |
|-------|--------------------|--------------------|----------|---------------|-----------|
| 0 Classic | 28/40 (70%) | 35/40 (87.5%) | **35/40 (87.5%)** ✓ | 95.0% | 2 |
| 1 Classic | 35/40 (87.5%) | 37/40 (92.5%) | **37/40 (92.5%)** ✓ | 100.0% | 0 |

### Failure breakdown (matches claimed 8 failures)
- Stage 0 gameovers (2): seeds 2 (3 kills), 13 (6 kills) — enemy walks along base row from afar
- Stage 0 timeouts (3): seeds 20 (18), 25 (17), 39 (17)
- Stage 1 timeouts (3): seeds 8 (18), 24 (18), 27 (16)
- Total: 2 gameover + 6 timeout = 8 failures ✓

## Code changes confirmed present
- **P1.1** `ThreatAssessor.ts:34`: dodge alignment threshold `CELL*0.75` (12px) → `TANK` (32px) in `findMostDangerousBullet`. (Note: a secondary candidate-cell safety check at `:256` still uses `CELL*0.75` — not part of the claimed fix.)
- **P1.2** `StrategyPlanner.ts:199` + `GodAIInput.ts:679`: `baseUnderThreat` / `isBaseUnderThreat()` threshold `row >= 20` → `row >= 18` (3-col window). New `isBaseUnderThreat()` method added (`GodAIInput.ts:672`).
- **P1.3** `StrategyPlanner.ts:220-223`: defense-return `if (baseUnderThreat && dist > maxPlayerDistFromBase) return defensePos` is now BEFORE the `if (canHunt)` hunt block (`:244`) — fires regardless of endgame.
- **P1.4** `GodAIInput.ts:500-504` (`skipT2aForDefense`) + `:561` (power-up guard): T2a camping and power-up collection skipped when base under threat and player far.

## Regression gate (tests/god-ai-regression-gate.test.ts, seeds 1..30, 18000t)
- Stage 0: winRate=86.7% (26/30), base 93.3%, avgKills 18.8 → PASS (floors 16/25/12)
- Stage 1: winRate=90.0% (27/30), base 100%, avgKills 19.7 → PASS (floors 20/27/14)
- **But floors are now too loose**: measured (86.7%/90%) vs floors (53%/67%). A
  meaningful regression would slip through. Recommend re-tightening (e.g.
  S0 wins>=22/base>=25/kills>=15, S1 wins>=24/base>=27/kills>=16) — same class
  of stale-gate issue fixed in the P0 round.

## Verdict
**Report is HONEST and reproducible.** Parity re-baseline coherent: seeds 999
and 55555 regressed stage_clear→timeout (matches DECISIONS §40); seed 12345
improved gameover→stage_clear.

## Open items
1. **Gate too loose** — tighten floors to P1 strength (see above) before/with P2.
2. P1 changes are UNCOMMITTED on `god-ai` (on top of `aec21f4`): `DECISIONS.md`,
   `GodAIInput.ts`, `StrategyPlanner.ts`, `ThreatAssessor.ts`, parity test.

## Next step
P2: re-run CMA-ES on the deadlock-free + defense-hardened architecture. Remaining
failures are pursuit/lead-aim (6 timeouts) and far-base-row walks (2 gameovers) —
predictive aiming may be needed for the timeouts.
