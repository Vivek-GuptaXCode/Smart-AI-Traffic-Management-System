"""GraphSAGE (Graph Sample and Aggregate) for Traffic Prediction.

Reference:
  Hamilton et al. "Inductive Representation Learning on Large Graphs"
  NeurIPS 2017. https://arxiv.org/abs/1706.02216

Temporal variant: GraphSAGE is inherently spatial-only.  To handle the same
5-timestep sequences fed to T-GCN we apply GraphSAGE independently at every
timestep and pool the representations across time (mean pooling).  This gives
the model access to the same temporal window WITHOUT learning temporal dynamics
— the deliberate architectural contrast vs. T-GCN's GRU memory.

Architecture
============
  InputProj     : feature_dim → hidden_dim
  SAGELayer ×2  : mean-aggregation (GraphSAGE)
  TemporalPool  : mean over sequence timesteps
  OutputHead    : hidden_dim → 1 (sigmoid congestion probability)
"""
from __future__ import annotations

import json
from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor


# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class GraphSAGEConfig:
    """Configuration matching T-GCN for a fair comparison."""

    node_feature_dim: int = 4    # Same features as T-GCN
    hidden_dim: int = 24         # Same capacity as T-GCN
    output_dim: int = 1
    num_layers: int = 2          # 2 hop neighbourhood aggregation
    sequence_length: int = 5     # Same temporal window as T-GCN
    dropout_rate: float = 0.1

    learning_rate: float = 0.01
    weight_decay: float = 5e-4
    batch_size: int = 12
    buffer_size: int = 500
    warmup_steps: int = 10

    congestion_threshold: float = 0.60
    log_interval: int = 200
    checkpoint_interval: int = 1000
    inference_every_n_steps: int = 10
    train_every_n_steps: int = 25

    use_ema_smoothing: bool = True
    ema_alpha: float = 0.3


# ─────────────────────────────────────────────────────────────────────────────
# GraphSAGE Layer (Mean Aggregation — original paper variant)
# ─────────────────────────────────────────────────────────────────────────────

class SAGELayer(nn.Module):
    """Single GraphSAGE layer using mean aggregation.

    h_v^k = σ( W · CONCAT( h_v^(k-1),  MEAN_{u ∈ N(v)} h_u^(k-1) ) )

    This is the MEAN aggregator from Section 3.2 of the paper.
    It aggregates all neighbours uniformly — no trainable temporal memory.
    """

    def __init__(self, in_dim: int, out_dim: int, dropout: float = 0.1):
        super().__init__()
        # W maps concat(self, neigh_mean) → out_dim
        self.linear = nn.Linear(in_dim * 2, out_dim, bias=True)
        self.dropout = nn.Dropout(dropout)
        self.norm = nn.LayerNorm(out_dim)
        nn.init.xavier_uniform_(self.linear.weight)

    def forward(self, x: Tensor, adj: Tensor) -> Tensor:
        """
        Args:
            x:   [batch, num_nodes, in_dim]
            adj: [num_nodes, num_nodes]  (row-normalised)
        Returns:
            out: [batch, num_nodes, out_dim]
        """
        # Neighbour mean aggregation: [batch, nodes, in_dim]
        neigh_mean = torch.bmm(
            adj.unsqueeze(0).expand(x.size(0), -1, -1),
            x,
        )
        # Concat self + neighbour: [batch, nodes, 2*in_dim]
        agg = torch.cat([x, neigh_mean], dim=-1)
        # Linear + activation
        out = F.relu(self.linear(self.dropout(agg)))
        return self.norm(out)


def _row_normalise(adj: np.ndarray) -> Tensor:
    """Row-normalise adjacency with self-loops: D^-1 (A + I)."""
    a = adj + np.eye(adj.shape[0], dtype=np.float32)
    row_sum = a.sum(axis=1, keepdims=True).clip(min=1e-8)
    return torch.FloatTensor(a / row_sum)


# ─────────────────────────────────────────────────────────────────────────────
# Full GraphSAGE Model
# ─────────────────────────────────────────────────────────────────────────────

