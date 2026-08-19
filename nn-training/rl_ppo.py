"""
PPO (Proximal Policy Optimization) for Battle City RL.

Implementation based on:
- "Proximal Policy Optimization Algorithms" (Schulman et al., 2017)
- CleanRL implementation patterns

Key features:
- GAE (Generalized Advantage Estimation) for advantage computation
- Clipped objective for stable training
- Entropy bonus for exploration
- Gradient clipping for stability
"""

from __future__ import annotations

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

import numpy as np
from typing import Optional

from rl_model import RLNet, MOVE_DIM, FIRE_DIM, ITEM_DIM


class RolloutBuffer:
    """Storage for PPO rollout data."""

    def __init__(self, num_steps: int, num_envs: int):
        self.num_steps = num_steps
        self.num_envs = num_envs
        self.reset()

    def reset(self):
        self.obs = []
        self.scalars = []
        self.actions = []
        self.log_probs = []
        self.rewards = []
        self.dones = []
        self.values = []

    def add(
        self,
        obs: np.ndarray,
        scalars: np.ndarray,
        action: np.ndarray,
        log_prob: np.ndarray,
        reward: np.ndarray,
        done: np.ndarray,
        value: np.ndarray,
    ):
        self.obs.append(obs)
        self.scalars.append(scalars)
        self.actions.append(action)
        self.log_probs.append(log_prob)
        self.rewards.append(reward)
        self.dones.append(done)
        self.values.append(value)

    def compute_returns(
        self,
        next_value: np.ndarray,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Compute GAE advantages and returns."""
        rewards = np.array(self.rewards, dtype=np.float32)
        values = np.array(self.values, dtype=np.float32)
        dones = np.array(self.dones, dtype=np.float32)

        num_steps = len(rewards)
        advantages = np.zeros_like(rewards)
        last_gae = 0

        for t in reversed(range(num_steps)):
            if t == num_steps - 1:
                next_val = next_value
            else:
                next_val = values[t + 1]
            next_non_terminal = 1.0 - dones[t]
            delta = rewards[t] + gamma * next_val * next_non_terminal - values[t]
            advantages[t] = last_gae = delta + gamma * gae_lambda * next_non_terminal * last_gae

        returns = advantages + values
        return advantages, returns

    def get_batches(
        self,
        advantages: np.ndarray,
        returns: np.ndarray,
        batch_size: int,
    ):
        """Generate shuffled mini-batches."""
        obs = np.array(self.obs, dtype=np.uint8)
        scalars = np.array(self.scalars, dtype=np.float32)
        actions = np.array(self.actions, dtype=np.int64)
        old_log_probs = np.array(self.log_probs, dtype=np.float32)

        num_samples = obs.shape[0] * obs.shape[1]  # steps × envs
        obs = obs.reshape(num_samples, *obs.shape[2:])
        scalars = scalars.reshape(num_samples, -1)
        # actions may be (steps, envs) or (steps, envs, 3) — flatten steps×envs
        if actions.ndim == 3:
            actions = actions.reshape(num_samples, 3)  # keep 3-component
        else:
            actions = actions.reshape(num_samples, 1)  # single int
        old_log_probs = old_log_probs.reshape(num_samples)
        advantages = advantages.reshape(num_samples)
        returns = returns.reshape(num_samples)

        # Normalize advantages
        advantages = (advantages - advantages.mean()) / (advantages.std() + 1e-8)

        indices = np.random.permutation(num_samples)
        for start in range(0, num_samples, batch_size):
            end = start + batch_size
            batch_idx = indices[start:end]
            yield (
                torch.from_numpy(obs[batch_idx]),
                torch.from_numpy(scalars[batch_idx]),
                torch.from_numpy(actions[batch_idx]),
                torch.from_numpy(old_log_probs[batch_idx]),
                torch.from_numpy(advantages[batch_idx]),
                torch.from_numpy(returns[batch_idx]),
            )


class PPO:
    """PPO trainer for Battle City RL."""

    def __init__(
        self,
        model: RLNet,
        learning_rate: float = 3e-4,
        gamma: float = 0.99,
        gae_lambda: float = 0.95,
        clip_epsilon: float = 0.2,
        entropy_coef: float = 0.01,
        value_coef: float = 0.5,
        max_grad_norm: float = 0.5,
        num_epochs: int = 4,
        num_minibatches: int = 4,
    ):
        self.model = model
        self.gamma = gamma
        self.gae_lambda = gae_lambda
        self.clip_epsilon = clip_epsilon
        self.entropy_coef = entropy_coef
        self.value_coef = value_coef
        self.max_grad_norm = max_grad_norm
        self.num_epochs = num_epochs
        self.num_minibatches = num_minibatches

        self.optimizer = optim.Adam(model.parameters(), lr=learning_rate, eps=1e-5)

    def update(
        self,
        buffer: RolloutBuffer,
        next_obs: np.ndarray,
        next_scalars: np.ndarray,
    ) -> dict:
        """
        Run PPO update on collected rollout.

        Returns:
            Dictionary of training metrics.
        """
        # Compute next value for GAE
        with torch.no_grad():
            next_value = self.model.get_value(
                torch.from_numpy(next_obs),
                torch.from_numpy(next_scalars),
            ).numpy()

        # Compute advantages and returns
        advantages, returns = buffer.compute_returns(
            next_value, self.gamma, self.gae_lambda
        )

        # PPO update
        total_policy_loss = 0
        total_value_loss = 0
        total_entropy = 0
        num_updates = 0

        for epoch in range(self.num_epochs):
            for batch in buffer.get_batches(
                advantages, returns,
                batch_size=buffer.num_steps * buffer.num_envs // self.num_minibatches
            ):
                obs, scalars, actions, old_log_probs, adv, ret = batch

                # Forward pass
                _, new_log_probs, entropy, new_values = self.model.get_action_and_value(
                    obs, scalars, actions
                )

                # Policy loss (clipped)
                ratio = torch.exp(new_log_probs - old_log_probs)
                surr1 = ratio * adv
                surr2 = torch.clamp(ratio, 1 - self.clip_epsilon, 1 + self.clip_epsilon) * adv
                policy_loss = -torch.min(surr1, surr2).mean()

                # Value loss (clipped)
                value_loss = nn.functional.mse_loss(new_values, ret)

                # Entropy bonus
                entropy_loss = -entropy.mean()

                # Total loss
                loss = (
                    policy_loss
                    + self.value_coef * value_loss
                    + self.entropy_coef * entropy_loss
                )

                # Backward pass
                self.optimizer.zero_grad()
                loss.backward()
                nn.utils.clip_grad_norm_(self.model.parameters(), self.max_grad_norm)
                self.optimizer.step()

                total_policy_loss += policy_loss.item()
                total_value_loss += value_loss.item()
                total_entropy += -entropy_loss.item()
                num_updates += 1

        return {
            "policy_loss": total_policy_loss / num_updates,
            "value_loss": total_value_loss / num_updates,
            "entropy": total_entropy / num_updates,
        }
