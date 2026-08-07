// §167 / B4: super-item strategic activation (超级道具战略激活).
//
// God AI historically never activated stocked super items (wasItemPressed
// returned false — "super items are human-only"). Hard 35×120 forensics
// (2026-08-07) showed ~8% of losing runs finish with an UNUSED guard stock
// and ~8.5% with an unused frenzy stock. This module decides, once per tick
// inside think(), whether the AI "presses" F5 (guard) / F6 (frenzy); the
// flags are consumed by wasItemPressed() on the same tick and reset in
// endFrame().
//
// Triggers are deliberately REACTIVE (threat / already-engaged), never
// positional — the §163/§164 family proved that proactive defensive
// posturing pins the player and starves the kill rhythm. Item activation
// also never touches the movement decision chain itself.
//
// Determinism: pure function of World state + params (no RNG). Sacrifice is
// passive (fires on life loss), rewind needs the RecoveryController (Game.ts
// — nobody consumes rewindPending headlessly), so neither is wired here.
import type { GodAIInput } from '../GodAIInput'
import type { DecisionContext } from './DecisionCore'
import { scanAheadImpl } from './FireControl'

/** Set self._pressGuard / self._pressFrenzy for this tick. */
export function superItemPressesImpl(self: GodAIInput, ctx: DecisionContext): void {
  self._pressGuard = false
  self._pressFrenzy = false
  const prm = self.params
  if (prm.superItemMode <= 0) return
  const w = ctx.w

  // ---- F5 天降神兵: summon the base guard while the base is threatened ----
  // Gate stack: stock available → base exists → reactive threat signal
  // (isBaseUnderThreat, per-tick cached) → no allied guard already on the
  // field (re-summoning while one lives adds 2 extra enemies for marginal
  // gain). Decoys live in w.allies too — they are not guards.
  if (prm.superItemGuardThreat > 0 && w.guardStock > 0 && self.hasBase) {
    let guardAlive = false
    const allies = w.allies
    for (let ai = 0; ai < allies.length; ai++) {
      const a = allies[ai]
      if (a.alive && !a.isDecoy) {
        guardAlive = true
        break
      }
    }
    if (!guardAlive && self.isBaseUnderThreat()) self._pressGuard = true
  }

  // ---- F6 狂暴宣泄: barrage down the current facing lane ----
  // Frenzy locks movement for the whole barrage, so only release when the
  // facing lane already holds an enemy (scanAhead — the shots will land,
  // and a wall in between counts as "no hit") and no bullet is coming at
  // the player (ctx.threat — dodge owns that tick; standing still into it
  // is a free death). Never re-release mid-barrage.
  if (
    prm.superItemFrenzyAim > 0 &&
    w.frenzyStock > 0 &&
    (ctx.p.frenzyTimer ?? 0) <= 0 &&
    ctx.threat === null &&
    scanAheadImpl(self, ctx.pcx, ctx.pcy, ctx.p.dir).enemy
  ) {
    self._pressFrenzy = true
  }
}
