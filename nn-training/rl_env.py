"""
Battle City RL Environment — wraps the game simulation as a gym-like interface.

Uses the existing TS game engine via subprocess calls to run simulations.
Each step:
  1. Encode current world state → obs (14×26×26) + scalars (24)
  2. Agent selects action (move/fire/item)
  3. Execute action in game engine
  4. Compute reward based on Score v7 dimensions
  5. Return (obs, reward, done, info)

Reward design (based on God AI Score v7):
  - Kill enemy: +4.77 (progress weight × 10)
  - Death: -2.56 (lives weight × 10)
  - Base wall destroyed: -1.70 (baseIntegrity weight × 10)
  - Base pressure: -0.44 (baseSafety weight × 10)
  - First kill < 30s: +0.18 (openingTempo weight × 10)
  - Power-up collected: +0.09 (loot weight × 10)
  - Star level up: +0.60 (growth weight × 10)
  - Stage clear: +100.0
  - Base destroyed: -100.0
  - Lives exhausted: -50.0
"""

from __future__ import annotations

import subprocess
import json
import os
import sys
from dataclasses import dataclass
from typing import Optional

import numpy as np

from schema import OBS_CHANNELS, BOARD, SCALAR_DIM


@dataclass
class EnvStep:
    obs: np.ndarray          # (14, 26, 26) uint8
    scalars: np.ndarray      # (24,) float32
    reward: float
    done: bool
    info: dict


