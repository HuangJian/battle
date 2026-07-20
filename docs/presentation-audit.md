# Presentation Audit

## Existing Rendering

### Sprite Loading Method
- **Programmatic Canvas 2D drawing** — all sprites are drawn at runtime using `ctx.fillRect`, `ctx.arc`, `ctx.beginPath`, etc.
- No external image assets (PNG, SVG, etc.)
- `SpriteFactory` class contains all drawing logic
- Theme colors are passed to `SpriteFactory` and used for all sprite colors

### Rendering Pipeline
```
Game.loop()
  → Simulation.tick() (fixed timestep)
  → World.consumeEvents() → AudioManager
  → Renderer.render(world)
    → clear canvas
    → if menu: renderMenu()
    → else:
      → renderTerrain (non-forest)
      → renderTanks
      → renderBullets
      → renderPowerUps
      → renderTerrain (forest overlay)
      → renderExplosions
      → renderPopups
      → renderHUD (canvas-drawn sidebar)
      → renderOverlays (pause, gameover, etc.)
```

### Coordinate System
- Origin: top-left
- Playfield: 416×416px (26×26 grid of 16px sub-blocks)
- HUD sidebar: 96px wide, right side of canvas
- Total canvas: 512×416px
- No camera transformation — world coordinates = screen coordinates

### Scaling Strategy
- Canvas internal resolution: 512×416
- CSS scales canvas: `height: min(90vh, 832px)`, `max-width: 95vw`
- `image-rendering: pixelated` for crisp pixel scaling
- No device pixel ratio (DPR) handling — canvas is not retina-aware

### Animation Handling
- **Frame-based** — uses `world.frame` counter (incremented per simulation tick)
- Tank tread animation: `(frame >> 2) & 1` — toggles every 4 frames
- Water animation: `Math.floor(frame / 30) % 2` — toggles every 30 frames
- Spawn animation: `Math.floor(frame / 4) % 4` — 4-phase cycle
- Shield: `Math.floor(frame / 3) % 2` — blink every 3 frames
- Power-up: `Math.floor(frame / 8) % 2` — blink every 8 frames
- No interpolation between simulation steps
- No transition animations

### UI Implementation
- **All UI drawn on canvas** — menu, HUD, overlays (pause/gameover/victory)
- Monospace font, basic text rendering
- No HTML/CSS UI elements
- No interactive buttons (keyboard only)

## Existing Assets

### Sprites (Programmatic)
- Tanks: player (3 levels), 4 enemy types
- Terrain: brick, steel, water, forest, ice, base
- Bullets, power-ups (6 types), explosions (small/big)
- Spawn animation, shield effect

### Audio (Synthesized)
- Web Audio API procedural synthesis
- Shoot, explosion, brick, steel, power-up, player hit, game over, stage clear, menu select, pause

### Assets Classification
| Asset | Status | Notes |
|-------|--------|-------|
| Tank sprites | Reusable (enhance) | Programmatic, upgrade visual quality |
| Terrain sprites | Reusable (enhance) | Add depth, texture variation |
| Bullet sprite | Reusable (enhance) | Add trail/glow |
| Explosion sprite | Replace | Multi-stage particle-based |
| Power-up sprites | Reusable (enhance) | Add glow, better icons |
| HUD | Replace | Move to HTML/CSS |
| Menu | Replace | Move to HTML/CSS |
| Audio | Reusable | Already good, no changes needed |

## Rendering Limitations

### Current
- Fixed pixel sprites (programmatic but basic)
- No animation system (frame-based only)
- No particles
- No camera effects (no shake, zoom, pan)
- Simple HUD drawn on canvas
- No interpolation between ticks (movement is stepped)
- No DPR handling (blurry on retina)
- No state transitions
- No visual feedback for hits (beyond explosion sprite)
- UI and game logic mixed in single canvas

### Target
- Layered rendering with depth
- Time-based smooth animation
- Modern HTML/CSS UI (overlay)
- Effects pipeline (particles, screen shake, flash)
- Camera abstraction
- DPR-aware crisp rendering
- Smooth state transitions
- Theme system with visual variety
