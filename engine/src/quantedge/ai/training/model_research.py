"""
Model Research and Multi-Candidate Benchmarking Engine.

Trains and compares candidate multi-output architectures strictly on Train
and evaluates on Validation:
- Baseline A: Deterministic SMC Only
- Baseline B: Linear / Ridge Multi-Output Regressor
- Baseline C: Phase C Multi-Output Random Forest (100 trees, depth 8)
- Baseline D: Regularized Extra-Trees Regressor (100 trees, depth 6, leaf 5)
- Baseline E: Multi-Output HistGradientBoosting Regressor

Includes hyperparameter tuning on validation to select the strongest generalizer.
"""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
import numpy as np
import pandas as pd
from sklearn.ensemble import ExtraTreesRegressor, HistGradientBoostingRegressor, RandomForestRegressor
from sklearn.linear_model import Ridge
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.multioutput import MultiOutputRegressor

from quantedge.ai.feature_contract import FEATURE_NAMES
from quantedge.ai.evaluation.smc_baseline import PerformanceMetrics, calculate_performance_metrics
from quantedge.ai.training.real_dataset_builder import REAL_TARGET_NAMES, TARGET_REALIZED_R


@dataclass
class CandidateModelEvaluation:
    """Evaluation summary for a candidate model architecture."""
    model_name: str
    val_r2_realized: float
    val_mae_realized: float
    val_mse_realized: float
    val_expectancy_r: float
    val_profit_factor: float
    val_win_rate_pct: float
    val_coverage_pct: float
    val_max_drawdown_r: float
    validation_fitness_score: float
    model_instance: Any


def train_and_evaluate_candidates(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    threshold_r: float = 0.0,
    seed: int = 42,
) -> Dict[str, CandidateModelEvaluation]:
    """
    Trains candidate ML architectures on Train split and evaluates them on Validation split.
    Does NOT touch out-of-sample data.
    """
    X_train = train_df[FEATURE_NAMES].values
    y_train = train_df[REAL_TARGET_NAMES].values
    X_val = val_df[FEATURE_NAMES].values
    y_val = val_df[REAL_TARGET_NAMES].values

    val_smc = calculate_performance_metrics(val_df)

    candidates = {
        "Ridge_Linear": MultiOutputRegressor(Ridge(alpha=10.0, random_state=seed)),
        "Random_Forest_Base": MultiOutputRegressor(
            RandomForestRegressor(n_estimators=100, max_depth=8, min_samples_leaf=3, max_features=0.7, random_state=seed, n_jobs=-1)
        ),
        "Extra_Trees_Regularized": MultiOutputRegressor(
            ExtraTreesRegressor(n_estimators=100, max_depth=6, min_samples_leaf=5, max_features=0.6, random_state=seed, n_jobs=-1)
        ),
        "Hist_Gradient_Boosting": MultiOutputRegressor(
            HistGradientBoostingRegressor(max_iter=50, max_depth=5, min_samples_leaf=10, random_state=seed)
        ),
    }

    evaluations = {}

    for name, model in candidates.items():
        model.fit(X_train, y_train)
        preds_val = model.predict(X_val)

        r2_val = float(r2_score(y_val[:, 0], preds_val[:, 0]))
        mae_val = float(mean_absolute_error(y_val[:, 0], preds_val[:, 0]))
        mse_val = float(mean_squared_error(y_val[:, 0], preds_val[:, 0]))

        # Filter setups on validation split
        mask_val = preds_val[:, 0] >= threshold_r
        val_ai_df = val_df[mask_val]
        metrics_ai = calculate_performance_metrics(val_ai_df, total_eligible_setups=len(val_df))

        # Validation Fitness = Expectancy * sqrt(Coverage/100) * DD_Penalty
        dd_ok = metrics_ai.max_drawdown_r <= (val_smc.max_drawdown_r * 1.25)
        dd_factor = 1.0 if dd_ok else 0.5
        coverage_factor = np.sqrt(max(0.01, metrics_ai.coverage_pct / 100.0))
        fitness = (metrics_ai.expectancy_r * coverage_factor * dd_factor) if metrics_ai.executed_setups >= 5 else -999.0

        evaluations[name] = CandidateModelEvaluation(
            model_name=name,
            val_r2_realized=round(r2_val, 4),
            val_mae_realized=round(mae_val, 4),
            val_mse_realized=round(mse_val, 4),
            val_expectancy_r=round(metrics_ai.expectancy_r, 4),
            val_profit_factor=round(metrics_ai.profit_factor, 3),
            val_win_rate_pct=round(metrics_ai.win_rate_pct, 1),
            val_coverage_pct=round(metrics_ai.coverage_pct, 1),
            val_max_drawdown_r=round(metrics_ai.max_drawdown_r, 2),
            validation_fitness_score=round(float(fitness), 4),
            model_instance=model,
        )

    return evaluations


