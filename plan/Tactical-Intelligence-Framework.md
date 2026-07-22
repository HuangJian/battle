# Tactical Intelligence Framework

Rule-Based Battlefield Intelligence

Version 1.0

---

# 1. Vision

The Tactical Intelligence Framework replaces the simplistic enemy behavior found in the original Battle City with a modern, extensible decision-making system.

Difficulty should primarily arise from **better decisions**, not stronger enemy statistics.

Enemy tanks become more dangerous because they understand the battlefield more effectively, cooperate more intelligently, and react more appropriately.

Hardware remains constant.

Intelligence evolves.

---

# 2. Design Philosophy

The AI should never feel unfair.

It should appear:

* observant
* cautious
* purposeful
* cooperative

It should not appear:

* omniscient
* robotic
* perfectly optimal
* impossible to outplay

Players should feel they lost because the enemy made a better decision—not because it cheated.

---

# 3. Design Goals

The framework should satisfy five principles.

### Better Thinking

Difficulty scales through intelligence.

Not statistics.

---

### Predictable Architecture

Every decision follows the same pipeline.

---

### Configurable

Changing AI level should primarily change configuration.

Not implementation.

---

### Extensible

New behaviors should be added without rewriting existing systems.

---

### Deterministic

Given the same World State and random seed, AI should produce identical decisions.

---

# 4. High-Level Architecture

```
                    World
                      │
                      ▼
                Perception
                      │
                      ▼
             Situation Analysis
                      │
                      ▼
              Goal Evaluation
                      │
                      ▼
                 Decision
                      │
                      ▼
               Action Planner
                      │
                      ▼
                 Execution
```

Each stage has a single responsibility.

---

# 5. Framework Components

## Perception

Collect observable battlefield information.

The AI never reads gameplay objects directly.

Instead it builds an observation snapshot.

Examples:

* visible enemies
* visible bullets
* visible walls
* nearby teammates
* nearby base

Future extensions:

* Fog of War
* radar
* sound
* night vision

---

## Situation Analysis

Convert observations into tactical knowledge.

Examples:

* bullet threat
* escape routes
* attack opportunities
* blocked paths
* nearby cover
* base danger

This stage contains no decisions.

Only analysis.

---

## Goal Evaluation

Generate candidate goals.

Examples:

* attack player
* attack base
* retreat
* destroy wall
* evade bullets
* regroup

Each goal receives a dynamic score.

---

## Decision

Choose the highest-value goal.

The chosen goal becomes the current tactical objective.

---

## Action Planner

Translate goals into executable actions.

Examples:

```
Move Left

Shoot

Wait

Rotate

Advance
```

---

## Execution

Interact with the Simulation.

Execution never reasons.

It only performs actions.

---

# 6. Thinking Hierarchy

Enemy thinking occurs at multiple time scales.

---

## Strategic Thinking

Default interval:

20 seconds

Purpose:

Long-term battlefield planning.

Examples:

* attack base
* defend base
* split attack
* regroup

---

## Tactical Thinking

Default interval:

5 seconds

Purpose:

Local decision updates.

Examples:

* choose route
* avoid bullets
* destroy wall
* engage player

---

## Reactive Layer

Runs every simulation update.

No reasoning.

Immediate reactions only.

Examples:

* continue moving
* continue rotating
* continue firing
* complete planned action

This keeps CPU usage extremely low while preserving responsiveness.

---

# 7. Intelligence Hierarchy

Every AI runs exactly the same framework.

Differences are entirely configuration-driven.

---

## Rookie

Characteristics

* short planning horizon
* weak bullet prediction
* low dodge probability
* weak strategic awareness
* no teamwork

---

## Soldier

Improves

* better routing
* basic dodging
* stronger targeting

---

## Veteran

Improves

* advanced bullet prediction
* stronger battlefield evaluation
* effective base attacks
* adaptive goal weighting

---

## Commander

Full capability

* strategic planning
* advanced dodge
* dynamic priorities
* team coordination

No new code paths are introduced.

Only stronger configuration.

---

# 8. Commander System

Most enemies behave independently.

Occasionally, one enemy becomes the battlefield commander.

Commander probability depends on difficulty.

Only one commander exists at a time.

Every 20 seconds:

Commander evaluates:

* overall battlefield
* player pressure
* base safety
* teammate distribution

Then broadcasts tactical directives.

Examples:

* push left
* defend base
* attack together
* spread out

Enemy tanks remain autonomous.

They may follow or ignore suggestions according to their own intelligence.

The Commander influences.

It never controls.

---

# 9. Tactical Evaluation

Every tactical decision is based on weighted evaluation.

Example:

```
Attack Base

score =

distance

+

success probability

-

bullet threat

-

wall cost
```

Another:

```
Attack Player

score =

distance

+

kill opportunity

+

line of sight

-

risk
```

Dynamic evaluation replaces hardcoded priority lists.

