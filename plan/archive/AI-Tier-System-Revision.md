# AI Tier System Revision — Spawn-Rolled Intelligence

> Revision of `plan/Tactical-Intelligence-Framework.md`. Where the two disagree,
> this document wins. Status: **design approved, pending implementation**.
>
> Origin: design review on 2026-07-26. All nine review questions were resolved
> by the owner; their decisions are baked in below and marked **[D1]–[D9]**.
> Follow-up 2026-07-26: the §5.3 combat-boost question was decided by the
> owner — Commander keeps the +15% elite modifier for now (**[D10]**,
> provisional). No open questions remain; ready for implementation.
>
> Code-review pass 2026-07-26 (`plan/ai-tier.review.md`): cross-referenced
> against the actual code. Findings folded in below — exact edit targets
> (snapshot serializer, `World.loadStage`, `Simulation` derivation site,
> broadcast predicate), one design fix (**[D9-fix]**, cap-vs-floor deadlock),
> and tightened acceptance bars. All references verified against source line
> numbers as of that date.

---

## 1. Vision

Intelligence tiers stop being welded to tank kinds. Every enemy kind
(basic/fast/power/armor) can carry any AI tier; the tier is **rolled at spawn
time from a per-difficulty probability table**. Difficulty stops scaling
capability numbers entirely — the *only* thing difficulty controls on the AI
axis is the tier distribution.

This replaces the current two-lever model (`KIND_TO_LEVEL` + `DIFFICULTY_AI`
scaling) with a single lever: **distribution**. Simpler to reason about,
simpler to tune, and it preserves the manifesto's rule that difficulty must
come from better decisions, not stronger stats.

---

## 2. The Five Tiers

A new bottom tier **None** joins the existing four. Tier capability values are
**fixed** — no per-difficulty scaling of any number in this table
(`DIFFICULTY_AI` retires, see §8).

| Tier | Strategic | Can command | Compliance | Dodge | Predict | React (ms) | Aim err | Route noise |
|---|---|---|---|---|---|---|---|---|
| **None** | — | — | 0% (deaf) | — | — | — | — | — |
| **Rookie** | ✗ | ✗ | 50% | 0.2 | 1 | 420 | 0.35 | 0.4 |
| **Soldier** | ✗ | ✗ | 70% | 0.45 | 2 | 300 | 0.2 | 0.22 |
| **Veteran** | ✗ | ✗ | 80% | 0.75 | 4 | 200 | 0.1 | 0.12 |
| **Commander** | ✓ (active only) | ✓ (active only) | 90% | 0.9 | 8 | 150 | 0.05 | 0.05 |

### 2.1 `teamwork` is split in two **[D1]**

The boolean `teamwork` field is **deleted** and replaced by:

- **Issuing directives** — exclusive to the *active* Commander (§4).
- **Obeying directives** — universal, gated by per-tier **compliance**
  (指令遵从度): the probability that a unit receives, understands, and executes
  a directive. None-tier tanks are deaf (0%).

Consequence: Veteran loses `strategicThinking`/`teamwork` flags **[from the
approved design]** but *gains* the ability to obey directives at 80% — obeying
is no longer "teamwork", it is baseline soldiering.

### 2.2 Compliance roll semantics **[D7]**

Rolled **once per directive, on arrival**, via `world.rng`. The result
(boolean) is cached in `aiState` together with the directive's sequence id and
holds until that directive expires or is replaced. No per-tick re-rolling
(which would collapse probabilities to ~0/1), no spawn-time lifelong roll.

Cache key = directive identity [review §6]: each active-Commander broadcast
increments the **World-level `world.directiveSeqCounter`** (§7); the directive
carries that seq. A receiving tank compares the incoming seq to its cached
`aiState.directiveSeq` — if different, it re-rolls compliance and stores the
new `(directiveSeq, directiveCompliant)`; if equal, the cached boolean holds.
Putting the counter on the World makes it snapshot-safe and **succession-safe**:
a new Commander's first broadcast gets a fresh seq, forcing every tank to
re-roll regardless of the previous Commander's last directive.

The active Commander always follows its own directives (no self-roll); the
`broadcastDirective` self-skip (`TacticalIntelligence.ts:506`) stays.

---