class GraphSAGE(nn.Module):
    """Temporal GraphSAGE for traffic congestion prediction.

    Processes each timestep independently (no temporal memory), then
    pools across the sequence with mean-pooling.  Contrast with T-GCN
    which uses a GRU to propagate hidden state across timesteps.
    """

    def __init__(self, adj: np.ndarray, config: GraphSAGEConfig):
        super().__init__()
        self.config = config
        self.num_nodes = adj.shape[0]

        # Row-normalised adjacency registered as buffer
        self.register_buffer("adj", _row_normalise(adj))

        # Input projection
        self.input_proj = nn.Sequential(
            nn.Linear(config.node_feature_dim, config.hidden_dim),
            nn.ReLU(),
            nn.Dropout(config.dropout_rate),
        )

        # SAGE layers
        self.sage_layers = nn.ModuleList([
            SAGELayer(config.hidden_dim, config.hidden_dim, config.dropout_rate)
            for _ in range(config.num_layers)
        ])

        # Output head (identical to T-GCN's)
        self.output_head = nn.Sequential(
            nn.Linear(config.hidden_dim, 32),
            nn.ReLU(),
            nn.Dropout(config.dropout_rate),
            nn.Linear(32, config.output_dim),
            nn.Sigmoid(),
        )

    def forward(self, x_seq: Tensor) -> Tensor:
        """
        Args:
            x_seq: [batch, seq_len, num_nodes, features]
                   or [seq_len, num_nodes, features]
        Returns:
            output: [batch, num_nodes, 1] congestion probabilities
        """
        if x_seq.dim() == 3:
            x_seq = x_seq.unsqueeze(0)

        batch_size, seq_len, num_nodes, features = x_seq.shape

        # Process each timestep independently (no temporal memory)
        timestep_outputs = []
        for t in range(seq_len):
            x_t = x_seq[:, t, :, :]              # [batch, nodes, features]
            h = self.input_proj(x_t)              # [batch, nodes, hidden]

            for layer in self.sage_layers:
                h = layer(h, self.adj)            # [batch, nodes, hidden]

            timestep_outputs.append(h)

        # MEAN pooling across time (no learned temporal dynamics)
        pooled = torch.stack(timestep_outputs, dim=1).mean(dim=1)  # [batch, nodes, hidden]

        return self.output_head(pooled)           # [batch, nodes, 1]


# ─────────────────────────────────────────────────────────────────────────────
# Attention-Augmented GraphSAGE (for fair comparison with A3T-GCN)
# ─────────────────────────────────────────────────────────────────────────────

class TemporalAttentionSAGE(nn.Module):
    """Soft temporal attention over timestep hidden states.

    Learns importance weights for each timestep:
        e_t = V * tanh(W * h_t + b)
        α_t = softmax(e_1, ..., e_T)
        context = Σ α_t * h_t
    """

    def __init__(self, hidden_dim: int, num_nodes: int):
        super().__init__()
        self.W = nn.Linear(hidden_dim, hidden_dim, bias=True)
        self.V = nn.Linear(hidden_dim, 1, bias=False)

    def forward(self, timestep_outputs: list[Tensor]) -> Tensor:
        """
        Args:
            timestep_outputs: list of [batch, nodes, hidden] tensors
        Returns:
            context: [batch, nodes, hidden] attention-weighted output
        """
        stacked = torch.stack(timestep_outputs, dim=1)  # [B, T, N, H]
        scores  = self.V(torch.tanh(self.W(stacked)))   # [B, T, N, 1]
        weights = F.softmax(scores, dim=1)               # softmax over T
        context = (stacked * weights).sum(dim=1)          # [B, N, H]
        return context


