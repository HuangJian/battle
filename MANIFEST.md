# Battle City Web — Manifest

> **A creed, not a spec.**
>
> The plan lives in `product.md`. The decisions live in `DECISIONS.md`.
>
> This file is the why behind both.

---

# 1. North Star

This project exists for one moment.

> Open the browser, play for five minutes, leave with a smile.

Every line of code, every abstraction, every future feature is judged against that moment.

If a change does not serve it, the change does not belong here.

---

# 2. The Dual Mandate

Two goals, equally important, never traded against each other.

* Preserve the fast, simple, satisfying feel of the original.
* Build a foundation the game can grow on for years.

Faithful to the classic. Designed for the future.

The first release is intentionally conservative. It should feel like the Battle City everyone remembers. Every later feature should be an extension, not a replacement.

---

# 3. One Author

The World is the complete runtime state.

Only the Simulation may modify it.

Everything else — input, renderer, audio, presentation, future stats — observes.

```
Input

↓

Simulation → World

↓

Renderer / Audio / UI / Stats
```

This separation is the most important rule in the project. It is what makes every other property possible: determinism, recovery, replay, disposable presentation, multiple game modes sharing one engine.

> Documented exemptions: controller-driven state transitions (`world.state = …`,
> `world.ui.*` — transitions, not entity mutations) and `genId()`'s module-level
> id counter (cross-snapshot uniqueness). Entity gameplay writes stay
> Simulation-only; takeover flows route through Simulation entry points.

---

# 4. No Hidden State

There is no gameplay state outside the World.

Not in a singleton. Not in a module variable. Not in a closure.

If it affects the game, it lives in the World. If it does not affect the game, it does not belong in the World.

This rule is what makes a snapshot sufficient.

---

# 5. Determinism as a Promise

The simulation runs on a fixed timestep.

Randomness flows through one central generator.

Input is recordable.

Same inputs, same RNG state, same World. Always.

This is not an optimization. It is a contract. It is what lets the game rewind time, replay a stage, and one day host multiplayer experiments — all without touching gameplay code.

---

# 6. Data Over Code

Tanks are config. Stages are config. Difficulty is config. Themes are config. Power-ups plug into an item system.

The engine executes. It does not hardcode.

Adding a new tank should mean adding a row, not editing a system. Adding a stage should mean appending a grid, not touching the loader. Adding a theme should mean swapping presentation data, never gameplay.

Where TypeScript is used today for type safety, the data shape is JSON-compatible so it can cross the network tomorrow.

---

# 7. Zero-Asset Discipline

Sprites are drawn, not shipped.

Sound is synthesized, not loaded.

This is not minimalism for its own sake.

It means the bundle stays tiny. It means themes are a color edit, not an art pipeline. It means a new effect is a function, not a file. It means the game is fully self-contained — open the page, and it just plays.

When bitmap assets eventually arrive, they extend the sprite registry. They do not replace it.

---

# 8. Presentation Is Disposable

The visual layer is ephemeral.

Particles, camera shake, animation state, screen flashes — none of it lives in the World. None of it survives a reset.

> Same World state. Better presentation.

When the game rewinds, the presentation is thrown away and rebuilt from the World. When a theme changes, only the colors move. When a new effect is added, no gameplay line is touched.

Presentation serves the game. It never becomes the game.

---

# 9. Recovery, Not Saving

Players never think about saving.

The game records recent history in the background, continuously and silently. When a stage is lost, the player can choose to rewind thirty seconds. Sixty seconds. Or start fresh.

This is not a save system. It is the feeling that failure is not final.

The Recovery System is built as infrastructure. The same machinery that powers rewind will one day power replay, checkpoints, and debugging — with no changes to gameplay.

---

# 10. Simple Beats Clever

Enemy AI is weighted random. Movement snaps to the grid every frame. Base destruction ends the game in one hit.

Each of these could be more sophisticated. None of them need to be.

> Readable in six months is worth more than elegant today.

Complexity is invited only when it noticeably improves the player's five minutes. Otherwise it is rejected.

---

# 11. Extension, Not Replacement

New enemies do not change the AI system. New power-ups do not change the item system. New game modes combine existing rules, stages, and victory conditions — they do not fork the engine.

The first release deliberately leaves room: rule sets, AI strategies, modifiers, theme packages, community content. The architecture anticipates them. The codebase does not implement them.

Room to grow is a feature.

---

# 12. Readable at a Glance

The screen must be legible in a single glance.

Friend and foe must never be confused. Players wear stars. Enemies wear faces. The distinction is instant and visual.

Terrain tiles fill the frame. Textures repeat on a period that divides the cell. There are no decorative borders, no centered insets, no mosaic seams — only running bond, continuous water, unbroken ice.

The look is Modern Retro: clean, colorful, polished, playful. Warm cream backgrounds, rounded corners, soft shadows. Two fonts, no more. Restraint is part of the aesthetic.

---

# 13. The Three Gates

Every future improvement must pass all three.

* It makes the game more enjoyable.
* It keeps the architecture simple.
* It respects the spirit of the original.

Two out of three is not enough.

If a feature adds complexity without noticeably improving the player's experience, it does not belong in this project.

---

# 14. Small on Purpose

This project is not trying to become a game engine.

It is trying to become the most polished implementation of one timeless game.

Small is the goal. Small is the discipline. Small is what lets the game stay enjoyable to maintain, to play, and to return to.

> Open the browser. Play for five minutes. Leave with a smile.