## 3. None Tier — Classic Behavior Branch **[D4] [D5]**

"None" is **not** the tactical pipeline with zeroed numbers — it is a separate,
minimal behavior branch styled after the original Battle City:

- Random wander with a **downward / toward-base bias** (weights to be tuned in
  playtesting — the known "headless fly" risk).
- Random fire on a cooldown-plus-jitter schedule.
- Direction re-rolls on wall collision / periodic timer.
- **All randomness through `world.rng`** (AGENTS §2.3). Deterministic,
  snapshot-safe, replay-safe.
- Ignores directives entirely; never dodges; never counted as a squad member.

**Code shape [review §5]**: None is **not** the tactical pipeline with zeroed
numbers. `TacticalIntelligence.updateTank` takes an **early branch** for
`level === 'none'` (a dedicated `updateNoneTank`) that bypasses
perception → analysis → goal-eval → dodge entirely and runs only the minimal
wander+fire logic. This keeps the None path cheap and its RNG draws isolated
from the tactical stream.

Acceptance criterion is **"classic style"**, not "bit-exact NES behavior" —
there is no NES behavior spec in this repo, so strict equivalence is
unverifiable and explicitly *not* the bar. To keep the branch *testable*
despite the subjective bar, add an **objective floor** (mirrors the existing
no-stall test at `tactical-ai.test.ts:108`):

- Over 600 ticks, None-tier tanks collectively traverse **> 0** net px (no
  freeze / no wall-lock) and each fires **at least once** within any
  `fireCooldown + maxJitter` window.
- Deterministic under a fixed seed (same seed ⇒ identical path + fire ticks).

**Replay / RNG-stream compatibility [review §5]**: there is **no replay
persistence system yet** (`plan/tasks.chat.md` lists Replay as unchecked
`[ ]`), so **backward compatibility with pre-revision Classic replays is a
non-goal** — we are free to let None consume `world.rng` on its own cadence.
The forward requirement stands: same seed + same inputs ⇒ identical run
(DoD §9). The one stream-preservation rule we *do* keep is the **tier-roll
gate** (§7): a 100%-None distribution consumes **zero** RNG for the tier roll,
so Classic's tier decision never perturbs the stream — only None's own
wander/fire draws do, which is acceptable since there is nothing to be
backward-compatible with.

**Classic difficulty = faithful-recreation mode, outside the difficulty
ladder.** It is 100% None tier. The Relax < Hard < Chaos ladder ranks the
*systemized-AI* modes only; Classic sits beside the ladder as "the original
game", and the UI should present it that way.

---

## 4. Command Authority **[D2] [D3]**

Multiple Commanders may coexist; only one holds command.

- **Active Commander** = the *alive* Commander with the **highest spawn
  sequence number** (last born). Derivable every tick from World state — the
  only persistent ingredient is a per-tank `spawnSeq` (assigned from a World
  counter at spawn; snapshot-safe by construction).
- **Succession**: when the active Commander dies, the previously-born, still
  alive Commander **regains** command automatically (it is now the
  highest-`spawnSeq` survivor). No election, no gap in the rule — just the max.
- **Taking office**: a Commander that becomes active (by birth or succession)
  broadcasts its first directive **1 s after taking office** (set
  `commanderTimer = 1000`). This replaces the old "first broadcast when spawn
  animation ends" behavior (today: `commanderTimer = 0` at spawn,
  `Simulation.ts:237`) and prevents Chaos-mode command churn from starving the
  strategic layer.
  - The 1 s is measured from **becoming active**, not from spawn. Set
    `commanderTimer = 1000` at the moment `activeCommanderId` *changes* to this
    tank — not in `createTank`. A Commander that becomes active by succession
    10 s into its life must still wait exactly 1 s, not 11 s.
  - On succession, the field is **overwritten** to `1000` (not incremented).
    Do not confuse this office-delay value with the normal re-broadcast
    interval `COMMANDER_INTERVAL_MS * (0.8 + rng*0.4)` used at
    `TacticalIntelligence.ts:85`.
- **Broadcast predicate change**: today the gate is
  `if (brain.isCommander && brain.commanderTimer <= 0)`
  (`TacticalIntelligence.ts:83`). Under the new design it becomes
  `if (tank.id === world.activeCommanderId && brain.commanderTimer <= 0)`.
  `isCommander` (born-as-commander flag) no longer gates broadcasting — only
  active-command identity does. `broadcastDirective`'s existing
  `t === commander` self-skip (`TacticalIntelligence.ts:506`) stays.
