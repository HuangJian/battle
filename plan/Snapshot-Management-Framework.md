# Snapshot Management Framework

## Persistent Timeline & Save Architecture

Version 1.0

---

# 1. Vision

The Snapshot Management Framework provides a unified infrastructure for persistent game state preservation, timeline management, and player-controlled save operations.

Rather than implementing multiple unrelated save mechanisms, every preserved game state is represented as a **Snapshot** governed by configurable retention policies.

Snapshots may be created automatically, manually, or by future gameplay systems, while sharing the same storage model, metadata, and lifecycle.

The framework is designed to support current save/load functionality as well as future features including checkpoints, replay, cloud synchronization, debugging, and branching timelines.

---

# 2. Design Philosophy

## One Snapshot Model

Every preserved state is a Snapshot.

Different save behaviors are implemented through policies rather than different data structures.

---

## Timeline Instead of Files

Players interact with a timeline of game history instead of a collection of save files.

---

## Metadata First

Every Snapshot contains rich metadata to support browsing, filtering, searching and future replay functionality.

---

## Storage Policies Instead of Special Cases

The framework should never contain logic such as:

```
if (autoSave)

if (manualSave)
```

Instead:

```
Snapshot

↓

Retention Policy
```

---

## Extensible

Future systems should be able to create snapshots without modifying the framework.

---

# 3. Snapshot Types

Version 1 supports four snapshot origins.

---

## Stage Start Snapshot

Created automatically whenever a stage begins.

Purpose:

Restore the beginning of a stage.

Retention:

20 snapshots (circular).

---

## Pause Snapshot

Created automatically whenever gameplay is paused.

Purpose:

Resume from recent pauses.

Retention:

20 snapshots (circular).

---

## Auto Snapshot

Created every 30 seconds after entering a stage.

Purpose:

Provide continuous progress recovery.

Retention:

20 snapshots (circular).

---

## Manual Snapshot

Created by player shortcut.

Default shortcut:

```
M
```

Configurable.

Retention:

100 snapshots.

No automatic overwrite.

When full:

Display notification requesting cleanup.

---

# 4. Snapshot Lifecycle

Every Snapshot follows the same lifecycle.

```text
Created

↓

Protected?

↓

Candidate

↓

Expired

↓

Deleted
```

Manual snapshots remain protected unless explicitly deleted.

Automatic snapshots become overwrite candidates according to their retention policy.

---

# 5. Snapshot Architecture

```text
                    Snapshot Manager

                           │

        ┌──────────────────┼──────────────────┐

        ▼                  ▼                  ▼

 Snapshot Store     Metadata Store     Thumbnail Store

                           │

                           ▼

                  Snapshot Browser
```

The Snapshot Manager owns all snapshot creation, loading, deletion, and retention.

---

# 6. Snapshot Object

Each Snapshot contains:

```text
Snapshot

UUID

Parent UUID

Snapshot Type

Created Time

Game Version

Metadata

Thumbnail

World State
```

The framework should use UUIDs instead of numbered save files.

---

# 7. Snapshot Metadata

Every Snapshot records descriptive gameplay information.

Minimum metadata:

* Stage
* Difficulty
* Lives
* Star Level
* Current HP
* Combat Capability Level
* Enemy Remaining
* Commander Presence
* Kill Count
* Score
* Total Play Time
* Snapshot Type
* Creation Time

Future fields may be added without changing the storage format.

---

# 8. Thumbnail Generation

Each Snapshot automatically generates a preview image when created.

The thumbnail is stored together with the Snapshot.

Recommended resolution:

```
320 × 180
```

or another configurable lightweight format.

The Snapshot Browser never renders live gameplay previews.

It simply displays stored thumbnails.

---

# 9. Retention Policies

Every Snapshot Type defines its own policy.

| Type        | Limit | Overwrite |
| ----------- | ----: | --------- |
| Stage Start |    20 | Circular  |
| Pause       |    20 | Circular  |
| Auto        |    20 | Circular  |
| Manual      |   100 | Never     |

Retention behavior belongs to the policy layer.

Not the Snapshot itself.

---

# 10. Snapshot Creation Rules

## Stage Start

Automatically create Snapshot.

---

## Pause

Automatically create Snapshot.

---

## Auto

Create every:

```
30 seconds
```

after entering a stage.

---

## Manual

Shortcut:

```
Shift+S
```

Configurable (rebindable in Controls).

Display success notification.

---

# 11. Failure Recovery

When the player loses:

```
Mission Failed

↓

Pause Simulation

↓

Recovery Menu
```

Display:

```
Continue

Load Latest Snapshot

Replay Stage

Restart Without Loading

Choose Snapshot
```

