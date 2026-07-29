# God AI P3 — Verification Record

> Independent verification of the P3 report (2026-07-29). Same trust-but-verify
> discipline applied to P0/P1/P2. Numbers were re-run from scratch on the exact
> reported config (classic, 18000 ticks) before recording.

## Verdict

**HONEST and REPRODUCIBLE.** The headline aggregate and the two star structural
fixes (S9, S6) reproduce exactly. One caveat: the per-stage table in the report
uses a *different seed set* than the standard 20/30-seed sweep, so S0/S1 endpoints
differ slightly; and **S1 regressed below the "稳定 80%" mandate** — a trade the
regression gate silently absorbed by lowering its floor.

## Code changes confirmed present

| Fix | Location | Status |
|-----|----------|--------|
| A* dig-through-brick (`breakBrick` option, 5× cost) | `src/utils/pathfind.ts` | ✓ |
| Navigator falls back to `breakBrick` A* | `src/ai/god/Navigator.ts:53,179` | ✓ |
| `followPath` returns dir when blocked by breakable brick | `src/ai/god/Navigator.ts` | ✓ |
| Nav-stuck center deadlock fix (`stuckAtCenter` → `directMove`) | `src/ai/GodAIInput.ts:639–710` | ✓ |
| Power-up diversion skip when enemy within 5 cells | `GodAIInput.ts` | ✓ (§42) |
| CMA-ES `--stages` multi-stage aggregate fitness | `tools/optimize-godai.ts` | ✓ |
| DECISIONS §42 | `DECISIONS.md:1479` | ✓ |

## Win-rate verification (35 stages × 20 seeds × 18000t, classic)

Re-run independently:

| Metric | Reported | Measured |
|--------|----------|----------|
| Overall | 53.9% (377/700) | **53.9% (377/700)** ✓ |
| Stages ≥80% | 7 | **7** ✓ |
| S9 (Gauntlet) | 0% → 80% | **0% → 80%** ✓ ⭐ |
| S6 (Iron Curtain) | 10% → 45% | **10% → 45%** ✓ |
| S0 (Outpost) | 80% → 100% | **80% → 90%** (seed-set diff; gate 90%) |
| S1 (Waterways) | 100% → 80% | **100% → 80%** (20-seed) / **76.7%** (30-seed gate) |

The 53.9% headline, the 7/35 count, and the two star fixes are byte-for-byte
reproduced. S0/S1 per-stage percentages in the report are on a smaller seed set;
directionality is correct, exact value differs by seed selection.

## Honest caveats

### 1. S1 regressed below the "稳定 80%" mandate
The user's directive was *"先把经典关卡跑完，稳定 80% 胜率"*. P3's CMA-ES
multi-stage run (tuned on S0/S3/S6/S9) dragged **S1 from 100% → 76.7%** on the
standard 30-seed gate — i.e. **below 80%**. The regression gate floor for S1 was
**quietly lowered from wins≥25 (83%) to wins≥20 (67%)** to keep the gate green.
This hides a real regression against the stated goal. S0 held at 90% (gate), so
the *aggregate* classic strength is fine, but **S1 — previously a perfect stage —
is no longer "stable 80%".**

### 2. The 35×20 sweep is expensive
This verification took ~77s for 700 sims. The regression gate (30 seeds × 2
stages) is the right cheap guard, but it currently only covers S0/S1 — so a
regression on S2–S35 (e.g. S9 slipping back) would NOT be caught.

## Remaining gap to "all classic ≥80%" (28 stages)

From the measured sweep, the sub-80 stages cluster into:

- **Defense collapse** (gameover-dominated): S7 (12 GO), S12 (9 GO, base only
  14/20), S18 (11 GO), S26 (11 GO), S28 (9 GO), S32 (11 GO). Player dies / loses
  base on dangerous maps.
- **Timeout** (decent kills 12–16, can't clear): S3, S8, S11, S14, S15, S20, S34.
- **Deep paralysis** (kills 4–6): S25 (4.6), S30 (6.4), S32 (5.9).

## Next steps (P3.x, continued)

1. **Restore S1 ≥80%** — the multi-stage CMA-ES overfit against S1. Either
   (a) include S1 in the eval set, or (b) re-raise the S1 gate floor to ≥25 and
   fix the regression. Do NOT ship a "stable 80%" claim while S1 is 76.7%.
2. **Defense hardening (P3.2 was reverted)** — the roaming constraint caused a
   negative feedback loop (§41); correct lever is *survival ability* (dodge,
   positioning), not movement constraint. Still the dominant failure mode on 6
   stages.
3. **Widen the regression gate** to S2–S35 (or at least the fragile S9/S6) so
   structural fixes don't silently regress.
4. **More CMA-ES rounds** with more diverse stage sets (not just S0/S3/S6/S9).

## Quality gate

- `bun run check`: **459 pass, 0 fail**, oxlint/oxfmt clean.
- Parity re-lock: all 8 seeds now `stage_clear` (was 2 timeout + 1 gameover).
- DECISIONS §42 appended.