- **Derivation site [review §2.4]**: `world.activeCommanderId` is recomputed
  by **`Simulation`, once per tick, before `ai.update`** —
  `activeCommanderId = argmax(spawnSeq) over alive commander-tier tanks`
  (null if none). If it changed vs last tick, set the new active tank's
  `commanderTimer = 1000`. The AI layer only **reads** the field; it never
  computes it (One-Author invariant).
- **Inactive (non-commanding) Commanders** — Commander-tier tanks that are
  alive but are *not* the highest-`spawnSeq` survivor. They keep their full
  individual capability row (dodge 0.9, predict 8, react 150 ms…) **and the
  +15% combat boost (§5.3)** — they are still Commander tier in every respect
  except command. They fight as "super Veterans": no strategic thinking, no
  broadcasting. They obey the active Commander's directives at their 90%
  compliance. (Distinct from a cap **downgrade** in §5.1, which converts a
  spawn to *actual Veteran tier* — that one is not a Commander and gets no
  boost.)
- **Crown / aura visibility [review §2.5]**: the commander crown+aura
  (`GameRenderer.ts:577`, gated on `aiState.isCommander === true`) marks the
  **tier**, not the command role. So **all** alive Commander-tier tanks (active
  *and* inactive, up to the §5.1 cap of 2) render the crown. `isCommander`
  keeps its "born as commander" meaning and remains the render flag; a cap
  **downgrade** to Veteran (§5.1) is *not* born-commander, so it gets Veteran
  insignia and no crown — consistent by construction.
- Implementation detail: `Simulation` maintains `world.activeCommanderId`
  (nullable). Stored on World ⇒ snapshot-safe (serializer touch points in §7).

---

## 5. Spawn-Time Tier Roll **[D8]**

Tier is rolled per spawn in `Simulation.updateSpawning`, via `world.rng`,
from the difficulty's distribution:

| Difficulty | None | Rookie | Soldier | Veteran | Commander |
|---|---|---|---|---|---|
| **Classic** | 100% | — | — | — | — |
| **Relax** | — | 60% | 20% | 15% | 5% |
| **Hard** | — | 30% | 30% | 28% | 12% |
| **Chaos** | — | 20% | 30% | 25% | 25% |

Data shape: `DIFFICULTY_TIER_DISTRIBUTION: Record<string, Partial<Record<IntelligenceLevel, number>>>`
in `src/ai/config.ts`. Each row must sum to 1 (unit-tested).

### 5.1 Commander floor & cap **[D9] [D9-fix]**

Each stage spawns exactly 20 enemies (`decodeForces`), so quotas are per-stage:

- **Floor (pseudo-random guarantee)**: Relax ≥ 1, Hard ≥ 2, Chaos ≥ 4
  Commanders *rolled* per stage. Enforced dynamically at roll time: while
  `remainingSpawns <= commanderQuotaRemaining`, the roll is **forced** to
  Commander. (All floors sit at/below the distribution expectations of
  1 / 2.4 / 5, so forcing is rare.)
- **Cap**: at most **2 Commander-tier tanks alive on screen** (active and
  inactive Commanders both count). If a roll (natural or forced) resolves to
  Commander while 2 are already alive, that spawn **downgrades to Veteran**
  (actual Veteran tier — Veteran insignia, no +15% boost, per §5.3).
- **Quota accounting [D9-fix, review §7]**: **rolling** Commander consumes one
  quota unit *regardless of whether the cap then downgrades it*. The floor
  guarantees a minimum number of Commander **attempts**, not a minimum number
  that survive the cap. This resolves the deadlock the review found: if the
  quota only decremented on *surviving* spawns, a late cluster of forced rolls
  against a full cap would keep re-forcing forever and the floor could be
  structurally **unsatisfiable** (e.g. Chaos floor 4 but cap binds ⇒ only 2
  ever born, quota stuck). Counting attempts makes the floor always reachable
  within 20 spawns.
  - Rationale: the cap is a hard readability/fairness limit (never > 2 on
    screen); the floor is a soft "the player should meet Commanders this often"
    intent. When they conflict, the cap wins on screen and the floor is
    satisfied in *intent* (attempts made). This is the review's recommended
    option (b), adopted as the design.
