# Recovery System

**Automatic State Preservation & Timeline Recovery**

Version 1.0

---

# 1. Objective

The Recovery System continuously preserves recent gameplay state and allows players to recover from failure without interrupting the natural flow of the game.

Unlike traditional save/load systems, recovery is automatic and transparent.

Players never need to think about saving.

The game is always ready to recover.

The initial release supports three recovery actions after failing a stage:

* Recover to **30 Seconds Ago**
* Recover to **60 Seconds Ago**
* Restart Current Stage

The Recovery System is designed as reusable infrastructure for future gameplay features including replay, checkpoints, save/load and debugging.

---

# 2. Design Goals

The Recovery System should satisfy the following goals.

## Automatic

State preservation happens entirely in the background.

No player interaction is required.

---

## Lightweight

Recording gameplay history should have minimal CPU and memory overhead.

Recording must never affect gameplay responsiveness.

---

## Deterministic

Restoring a snapshot must reproduce the exact gameplay state.

Nothing should be regenerated after restoration.

---

## Transparent

Gameplay systems should not know whether recovery exists.

The Recovery System observes the World rather than participating in gameplay.

---

## Extensible

Future save, replay and checkpoint features should naturally reuse the same infrastructure.

---

# 3. Scope

## Included

* automatic state recording
* stage snapshots
* history snapshots
* recovery menu
* stage restart
* snapshot restoration

---

## Excluded

* replay
* manual save
* cloud save
* persistent storage
* cross-session recovery
* timeline scrubbing

---

# 4. Architecture Overview

```text
                    Simulation
                         │
                         ▼
                     World State
                         │
          ┌──────────────┴──────────────┐
          │                             │
          ▼                             ▼
   Presentation                 Recovery System
                                         │
                                         ▼
                                 Snapshot Manager
                                         │
                      ┌──────────────────┴──────────────────┐
                      ▼                                     ▼
             Stage Snapshot                     History Buffer
```

Simulation remains the only owner of gameplay state.

Recovery only records and restores.

It never changes gameplay behavior.

---

# 5. Core Components

The Recovery System consists of four independent modules.

## Snapshot Manager

Creates and restores snapshots.

Responsible for cloning the World.

---

## History Recorder

Automatically records gameplay every second.

Maintains rolling history.

---

## Recovery Controller

Coordinates restoration.

Suspends simulation.

Loads snapshots.

Resumes gameplay.

---

## Recovery UI

Displays recovery options after failure.

Handles player interaction.

---

# 6. Snapshot Hierarchy

The Recovery System manages multiple snapshot categories.

Each category has a different lifecycle.

---

## Stage Snapshot

Represents the beginning of the current stage.

Characteristics

* created once
* immutable
* replaced only when entering a new stage

Purpose

Restart Current Stage.

---

## History Snapshot

Represents recent gameplay.

Characteristics

* created automatically
* one snapshot every second
* stored in circular buffer
* oldest snapshots overwritten

Purpose

Recover gameplay history.

---

## Future Snapshot Types

The architecture should support additional snapshot categories.

Future examples:

* Checkpoint Snapshot
* Manual Save Snapshot
* Auto Save Snapshot

No architectural changes should be required.

---

# 7. Snapshot Lifecycle

## Stage Start

```text
Load Stage

↓

Create Stage Snapshot

↓

Clear History Buffer

↓

Start Recording
```

---

## Gameplay

Every second:

```text
Clone World

↓

Create History Snapshot

↓

Append Buffer
```

---

## Restart Stage

```text
Restore Stage Snapshot

↓

Clear History

↓

Resume Recording
```

---

## Recover History

```text
Pause Simulation

↓

Restore History Snapshot

↓

Resume Simulation
```

---

# 8. History Buffer

## Recording Interval

1 second.

---

## Maximum History

60 snapshots.

---

## Buffer Type

Fixed-size circular buffer.

---

## Storage Policy

When full:

```text
Newest Snapshot

↓

Overwrite Oldest Snapshot
```

Memory usage remains constant.

---

# 9. Snapshot Contents

Every snapshot must completely describe the gameplay state.

Required:

* stage
* tile map
* player
* enemies
* bullets
* items
* score
* lives
* enemy queue
* timers
* random generator state
* entity states

Do not record:

* transient particle effects
* animations
* UI transitions
* menus

Presentation should rebuild itself after restoration.

---

# 10. Recovery Flow

Player loses:

```text
Mission Failed

↓

Pause Simulation

↓

Recovery Overlay
```

Recovery Menu

```
Continue From

30 Seconds Ago

60 Seconds Ago

Restart Stage
```

Selection:

```text
Fade Out

↓

Restore Snapshot

↓

Countdown

3

2

1

↓

Resume
```

