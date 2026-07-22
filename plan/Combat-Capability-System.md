# Combat Capability System

## Tank Profile & Progression Framework

Version 1.0

---

# 1. Objective

Introduce a configurable combat capability framework that defines the combat characteristics of every tank.

The system replaces traditional difficulty scaling through raw statistics with a more expressive model:

> Difficulty and progression emerge from differences in combat capabilities, tactical roles, and intelligent decision-making.

All tanks share the same capability dimensions.

Different tank types specialize in different dimensions while sacrificing others.

Player progression increases overall combat capability through star upgrades.

---

# 2. Design Philosophy

## Same Battlefield Rules

All tanks use the same combat model.

There are no hidden advantages.

---

## Different Combat Profiles

Tank types differ through capability distribution.

Example:

Fast tank:

```
High Mobility

Low Armor
```

Heavy tank:

```
High Armor

Low Mobility
```

---

## Intelligence + Capability

Combat strength is determined by:

```
Combat Power

=

Capability Profile

+

Tactical Intelligence
```

A stronger tank is not only stronger statistically.

It also understands how to use its strengths.

---

# 3. Combat Profile Architecture

Each tank owns a Combat Profile.

```text
Tank

 ├── Combat Profile
 │
 ├── Tank Type
 │
 ├── AI Level
 │
 └── Modifiers
```

---

Example:

```yaml
Tank:

type:
  Heavy

combatProfile:
  firepower: 60
  projectileSpeed: 45
  fireControl: 55
  mobility: 30
  armor: 90
  special: 40

intelligence:
  Veteran
```

---

# 4. Capability Dimensions

All tanks share six universal attributes.

---

# 4.1 Firepower

## Definition

Projectile destructive capability.

Influences:

* damage
* wall destruction
* armor damage

---

Example:

Low:

```
Basic cannon
```

High:

```
Heavy cannon
```

---

# 4.2 Projectile Speed

## Definition

Bullet travel speed.

Influences:

* hit probability
* reaction difficulty
* ranged pressure

---

Fast projectile:

* harder to dodge
* better for long distance combat

---

# 4.3 Fire Control

## Definition

Shooting effectiveness.

Includes:

* aiming efficiency
* firing timing
* direction switching efficiency
* attack opportunity detection

---

This attribute connects directly with AI.

Example:

High Fire Control:

```
Detect opening

↓

Immediately fire
```

Low:

```
Target appears

↓

Delay

↓

Fire
```

---

# 4.4 Mobility

## Definition

Overall movement capability.

Includes:

* movement speed
* acceleration
* turning efficiency
* maneuverability

---

Fast tank:

High Mobility

---

Heavy tank:

Low Mobility

---

# 4.5 Armor

## Definition

Tank durability.

Armor directly represents HP.

```
Armor = Maximum HP
```

Damage reduces current HP.

---

Example:

```
Armor: 80

Current HP:
80 / 80
```

After damage:

```
35 / 80
```

---

# 4.6 Special

Reserved extension attribute.

Possible future usage:

* shield
* regeneration
* stealth
* radar
* construction
* support ability

Version 1.0 does not implement specific abilities.

---

# 5. Attribute Representation

Internally:

0-100 abstract score.

Example:

```
50 = baseline capability
```

---

External display:

Optional conversion.

Example:

```
★★★★★
```

---

The system should never depend on displayed values.

---

# 6. Combat Budget System

Every tank type is generated from a capability budget.

---

## Purpose

Ensure normal enemies maintain similar total combat power.

---

Example:

Baseline budget:

```
300 points
```

---

Balanced Tank:

```
Firepower       50
Projectile      50
Fire Control    50
Mobility        50
Armor           50
Special         50

Total:

300
```

---

Fast Tank:

```
Firepower       40
Projectile      45
Fire Control    45
Mobility        80
Armor           45
Special         45

Total:

300
```

---

Heavy Tank:

```
Firepower       55
Projectile      40
Fire Control    45
Mobility        30
Armor           90
Special         40

Total:

300
```

---

# 7. Tank Types

Version 1.0 includes four types.

---

# 7.1 Balanced Tank

Role:

General combat.

Profile:

```
All dimensions near average
```

Purpose:

Baseline enemy.

---

# 7.2 Fast Tank

Role:

Mobility pressure.

Advantages:

* rapid positioning
* flanking
* escaping danger

Weakness:

* low armor
* weaker exchange ability

AI preference:

* avoid direct fights
* exploit mobility

---

# 7.3 Power Tank

Role:

Offensive pressure.

Advantages:

* strong firepower
* strong projectile pressure

Weakness:

* reduced mobility
* lower defense

AI preference:

* seek combat opportunities

---

# 7.4 Heavy Armor Tank

Role:

Frontline pressure.

Advantages:

* high HP
* survive exchanges

Weakness:

* slow movement

AI preference:

* push forward
* absorb damage

---

# 8. Elite Commander Tank

Elite commanders are not independent tank types.

They are a combination.