class AttentionGraphSAGE(nn.Module):
    """GraphSAGE augmented with temporal attention (fair comparison vs A3T-GCN).

    Replaces naive mean-pooling with learnable soft-attention over the
    sequence dimension.  This gives GraphSAGE the same temporal weighting
    ability that A3T-GCN has — the ONLY remaining difference is that
    T-GCN uses a GRU (recurrent memory) while this uses independent
    per-timestep SAGE processing.
    """

    def __init__(self, adj: np.ndarray, config: GraphSAGEConfig):
        super().__init__()
        self.config = config
        self.num_nodes = adj.shape[0]

        self.register_buffer("adj", _row_normalise(adj))

        # Input projection
        self.input_proj = nn.Sequential(
            nn.Linear(config.node_feature_dim, config.hidden_dim),
            nn.ReLU(),
            nn.Dropout(config.dropout_rate),
        )

        # SAGE layers
        self.sage_layers = nn.ModuleList([
            SAGELayer(config.hidden_dim, config.hidden_dim, config.dropout_rate)
            for _ in range(config.num_layers)
        ])

        # Temporal attention (replaces mean-pool)
        self.temporal_attention = TemporalAttentionSAGE(
            config.hidden_dim, self.num_nodes
        )

        # LayerNorm (same as Improved T-GCN)
        self.layer_norm = nn.LayerNorm(config.hidden_dim)

        # Output head
        self.output_head = nn.Sequential(
            nn.Linear(config.hidden_dim, 32),
            nn.ReLU(),
            nn.Dropout(config.dropout_rate),
            nn.Linear(32, config.output_dim),
            nn.Sigmoid(),
        )

    def forward(self, x_seq: Tensor) -> Tensor:
        if x_seq.dim() == 3:
            x_seq = x_seq.unsqueeze(0)

        batch_size, seq_len, num_nodes, features = x_seq.shape

        timestep_outputs = []
        for t in range(seq_len):
            x_t = x_seq[:, t, :, :]
            h = self.input_proj(x_t)
            for layer in self.sage_layers:
                h = layer(h, self.adj)
            timestep_outputs.append(h)

        # Temporal ATTENTION (not mean-pool)
        context = self.temporal_attention(timestep_outputs)
        context = self.layer_norm(context)
        return self.output_head(context)


# ─────────────────────────────────────────────────────────────────────────────
# Simple Replay Buffer (mirrors T-GCN's for fair comparison)
# ─────────────────────────────────────────────────────────────────────────────

class ReplayBuffer:
    def __init__(self, capacity: int):
        self.buffer: deque = deque(maxlen=capacity)

    def push(self, state_seq: np.ndarray, target: np.ndarray):
        self.buffer.append((state_seq.copy(), target.copy()))

    def sample(self, batch_size: int):
        idx = np.random.choice(len(self.buffer), min(batch_size, len(self.buffer)), replace=False)
        states, targets = zip(*[self.buffer[i] for i in idx])
        return np.array(states), np.array(targets)

    def __len__(self):
        return len(self.buffer)


# ─────────────────────────────────────────────────────────────────────────────
# GraphSAGE Reroute Engine (mirrors PyTorchGNNRerouteEngine interface)
# ─────────────────────────────────────────────────────────────────────────────