- **Precedence**: cap wins in the moment; floor is satisfied by attempt-count.
  With 20 spawns, quota ≤ 4, and the attempt-based decrement, the floor is
  always met.

### 5.2 Retirements

- `difficulty.eliteChance` — **deleted** (Commander probability now lives in
  the distribution table; keeping both would double-roll Commanders).
- The elite roll block in `Simulation.updateSpawning` — replaced by the tier
  roll.
- `KIND_TO_LEVEL`, `levelForKind` — deleted (kind no longer implies tier).
- `DIFFICULTY_AI`, `resolveConfig`'s scaling path — deleted. With no scaling,
  `resolveConfig(level)` collapses to `INTELLIGENCE_LEVELS[level]` (already a
  direct lookup), so the function and its memoization can be **deleted
  outright** — call sites read `INTELLIGENCE_LEVELS[level]` directly. The
  `ResolvedConfig` type also simplifies: its `difficultyKey` member is no
  longer meaningful (difficulty no longer touches config), so drop it — a tier
  config is just `IntelligenceConfig` + `level`.

Dead config is deleted, not kept "just in case" (AGENTS: keep it simple).

### 5.3 ✅ Decided — Commander keeps the +15% combat boost **[D10]**

Today an elite gets `applyEliteModifier` (**+15% combat stats**) *and* the
commander AI tier. Keeping the boost means Chaos ships up to 25% stat-boosted
enemies — a partial exception to the standing decision that **difficulty must
not scale enemy combat power** (`config/difficulty.ts` header, DECISIONS.md
Combat Capability System).

**Decision (owner call): KEEP the boost for now.** Every Commander-tier spawn
still receives `applyEliteModifier` (+15%), exactly as elites do today — the
call just moves from the old `eliteChance` roll to the new tier roll. This is
explicitly marked **provisional**: revisit after playtesting; if Commanders
feel unfairly strong on Hard/Chaos, drop the modifier without touching the
tier system (the two remain one-line separable in `updateSpawning`).

Notes:
- The exception must be recorded in DECISIONS.md as a *scoped carve-out* of
  the "difficulty never scales combat power" rule (boost rides the tier, and
  tier distribution is difficulty-driven).
- **The boost rides the *tier*, not the command role [D10-fix].** *Every*
  Commander-tier tank alive gets the +15% boost — including inactive
  (non-commanding) Commanders when 2 coexist on screen (§4). Command authority
  (who broadcasts) and the combat boost are fully independent: only the
  latest-born Commander issues directives, but **all** Commanders fight boosted.
- The **only** Commander-roll that gets no boost is a **cap downgrade (§5.1)**:
  when the roll is converted to *actual Veteran tier* because 2 Commanders are
  already alive. That tank is a Veteran (Veteran insignia, no boost), not a
  Commander. The downgrade is decided at spawn, before stats are finalized.
- **Code shape [review §3]**: today the elite block
  (`Simulation.ts:214–237`) applies `applyEliteModifier` **then** sets
  `level = 'commander'`. The new code must reverse the order so the cap can
  veto cleanly: **decide the final tier first**, then apply the boost
  conditionally. i.e. `roll tier → if commander && aliveCommanders < 2: apply
  boost + set level='commander'; else if commander (cap full): set
  level='veteran', NO boost; else: set rolled level`. Never boost-then-
  downgrade — that would leak a +15% Veteran.

---

## 6. Readability — Rank Insignia **[D6]**

Kind no longer predicts behavior, so the tier must be readable at a glance
(MANIFEST §12):

- **Commander**: already has its special visual treatment — unchanged.
- **Rookie / Soldier / Veteran**: add small rank insignia (chevron-style
  military marks: 1 / 2 / 3 bars) as a decorative overlay on the enemy hull.
  Follows the faction convention (enemy angry-face stays; insignia is
  additive), rendered like other overlay sprites (presentation-only,
  disposable, never in World).
- **None**: no insignia — Classic mode looks exactly like the original.

New SVG assets: 3 small overlay marks in `src/assets/sprites/`, registered in
the sprite index, composited by `SpriteArtist` per `aiState.level`.