```
Elite Commander

=

Tank Type

+

Combat Modifier

+

Commander AI
```

---

Example:

Fast Commander:

Combat:

```
Mobility +15%
```

AI:

```
Flanking strategy
```

---

Heavy Commander:

Combat:

```
Armor +15%
```

AI:

```
Formation push
```

---

# 9. Normal Enemy Rules

Normal enemies:

* use standard budget
* no total power increase
* specialize through redistribution

Purpose:

Difficulty comes from variety.

Not inflation.

---

# 10. Elite Rules

Elite enemies:

Allowed:

* higher budget
* stronger specialization
* commander AI

Example:

Normal:

```
300 budget
```

Elite:

```
360 budget
```

---

The increase should remain controlled.

Elite enemies should feel dangerous.

Not unfair.

---

# 11. Player Progression System

Player starts equal to Balanced Enemy.

Baseline:

```
Player Level 0

=

Balanced Tank
```

---

Stars increase all dimensions.

Unlike enemy specialization:

Player growth is universal.

---

Example:

## Level 0

```
50
50
50
50
50
50
```

---

## Level 1

```
60
60
60
60
60
60
```

---

## Level 2

```
70
70
70
70
70
70
```

---

# 12. Star Damage Regression

Player upgrades are represented as capability tiers.

Example:

Level 3:

```
HP:

150
```

Damage:

```
HP -> 95
```

If level 2 threshold:

```
100
```

is crossed:

Player loses one star level.

All attributes downgrade together.

---

Example:

Before:

```
Level 3

80 / 80 / 80 / 80 / 80 / 80
```

Damage:

```
HP drops below Level 3 threshold
```

After:

```
Level 2

70 / 70 / 70 / 70 / 70 / 70
```

---

# 13. Player Maximum Configuration

Default:

## Option A

Player dominates normal enemies.

Recommended default.

---

Supported alternatives:

## Option B

Player slightly stronger.

Hardcore mode.

---

## Option C

Player approaches Commander level.

Challenge mode.

---

Implemented through configuration.

Example:

```yaml
playerProgression:

maximumLevel: 3

maxMultiplier: 1.5
```

---

# 14. AI Integration

AI must read Combat Profile.

Combat attributes influence decision evaluation.

---

Example:

Fast Tank:

```
Mobility advantage

↓

Lower movement cost

↓

Higher flank score
```

---

Heavy Tank:

```
Armor advantage

↓

Lower risk penalty

↓

More aggressive push
```

---

Power Tank:

```
Firepower advantage

↓

Higher attack score
```

---

The AI does not receive special scripts.

It evaluates its own capabilities.

---

# 15. Configuration Driven Design

All profiles should be data-driven.

Example:

```
configs/

 tanks/

   balanced.yaml

   fast.yaml

   power.yaml

   heavy.yaml


 player/

   progression.yaml


 elite/

   commander.yaml
```

---

Adding a new tank:

Create configuration.

No engine modification.

---

# 16. Development Milestones

---

## Milestone 1

Combat Profile Foundation

Implement:

* attributes
* profile object
* budget system

Acceptance:

Any tank can describe its capability.

---

## Milestone 2

Tank Type Profiles

Implement:

* Balanced
* Fast
* Power
* Heavy

Acceptance:

Different combat styles emerge.

---

## Milestone 3

Player Progression

Implement:

* stars
* global upgrades
* damage regression

Acceptance:

Player growth works.

---

## Milestone 4

AI Integration

Connect:

* capability evaluation
* tactical decision weighting

Acceptance:

AI uses strengths intelligently.

---

## Milestone 5

Elite Commander

Implement:

* elite modifier
* commander capability bonus
* commander AI integration

Acceptance:

Elite units feel fundamentally different.

---

# 17. Testing

Verify:

## Balance

Normal tanks have similar total combat budget.

---

## Progression

Player growth feels meaningful.

---

## Regression

Damage correctly reduces star level.

---

## AI

AI chooses tactics matching capabilities.

---

## Extensibility

Adding a new tank requires configuration only.

---

# 18. Definition of Done

The Combat Capability System is complete when:

* ✅ Every tank uses the same six capability dimensions.
* ✅ Tank types are profile variations, not separate implementations.
* ✅ Normal enemies maintain similar combat budgets.
* ✅ Elite commanders combine tank type, combat modifiers and commander AI.
* ✅ Player progression improves all dimensions together.
* ✅ Player power has configurable maximum limits.
* ✅ AI evaluates decisions based on its own capabilities.
* ✅ New tank types can be added without engine changes.
* ✅ The system supports future procedural tank generation.

---

# 19. Combat System Constitution

1. **Attributes define possibilities, AI defines decisions.**

2. **Tank identity comes from capability distribution, not special rules.**

3. **Normal enemies compete through specialization, not inflated numbers.**

4. **Elite enemies break the budget only because they represent exceptional units.**

5. **Player growth should feel powerful but remain bounded.**

6. **Combat capability must always be visible to AI decision-making.**

7. **New tanks should emerge from configuration, not code branches.**
