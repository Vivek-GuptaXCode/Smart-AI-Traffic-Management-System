# Experiments

This directory contains configuration files for experiments and training runs.

## Configuration Files

| File | Description |
|------|-------------|
| `phase2_forecast_config.json` | Phase 2 model training configuration |
| `phase2_data_sweep_config.json` | Data sweep experiment settings |
| `phase2_data_sweep_2x_increment.json` | Incremental data sweep |
| `phase2_data_sweep_non_demo_relaxed.json` | Relaxed sweep for non-demo scenarios |
| `training_profiles.json` | RL training profiles (smoke, medium, full) |

## Phase 2: Forecasting Configuration

### `phase2_forecast_config.json`

Controls the congestion forecasting model training:

```json
{
  "model_type": "lightgbm",
  "feature_version": "v2",
  "horizon_minutes": 5,
  "train_split": 0.7,
  "val_split": 0.15,
  "test_split": 0.15,
  "hyperparameters": {
    "n_estimators": 100,
    "max_depth": 6,
    "learning_rate": 0.1
  }
}
```

### `phase2_data_sweep_config.json`

Defines data sweep experiments to find optimal training data:

```json
{
  "run_dirs": ["run_001", "run_002", ...],
  "sweep_sizes": [10, 20, 50, 100],
  "metrics": ["accuracy", "f1", "auc"],
  "seeds": [42, 123, 456]
}
```

## Phase 4: Training Profiles

### `training_profiles.json`

Defines RL training configurations:

```json
{
  "smoke": {
    "description": "Quick validation (2-3 min)",
    "max_episodes": 10,
    "max_steps_per_episode": 120,
    "eval_episodes": 2
  },
  "medium": {
    "description": "Medium training (15-30 min)",
    "max_episodes": 50,
    "max_steps_per_episode": 600,
    "eval_episodes": 5
  },
  "full": {
    "description": "Full training (1-2 hours)",
    "max_episodes": 200,
    "max_steps_per_episode": 1800,
    "eval_episodes": 10
  }
}
```

## Usage

### Running with Configuration

```bash
# Phase 2 training with config
python3 models/forecast/train_phase2_improved.py \
  --config experiments/phase2_forecast_config.json

# Phase 4 training with profile
python3 controllers/rl/train_phase4.py \
  --profile medium
```

### Creating New Experiments

1. Copy an existing config file
2. Modify parameters as needed
3. Run with the `--config` flag

```bash
cp experiments/phase2_forecast_config.json experiments/my_experiment.json
# Edit my_experiment.json
python3 models/forecast/train_phase2_improved.py --config experiments/my_experiment.json
```

## Best Practices

- **Version configs**: Use descriptive names with dates or versions
- **Document changes**: Note what changed and why
- **Track seeds**: Always specify random seeds for reproducibility
- **Save results**: Outputs go to `evaluation/` directory
