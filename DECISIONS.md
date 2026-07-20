# Design Decisions

> Key decisions made during MVP development, as requested.

---

## 1. Sprite Rendering: Programmatic Canvas Drawing

**Product spec says:** Assets = PNG + JSON

**Decision:** Draw all sprites programmatically using Canvas 2D primitives (rectangles, arcs, paths). No PNG assets.

**Rationale:**
- Keeps the MVP self-contained — no need to source or create bitmap assets
- Pixel-art style is fully achievable with canvas drawing
- Theme support is trivial: just change colors in the theme config
- Smaller bundle size (37KB vs. potentially MBs of PNGs)
- Future: can add PNG sprite support by extending SpriteFactory without changing game logic

---

## 2. Audio: Web Audio API Synthesis

**Product spec says:** Audio = HTML Audio / Howler.js

**Decision:** Use Web Audio API to synthesize 8-bit style sound effects at runtime. No audio files, no Howler.js dependency.

**Rationale:**
- Zero audio assets to manage
- Procedurally generated sounds (beeps, sweeps, noise) fit the retro aesthetic
- No additional dependency
- Future: can add Howler.js for music streaming without changing the AudioManager interface

---

## 3. Tile System: 26×26 Sub-block Grid

**Decision:** Use a 26×26 grid of 16px sub-blocks. Tanks are 2×2 sub-blocks (32×32px). Playfield = 416×416px.

**Rationale:**
- Matches classic Battle City proportions (13×13 tiles, each subdivided into 2×2)
- Sub-block granularity allows precise brick destruction
- 416×416 is a good internal resolution; CSS scales it up with `image-rendering: pixelated`
- 16px sub-blocks are large enough for clear visuals, small enough for satisfying destruction

---

## 4. Stage Data: TypeScript Config (not JSON)

**Product spec says:** Resources in JSON format (assets/maps/stage01.json)

**Decision:** Stage data is defined in TypeScript config files (`src/config/stages.ts`) for the MVP.

**Rationale:**
- TypeScript provides type safety and IDE autocompletion for stage data
- No async loading needed — stages are bundled at build time
- The data structure is JSON-compatible and can be extracted to `.json` files later
- Architecture supports the transition: just replace the import with a `fetch()` call

---

## 5. Classic Stages: Authentic NES Layouts (35 stages)

**Reference:** `github.com/artF412/BattleCity-HD-Reforged` (core `stageData.ts` + `stages.ts`),
which itself derives the maps from `github.com/FrontHeads/tanchiki` (original Famicom layouts).

**Decision:** Ship the 35 authentic classic stages. Raw numeric level grids live in
`src/config/stageData.ts` (`LEVELS` + `ENEMY_FORCES`); `src/config/stages.ts` holds a
codec that decodes each 13×13 numeric level into the engine's native 26×26 char grid
(one char per 16px sub-block) and builds `STAGES`.

**Rationale / best-choice论证:**
- The authentic data uses a 13×13 grid of *numeric tile codes*, where each code names a
  material **plus which of the four 2×2 sub-cells it fills** (whole / half / quarter brick
  & steel). This reproduces the original's partial brick/steel pieces — the signature look.
- This engine's terrain is already a 26×26 sub-block grid (`GRID=26`, `CELL=16`) and the
  renderer draws each 16px cell independently, so decoding to a 26×26 char grid preserves
  partial fills **losslessly** with no engine changes beyond the loader.
- The base eagle (code `15`) is decoded into the 2×2 sub-blocks at rows 24–25 / cols 12–13,
  which exactly matches `BASE_POS` and the renderer's base-ruins region — so base detection,
  destruction, and rendering all work unchanged.
- Enemy spawn order uses the authentic `ENEMY_FORCES` strings (a=BASIC, b=FAST, c=POWER,
  d=ARMOR), mapped to this engine's `TankKind` values. Bonus-enemy cadence (every 4th) is
  unchanged.
- Keeping the raw numeric data separate from the codec makes the data trivially diffable
  against the reference and easy to extend (append a 13×13 grid + 20-char force string).

---

## 5. Movement Alignment: Perpendicular Axis Snapping

**Decision:** When a tank moves, the perpendicular axis is snapped to the nearest 16px cell boundary every frame.

**Rationale:**
- Ensures tanks can navigate through 1-tile-wide corridors
- Allows turning only at grid intersections (classic Battle City behavior)
- The snap distance is at most 8px, which is imperceptible at normal movement speeds
- Simpler than tracking "last turn position" and aligning on direction change

---

## 6. Enemy AI: Weighted Random Direction

**Decision:** Enemy AI uses a simple weighted random system:
- 30% chance: move toward player
- 20% chance: move toward base
- 50% chance: random direction
- Re-evaluate every 0.5–2.0 seconds

**Rationale:**
- Simple but produces believable, varied behavior
- Enemies naturally navigate toward objectives without complex pathfinding
- Different tank types (fast, power, armor) create gameplay variety through stats, not AI complexity
- Future: AI strategy interface is anticipated — `aiState` can be replaced with different strategies

---

## 7. Game Loop: Fixed Timestep with Accumulator

**Decision:** Use a fixed timestep (1000/60ms) with an accumulator. Max 5 simulation steps per render frame.

**Rationale:**
- Deterministic simulation (required for future replay system)
- Stable physics regardless of frame rate
- 5-step cap prevents spiral-of-death on slow devices
- Product spec requires 60 FPS — fixed timestep ensures consistent gameplay speed