---

# 11. Public API

Suggested interface.

```ts
interface RecoverySystem {

    createStageSnapshot(world: World): void;

    recordHistory(world: World): void;

    restoreStage(): boolean;

    restoreHistory(seconds: number): boolean;

    clearHistory(): void;

    reset(): void;

}
```

The internal implementation remains hidden.

---

# 12. World Cloning

Snapshots should exist entirely in memory.

Preferred strategy:

Deep clone the World.

Avoid:

* JSON serialization
* localStorage
* filesystem
* incremental reconstruction

Correctness is the priority.

---

# 13. Integration

Recovery integrates with the following systems.

## Stage System

Creates Stage Snapshot.

Clears history.

---

## Simulation

Provides World.

Never knows snapshots exist.

---

## Presentation

Listens for restoration.

Rebuilds visual state.

---

## Audio

Stops active sounds.

Restarts ambience.

---

## UI

Displays recovery overlay.

Handles recovery selection.

---

# 14. Performance Targets

| Metric             | Target       |
| ------------------ | ------------ |
| Recording Interval | 1 second     |
| History Length     | 60 snapshots |
| Restore Time       | <100 ms      |
| Frame Impact       | None         |
| Memory Growth      | Constant     |

Recording should never introduce visible frame drops.

---

# 15. Coding Guidelines

## Must

* keep Simulation independent
* keep Presentation independent
* keep fixed memory usage
* restore atomically
* deep clone World
* document snapshot structure

---

## Must Not

* modify gameplay logic
* serialize to JSON
* use localStorage
* record every frame
* couple Recovery with Renderer

---

# 16. Future Evolution

The Recovery System should become shared infrastructure.

Potential future consumers:

## Replay

Playback recorded gameplay.

---

## Manual Save

Save snapshots permanently.

---

## Checkpoints

Create snapshots during gameplay.

---

## Debug Rollback

Restore previous states while debugging.

---

## Ghost Runs

Replay previous attempts alongside current gameplay.

---

## Instant Replay

Replay the final moments before victory or defeat.

---

## Time Scrubber

Developer timeline navigation.

---

# 17. Development Milestones

## Milestone 1

### Snapshot Infrastructure

Implement:

* World cloning
* Snapshot object
* Snapshot restoration

Acceptance:

World restores exactly.

---

## Milestone 2

### Recovery Infrastructure

Implement:

* Snapshot Manager
* Stage Snapshot
* History Recorder
* Circular Buffer

Acceptance:

History records automatically for 60 seconds.

---

## Milestone 3

### Recovery Gameplay

Implement:

* 30-second recovery
* 60-second recovery
* Restart Stage

Acceptance:

All three recovery options work correctly.

---

## Milestone 4

### Recovery Experience

Implement:

* Recovery Overlay
* Fade animation
* Countdown
* Keyboard/controller navigation

Acceptance:

Recovery feels seamless and polished.

---

# 18. Testing Strategy

Verify restoration of:

* player position
* enemy positions
* bullets
* terrain destruction
* items
* score
* lives
* timers
* enemy queue
* random generator state

Long-running sessions should demonstrate:

* stable memory usage
* constant recording interval
* no frame rate degradation

---

# 19. Definition of Done

The Recovery System is complete when:

* ✅ A Stage Snapshot is created whenever a stage begins.
* ✅ A History Snapshot is recorded automatically every second.
* ✅ The system continuously maintains the latest 60 seconds of gameplay.
* ✅ Restart Stage restores the Stage Snapshot.
* ✅ Recover 30 Seconds Ago restores the correct History Snapshot.
* ✅ Recover 60 Seconds Ago restores the oldest available History Snapshot.
* ✅ Restoration is atomic and deterministic.
* ✅ Gameplay continues naturally after recovery.
* ✅ Memory usage remains constant.
* ✅ Rendering and gameplay remain fully decoupled.
* ✅ The architecture is ready to support replay, checkpoints and save/load without redesign.

# 20. Architectural Constraints

The Coding Agent should treat the following rules as non-negotiable:

1. **Recovery owns snapshots; Simulation owns gameplay.** Snapshot creation and restoration must never leak gameplay rules into the Recovery layer.

2. **A snapshot is a complete description of the World.** Never reconstruct missing state during restoration.

3. **History recording must be invisible.** Recording should not pause the game or allocate unbounded memory.

4. **Presentation is disposable.** Visual effects, particles, animations and UI transitions must be rebuilt after restoration instead of being serialized.

5. **New recovery features extend existing abstractions.** Future additions—such as checkpoints, manual saves, replay or debugging—must reuse the Snapshot Manager rather than introducing parallel save mechanisms.