**Render shape [review §8]**:
- The current path passes only `isCommander` to `drawEnemyTank`
  (`GameRenderer.ts:576–590`); to draw insignia it must also read
  `tank.aiState?.level` — a **read-only** presentation access (Presentation
  observes World; no invariant issue). `SpriteArtist.drawEnemyTank`
  (`SpriteArtist.ts:588`) gains a `level` parameter.
- Follow the existing **hit-overlay pattern** exactly (`fx.hit1–4` via
  `SpriteCache.getHitSprite`): pre-rasterize the 3 insignia in `SpriteCache`,
  draw them **after the hull, before the commander aura**.
- **Suppression / no-stacking**: insignia is drawn **only** for
  Rookie/Soldier/Veteran. Commander-tier draws the **crown instead** (never
  crown + insignia together); None draws **nothing** (Classic stays clean).
  Note a single enemy already can carry the HP-level aura (`drawHpLevelAura`,
  levels 2–6) + one tier mark — the tier mark (insignia *xor* crown) must be
  mutually exclusive so at most those two decorative layers coexist.

---

## 7. State & Determinism Requirements

**New state (all flat, serializable, RecoverySystem shallow-copy safe):**

| Field | Location | Init / reset point |
|---|---|---|
| `spawnSeq` | per tank (`aiState`) | assigned from `world.spawnSeqCounter++` in `createTank` |
| `spawnSeqCounter` | `World` | 0 at `World` construction; **not** reset per stage (monotonic) |
| `activeCommanderId` | `World` | recomputed each tick by `Simulation` (§4); `null` when no commander alive |
| `commanderQuotaRemaining` | `World` | **set in `World.loadStage`** from the difficulty floor (Relax 1 / Hard 2 / Chaos 4 / Classic 0) |
| `directiveSeqCounter` | `World` | 0 at construction; `++` on every active-Commander broadcast |
| `directiveSeq` | per-brain (`aiState`) | seq of the directive this tank last rolled compliance for |
| `directiveCompliant` | per-brain (`aiState`) | cached boolean result of that roll |

**Snapshot serializer is an explicit edit target [review §2.1]** — this is the
easiest field to forget and a silent determinism bug if missed:
- `WorldSnapshot` (`src/snapshot/types.ts:88–131`) is a fixed-field interface;
  add `spawnSeqCounter`, `activeCommanderId`, `commanderQuotaRemaining`,
  `directiveSeqCounter` to it.
- `cloneWorld` / `restoreWorld` (`WorldSerializer.ts`) hand-copy every field —
  add the four World-level fields there too. Use the existing **`bulletSeq`**
  handling as the template (added for exactly this reason — see DECISIONS.md
  bullet-speed section).
- The **per-tank** fields (`spawnSeq`, `directiveSeq`, `directiveCompliant`)
  ride for free inside `cloneTank`'s `aiState: { ...t.aiState }` shallow copy
  (`WorldSerializer.ts:11–16`) — no extra work.

**`spawnSeq` must NOT reuse `genId()` [review §2.3]**: the module-level
`genId()` (`World.ts:7`) is **not** reset between Worlds, so it cannot serve as
a per-World sequence. Use the dedicated `world.spawnSeqCounter` (same trap the
bullet-speed design avoided with `bulletSeq`).

**`commanderPresent` metadata [review §9]**: `SnapshotManager.ts:175` records
`commanderPresent` (asserted boolean in `snapshot-framework.test.ts:127`).
Define it as **`world.activeCommanderId !== null`** — "an active Commander is
in charge" — which is the user-facing meaning in the snapshot browser.

**Determinism / RNG:**
- Every roll (tier, compliance, None wander/fire) goes through `world.rng`.
- **Tier-roll gate**: a 100%-None distribution (Classic) consumes **zero** RNG
  for the tier roll — mirror today's `eliteChance > 0` gate at
  `Simulation.ts:212`. (None's own wander/fire draws still consume `world.rng`;
  that is fine — see §3, no backward replay constraint exists.)

---

## 8. Testing

