# Battle City Web

> **A modern, customizable Battle City for the browser.**
>
> Faithful to the classic. Designed for the future.

---

# 1. Vision

Battle City Web is a modern web implementation of the classic Battle City.

The project has two equally important goals:

* Preserve the fast, simple and satisfying gameplay of the original.
* Build a clean foundation that allows the game to evolve naturally over time.

The first release is intentionally conservative.

It should feel like the Battle City everyone remembers.

Every future feature should be an extension rather than a replacement.

This project is ultimately designed for one purpose:

> Open the browser, play for five minutes, leave with a smile.

---

# 2. Design Goals

The project should always prioritize the following goals, in order.

### 1. Fun

Gameplay always comes first.

Everything else exists to support the gameplay.

---

### 2. Simplicity

Avoid unnecessary abstraction.

Choose solutions that remain understandable six months later.

---

### 3. Extensibility

Adding a new tank, item or game mode should primarily involve adding new modules or configuration rather than modifying existing systems.

---

### 4. Maintainability

The project should remain enjoyable to maintain.

Readable code is preferred over clever code.

---

### 5. Performance

Stable 60 FPS on modern browsers.

Performance optimization happens only after correctness.

---

# 3. Scope

## Phase One

Faithfully reproduce the classic Battle City experience.

Deliver a polished game that people actually enjoy playing.

---

## Not Included

The first release intentionally excludes:

* Multiplayer
* Online services
* Cloud save
* Complex map editor
* Boss battles
* Procedural generation
* Tower Defense
* Extensive modding

These are future capabilities, not MVP requirements.

---

# 4. Technical Stack

| Area      | Choice                 |
| --------- | ---------------------- |
| Language  | TypeScript             |
| all-in-on tool  | Bun             |
| Build     | Vite                   |
| Rendering | Canvas 2D              |
| Styling   | CSS Variables          |
| Storage   | localStorage           |
| Assets    | PNG + JSON             |
| Audio     | HTML Audio / Howler.js |

The technology should remain lightweight.

No heavy game framework is required.

---

# 5. Architecture

The project is organized around responsibilities.

```
Input

↓

Simulation

↓

World

↓

Renderer

↓

UI
```

Only the Simulation layer is allowed to modify the World.

Everything else observes the World.

This separation is the most important architectural rule in the project.

---

# 6. World Model

The World represents the complete runtime state.

It contains:

* Tile Map
* Entities
* Stage
* Timers
* Random Generator
* Events
* Game State

There should be no hidden gameplay state outside the World.

---

# 7. Core Systems

The engine consists of small, focused systems.

Initial systems include:

* Input System
* Movement System
* Collision System
* Bullet System
* Enemy AI System
* Spawn System
* Item System
* Animation System
* Audio System
* Render System

Each system has one responsibility.

---

# 8. Gameplay

## Player

* Move
* Shoot
* Lives
* Upgrade

---

## Enemy

Phase One includes four classic enemy types.

* Basic
* Fast
* Power
* Armor

Future enemies should be added without changing existing systems.

---

## Terrain

Classic terrain:

* Empty
* Brick
* Steel
* Water
* Forest
* Base

Terrain behavior should come from configuration rather than hardcoded logic.

---

## Power-ups

Initial power-ups:

* Star
* Bomb
* Shield
* Freeze

Future power-ups should plug into the same item system.

---

# 9. User Experience

The game should start immediately.

Target flow:

```
Open Page

↓

Press Enter

↓

Play
```

Minimal interruption.

Minimal waiting.

Minimal configuration.

---

### Controls

Desktop

* WASD / Arrow Keys
* Space
* P
* R

Mobile

* Virtual Joystick
* Fire Button
* Pause Button

---

# 10. Customization

Customization is a first-class feature.

Players should be able to personalize the game without changing gameplay.

---

## Difficulty

Difficulty is configuration-driven.

Initial presets:

* Relax
* Classic
* Hard
* Chaos

Future presets can be added without code changes.

---

## Theme

Themes replace presentation only.

They may customize:

* Sprites
* Colors
* Fonts
* HUD
* Effects

Gameplay remains identical.

---

## Settings

Persist locally.

Examples:

* Volume
* Key bindings
* Difficulty
* Theme
* Screen scale

---