class GraphSAGERerouteEngine:
    """GraphSAGE-based traffic prediction engine.

    Drop-in alternative to PyTorchGNNRerouteEngine for comparative study.
    Implements the same predict() / train_step() / get_metrics() interface.
    """

    def __init__(
        self,
        rsu_graph,          # networkx Graph
        rsu_ids: list[str],
        config: GraphSAGEConfig | None = None,
        device: str | None = None,
    ):
        self.rsu_ids = list(rsu_ids)
        self.num_nodes = len(rsu_ids)
        self.node_index = {nid: i for i, nid in enumerate(self.rsu_ids)}
        self.config = config or GraphSAGEConfig()

        # Build adjacency matrix from graph
        adj = np.zeros((self.num_nodes, self.num_nodes), dtype=np.float32)
        for u, v in rsu_graph.edges():
            if u in self.node_index and v in self.node_index:
                i, j = self.node_index[u], self.node_index[v]
                adj[i, j] = adj[j, i] = 1.0
        self.adj = adj

        # Device
        self.device = torch.device(
            device if device else ("cuda" if torch.cuda.is_available() else "cpu")
        )

        # Model + optimiser
        self.model = GraphSAGE(adj, self.config).to(self.device)
        self.optimizer = torch.optim.Adam(
            self.model.parameters(),
            lr=self.config.learning_rate,
            weight_decay=self.config.weight_decay,
        )
        self.scheduler = torch.optim.lr_scheduler.StepLR(
            self.optimizer, step_size=500, gamma=0.5
        )

        # State tracking (identical to T-GCN engine)
        self.seq_buffer: deque = deque(maxlen=self.config.sequence_length)
        self.replay = ReplayBuffer(self.config.buffer_size)
        self._step = 0
        self._cached_pred: np.ndarray | None = None
        self._ema: np.ndarray | None = None

        # Metrics
        from routing.pytorch_gnn import MetricsTracker
        self.metrics = MetricsTracker()
        self.metrics_history: list[dict] = []

    # ── Feature engineering (identical to T-GCN) ─────────────────────────
    def _build_node_features(self, rsu_states: dict[str, Any]) -> np.ndarray:
        features = np.zeros((self.num_nodes, self.config.node_feature_dim), dtype=np.float32)
        counts = np.array([rsu_states.get(nid, {}).get("vehicle_count", 0) for nid in self.rsu_ids], dtype=np.float32)
        speeds = np.array([rsu_states.get(nid, {}).get("avg_speed", 14.0) for nid in self.rsu_ids], dtype=np.float32)
        max_count = max(counts.max(), 1.0)
        features[:, 0] = counts / max_count
        features[:, 1] = np.clip(speeds / 14.0, 0, 2)
        features[:, 2] = np.array([float(rsu_states.get(nid, {}).get("incident_flag", False)) for nid in self.rsu_ids])
        # Spatial gradient via adjacency
        neighbor_density = (self.adj @ features[:, 0]) / np.maximum(self.adj.sum(axis=1), 1)
        features[:, 3] = neighbor_density
        return features

    # ── Predict ──────────────────────────────────────────────────────────
    def predict(self, rsu_states: dict[str, Any]) -> dict:
        features = self._build_node_features(rsu_states)
        self.seq_buffer.append(features)
        self._step += 1

        if self._step % self.config.inference_every_n_steps != 0 and self._cached_pred is not None:
            return self._make_response(self._cached_pred, rsu_states)

        if len(self.seq_buffer) < self.config.sequence_length:
            pred = np.zeros(self.num_nodes, dtype=np.float32)
            self._cached_pred = pred
            return self._make_response(pred, rsu_states)

        seq = np.array(list(self.seq_buffer))   # [seq_len, nodes, features]
        x = torch.FloatTensor(seq).unsqueeze(0).to(self.device)

        self.model.eval()
        with torch.no_grad():
            out = self.model(x).squeeze(0).squeeze(-1).cpu().numpy()  # [nodes]

        # EMA smoothing
        if self.config.use_ema_smoothing:
            if self._ema is None:
                self._ema = out.copy()
            else:
                self._ema = self.config.ema_alpha * out + (1 - self.config.ema_alpha) * self._ema
            pred = self._ema
        else:
            pred = out

        self._cached_pred = pred
        return self._make_response(pred, rsu_states)

    def _make_response(self, pred: np.ndarray, rsu_states: dict) -> dict:
        congested = {
            nid: float(pred[i])
            for i, nid in enumerate(self.rsu_ids)
            if pred[i] > self.config.congestion_threshold
        }
        return {
            "congestion_probabilities": {nid: float(pred[i]) for i, nid in enumerate(self.rsu_ids)},
            "high_risk_rsus": congested,
            "step": self._step,
        }

    # ── Training ─────────────────────────────────────────────────────────
    def train_step(self, rsu_states: dict[str, Any], congestion_ground_truth: dict[str, float]) -> float:
        if len(self.seq_buffer) < self.config.sequence_length:
            return 0.0

        seq = np.array(list(self.seq_buffer))
        target = np.array([congestion_ground_truth.get(nid, 0.0) for nid in self.rsu_ids], dtype=np.float32)
        self.replay.push(seq, target)

        if self._step % self.config.train_every_n_steps != 0 or len(self.replay) < self.config.batch_size:
            return 0.0

        states, targets = self.replay.sample(self.config.batch_size)
        x = torch.FloatTensor(states).to(self.device)        # [B, seq, nodes, feat]
        y = torch.FloatTensor(targets).to(self.device)       # [B, nodes]

        self.model.train()
        self.optimizer.zero_grad()
        out = self.model(x).squeeze(-1)                      # [B, nodes]
        loss = F.mse_loss(out, y)
        loss.backward()
        torch.nn.utils.clip_grad_norm_(self.model.parameters(), 1.0)
        self.optimizer.step()
        self.scheduler.step()

        loss_val = float(loss.item())
        self.metrics.update(out.detach().cpu().numpy().flatten(),
                            y.cpu().numpy().flatten(), loss_val)
        return loss_val

    def get_metrics(self) -> dict:
        return self.metrics.get_all_metrics()