**Files to rewrite [review §9]:**
- `tests/tactical-ai.test.ts` — substantial rewrite: `KIND_TO_LEVEL` import
  (line 22) dies; the "classic never yields a commander" assertion (~line 155)
  stays valid but switches from the `eliteChance` mechanism to the
  distribution; the `survivalRate` test (~line 170) still works (tier on
  `aiState`) — same-kind-two-tiers is now the *normal* case.
- `tests/elite-spawn.test.ts` — the whole file assumes `eliteChance` +
  `isCommander`-via-elite; rewrite it against the tier roll (or fold its
  assertions into the rewritten tactical-ai.test.ts).

**New coverage:**

1. Distribution rows sum to 1; spawn rolls are deterministic per seed.
2. Floor (attempt-based, **[D9-fix]**): seeds engineered to roll zero natural
   Commanders still make ≥ quota Commander **attempts** per stage (quota
   decrements on every Commander *roll*, incl. cap-downgraded ones).
3. Cap: never > 2 Commander-tier **alive** simultaneously; a roll against a
   full cap resolves to actual Veteran tier (no boost) and **still** consumes
   quota (§5.1 [D9-fix]).
4. Succession: kill active Commander → previous-born becomes active, first
   broadcast exactly 1 s after taking office (not 1 s after its own spawn).
5. Compliance: one roll per directive arrival keyed on `directiveSeqCounter`,
   cached; None ignores; higher tiers comply more often across many directives
   (statistical, seeded). New Commander's first broadcast forces a re-roll.
6. None branch: deterministic, never dodges, never obeys, uses `world.rng`;
   **objective floor** (§3) — no freeze over 600 ticks, fires within its
   cooldown+jitter window.
7. Snapshot round-trip of **all** new fields (World-level: `spawnSeqCounter`,
   `activeCommanderId`, `commanderQuotaRemaining`, `directiveSeqCounter`;
   per-tank: `spawnSeq`, `directiveSeq`, `directiveCompliant`) mid-directive /
   mid-succession.
8. `commanderPresent === (activeCommanderId !== null)`
   (`snapshot-framework.test.ts:127` still green; meaning updated).
9. Boost carve-out: a Commander-tier spawn has the +15% profile; a
   cap-downgraded Veteran does **not**; two coexisting Commanders are **both**
   boosted.

---

## 9. Definition of Done

- [ ] Five tiers incl. None classic-branch (early-return `updateNoneTank`),
      behind the single distribution lever.
- [ ] None objective floor met: no freeze over 600 ticks, fires within
      cooldown+jitter; deterministic per seed.
- [ ] `eliteChance` / `KIND_TO_LEVEL` / `levelForKind` / `DIFFICULTY_AI` fully
      removed; `resolveConfig` collapsed to `INTELLIGENCE_LEVELS[level]`.
- [ ] Command authority: `activeCommanderId` recomputed by Simulation once/tick
      before `ai.update`; broadcast predicate is `id === activeCommanderId &&
      commanderTimer <= 0`; succession on death; 1 s office delay from becoming
      active (overwrite, not add).
- [ ] Compliance rolls once per directive keyed on `directiveSeqCounter`,
      cached, all tiers; None deaf.
- [ ] Floor/cap enforcement per §5.1 **[D9-fix]**: cap ≤ 2 alive; quota
      decrements per Commander *roll* (attempt-based), so the floor is always
      satisfiable.
- [ ] Rank insignia rendered for Rookie/Soldier/Veteran (tier param on
      `drawEnemyTank`, hit-overlay pattern); crown-xor-insignia (no stacking);
      Classic visually clean.
- [ ] §5.3 [D10] Commander keeps +15% boost (provisional); carve-out recorded
      in DECISIONS.md. Boost rides the *tier*, not command role: ALL alive
      Commanders boosted (incl. inactive ones); only cap-downgrades-to-Veteran
      (§5.1) receive no boost. Code decides tier first, boosts conditionally.
- [ ] Snapshot serializer updated: `WorldSnapshot` interface + `cloneWorld` +
      `restoreWorld` round-trip the 4 World-level fields;
      `commanderQuotaRemaining` initialized in `World.loadStage`;
      `commanderPresent = activeCommanderId !== null`.
- [ ] `tests/elite-spawn.test.ts` rewritten (or folded); full suite green;
      forward replay determinism verified across Classic + one systemized
      difficulty (no backward-replay constraint — no replay system yet).
