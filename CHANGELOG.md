# Changelog

All notable changes to the Hybrid AI Traffic Management System will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- Restructured codebase for GitHub best practices
- Added comprehensive documentation across all modules
- Pinned dependency versions in requirements.txt

## [0.5.0] - 2026-04-02

### Added
- Phase 5 Hybrid Fusion Controller framework
- Ablation experiment suite with 9 configurations
- Fusion orchestrator for combining routing + signal control
- run_ablation.py for systematic ablation studies

### Changed
- Updated checklist: 95% overall completion
- Improved DQN training stability

## [0.4.0] - 2026-04-01

### Added
- Phase 4 Adaptive Signal Control with Deep Q-Network
- Improved DQN agent with Double DQN and larger network
- Safety guardrails for traffic signal control
- Training profiles for different scenarios (smoke, medium, full)

### Changed
- Enhanced traffic signal environment with multi-junction support
- Improved reward shaping for RL training

## [0.3.0] - 2026-03-30

### Added
- Phase 3 Uncertainty-Aware Risk Routing
- Confidence-based fallback mechanism
- Route audit logging (JSONL format)
- Risk score calculation combining probability and uncertainty

### Fixed
- Edge case handling in routing decisions
- Improved fallback behavior for low-confidence predictions

## [0.2.0] - 2026-03-28

### Added
- Phase 2 Congestion Forecasting Model
- LightGBM-based predictor (87.3% accuracy, 91.3% F1)
- V2 feature builder with 31 features (lags, rolling stats, dynamics)
- V3 feature builder variants with enhanced temporal features
- Forecast inference engine with uncertainty estimation

### Changed
- Improved feature engineering for temporal patterns
- Added hyperparameter sweep capabilities

## [0.1.0] - 2026-03-25

### Added
- Phase 1 Data Pipeline
- Runtime logger for 1 Hz SUMO telemetry
- Horizon labeler for future congestion labeling
- Temporal split for train/val/test with leakage validation
- Export dataset bundle functionality

### Added (Initial)
- V2X central server (Flask + SocketIO)
- SUMO simulation pipeline with TraCI/libsumo
- RSU (Road Side Unit) network graph
- Basic vehicle telemetry processing
- Demo scenario with road networks

## Project Phases

| Phase | Description | Version |
|-------|-------------|---------|
| 1 | Data Pipeline | v0.1.0 |
| 2 | Congestion Forecasting | v0.2.0 |
| 3 | Uncertainty-Aware Routing | v0.3.0 |
| 4 | Adaptive Signal Control | v0.4.0 |
| 5 | Hybrid Fusion Controller | v0.5.0 |