---

# 10. Cost Evaluation

Routes are evaluated using total tactical cost.

Possible costs:

* travel distance
* exposed area
* bullets
* walls
* dead ends
* congestion

The AI prefers the route with the lowest overall cost.

Not necessarily the shortest.

---

# 11. Bullet Avoidance

Difficulty primarily changes decision quality.

Higher intelligence:

* predicts trajectories
* estimates collision time
* evaluates multiple escape routes
* preserves strategic objective while dodging

Lower intelligence:

* reacts later
* evaluates fewer options
* occasionally ignores danger

---

# 12. Goal Stability

Strategic goals should remain stable.

Tactical actions may change frequently.

Example:

```
Strategic Goal

↓

Attack Base

↓

Bullet Incoming

↓

Temporary Dodge

↓

Resume Base Attack
```

Short-term danger never invalidates long-term objectives.

---

# 13. Imperfection Model

Perfect AI is not desirable.

Every important decision includes configurable uncertainty.

Examples:

* delayed reaction
* failed dodge
* imperfect route choice
* occasional targeting mistakes

Higher levels make fewer mistakes.

They never become flawless.

---

# 14. Team Cooperation

The first version supports lightweight cooperation.

Examples:

* avoid congestion
* avoid identical routes
* spread attack directions
* respond to commander directives

No centralized control.

Every tank remains autonomous.

---

# 15. Event System

The framework reacts to battlefield events.

Examples:

* PlayerSpotted
* BulletDetected
* BaseThreatened
* WallDestroyed
* EnemyDestroyed
* PowerUpAppeared
* CommanderDirective

Events may trigger early tactical re-evaluation without waiting for the next scheduled thinking cycle.

---

# 16. AI Configuration

Every intelligence level is defined through configuration rather than code.

Example:

```yaml
Rookie:
  strategicThinking: false
  teamwork: false
  dodgeProbability: 0.20
  predictionDepth: 1
  routeLookAhead: 2

Soldier:
  strategicThinking: true
  teamwork: false
  dodgeProbability: 0.45
  predictionDepth: 2
  routeLookAhead: 4

Veteran:
  strategicThinking: true
  teamwork: true
  dodgeProbability: 0.75
  predictionDepth: 4
  routeLookAhead: 6

Commander:
  strategicThinking: true
  teamwork: true
  commander: true
  dodgeProbability: 0.90
  predictionDepth: 8
  routeLookAhead: 10
```

Future difficulty levels require only new configuration files.

---

# 17. Development Milestones

### Milestone 1

Framework Infrastructure

* Thinking pipeline
* Tactical evaluation
* Configuration system

---

### Milestone 2

Individual Intelligence

* movement
* targeting
* routing
* bullet avoidance

---

### Milestone 3

Strategic Layer

* strategic goals
* goal stability
* dynamic priorities

---

### Milestone 4

Commander System

* commander election
* tactical directives
* lightweight cooperation

---

### Milestone 5

Polish

* imperfection model
* balancing
* profiling
* tuning

---

# 18. Testing Strategy

Verify:

* identical inputs produce identical decisions
* AI never stalls
* strategic goals remain stable
* tanks avoid deadlocks
* commander directives improve coordination
* lower levels exhibit believable mistakes
* CPU cost remains bounded regardless of enemy count

---

# 19. Definition of Done

The Tactical Intelligence Framework is complete when:

* ✅ All enemies use the same decision pipeline.
* ✅ Intelligence differences are configuration-driven.
* ✅ Strategic and tactical thinking are separated.
* ✅ Commander directives coordinate, but never override, autonomous tanks.
* ✅ Goal evaluation uses dynamic scoring instead of fixed priority lists.
* ✅ Bullet avoidance improves with intelligence level.
* ✅ AI exhibits configurable imperfection.
* ✅ The framework remains deterministic under identical inputs.
* ✅ New intelligence levels can be added without modifying the engine.
* ✅ Future behaviors (Bosses, Tower Defense, Replay, Mods) can be built on the same framework.

# 20. The AI Constitution

Every AI implementation in this project should obey the following principles:

1. **Perceive before deciding.** Decisions are always based on perceived information, never on privileged access to the World.

2. **Think in layers.** Long-term strategy and short-term tactics serve different purposes and evolve at different time scales.

3. **Evaluate instead of hardcoding.** Goals compete through dynamic scores rather than fixed priority chains.

4. **Stay imperfect.** Intelligence is expressed through better judgment, not flawless execution.

5. **Cooperate through influence, not control.** Even commanders guide the battlefield instead of puppeteering individual tanks.

6. **Configuration defines intelligence.** New AI levels are created by tuning capabilities and weights, not by branching the codebase.

7. **The engine reasons; behaviors emerge.** The framework should not encode scripts like "always attack the base." It should provide enough perception, evaluation, and decision-making that purposeful battlefield behavior emerges naturally.
