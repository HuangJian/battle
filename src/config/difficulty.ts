import type { DifficultyConfig } from '../types'

/**
 * Difficulty presets.
 *
 * IMPORTANT: difficulty must NOT scale enemy combat power (armor / speed /
 * bullet speed / HP). Those dimensions are fixed per-tank-kind and only vary by
 * the Combat Capability System. Difficulty changes difficulty *only* by making
 * the same enemies smarter, through the spawn-time AI tier DISTRIBUTION
 * (`DIFFICULTY_TIER_DISTRIBUTION`) in src/ai/config.ts.
 *
 * The only remaining per-difficulty levers here are player-side resources:
 *   - `startLives`      : how many lives the player gets.
 *   - `playerStartLevel`: the player's starting star level (0 = unbuffed).
 *
 * Adding a new preset = adding one entry here.
 */
export const DIFFICULTIES: Record<string, DifficultyConfig> = {
  relax: {
    name: 'Relax',
    startLives: 5,
    playerStartLevel: 1,
  },
  classic: {
    name: 'Classic',
    startLives: 3,
    playerStartLevel: 0,
  },
  hard: {
    name: 'Hard',
    startLives: 2,
    // §104 (M6, 2026-08-03): 出生即一星。死亡归因重跑 + 死亡机制探针发现玩家
    // 93% 存活时间都是 0★（单发慢弹）——这是 hard/chaos 打不好的根本瓶颈，
    // 不是 dodge 分支（82-83% 死亡占比经 M4/M5 验证无法用行为改动解决）。
    // 60-seed 官方口径 A/B：hard +9.0pp（36.2%→45.3%）、chaos +7.9pp
    // （34.4%→42.3%），31/29 关变好、无 >1σ 回退。§99 备用档正式启用。
    // （注：36.2% 是 60-seed OFF 基线；§96 记录的 38.6% 是 20-seed 基线，
    // 种子集不同所致，两口径各自内部一致。）
    // §104 (M6, 2026-08-03): 出生即一星（60-seed +7.9~9.0pp 强信号，已发布）。
    // §109 (M11, 2026-08-03): 曾尝试出生二星（60-seed hard +9.4pp / chaos
    // +7.5pp，31/29 关变好）——§110 (2026-08-03) 用户否决回退为 1★：
    // 「2★ 起步有点儿欺负敌人」（影响人类 hard/chaos 游戏体验，非 AI 能力
    // 提升）。A/B 数据保留供参考；God AI 达成 50% 目标需非星经济杠杆。
    playerStartLevel: 1,
  },
  chaos: {
    name: 'Chaos',
    // §99 (God-AI-Redesign-v2 评审决议 4): chaos 命数 1→3。
    // 原 1 命下 God AI 在 chaos 的 35% 过关率被"一命即死"方差压制（实测
    // 失败模式 100% lives_exhausted）；3 命与 hard 对齐后目标 50% 才可达。
    // §104 (M6, 2026-08-03): 备用档"出生即一星"正式启用（60-seed +7.9pp
    // 强信号，见 hard 条目）。
    // §109/§110 (2026-08-03): 出生二星曾发布（+7.5pp）被用户否决回退 1★，
    // 见 hard 条目。
    startLives: 3,
    playerStartLevel: 1,
  },
}

export const DIFFICULTY_KEYS = Object.keys(DIFFICULTIES)