---

## 8. Input: Per-frame Edge Detection

**Decision:** `endFrame()` is called once per render frame (not per simulation tick) to clear "just pressed" state.

**Rationale:**
- Menu navigation and state transitions need per-frame edge detection
- If cleared per-tick, multiple ticks per frame could miss input
- If never cleared (e.g., in menu state), keys would repeat every frame
- Calling once per frame at the end of the loop is the cleanest solution

---

## 9. Base Destruction: All Cells at Once

**Decision:** When a bullet hits any base sub-block, all base sub-blocks are destroyed simultaneously.

**Rationale:**
- In classic Battle City, any hit on the base ends the game
- The base is 2×2 sub-blocks; destroying one would leave a visual gap but the game should end immediately
- Destroying all cells ensures `isBaseDestroyed()` returns true on the next check
- The destroyed base is rendered as ruins for visual feedback

---

## Presentation Upgrade Decisions

### 10. Presentation Adapter: Event-Driven Visual State

**Decision:** The PresentationLayer observes the World (read-only) and consumes GameEvents to build its own visual state. It does not modify the World.

**Rationale:**
- Preserves the Simulation → World → Renderer architecture
- GameEvents already exist for audio; presentation reuses the same stream
- Visual state (particles, animation transitions, camera shake) is ephemeral and belongs in the presentation layer, not the World
- Game.ts passes events to both AudioManager and PresentationLayer

---

### 11. Canvas Resolution: Playfield-Only (416×416) + HTML HUD

**Decision:** Reduce canvas to 416×416 (playfield only). Move HUD and menu to HTML/CSS overlay elements positioned around the canvas.

**Rationale:**
- The simulation only uses the 416×416 playfield; the 96px HUD sidebar was purely visual
- HTML/CSS HUD enables modern typography, smooth animations, and responsive layout
- Separating UI from game canvas is explicitly required by the upgrade plan
- Canvas becomes simpler — no need to handle two rendering zones

---

### 12. DPR-Aware Rendering with Offscreen Buffer

**Decision:** Use an offscreen canvas at logical resolution (416×416), then blit to a DPR-scaled display canvas. This gives crisp pixels on retina displays.

**Rationale:**
- Current canvas is blurry on retina/HiDPI displays
- Offscreen buffer keeps drawing code simple (logical coordinates)
- Display canvas is sized to `416 * DPR` for pixel-perfect output
- CSS scales the display canvas responsively

---

### 13. Animation System: Time-Based with Visual Components

**Decision:** Introduce time-based animation using `performance.now()` elapsed time. Each entity gets a `VisualComponent` that tracks animation state (name, elapsed time, direction). The animation system computes the current frame from elapsed time and FPS config.

**Rationale:**
- Time-based animation is frame-rate independent (required by upgrade plan)
- Visual components decouple visual state from simulation state
- Frame computation: `frame = floor(elapsed / (1000 / fps)) % frameCount`
- Supports smooth transitions between animation states (idle → move → destroy)

---

### 14. Particle System: Pool-Based with Configurable Emitters

**Decision:** Implement a pool-based particle system. Particles are pre-allocated and reused. Emitters are configured via data (position, velocity, lifetime, color, size, gravity).

**Rationale:**
- Pool allocation avoids GC pressure during gameplay
- Configurable emitters allow adding new effects without code changes
- Used for: explosion debris, bullet sparks, dust, ambient effects
- Each particle: position, velocity, life, maxLife, size, color, type

---

### 15. Camera System: Shake + Offset (No Zoom for Now)

**Decision:** Implement a camera with position offset and shake. Zoom is architecture-ready but disabled (scale = 1.0) to avoid gameplay readability issues on small maps.

**Rationale:**
- Screen shake adds impact feedback for explosions and hits
- Position offset enables future camera panning and stage transitions
- Zoom on a 416×416 map would reduce visibility — kept at 1.0 for now
- Camera shake decays exponentially: `shake * pow(0.85, frames)`

---

### 16. Theme System: Extended ThemeColors + CSS Variables

**Decision:** Extend `ThemeColors` with additional fields for UI styling (panel colors, text colors, gradients, shadows). Generate CSS custom properties from the active theme at runtime.

**Rationale:**
- CSS variables allow HTML UI to react to theme changes instantly
- Extended color palette supports modern UI elements (panels, buttons, shadows)
- Themes remain pure data — no rendering code changes needed
- Two initial themes: Classic (enhanced) and Neon

---

### 17. Visual Asset Format: Programmatic Drawing + Metadata Registry

**Decision:** Keep programmatic Canvas 2D drawing but organize it through a metadata-driven sprite registry. Each sprite type has a `VisualDefinition` with animation states, frame counts, and metadata. The `SpriteArtist` reads these definitions to draw.

**Rationale:**
- Maintains the MVP's zero-asset approach (no PNG files to source)
- Metadata registry enables future PNG sprite support — just swap the artist
- Animation definitions are data, not code — easy to add new animations
- `VisualDefinition` format is compatible with the plan's `metadata.json` structure

---

### 18. State Transitions: CSS-Animated Overlays

**Decision:** Game state transitions (menu → playing, stage clear, game over) use CSS-animated HTML overlays with fade/slide effects. No canvas-based transition animations.

**Rationale:**
- CSS transitions are GPU-accelerated and smooth
- HTML overlays can use modern typography and layout
- Keeps canvas focused on game rendering
- Transitions are declarative (CSS classes) not imperative (JS animation)