def run_hyperparameter_search(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    seed: int = 42,
) -> Tuple[Dict[str, Any], CandidateModelEvaluation]:
    """
    Performs focused grid search over regularization hyperparameters
    using strictly Train and Validation splits.
    """
    X_train = train_df[FEATURE_NAMES].values
    y_train = train_df[REAL_TARGET_NAMES].values
    X_val = val_df[FEATURE_NAMES].values
    y_val = val_df[REAL_TARGET_NAMES].values

    val_smc = calculate_performance_metrics(val_df)

    param_grid = [
        {"max_depth": 4, "min_samples_leaf": 5, "max_features": 0.5, "n_estimators": 100},
        {"max_depth": 6, "min_samples_leaf": 4, "max_features": 0.6, "n_estimators": 100},
        {"max_depth": 8, "min_samples_leaf": 3, "max_features": 0.7, "n_estimators": 100},
        {"max_depth": 10, "min_samples_leaf": 2, "max_features": 0.8, "n_estimators": 100},
    ]

    best_params = param_grid[1]
    best_eval: Optional[CandidateModelEvaluation] = None
    best_fitness = -9999.0

    for params in param_grid:
        rf = MultiOutputRegressor(
            RandomForestRegressor(
                n_estimators=params["n_estimators"],
                max_depth=params["max_depth"],
                min_samples_leaf=params["min_samples_leaf"],
                max_features=params["max_features"],
                random_state=seed,
                n_jobs=-1,
            )
        )
        rf.fit(X_train, y_train)
        preds_v = rf.predict(X_val)

        r2_v = float(r2_score(y_val[:, 0], preds_v[:, 0]))
        mae_v = float(mean_absolute_error(y_val[:, 0], preds_v[:, 0]))
        mse_v = float(mean_squared_error(y_val[:, 0], preds_v[:, 0]))

        mask_v = preds_v[:, 0] >= 0.0
        ai_df = val_df[mask_v]
        m = calculate_performance_metrics(ai_df, total_eligible_setups=len(val_df))

        dd_ok = m.max_drawdown_r <= (val_smc.max_drawdown_r * 1.25)
        dd_factor = 1.0 if dd_ok else 0.5
        coverage_factor = np.sqrt(max(0.01, m.coverage_pct / 100.0))
        fitness = m.expectancy_r * coverage_factor * dd_factor

        cand_eval = CandidateModelEvaluation(
            model_name=f"RF_depth{params['max_depth']}_leaf{params['min_samples_leaf']}",
            val_r2_realized=round(r2_v, 4),
            val_mae_realized=round(mae_v, 4),
            val_mse_realized=round(mse_v, 4),
            val_expectancy_r=round(m.expectancy_r, 4),
            val_profit_factor=round(m.profit_factor, 3),
            val_win_rate_pct=round(m.win_rate_pct, 1),
            val_coverage_pct=round(m.coverage_pct, 1),
            val_max_drawdown_r=round(m.max_drawdown_r, 2),
            validation_fitness_score=round(float(fitness), 4),
            model_instance=rf,
        )

        if fitness > best_fitness:
            best_fitness = fitness
            best_params = params
            best_eval = cand_eval

    return best_params, best_eval  # type: ignore
