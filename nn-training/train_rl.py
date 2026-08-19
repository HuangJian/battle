"""
RL Training Loop — PPO for Battle City.

Features:
- Automatic progress reports every N updates
- Checkpoint saving
- Win rate tracking
- Tensorboard-compatible logging

Usage:
  python train_rl.py
  python train_rl.py --num-envs 8 --num-steps 2048
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
import torch

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from rl_model import RLNet, count_params
from rl_env import BattleCityEnv, VecBattleCityEnv
from rl_ppo import PPO, RolloutBuffer


def parse_args():
    parser = argparse.ArgumentParser(description="PPO Training for Battle City")
    parser.add_argument("--num-envs", type=int, default=8, help="Number of parallel environments")
    parser.add_argument("--num-steps", type=int, default=2048, help="Steps per rollout")
    parser.add_argument("--num-updates", type=int, default=10000, help="Total training updates")
    parser.add_argument("--learning-rate", type=float, default=3e-4, help="Learning rate")
    parser.add_argument("--gamma", type=float, default=0.99, help="Discount factor")
    parser.add_argument("--gae-lambda", type=float, default=0.95, help="GAE lambda")
    parser.add_argument("--clip-epsilon", type=float, default=0.2, help="PPO clip epsilon")
    parser.add_argument("--entropy-coef", type=float, default=0.01, help="Entropy bonus coefficient")
    parser.add_argument("--value-coef", type=float, default=0.5, help="Value loss coefficient")
    parser.add_argument("--max-grad-norm", type=float, default=0.5, help="Max gradient norm")
    parser.add_argument("--num-epochs", type=int, default=4, help="PPO epochs per update")
    parser.add_argument("--num-minibatches", type=int, default=4, help="Minibatches per update")
    parser.add_argument("--save-freq", type=int, default=100, help="Save checkpoint every N updates")
    parser.add_argument("--report-freq", type=int, default=10, help="Report progress every N updates")
    parser.add_argument("--eval-freq", type=int, default=50, help="Evaluate every N updates")
    parser.add_argument("--eval-episodes", type=int, default=10, help="Episodes per evaluation")
    parser.add_argument("--seed", type=int, default=42, help="Random seed")
    parser.add_argument("--difficulty", type=str, default="hard", help="Game difficulty")
    parser.add_argument("--output-dir", type=str, default="weights/rl", help="Output directory")
    return parser.parse_args()


def evaluate(model: RLNet, num_episodes: int, difficulty: str, seed: int) -> dict:
    """Evaluate model by running complete games."""
    model.eval()
    env = BattleCityEnv(stage_index=0, seed=seed, difficulty=difficulty)

    wins = 0
    total_kills = 0
    total_ticks = 0
    total_reward = 0

    for ep in range(num_episodes):
        obs, scalars = env.reset()
        ep_reward = 0
        ep_kills = 0
        ep_ticks = 0

        max_ticks = 36000  # 10 minutes at 60fps
        while ep_ticks < max_ticks:
            with torch.no_grad():
                obs_tensor = torch.from_numpy(obs).unsqueeze(0)
                scalars_tensor = torch.from_numpy(scalars).unsqueeze(0)
                logits, _ = model(obs_tensor, scalars_tensor)

                # Greedy action selection: decode 3 heads
                move_a = logits[0, :5].argmax().item()
                fire_a = logits[0, 5:7].argmax().item()
                item_a = logits[0, 7:10].argmax().item()
                action = move_a + fire_a * 5 + item_a * 10

            result = env.step(action)
            ep_reward += result.reward
            ep_ticks += 1

            if "kills" in result.info:
                ep_kills = result.info["kills"]

            if result.done:
                if result.info.get("outcome") == "stage_clear":
                    wins += 1
                break

            obs = result.obs
            scalars = result.scalars

            obs = result.obs
            scalars = result.scalars

        total_kills += ep_kills
        total_ticks += ep_ticks
        total_reward += ep_reward

    env.close()
    model.train()

    return {
        "win_rate": wins / num_episodes,
        "avg_kills": total_kills / num_episodes,
        "avg_ticks": total_ticks / num_episodes,
        "avg_reward": total_reward / num_episodes,
    }


def main():
    args = parse_args()

    # Set seed
    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Initialize model
    model = RLNet()
    num_params = count_params(model)
    print(f"RLNet params: {num_params:,} ({num_params/1000:.1f}K)", flush=True)
    print(f"Receptive field: 27×27 (104% of 26×26)", flush=True)

    # Initialize PPO
    ppo = PPO(
        model=model,
        learning_rate=args.learning_rate,
        gamma=args.gamma,
        gae_lambda=args.gae_lambda,
        clip_epsilon=args.clip_epsilon,
        entropy_coef=args.entropy_coef,
        value_coef=args.value_coef,
        max_grad_norm=args.max_grad_norm,
        num_epochs=args.num_epochs,
        num_minibatches=args.num_minibatches,
    )

    # Initialize environment
    env = VecBattleCityEnv(
        num_envs=args.num_envs,
        difficulty=args.difficulty,
        seed=args.seed,
    )

    # Initialize rollout buffer
    buffer = RolloutBuffer(num_steps=args.num_steps, num_envs=args.num_envs)

    # Training loop
    print(f"\nStarting training...", flush=True)
    print(f"  Num envs: {args.num_envs}", flush=True)
    print(f"  Steps per rollout: {args.num_steps}", flush=True)
    print(f"  Total updates: {args.num_updates}", flush=True)
    print(f"  Batch size: {args.num_steps * args.num_envs // args.num_minibatches}", flush=True)
    print(f"  Difficulty: {args.difficulty}", flush=True)
    print(f"  Output: {output_dir}", flush=True)
    print(flush=True)

    start_time = time.time()
    total_steps = 0
    best_win_rate = 0

    # Initial evaluation
    eval_stats = evaluate(model, args.eval_episodes, args.difficulty, args.seed + 1000)
    print(f"[Init] Win rate: {eval_stats['win_rate']:.1%} | "
          f"Avg kills: {eval_stats['avg_kills']:.1f} | "
          f"Avg reward: {eval_stats['avg_reward']:.1f}", flush=True)

    for update in range(1, args.num_updates + 1):
        update_start = time.time()

        # Collect rollout
        obs, scalars = env.reset()
        buffer.reset()

        for step in range(args.num_steps):
            with torch.no_grad():
                obs_tensor = torch.from_numpy(obs)
                scalars_tensor = torch.from_numpy(scalars)
                action, log_prob, _, value = model.get_action_and_value(
                    obs_tensor, scalars_tensor
                )
                # action shape: (B, 3) with [move_idx, fire_idx, item_idx]
                action_np = action.numpy()  # (B, 3)
                log_prob_np = log_prob.numpy()
                value_np = value.numpy()

            # Encode 3-component action to single int for env
            combined = (action_np[:, 0]
                        + action_np[:, 1] * 5
                        + action_np[:, 2] * 10).astype(np.int64)
            next_obs, rewards, dones, infos = env.step(combined)

            # Store transition
            buffer.add(obs, scalars, action_np, log_prob_np, rewards, dones, value_np)

            obs = next_obs
            total_steps += args.num_envs

        # PPO update
        metrics = ppo.update(buffer, obs, scalars)

        # Report progress
        if update % args.report_freq == 0:
            elapsed = time.time() - start_time
            steps_per_sec = total_steps / elapsed
            print(f"[{update:5d}/{args.num_updates}] "
                  f"policy_loss={metrics['policy_loss']:.4f} "
                  f"value_loss={metrics['value_loss']:.4f} "
                  f"entropy={metrics['entropy']:.4f} "
                  f"steps={total_steps:,} "
                  f"({steps_per_sec:.0f} sps) "
                  f"time={elapsed:.0f}s", flush=True)

        # Evaluate
        if update % args.eval_freq == 0:
            eval_stats = evaluate(model, args.eval_episodes, args.difficulty, args.seed + 1000)
            print(f"[Eval] Win rate: {eval_stats['win_rate']:.1%} | "
                  f"Avg kills: {eval_stats['avg_kills']:.1f} | "
                  f"Avg ticks: {eval_stats['avg_ticks']:.0f} | "
                  f"Avg reward: {eval_stats['avg_reward']:.1f}", flush=True)

            # Save best model
            if eval_stats["win_rate"] > best_win_rate:
                best_win_rate = eval_stats["win_rate"]
                save_checkpoint(model, ppo, update, output_dir / "best.pt", eval_stats)

        # Save checkpoint
        if update % args.save_freq == 0:
            save_checkpoint(model, ppo, update, output_dir / f"checkpoint_{update:06d}.pt")

    # Final save
    save_checkpoint(model, ppo, args.num_updates, output_dir / "final.pt")

    # Final evaluation
    eval_stats = evaluate(model, args.eval_episodes * 2, args.difficulty, args.seed + 1000)
    print(f"\n[Final] Win rate: {eval_stats['win_rate']:.1%} | "
          f"Avg kills: {eval_stats['avg_kills']:.1f} | "
          f"Avg ticks: {eval_stats['avg_ticks']:.0f}", flush=True)

    print(f"\nTraining complete! Best win rate: {best_win_rate:.1%}", flush=True)
    print(f"Total steps: {total_steps:,}", flush=True)
    print(f"Total time: {time.time() - start_time:.0f}s", flush=True)

    env.close()


def save_checkpoint(model: RLNet, ppo: PPO, update: int, path: Path, eval_stats: dict = None):
    """Save training checkpoint."""
    checkpoint = {
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": ppo.optimizer.state_dict(),
        "update": update,
        "eval_stats": eval_stats,
    }
    torch.save(checkpoint, path)
    print(f"  Saved checkpoint: {path}", flush=True)


if __name__ == "__main__":
    main()
