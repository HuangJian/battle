# Performance Observatory

**A thin observability milestone — not a framework.**

Version 1.0 (trimmed from `Adaptive-Performance-Framework.md` v1.0)

---

# 1. Why this exists, and why it is small

The MANIFEST (§14) is explicit: *"This project is not trying to become a game
engine. It is trying to become the most polished implementation of one timeless
game. Small is the goal."*

The earlier `Adaptive-Performance-Framework.md` proposed a 6-component framework
(Performance Monitor, Budget Manager, Quality Controller, Rendering Optimizer,
Performance Overlay, future Performance CI), 5 quality levels, and a
`PerformanceManager` public API. That scope is an engine layer the MANIFEST
forbids, and it solves a problem the codebase does not have: the headless sim
bench (`tools/perf/sim-bench.ts`) measures the simulation tick at **<0.3 ms
median, p95 <0.5 ms** at normal scale — under 1% of the 16.67 ms frame budget.

This document keeps the philosophy and the constitution of the original plan,
and ships only the one piece that is genuinely missing: **a developer overlay
that makes every millisecond visible.** Everything else is deferred until
measurement on real hardware shows a real problem.

> Measure before optimizing. (Original plan §2; MANIFEST §10 "Simple beats clever".)

---

# 2. What already exists (do not rebuild)

An agent executing this plan must build on, not parallel, the existing
infrastructure:

| Capability | Existing artifact |
|---|---|
| Headless sim budget gate (6 ms p95, CI-exitable) | `tools/perf/sim-bench.ts` |
| In-browser frame timing (rAF wrap, p50/p95/p99, longtasks) | `src/perf/browser-harness.ts` |
| Rolling FPS + slow-frame counter | `Game.fps`, `Game._slowSeconds` |
| Render-FPS cap + DPR cap (Performance Mode toggle) | `Game.renderFpsCap`, `PresentationLayer.dpr`, `PERF_MODE_RENDER_FPS` |
| Sprite cache (pre-rasterized SVG → canvas bitmaps) | `src/presentation/renderer/SpriteCache.ts` |
| Static-layer offscreen caches (terrain / forest / vignette) | `GameRenderer` (`terrainCache`, `forestCache`, `vignetteCanvas`) |
| Incremental terrain redraw (dirty cells, not full rebuild) | `TileMap.dirtyCells`, DECISIONS §22 |
| OffscreenCanvas helper | `src/utils/canvas.ts` `createOffscreenCanvas` |
| Per-frame allocation elimination | DECISIONS §21, §24 |
| Performance report (baseline numbers) | `docs/performance-report.md` |

The "Rendering Optimizer" milestones of the original plan (sprite cache,
offscreen rendering, layered rendering, dirty regions) are **already
implemented**. The terrain cache *is* the dirty-region optimization, done
correctly: a single `drawImage` blit for the static layer, with incremental
in-place redraws for changed cells. A second dirty-rect system on top would be
complexity without benefit (violates Gate 2).

---

# 3. Scope — the one deliverable

## Performance Observatory (F6 overlay)

A developer-only, read-only debug HUD that surfaces what the engine is already
computing, plus a few cheap additional counters. **Not a gameplay element.**
**Not drawn on the game canvas** (AGENTS §2.5 — UI is HTML/CSS).

### Hotkey

`F6` toggles the overlay on/off. State is session-only (not persisted).

### Layout

A compact, fixed-position HTML panel (top-left of the viewport, above the
playfield), monospace, semi-transparent dark background so it never obscures
gameplay. Two columns of key/value pairs:

```
FPS            60          Frame     16.4 ms
Sim            0.3 ms      Render    8.1 ms
UI             0.2 ms      Idle      7.8 ms
Draw calls     42          Sprites   28
Bullets        6           Tanks     4
Particles      12          GC        0
Quality        High        PerfMode  OFF
```

### Metrics

Only metrics that are **cheap to collect** and **already meaningful** are
included. No new instrumentation infrastructure is built to feed the overlay;
where a number is not currently tracked, it is either derived from existing
state or omitted.

**Timing** (per frame, rolling p95 over ~120 frames):
- `FPS` — from `Game.fps` (already computed once/sec; overlay shows the live value).
- `Frame` — total frame cost, from the `browser-harness.ts` rAF wrap pattern (a thin `performance.now()` delta around the loop body). This is the one new timing probe.
- `Sim` — `Simulation.updatePlaying()` wall time. Wrap with `performance.now()` in `Game.loop()` only while the overlay is active (zero cost when off).
- `Render` — `PresentationLayer.render()` wall time, same gated wrap.
- `UI` — `UIManager` update wall time, same gated wrap.
- `Idle` — `Frame − Sim − Render − UI` (derived; clamped at 0).

