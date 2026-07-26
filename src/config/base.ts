/**
 * Base (eagle) durability — ONE fixed HP, damaged by the SHOOTER's FIREPOWER.
 *
 * Single rule: one bullet chips exactly `firePower` off the base pool. The
 * damage routine takes ONLY the shooter's `firepower` number (the call site
 * resolves kind/star into that one number), so it knows nothing about enemy
 * kinds, player stars, or difficulty.
 *
 * How BASE_MAX_HP was derived (pre-interpolated from the firepower config,
 * then baked in — there is NO runtime formula and NO per-kind code here):
 *   Firepower values live in config/combat.ts: fast 36, armor 43, basic 50,
 *   power 64. With `damage = firePower`, the hits-to-destroy for any shooter
 *   is ceil(BASE_MAX_HP / firePower). Solving for the spec'd anchors
 *   (fast 4, basic 3, power 2) yields BASE_MAX_HP ∈ (108, 128]; 120 is
 *   chosen (clean, central). Result on the current config:
 *     fast  (36) → ceil(120 / 36) = 4 hits
 *     basic (50) → ceil(120 / 50) = 3 hits
 *     power (64) → ceil(120 / 64) = 2 hits
 *     armor (43) → ceil(120 / 43) = 3 hits   ← a CONSEQUENCE of raw-firepower
 *                  damage: a single pool cannot also pin armor to 4 while power
 *                  is 2 (those four points aren't consistent with dmg=firePower).
 *                  Flagged for the designer; behaviorally armor still needs 3 hits.
 *
 * Adding a new enemy kind needs NO change here — its firepower from
 * config/combat.ts lands automatically on this single-pool scale.
 */

/** Fixed base HP for every non-classic difficulty. */
export const BASE_MAX_HP = 120

/** Classic keeps the authentic one-shot model: ANY single hit destroys it. With
 *  HP 1, even the weakest shot (firePower 36) exceeds it on the first hit, so
 *  the damage routine needs no special case. */
export const CLASSIC_BASE_MAX_HP = 1