---

## Load Latest Rule

If the latest snapshot was created less than:

```
15 seconds
```

before failure,

automatically select the previous snapshot.

Reason:

Avoid restoring immediately before unavoidable failure.

The threshold should be configurable.

---

# 12. Snapshot Browser

The Snapshot Browser displays all available snapshots.

Each entry shows:

* thumbnail
* snapshot type
* stage
* lives
* star level
* HP
* score
* kill count
* total play time
* creation time

Hovering a snapshot enlarges the thumbnail.

---

Available actions:

```
Load

Delete
```

Future:

```
Rename

Favorite

Export

Share
```

---

# 13. Control Center

The left sidebar becomes a unified Control Center.

```
Control Center

├── Snapshot Manager

├── Controls

├── Gameplay

└── Reserved
```

Reserved for future modules:

* Themes
* Accessibility
* Mods
* Statistics

The Snapshot Browser is opened from the Snapshot Manager.

---

# 14. Timeline Awareness

Snapshots belong to a timeline.

Every Snapshot stores:

```
Parent Snapshot UUID
```

Version 1 does not expose branching timelines.

However, the data model should already support them.

Future:

```
Replay

Checkpoint

Timeline Browser

Cloud Sync

Debug Rollback
```

will reuse the same structure.

---

# 15. Public API

```ts
interface SnapshotManager {

    createSnapshot(type: SnapshotType): SnapshotID;

    loadSnapshot(id: SnapshotID): boolean;

    deleteSnapshot(id: SnapshotID): void;

    getSnapshots(filter?: SnapshotFilter): Snapshot[];

    generateThumbnail(snapshot: Snapshot): void;

    cleanup(policy: RetentionPolicy): void;

}
```

Gameplay code never manipulates snapshot files directly.

---

# 16. Storage Layout

Suggested logical organization:

```
Snapshots

├── Metadata

├── World State

├── Thumbnail

├── Retention Policy

└── Timeline
```

Physical storage implementation remains internal.

---

# 17. Development Milestones

### Milestone 1

Snapshot Infrastructure

* Snapshot object
* UUID
* Metadata
* Retention policy

---

### Milestone 2

Automatic Snapshots

* Stage Start
* Pause
* Auto (30s)

---

### Milestone 3

Manual Save

* Configurable shortcut
* Save notifications
* Capacity management

---

### Milestone 4

Snapshot Browser

* Metadata list
* Thumbnail preview
* Load/Delete

---

### Milestone 5

Failure Recovery

* Recovery menu
* Latest snapshot fallback
* Stage replay
* Restart without loading

---

### Milestone 6

Control Center

* Sidebar
* Snapshot Manager entry
* Settings placeholders

---

# 18. Testing Strategy

Verify:

* Snapshot creation timing
* Retention policies
* Circular overwrite behavior
* Manual save capacity
* Metadata correctness
* Thumbnail generation
* Recovery correctness
* Load performance
* Snapshot deletion
* Control Center integration

Long-running sessions should demonstrate:

* Stable storage usage
* No orphaned metadata
* Correct policy cleanup
* Constant loading performance

---

# 19. Definition of Done

The Snapshot Management Framework is complete when:

* ✅ All snapshot types use the same Snapshot model.
* ✅ Automatic snapshots follow configurable retention policies.
* ✅ Manual snapshots are never overwritten automatically.
* ✅ The latest snapshot fallback rule prevents immediate pre-failure restores.
* ✅ Every snapshot contains metadata and a thumbnail.
* ✅ The Snapshot Browser supports browsing, loading and deletion.
* ✅ The Control Center provides unified access to snapshot management.
* ✅ Parent UUIDs are recorded for future timeline features.
* ✅ New snapshot types can be added by configuration rather than redesign.
* ✅ The framework is ready for replay, checkpoints and cloud synchronization.

---

# 20. Snapshot Constitution

1. **A Snapshot is the only persistent representation of game state.** Every save, checkpoint or replay frame derives from the same abstraction.

2. **Policies govern retention, not snapshot implementations.** Storage behavior is declarative rather than hard-coded.

3. **Metadata is a first-class citizen.** A snapshot without context is not a useful snapshot.

4. **Timelines are immutable.** Loading a snapshot restores history; it does not rewrite it.

5. **The user manages moments, not files.** The interface should present meaningful gameplay history instead of raw storage details.

6. **Every snapshot is self-describing.** It should contain everything required for identification, restoration and future extension.

7. **The framework anticipates evolution.** Replay, checkpoints, cloud synchronization, debugging and branching timelines should all emerge from the existing snapshot model rather than introducing parallel systems.