class BattleCityEnv:
    """
    Gym-like environment for Battle City.

    Uses the TS simulation runner via subprocess to execute game steps.
    Maintains state between steps via the simulation process.
    """

    def __init__(
        self,
        stage_index: int = 0,
        seed: int = 42,
        difficulty: str = "hard",
        max_ticks: int = 36000,
    ):
        self.stage_index = stage_index
        self.seed = seed
        self.difficulty = difficulty
        self.max_ticks = max_ticks
        self.tick = 0
        self.done = False
        self.process: Optional[subprocess.Popen] = None
        self.last_obs: Optional[np.ndarray] = None
        self.last_scalars: Optional[np.ndarray] = None

        # Reward tracking
        self.prev_kills = 0
        self.prev_lives = 3
        self.prev_base_walls = 8
        self.prev_player_level = 0
        self.first_kill_tick: Optional[int] = None

        # Score v7 reward weights (from godai-score.ts)
        self.REWARD_KILL = 4.77        # progress: 0.477 × 10
        self.REWARD_DEATH = -2.56      # lives: 0.256 × 10
        self.REWARD_BASE_WALL = -1.70  # baseIntegrity: 0.170 × 10
        self.REWARD_BASE_PRESSURE = -0.44  # baseSafety: 0.044 × 10
        self.REWARD_FIRST_KILL = 0.18  # openingTempo: 0.018 × 10
        self.REWARD_LOOT = 0.09        # loot: 0.009 × 10
        self.REWARD_GROWTH = 0.60      # growth: 0.060 × 10
        self.REWARD_CLEAR = 100.0
        self.REWARD_BASE_DESTROYED = -100.0
        self.REWARD_LIVES_EXHAUSTED = -50.0

    def reset(self) -> tuple[np.ndarray, np.ndarray]:
        """Reset environment and return initial observation."""
        self.tick = 0
        self.done = False
        self.prev_kills = 0
        self.prev_lives = 3
        self.prev_base_walls = 8
        self.prev_player_level = 0
        self.first_kill_tick = None

        # Start new simulation
        obs, scalars = self._start_simulation()
        self.last_obs = obs
        self.last_scalars = scalars
        return obs, scalars

    def step(self, action: int) -> EnvStep:
        """
        Execute one step in the environment.

        Args:
            action: Combined action index (0-9)
                    0-4: move (none/up/down/left/right)
                    5-6: fire (hold/fire)
                    7-9: item (none/guard/frenzy)

        Returns:
            EnvStep with obs, reward, done, info
        """
        if self.done:
            raise RuntimeError("Environment is done. Call reset() first.")

        # Decode action
        move_idx = action % 5
        fire_idx = (action // 5) % 2
        item_idx = (action // 10) % 3

        # Map to game actions
        MOVES = ["none", "up", "down", "left", "right"]
        FIRES = [False, True]
        ITEMS = ["none", "guard", "frenzy"]

        game_action = {
            "move": MOVES[move_idx],
            "fire": FIRES[fire_idx],
            "item": ITEMS[item_idx],
        }

        # Execute step in game engine
        result = self._execute_step(game_action)

        # Parse result
        obs = np.array(result["obs"], dtype=np.uint8).reshape(OBS_CHANNELS, BOARD, BOARD)
        scalars = np.array(result["scalars"], dtype=np.float32)
        info = result["info"]

        # Compute reward
        reward = self._compute_reward(info)

        # Update state
        self.tick += 1
        self.done = result["done"]
        self.last_obs = obs
        self.last_scalars = scalars

        return EnvStep(
            obs=obs,
            scalars=scalars,
            reward=reward,
            done=self.done,
            info=info,
        )

    def _start_simulation(self) -> tuple[np.ndarray, np.ndarray]:
        """Start a new game simulation and get initial observation."""
        # Use the TS simulation runner to start a game
        cmd = [
            "bun", "run", "../tools/sim/simulation-runner.ts",
            "--seed", str(self.seed),
            "--stage", str(self.stage_index),
            "--difficulty", self.difficulty,
            "--max-ticks", str(self.max_ticks),
            "--policy", "nn-rl",  # RL mode: return obs instead of running AI
        ]

        # This would need a custom TS script to interface with Python
        # For now, we'll simulate with random actions to test the interface
        obs = np.random.randint(0, 256, (OBS_CHANNELS, BOARD, BOARD), dtype=np.uint8)
        scalars = np.random.randn(SCALAR_DIM).astype(np.float32)
        return obs, scalars

    def _execute_step(self, action: dict) -> dict:
        """Execute one step in the game engine."""
        # Placeholder: in real implementation, this calls the TS engine
        # For now, return random observation and simulate game events
        obs = np.random.randint(0, 256, (OBS_CHANNELS * BOARD * BOARD,)).tolist()
        scalars = np.random.randn(SCALAR_DIM).tolist()

        # Random kills and deaths
        new_kills = self.prev_kills + np.random.randint(0, 2)
        new_lives = self.prev_lives - (1 if np.random.random() < 0.01 else 0)
        new_base_walls = self.prev_base_walls - (1 if np.random.random() < 0.005 else 0)
        new_level = min(self.prev_player_level + (1 if np.random.random() < 0.005 else 0), 4)

        # Terminate conditions
        done = False
        outcome = "playing"
        if new_lives <= 0:
            done = True
            outcome = "lives_exhausted"
        elif new_base_walls <= 0 and np.random.random() < 0.1:
            done = True
            outcome = "base_destroyed"
        elif new_kills >= 20:
            done = True
            outcome = "stage_clear"
        elif self.tick >= 3600:
            done = True
            outcome = "timeout"

        info = {
            "kills": new_kills,
            "lives": new_lives,
            "base_walls": new_base_walls,
            "player_level": new_level,
            "base_alive": new_base_walls > 0,
            "player_alive": new_lives > 0,
            "outcome": outcome,
        }

        return {
            "obs": obs,
            "scalars": scalars,
            "info": info,
            "done": done,
        }

    def _compute_reward(self, info: dict) -> float:
        """Compute reward based on Score v7 dimensions."""
        reward = 0.0

        # Kill reward
        kills_delta = info["kills"] - self.prev_kills
        if kills_delta > 0:
            reward += kills_delta * self.REWARD_KILL
            if self.first_kill_tick is None:
                self.first_kill_tick = self.tick
                if self.tick < 1800:  # 30 seconds at 60fps
                    reward += self.REWARD_FIRST_KILL

        # Death penalty
        lives_delta = info["lives"] - self.prev_lives
        if lives_delta < 0:
            reward += lives_delta * self.REWARD_DEATH

        # Base wall penalty
        walls_delta = info["base_walls"] - self.prev_base_walls
        if walls_delta < 0:
            reward += walls_delta * self.REWARD_BASE_WALL

        # Star level reward
        level_delta = info["player_level"] - self.prev_player_level
        if level_delta > 0:
            reward += level_delta * self.REWARD_GROWTH

        # Terminal rewards
        if info["outcome"] == "stage_clear":
            reward += self.REWARD_CLEAR
        elif info["outcome"] == "base_destroyed":
            reward += self.REWARD_BASE_DESTROYED
        elif info["outcome"] == "lives_exhausted":
            reward += self.REWARD_LIVES_EXHAUSTED

        # Update previous state
        self.prev_kills = info["kills"]
        self.prev_lives = info["lives"]
        self.prev_base_walls = info["base_walls"]
        self.prev_player_level = info["player_level"]

        return reward

    def close(self):
        """Clean up environment."""
        if self.process:
            self.process.terminate()
            self.process.wait()


class VecBattleCityEnv:
    """
    Vectorized environment for parallel training.
    Runs N independent game instances in parallel.
    """

    def __init__(self, num_envs: int, difficulty: str = "hard", seed: int = 42, **env_kwargs):
        self.num_envs = num_envs
        self.envs = [
            BattleCityEnv(stage_index=i % 35, seed=seed + i, difficulty=difficulty, **env_kwargs)
            for i in range(num_envs)
        ]

    def reset(self) -> tuple[np.ndarray, np.ndarray]:
        """Reset all environments."""
        obs_list = []
        scalars_list = []
        for env in self.envs:
            obs, scalars = env.reset()
            obs_list.append(obs)
            scalars_list.append(scalars)
        return np.stack(obs_list), np.stack(scalars_list)

    def step(self, actions: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[dict]]:
        """Step all environments."""
        obs_list = []
        rewards = []
        dones = []
        infos = []

        for env, action in zip(self.envs, actions):
            result = env.step(int(action))
            obs_list.append(result.obs)
            rewards.append(result.reward)
            dones.append(result.done)
            infos.append(result.info)

            # Auto-reset done environments
            if result.done:
                obs, scalars = env.reset()
                obs_list[-1] = obs

        return (
            np.stack(obs_list),
            np.array(rewards, dtype=np.float32),
            np.array(dones, dtype=bool),
            infos,
        )

    def close(self):
        for env in self.envs:
            env.close()


if __name__ == "__main__":
    # Test single environment
    env = BattleCityEnv(stage_index=0, seed=42)
    obs, scalars = env.reset()
    print(f"obs shape: {obs.shape}, scalars shape: {scalars.shape}")

    total_reward = 0
    for _ in range(100):
        action = np.random.randint(0, 10)
        result = env.step(action)
        total_reward += result.reward
        if result.done:
            break

    print(f"Total reward: {total_reward:.2f}")
    env.close()
