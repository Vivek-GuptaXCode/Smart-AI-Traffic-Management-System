# Contributing to Hybrid AI Traffic Management System

Thank you for your interest in contributing! This document provides guidelines and instructions for contributing to the project.

## Table of Contents

- [Getting Started](#getting-started)
- [Development Setup](#development-setup)
- [Project Structure](#project-structure)
- [Code Style](#code-style)
- [Submitting Changes](#submitting-changes)
- [Running Tests](#running-tests)

## Getting Started

1. Fork the repository on GitHub
2. Clone your fork locally:
   ```bash
   git clone https://github.com/YOUR_USERNAME/final_year_project.git
   cd final_year_project
   ```
3. Add the upstream remote:
   ```bash
   git remote add upstream https://github.com/Programmerlogic/final_year_project.git
   ```

## Development Setup

### Prerequisites

- Python 3.10 or higher
- SUMO (Simulation of Urban Mobility) with TraCI or libsumo
- Git

### Installation

1. Create and activate a virtual environment:
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Verify SUMO installation:
   ```bash
   sumo --version
   ```

### Environment Variables

For development with the full feature set:
```bash
export HYBRID_ENABLE_FORECAST_MODEL=1
export HYBRID_ENABLE_PHASE3_ROUTING=1
```

## Project Structure

```
├── server.py                 # V2X central server (Flask + SocketIO)
├── controllers/              # Phase 4 & 5: RL and Fusion controllers
│   ├── rl/                   # Deep Q-Network traffic signal control
│   └── fusion/               # Hybrid fusion orchestrator
├── models/                   # Phase 2: Forecasting models
│   └── forecast/             # LightGBM congestion forecasting
├── pipelines/                # Phase 1: Data processing
│   ├── processing/           # Labeling, splitting, validation
│   └── logging/              # Runtime telemetry logging
├── routing/                  # Phase 3: Risk-aware routing
├── sumo/                     # SUMO simulation infrastructure
│   ├── networks/             # Road network files (.net.xml)
│   ├── scenarios/            # Scenario configurations
│   └── routes/               # Vehicle demand definitions
├── evaluation/               # KPI evaluation and results
├── experiments/              # Experiment configurations
├── data/                     # Datasets and logs
└── docs/                     # Documentation and reports
```

## Code Style

### Python

- Use 4 spaces for indentation (no tabs)
- Follow PEP 8 guidelines
- Maximum line length: 100 characters
- Use descriptive variable and function names
- Add docstrings to functions and classes:
  ```python
  def calculate_risk_score(probability: float, uncertainty: float) -> float:
      """
      Calculate risk score from congestion probability and uncertainty.

      Args:
          probability: Predicted congestion probability [0, 1]
          uncertainty: Model uncertainty estimate [0, 1]

      Returns:
          Risk score combining probability and uncertainty
      """
      return probability + 0.5 * uncertainty
  ```

### Commit Messages

- Use present tense ("Add feature" not "Added feature")
- Use imperative mood ("Move cursor to..." not "Moves cursor to...")
- Limit first line to 72 characters
- Reference issues and PRs where appropriate

Example:
```
Add uncertainty estimation to Phase 3 router

- Implement dropout-based uncertainty for LightGBM predictions
- Add confidence fallback mechanism for low-certainty decisions
- Update routing tests

Closes #42
```

## Submitting Changes

1. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes and commit:
   ```bash
   git add .
   git commit -m "Add your descriptive commit message"
   ```

3. Push to your fork:
   ```bash
   git push origin feature/your-feature-name
   ```

4. Open a Pull Request on GitHub

### Pull Request Guidelines

- Fill out the PR template completely
- Link related issues
- Include screenshots for UI changes
- Ensure all CI checks pass
- Request review from maintainers

## Running Tests

### Smoke Test (SUMO)

```bash
python3 sumo/run_sumo_pipeline.py --scenario demo --gui --max-steps 120
```

### Full Pipeline Test

```bash
python3 sumo/run_sumo_pipeline.py \
  --scenario demo --gui --max-steps 1800 \
  --traffic-scale 1.8 \
  --enable-hybrid-uplink-stub \
  --server-url http://127.0.0.1:5000
```

### Evaluation Scripts

```bash
# Phase 3 routing comparison
python3 evaluation/phase3_comparison.py

# Phase 4 RL evaluation
python3 evaluation/phase4_kpi_eval.py
```

## Phase Development

This project follows a phased development approach:

| Phase | Component | Status |
|-------|-----------|--------|
| 1 | Data Pipeline | Complete |
| 2 | Congestion Forecasting | Complete |
| 3 | Uncertainty-Aware Routing | Complete |
| 4 | Adaptive Signal Control (RL) | Complete |
| 5 | Hybrid Fusion Controller | In Progress |

When contributing to a specific phase, please refer to the corresponding documentation in `docs/reports/`.

## Questions?

Feel free to open an issue for questions or discussions about the project.