# 11. Resource System

All gameplay content should come from external resources whenever practical.

Examples:

```
assets/

maps/

stage01.json

configs/

tanks.json

items.json

themes/

classic/

neon/

desert/
```

Adding content should not require modifying engine code.

---

# 12. Development Milestones

## Milestone 1

Core Gameplay

* Player
* Tank
* Bullet
* Collision
* Terrain

Deliverable:

A playable sandbox.

---

## Milestone 2

Complete Classic Mode

* Enemy AI
* Power-ups
* Stage progression
* Score
* Lives
* Game Over

Deliverable:

A complete Battle City experience.

---

## Milestone 3

Polish

* Audio
* Animation
* UI
* Menu
* Settings
* Save

Deliverable:

A game ready for daily play.

---

# 13. Definition of Done

A feature is complete only if:

* Works correctly
* Has no TypeScript errors
* Produces no runtime errors
* Maintains 60 FPS
* Integrates with existing systems
* Can be restarted safely
* Does not introduce hidden state

---

# 14. Future Evolution

Phase One should deliberately leave room for future expansion.

The following capabilities should be anticipated by today's architecture, even if they are not implemented yet.

## Rule System

Gameplay rules should be organized so that different game styles can coexist.

Examples:

* Classic
* Modern
* Hardcore
* Endless
* Tower Defense

The engine executes rules.

Game modes define rules.

---

## AI Strategies

Enemy behavior should be replaceable.

Possible future strategies:

* Classic AI
* Aggressive AI
* Defensive AI
* Boss AI
* Cooperative AI

Enemy entities should not need to change.

---

## Game Modes

The architecture should support multiple modes sharing the same engine.

Potential modes:

* Classic
* Relax
* Endless
* Survival
* Boss Rush
* Tower Defense

Each mode combines:

* Rules
* Stage generation
* Enemy composition
* Victory conditions

---

## Theme Packages

Themes should evolve independently from gameplay.

Future themes may include:

* Classic
* Neon
* Desert
* Winter
* Military
* Cyberpunk

Changing themes should only replace presentation assets.

---

## Gameplay Modifiers

Temporary gameplay variations should be composable.

Examples:

* Double Bullets
* Ricochet
* Fast Enemies
* Fog
* Infinite Ammo
* Iron Walls

Modifiers adjust existing rules instead of introducing special-case logic.

---

## Statistics

Gameplay events should naturally support future statistics.

Possible metrics:

* Total Games
* Enemies Destroyed
* Accuracy
* Longest Survival
* Favorite Difficulty

The statistics system should consume gameplay events without modifying gameplay logic.

---

## Replay

Although replay is not implemented initially, the architecture should make it straightforward.

Requirements:

* Centralized random generator
* Deterministic simulation
* Recordable input stream

This enables replay, debugging, and future multiplayer experiments.

---

## Community Content

The resource pipeline should eventually support user-created content.

Possible extensions include:

* Custom maps
* Theme packs
* Challenge presets
* Community campaigns

The initial JSON-based resource structure should be designed with this evolution in mind.

---

# 15. Capability Matrix

| Capability        | Phase One          | Future Evolution         |
| ----------------- | ------------------ | ------------------------ |
| Classic Gameplay  | ✅ Complete         | Continue polishing       |
| Difficulty        | 4 presets          | Fully configurable       |
| Theme             | Classic            | Theme packages           |
| Enemy AI          | Classic            | Strategy-based AI        |
| Rules             | Classic rules      | Multiple Rule Sets       |
| Maps              | JSON stages        | Procedural generation    |
| Statistics        | Event-ready        | Dashboard & History      |
| Replay            | Architecture ready | Full replay system       |
| Modifiers         | Framework ready    | Challenge modes          |
| Game Modes        | Classic            | Endless / TD / Boss Rush |
| Community Content | Resource-ready     | Mods & custom campaigns  |

---

# 16. Long-Term Direction

This project is intentionally small.

It is not trying to become a full-featured game engine.

Instead, it aims to become an exceptionally polished implementation of one timeless game.

Every future improvement should satisfy three conditions:

* It makes the game more enjoyable.
* It keeps the architecture simple.
* It respects the spirit of the original.

If a feature increases complexity without noticeably improving the player experience, it does not belong in this project.