**Counts** (read from World / renderer, no new tracking):
- `Draw calls` — count of `drawImage`/`fill` calls in `GameRenderer` per frame. Incremented via a debug counter on the renderer, reset each frame, only when overlay is on.
- `Sprites` — `world.tanks.length + world.bullets.length + particles.activeCount`.
- `Bullets` — `world.bullets.filter(b => b.alive).length`.
- `Tanks` — `world.tanks.filter(t => t.alive).length`.
- `Particles` — `ParticleSystem.activeCount` (add a cheap `get activeCount()` if not present).

**State**:
- `Quality` — always `High` for now (see §5 — no adaptive quality yet).
- `PerfMode` — `ON`/`OFF` from `settings.performanceMode`.
- `GC` — `PerformanceObserver` `gc` entry count since last reset (best-effort; hidden if unsupported).

### Implementation notes

- Lives in `src/presentation/ui/` (a new `PerfOverlay.ts`), wired by `UIManager`. Pure presentation: reads World + renderer counters, never mutates.
- All timing probes are **gated behind `overlay.active`** so the overlay has zero cost when off. This is the "overlay introduces negligible overhead" test from the original plan §17.
- No new classes for gameplay state. `PerfOverlay` is a small object with `toggle()`, `update(world, renderer, timings)`, and `render()` — matching AGENTS §5 ("no classes where a function suffices" applies to *gameplay state*; a UI controller object is fine).
- No new runtime dependency. No new build tool.

---

# 4. Acceptance criteria (Definition of Done)

- [ ] `F6` toggles a compact HTML overlay; it does not appear on the game canvas.
- [ ] Overlay shows FPS, Frame, Sim, Render, UI, Idle, Draw calls, Sprites, Bullets, Tanks, Particles, PerfMode.
- [ ] With the overlay **off**, `bun run check` is green and there is no measurable frame-time regression vs. baseline (verify with `perf.html`).
- [ ] With the overlay **on** on the stress scenario (`perf.html` → Stress), p95 frame time stays < 16.67 ms (the overlay must not itself cause slow frames).
- [ ] `bun run check` green; `bun run build` succeeds.
- [ ] No new `Math.random()` in the overlay (it is pure presentation, but there is no reason for any randomness).
- [ ] No new module-level mutable gameplay state (AGENTS §2.2).
- [ ] Numbers match `browser-harness.ts` within noise (cross-check during dev).
- [ ] A note is appended to `docs/performance-report.md` pointing at the overlay as the live-debug path.

---

# 5. Explicitly deferred (to be recorded in DECISIONS.md)

The following from the original plan are **not** built now. They are deferred
until the Observatory, run on the slowest target hardware (integrated GPU,
old laptop), shows a sustained frame-time problem that they would actually
fix. Deferring without measurement would violate "measure before optimizing."

- **Adaptive quality levels (Ultra/High/Medium/Low/Retro)** — original §10.
- **Automatic quality adjustment with hysteresis** — original §11. The 18 ms / 12 ms thresholds were specified without measurement; they will be set from real `browser-harness.ts` data if/when needed.
- **`PerformanceManager` public API** (`beginFrame/endFrame/record/getMetrics/getQualityLevel/setQuality`) — original §15. Wrong shape for this codebase (AGENTS §5: no classes for state that a function can hold). The overlay uses a few gated `performance.now()` calls instead.
- **Performance CI** (original §17 future note) — `sim-bench.ts` already exits non-zero on budget breach; a render-side CI gate is meaningful only after the Observatory defines what to gate on.
- **WebGL / WebGPU / split-screen / tower-defense** (original §14) — out of scope for this project entirely (MANIFEST §14).

The existing `performanceMode` toggle (DPR cap + render-FPS cap) remains the
only quality control. It is a manual, binary switch — which is the right
amount of complexity until measurement says otherwise.

---

# 6. Constitution (carried from the original plan §19, unchanged)

1. **Never optimize blindly.** Every optimization begins with measurement and ends with validation.
2. **Protect simulation first.** Gameplay correctness and responsiveness always take precedence over visual effects.
3. **Budgets create discipline.** Every subsystem must know its performance budget and remain accountable to it.
4. **Adapt presentation, not behavior.** Lower-quality rendering must never alter game rules or AI decisions.
5. **Cache computation, not complexity.** Expensive work should be performed once and reused whenever possible.
6. **Policies over special cases.** Rendering optimizations should be selectable strategies rather than scattered conditional logic.
7. **Performance is a feature.** Efficient resource usage, low thermal output, and long battery life are first-class product goals.

These seven rules govern any future work that the Observatory's data might
justify. They are the durable part of the original plan; the framework scaffolding
was not.

---

# 7. The Three Gates check

- **Gate 1 (enjoyable):** The overlay is a dev tool; it does not touch the player's five minutes. It enables *keeping* the game enjoyable by catching regressions early. Pass.
- **Gate 2 (architecture simple):** One small HTML panel, a few gated timing probes, no new framework, no new state in the World. Pass.
- **Gate 3 (spirit of the original):** A debug overlay does not alter the classic feel. Pass.

3/3. The original plan, by contrast, failed Gate 2 at its proposed scope.
