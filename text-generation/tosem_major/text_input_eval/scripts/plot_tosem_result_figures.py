from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.lines import Line2D


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DATA_DIR = PROJECT_ROOT / "output" / "tosem_rq_metrics"
SUMMARY_CSV = DATA_DIR / "rq_metric_summary.csv"
FIGURE_DIR = (
    Path(os.environ["TOSEM_FIGURE_DIR"])
    if "TOSEM_FIGURE_DIR" in os.environ
    else PROJECT_ROOT
    / "论文及审稿意见"
    / "Android_Empirical_Study"
    / "figures"
    / "results"
)

SETTING_ORDER = ("extracted", "xml", "vision")
SETTING_LABEL = {
    "extracted": "Extracted\nContext",
    "xml": "UI Hierarchy\nXML",
    "vision": "Screenshot\nVision",
}
SETTING_LABEL_INLINE = {
    "extracted": "Extracted Context",
    "xml": "UI Hierarchy XML",
    "vision": "Screenshot Vision",
}
SETTING_COLOR = {
    "extracted": "#222222",
    "xml": "#0072B2",
    "vision": "#D55E00",
}
SETTING_MARKER = {
    "extracted": "D",
    "xml": "s",
    "vision": "o",
}

METHOD_COLOR = {
    "one_shot": "#555555",
    "feedback": "#D55E00",
}
METHOD_MARKER = {
    "one_shot": "D",
    "feedback": "o",
}
METHOD_LABEL = {
    "one_shot": "One-shot",
    "feedback": "Feedback",
}

MODEL_ORDER = (
    "gemini-3.1-pro-preview",
    "claude-opus-4-6",
    "grok-4.20-reasoning",
    "gpt-5.4-2026-03-05",
    "Doubao-Seed-2.0-pro",
    "qwen3.5-plus",
    "baidu/ernie-5.0-thinking-preview",
    "kimi/kimi-k2.5",
    "mistralai/mistral-large-2512",
)
MODEL_LABEL = {
    "gemini-3.1-pro-preview": "Gemini",
    "claude-opus-4-6": "Claude",
    "grok-4.20-reasoning": "Grok",
    "gpt-5.4-2026-03-05": "GPT",
    "Doubao-Seed-2.0-pro": "Doubao",
    "qwen3.5-plus": "Qwen",
    "baidu/ernie-5.0-thinking-preview": "ERNIE",
    "kimi/kimi-k2.5": "Kimi",
    "mistralai/mistral-large-2512": "Mistral",
}
MODEL_KEYS = ("Average", *MODEL_ORDER)
MODEL_Y_LABELS = ("Average", *(MODEL_LABEL[model] for model in MODEL_ORDER))

TASK_LABEL = {
    "rq1_valid": "RQ1.1 Valid",
    "rq1_invalid": "RQ1.2 Invalid",
    "rq2_valid_feedback": "RQ2.1 Valid",
    "rq2_invalid_feedback": "RQ2.2 Invalid",
}


@dataclass(frozen=True)
class EffectivenessSpec:
    experiment: str
    metrics: tuple[tuple[str, str], ...]
    xlim: tuple[float, float]
    stem: str
    title: str


@dataclass(frozen=True)
class FeedbackSpec:
    baseline_experiment: str
    feedback_experiment: str
    metrics: tuple[tuple[str, str], ...]
    xlim: tuple[float, float]
    stem: str
    title: str
    note: str


RQ1_VALID_SPEC = EffectivenessSpec(
    experiment="rq1_valid",
    metrics=(("@1", "PPTR@1"), ("@2", "PPTR@2"), ("@3", "PPTR@3")),
    xlim=(0, 90),
    stem="rq1_valid_setting_cumulative_point_lines",
    title="RQ1.1 Valid-Input Effectiveness Across Prompt Settings",
)
RQ1_INVALID_SPEC = EffectivenessSpec(
    experiment="rq1_invalid",
    metrics=(
        ("@1", "BugReveal@1"),
        ("@5", "BugReveal@5"),
        ("@10", "BugReveal@10"),
        ("@30", "BugReveal@30"),
    ),
    xlim=(0, 85),
    stem="rq1_invalid_setting_cumulative_point_lines",
    title="RQ1.2 Invalid-Input Effectiveness Across Prompt Settings",
)
RQ2_VALID_SETTING_SPEC = EffectivenessSpec(
    experiment="rq2_valid_feedback",
    metrics=(("@1", "PPTR@1"), ("@2", "PPTR@2"), ("@3", "PPTR@3")),
    xlim=(0, 90),
    stem="rq2_valid_feedback_setting_cumulative_point_lines",
    title="RQ2.1 Feedback Effectiveness Across Initial Prompt Settings",
)
RQ2_INVALID_SETTING_SPEC = EffectivenessSpec(
    experiment="rq2_invalid_feedback",
    metrics=(
        ("@1", "BugReveal@1"),
        ("@5", "BugReveal@5"),
        ("@10", "BugReveal@10"),
        ("@30", "BugReveal@30"),
    ),
    xlim=(0, 85),
    stem="rq2_invalid_feedback_setting_cumulative_point_lines",
    title="RQ2.2 Feedback Effectiveness Across Initial Prompt Settings",
)
RQ2_VALID_SPEC = FeedbackSpec(
    baseline_experiment="rq1_valid",
    feedback_experiment="rq2_valid_feedback",
    metrics=(("@1", "PPTR@1"), ("@2", "PPTR@2"), ("@3", "PPTR@3")),
    xlim=(0, 90),
    stem="rq2_valid_feedback_paired_stage_point_lines",
    title="RQ2.1 One-Shot vs Feedback for Valid Inputs",
    note=(
        "Rows denote the initial setting; feedback turns after @1 use "
        "text-only corrective prompts."
    ),
)
RQ2_INVALID_SPEC = FeedbackSpec(
    baseline_experiment="rq1_invalid",
    feedback_experiment="rq2_invalid_feedback",
    metrics=(
        ("@1", "BugReveal@1"),
        ("@5", "BugReveal@5"),
        ("@10", "BugReveal@10"),
        ("@30", "BugReveal@30"),
    ),
    xlim=(0, 85),
    stem="rq2_invalid_feedback_paired_stage_point_lines",
    title="RQ2.2 One-Shot vs Feedback for Invalid Inputs",
    note=(
        "Feedback may accelerate discovery without necessarily expanding the "
        "final bug-detection set."
    ),
)


def setup_style() -> None:
    plt.rcParams.update(
        {
            "font.family": "DejaVu Sans",
            "font.size": 8,
            "axes.titlesize": 9,
            "axes.labelsize": 8,
            "xtick.labelsize": 8,
            "ytick.labelsize": 8,
            "legend.fontsize": 7,
            "figure.dpi": 150,
            "savefig.dpi": 300,
            "pdf.fonttype": 42,
            "ps.fonttype": 42,
            "svg.fonttype": "none",
            "axes.spines.top": True,
            "axes.spines.right": True,
        }
    )


def read_summary() -> pd.DataFrame:
    summary = pd.read_csv(SUMMARY_CSV)
    return summary[
        summary["model"].isin(MODEL_ORDER)
        & summary["prompt_setting"].isin(SETTING_ORDER)
    ].copy()


def metric_rate_pct(row: pd.Series, metric: str) -> float:
    if metric == "PPTR@2":
        return (float(row["PPTR@1"]) + float(row["NewPPTR_2"])) * 100.0
    return float(row[metric]) * 100.0


def format_token_tick(value: float) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}M"
    if value >= 1000:
        return f"{value / 1000:g}k"
    return f"{value:g}"


def model_y_positions() -> tuple[np.ndarray, dict[str, int]]:
    positions = np.arange(len(MODEL_KEYS))
    return positions, {model: i for i, model in enumerate(MODEL_KEYS)}


def add_average_rows(
    data: pd.DataFrame,
    group_cols: Sequence[str],
    value_cols: Sequence[str],
) -> pd.DataFrame:
    avg_rows = []
    for keys, group in data.groupby(list(group_cols), sort=False):
        if not isinstance(keys, tuple):
            keys = (keys,)
        row = dict(zip(group_cols, keys))
        row.update(
            {
                "model": "Average",
                "model_label": "Average",
            }
        )
        for col in value_cols:
            row[col] = float(group[col].mean(skipna=True))
        avg_rows.append(row)
    return pd.concat([data, pd.DataFrame(avg_rows)], ignore_index=True)


def style_panel(
    ax: plt.Axes,
    *,
    show_ylabels: bool,
    xlabel: str | None = None,
    title: str | None = None,
    xlim: tuple[float, float] | None = None,
    log_scale: bool = False,
) -> None:
    ax.set_box_aspect(1)
    ax.grid(axis="x", color="#d9d9d9", linewidth=0.6, linestyle="--")
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_linewidth(0.8)
        spine.set_color("#222222")
    ax.axhline(0.5, color="#777777", linewidth=0.5, linestyle=":", zorder=0)
    ax.set_ylim(len(MODEL_KEYS) - 0.5, -0.5)
    ax.set_yticks(np.arange(len(MODEL_KEYS)))
    if show_ylabels:
        ax.set_yticklabels(MODEL_Y_LABELS)
        ax.tick_params(axis="y", labelleft=True)
    else:
        ax.set_yticklabels([])
        ax.tick_params(axis="y", labelleft=False)
    if title:
        ax.set_title(title)
    if xlabel:
        ax.set_xlabel(xlabel)
    if xlim:
        ax.set_xlim(xlim)
    if log_scale:
        ax.set_xscale("log")


def save_all(fig: plt.Figure, stem: str) -> None:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("png", "pdf", "svg"):
        fig.savefig(FIGURE_DIR / f"{stem}.{suffix}", bbox_inches="tight")


def setting_legend() -> list[Line2D]:
    return [
        Line2D(
            [0],
            [0],
            marker=SETTING_MARKER[setting],
            color="none",
            markerfacecolor=SETTING_COLOR[setting],
            markeredgecolor="white",
            markeredgewidth=0.5,
            markersize=6,
            label=SETTING_LABEL_INLINE[setting],
        )
        for setting in SETTING_ORDER
    ]


def method_legend() -> list[Line2D]:
    return [
        Line2D(
            [0],
            [0],
            marker=METHOD_MARKER[method],
            color="none",
            markerfacecolor=METHOD_COLOR[method],
            markeredgecolor="white",
            markeredgewidth=0.5,
            markersize=6,
            label=METHOD_LABEL[method],
        )
        for method in ("one_shot", "feedback")
    ]


def plot_setting_pointline_panel(
    ax: plt.Axes,
    data: pd.DataFrame,
    *,
    value_col: str,
    show_ylabels: bool,
    xlabel: str,
    title: str,
    xlim: tuple[float, float] | None = None,
    log_scale: bool = False,
) -> None:
    style_panel(
        ax,
        show_ylabels=show_ylabels,
        xlabel=xlabel,
        title=title,
        xlim=xlim,
        log_scale=log_scale,
    )
    y_positions, y_by_model = model_y_positions()
    for model in MODEL_KEYS:
        rows = data[data["model"] == model]
        values = [
            float(rows[rows["prompt_setting"] == setting][value_col].iloc[0])
            for setting in SETTING_ORDER
        ]
        plot_values = [max(v, 1e-9) if log_scale else v for v in values]
        ax.plot(
            plot_values,
            [y_by_model[model]] * len(plot_values),
            color="#b8b8b8",
            linewidth=0.8,
            zorder=1,
        )

    for setting in SETTING_ORDER:
        xs = []
        for model in MODEL_KEYS:
            rows = data[(data["model"] == model) & (data["prompt_setting"] == setting)]
            value = float(rows[value_col].iloc[0])
            xs.append(max(value, 1e-9) if log_scale else value)
        ax.scatter(
            xs,
            y_positions,
            s=34,
            marker=SETTING_MARKER[setting],
            facecolor=SETTING_COLOR[setting],
            edgecolor="white",
            linewidth=0.5,
            zorder=3,
        )


def plot_paired_pointline_panel(
    ax: plt.Axes,
    data: pd.DataFrame,
    *,
    value_col: str,
    show_ylabels: bool,
    xlabel: str,
    title: str,
    xlim: tuple[float, float] | None = None,
    log_scale: bool = False,
) -> None:
    style_panel(
        ax,
        show_ylabels=show_ylabels,
        xlabel=xlabel,
        title=title,
        xlim=xlim,
        log_scale=log_scale,
    )
    y_positions, y_by_model = model_y_positions()
    for model in MODEL_KEYS:
        rows = data[data["model"] == model]
        values = [
            float(rows[rows["method"] == method][value_col].iloc[0])
            for method in ("one_shot", "feedback")
        ]
        plot_values = [max(value, 1e-9) if log_scale else value for value in values]
        ax.plot(
            plot_values,
            [y_by_model[model]] * len(plot_values),
            color="#b8b8b8",
            linewidth=0.8,
            zorder=1,
        )

    for method in ("one_shot", "feedback"):
        xs = []
        for model in MODEL_KEYS:
            value = float(
                data[(data["model"] == model) & (data["method"] == method)][
                    value_col
                ].iloc[0]
            )
            xs.append(max(value, 1e-9) if log_scale else value)
        ax.scatter(
            xs,
            y_positions,
            s=34,
            marker=METHOD_MARKER[method],
            facecolor=METHOD_COLOR[method],
            edgecolor="white",
            linewidth=0.5,
            zorder=3,
        )


def build_effectiveness_data(summary: pd.DataFrame, spec: EffectivenessSpec) -> pd.DataFrame:
    subset = summary[summary["experiment"] == spec.experiment]
    rows = []
    for _, row in subset.iterrows():
        for cutoff_label, metric in spec.metrics:
            rows.append(
                {
                    "experiment": spec.experiment,
                    "cutoff_label": cutoff_label,
                    "metric": metric,
                    "model": row["model"],
                    "model_label": MODEL_LABEL[row["model"]],
                    "prompt_setting": row["prompt_setting"],
                    "value": metric_rate_pct(row, metric),
                }
            )
    data = pd.DataFrame(rows)
    return add_average_rows(
        data,
        group_cols=("experiment", "cutoff_label", "metric", "prompt_setting"),
        value_cols=("value",),
    )


def plot_rq1_effectiveness(summary: pd.DataFrame, spec: EffectivenessSpec) -> pd.DataFrame:
    data = build_effectiveness_data(summary, spec)
    ncols = len(spec.metrics)
    panel = 2.25
    fig, axes = plt.subplots(
        1,
        ncols,
        figsize=(panel * ncols + 1.2, panel + 1.25),
        squeeze=False,
    )
    for col_idx, (cutoff_label, metric) in enumerate(spec.metrics):
        ax = axes[0][col_idx]
        subset = data[data["metric"] == metric]
        plot_setting_pointline_panel(
            ax,
            subset,
            value_col="value",
            show_ylabels=(col_idx == 0),
            xlabel="Effectiveness (%)",
            title=cutoff_label,
            xlim=spec.xlim,
        )
    fig.suptitle(spec.title, y=0.98, fontsize=10, fontweight="bold")
    fig.legend(
        handles=setting_legend(),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=3,
        frameon=False,
    )
    fig.subplots_adjust(left=0.15, right=0.99, top=0.78, bottom=0.24, wspace=0.18)
    save_all(fig, spec.stem)
    plt.close(fig)
    return data


def build_overhead_data(
    summary: pd.DataFrame,
    experiments: Sequence[str],
    value_cols: Sequence[str],
) -> pd.DataFrame:
    subset = summary[summary["experiment"].isin(experiments)]
    rows = []
    for _, row in subset.iterrows():
        for value_col in value_cols:
            rows.append(
                {
                    "experiment": row["experiment"],
                    "model": row["model"],
                    "model_label": MODEL_LABEL[row["model"]],
                    "prompt_setting": row["prompt_setting"],
                    "metric": value_col,
                    "value": float(row[value_col]),
                }
            )
    data = pd.DataFrame(rows)
    return add_average_rows(
        data,
        group_cols=("experiment", "prompt_setting", "metric"),
        value_cols=("value",),
    )


def log_xlim(values: Iterable[float]) -> tuple[float, float]:
    positives = [float(v) for v in values if pd.notna(v) and float(v) > 0]
    if not positives:
        return (1, 10)
    low = 10 ** np.floor(np.log10(min(positives) * 0.75))
    high = 10 ** np.ceil(np.log10(max(positives) * 1.25))
    return (max(low, 1), high)


def token_ticks(xlim: tuple[float, float]) -> list[float]:
    if xlim[1] <= 30000:
        candidates = [50, 100, 1000, 10000, 30000]
    else:
        candidates = [50, 100, 1000, 10000, 150000]
    return [tick for tick in candidates if xlim[0] <= tick <= xlim[1]]


def token_xlim_for_experiment(experiment: str) -> tuple[float, float]:
    if "invalid" in experiment:
        return (50, 150000)
    return (50, 30000)


def plot_token_overhead(
    summary: pd.DataFrame,
    *,
    experiments: Sequence[str],
    stem: str,
    title: str,
) -> pd.DataFrame:
    token_cols = (
        "prompt_tokens_per_call",
        "completion_tokens_per_call",
        "total_tokens_per_call",
    )
    token_labels = {
        "prompt_tokens_per_call": "Input Tokens",
        "completion_tokens_per_call": "Output Tokens",
        "total_tokens_per_call": "Total Tokens",
    }
    data = build_overhead_data(summary, experiments, token_cols)
    panel = 2.25
    fig, axes = plt.subplots(
        len(experiments),
        len(token_cols),
        figsize=(panel * len(token_cols) + 1.2, panel * len(experiments) + 1.45),
        squeeze=False,
    )
    for row_idx, experiment in enumerate(experiments):
        for col_idx, metric in enumerate(token_cols):
            ax = axes[row_idx][col_idx]
            subset = data[
                (data["experiment"] == experiment) & (data["metric"] == metric)
            ]
            xlim = token_xlim_for_experiment(experiment)
            plot_setting_pointline_panel(
                ax,
                subset,
                value_col="value",
                show_ylabels=(col_idx == 0),
                xlabel="Tokens / attempt" if row_idx == len(experiments) - 1 else "",
                title=token_labels[metric] if row_idx == 0 else "",
                xlim=xlim,
                log_scale=True,
            )
            ticks = token_ticks(xlim)
            ax.set_xticks(ticks)
            ax.set_xticklabels([format_token_tick(tick) for tick in ticks])
            ax.tick_params(axis="x", labelsize=7)
            if col_idx == 0:
                ax.text(
                    -0.42,
                    0.5,
                    TASK_LABEL[experiment],
                    rotation=90,
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                    fontsize=8,
                    fontweight="bold",
                )
    fig.suptitle(title, y=0.985, fontsize=10, fontweight="bold")
    fig.legend(
        handles=setting_legend(),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=3,
        frameon=False,
    )
    fig.subplots_adjust(left=0.16, right=0.99, top=0.86, bottom=0.18, wspace=0.18, hspace=0.32)
    save_all(fig, stem)
    plt.close(fig)
    return data


def plot_single_metric_setting_grid(
    summary: pd.DataFrame,
    *,
    experiments: Sequence[str],
    value_col: str,
    transform,
    stem: str,
    title: str,
    xlabel: str,
    xlims: dict[str, tuple[float, float]] | None = None,
) -> pd.DataFrame:
    subset = summary[summary["experiment"].isin(experiments)].copy()
    subset["_plot_value"] = subset[value_col].map(transform)
    rows = []
    for _, row in subset.iterrows():
        rows.append(
            {
                "experiment": row["experiment"],
                "model": row["model"],
                "model_label": MODEL_LABEL[row["model"]],
                "prompt_setting": row["prompt_setting"],
                "value": float(row["_plot_value"]),
            }
        )
    data = add_average_rows(
        pd.DataFrame(rows),
        group_cols=("experiment", "prompt_setting"),
        value_cols=("value",),
    )
    panel = 2.35
    fig, axes = plt.subplots(
        1,
        len(experiments),
        figsize=(panel * len(experiments) + 1.3, panel + 1.15),
        squeeze=False,
    )
    for col_idx, experiment in enumerate(experiments):
        ax = axes[0][col_idx]
        exp_data = data[data["experiment"] == experiment]
        xlim = None
        if xlims:
            xlim = xlims.get(experiment)
        elif exp_data["value"].notna().any():
            high = float(exp_data["value"].max()) * 1.15
            xlim = (0, high if high > 0 else 1)
        plot_setting_pointline_panel(
            ax,
            exp_data,
            value_col="value",
            show_ylabels=(col_idx == 0),
            xlabel=xlabel,
            title=TASK_LABEL[experiment],
            xlim=xlim,
        )
    fig.suptitle(title, y=0.98, fontsize=10, fontweight="bold")
    fig.legend(
        handles=setting_legend(),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=3,
        frameon=False,
    )
    fig.subplots_adjust(left=0.16, right=0.99, top=0.78, bottom=0.24, wspace=0.18)
    save_all(fig, stem)
    plt.close(fig)
    return data


def build_feedback_effectiveness_data(summary: pd.DataFrame, spec: FeedbackSpec) -> pd.DataFrame:
    rows = []
    for model in MODEL_ORDER:
        for setting in SETTING_ORDER:
            baseline = summary[
                (summary["experiment"] == spec.baseline_experiment)
                & (summary["model"] == model)
                & (summary["prompt_setting"] == setting)
            ]
            feedback = summary[
                (summary["experiment"] == spec.feedback_experiment)
                & (summary["model"] == model)
                & (summary["prompt_setting"] == setting)
            ]
            if baseline.empty or feedback.empty:
                continue
            baseline_row = baseline.iloc[0]
            feedback_row = feedback.iloc[0]
            for cutoff_label, metric in spec.metrics:
                rows.append(
                    {
                        "baseline_experiment": spec.baseline_experiment,
                        "feedback_experiment": spec.feedback_experiment,
                        "cutoff_label": cutoff_label,
                        "metric": metric,
                        "model": model,
                        "model_label": MODEL_LABEL[model],
                        "prompt_setting": setting,
                        "method": "one_shot",
                        "value": metric_rate_pct(baseline_row, metric),
                    }
                )
                rows.append(
                    {
                        "baseline_experiment": spec.baseline_experiment,
                        "feedback_experiment": spec.feedback_experiment,
                        "cutoff_label": cutoff_label,
                        "metric": metric,
                        "model": model,
                        "model_label": MODEL_LABEL[model],
                        "prompt_setting": setting,
                        "method": "feedback",
                        "value": metric_rate_pct(feedback_row, metric),
                    }
                )
    data = pd.DataFrame(rows)
    return add_average_rows(
        data,
        group_cols=(
            "baseline_experiment",
            "feedback_experiment",
            "cutoff_label",
            "metric",
            "prompt_setting",
            "method",
        ),
        value_cols=("value",),
    )


def build_feedback_overhead_comparison_data(
    summary: pd.DataFrame,
    metric_defs: Sequence[tuple[str, str, str, object]],
) -> pd.DataFrame:
    pairs = (
        ("rq1_valid", "rq2_valid_feedback"),
        ("rq1_invalid", "rq2_invalid_feedback"),
    )
    rows = []
    for baseline_experiment, feedback_experiment in pairs:
        for model in MODEL_ORDER:
            for setting in SETTING_ORDER:
                baseline = summary[
                    (summary["experiment"] == baseline_experiment)
                    & (summary["model"] == model)
                    & (summary["prompt_setting"] == setting)
                ]
                feedback = summary[
                    (summary["experiment"] == feedback_experiment)
                    & (summary["model"] == model)
                    & (summary["prompt_setting"] == setting)
                ]
                if baseline.empty or feedback.empty:
                    continue
                for metric, column, metric_label, transform in metric_defs:
                    for method, row in (
                        ("one_shot", baseline.iloc[0]),
                        ("feedback", feedback.iloc[0]),
                    ):
                        rows.append(
                            {
                                "baseline_experiment": baseline_experiment,
                                "feedback_experiment": feedback_experiment,
                                "task_pair": feedback_experiment,
                                "model": model,
                                "model_label": MODEL_LABEL[model],
                                "prompt_setting": setting,
                                "method": method,
                                "metric": metric,
                                "metric_label": metric_label,
                                "value": float(transform(row[column])),
                            }
                        )
    data = pd.DataFrame(rows)
    return add_average_rows(
        data,
        group_cols=(
            "baseline_experiment",
            "feedback_experiment",
            "task_pair",
            "prompt_setting",
            "method",
            "metric",
            "metric_label",
        ),
        value_cols=("value",),
    )


def plot_feedback_effectiveness(summary: pd.DataFrame, spec: FeedbackSpec) -> pd.DataFrame:
    data = build_feedback_effectiveness_data(summary, spec)
    ncols = len(spec.metrics)
    panel = 2.15
    fig, axes = plt.subplots(
        len(SETTING_ORDER),
        ncols,
        figsize=(panel * ncols + 1.35, panel * len(SETTING_ORDER) + 1.6),
        squeeze=False,
    )
    for row_idx, setting in enumerate(SETTING_ORDER):
        for col_idx, (cutoff_label, metric) in enumerate(spec.metrics):
            ax = axes[row_idx][col_idx]
            subset = data[
                (data["prompt_setting"] == setting) & (data["metric"] == metric)
            ]
            plot_paired_pointline_panel(
                ax,
                subset,
                value_col="value",
                show_ylabels=(col_idx == 0),
                xlabel="Effectiveness (%)" if row_idx == len(SETTING_ORDER) - 1 else "",
                title=cutoff_label if row_idx == 0 else "",
                xlim=spec.xlim,
            )
            if col_idx == 0:
                ax.text(
                    -0.42,
                    0.5,
                    f"Initial\n{SETTING_LABEL_INLINE[setting]}",
                    rotation=90,
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                    fontsize=8,
                    fontweight="bold",
                )
    fig.suptitle(spec.title, y=0.992, fontsize=10, fontweight="bold")
    fig.text(0.5, 0.945, spec.note, ha="center", va="center", fontsize=7, color="#444444")
    fig.legend(
        handles=method_legend(),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=2,
        frameon=False,
    )
    fig.subplots_adjust(left=0.20, right=0.99, top=0.86, bottom=0.13, wspace=0.12, hspace=0.28)
    save_all(fig, spec.stem)
    plt.close(fig)
    return data


def plot_feedback_overhead_comparison(
    summary: pd.DataFrame,
    *,
    task_pair: str,
    stem: str,
    title: str,
) -> pd.DataFrame:
    metric_defs = (
        ("input_tokens", "prompt_tokens_per_call", "Input Tokens", float),
        ("output_tokens", "completion_tokens_per_call", "Output Tokens", float),
        ("total_tokens", "total_tokens_per_call", "Total Tokens", float),
        (
            "latency_s",
            "elapsed_ms_per_generation_mean",
            "Latency",
            lambda value: float(value) / 1000.0,
        ),
    )
    data = build_feedback_overhead_comparison_data(summary, metric_defs)
    task_data = data[data["task_pair"] == task_pair]
    panel = 2.15
    fig, axes = plt.subplots(
        len(SETTING_ORDER),
        len(metric_defs),
        figsize=(panel * len(metric_defs) + 1.45, panel * len(SETTING_ORDER) + 1.45),
        squeeze=False,
    )
    latency_data = task_data[task_data["metric"] == "latency_s"]
    latency_high = float(latency_data["value"].max()) * 1.15
    latency_xlim = (0, latency_high if latency_high > 0 else 1)
    token_xlim = token_xlim_for_experiment(task_pair)
    for row_idx, setting in enumerate(SETTING_ORDER):
        for col_idx, (metric, _column, metric_label, _transform) in enumerate(metric_defs):
            ax = axes[row_idx][col_idx]
            subset = task_data[
                (task_data["prompt_setting"] == setting)
                & (task_data["metric"] == metric)
            ]
            is_token = metric != "latency_s"
            xlabel = (
                "Tokens / attempt"
                if is_token and row_idx == len(SETTING_ORDER) - 1
                else "Latency / attempt (s)"
                if row_idx == len(SETTING_ORDER) - 1
                else ""
            )
            plot_paired_pointline_panel(
                ax,
                subset,
                value_col="value",
                show_ylabels=(col_idx == 0),
                xlabel=xlabel,
                title=metric_label if row_idx == 0 else "",
                xlim=token_xlim if is_token else latency_xlim,
                log_scale=is_token,
            )
            if is_token:
                ticks = token_ticks(token_xlim)
                ax.set_xticks(ticks)
                ax.set_xticklabels([format_token_tick(tick) for tick in ticks])
                ax.tick_params(axis="x", labelsize=7)
            if col_idx == 0:
                ax.text(
                    -0.42,
                    0.5,
                    SETTING_LABEL_INLINE[setting],
                    rotation=90,
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                    fontsize=8,
                    fontweight="bold",
                )
    fig.suptitle(title, y=0.99, fontsize=10, fontweight="bold")
    fig.legend(
        handles=method_legend(),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=2,
        frameon=False,
    )
    fig.subplots_adjust(left=0.20, right=0.99, top=0.90, bottom=0.12, wspace=0.12, hspace=0.28)
    save_all(fig, f"{stem}__{task_pair}")
    plt.close(fig)
    return task_data


def case_level_path(experiment: str) -> Path:
    return DATA_DIR / f"{experiment}_case_level.csv"


def mean_first_success_attempt(experiment: str, model: str, setting: str) -> float:
    data = pd.read_csv(case_level_path(experiment))
    subset = data[
        (data["model"] == model)
        & (data["prompt_setting"] == setting)
        & data["first_success_index"].notna()
    ]
    if subset.empty:
        return float("nan")
    return float(subset["first_success_index"].mean())


def build_mfsa_data() -> pd.DataFrame:
    pairs = (
        ("rq1_valid", "rq2_valid_feedback"),
        ("rq1_invalid", "rq2_invalid_feedback"),
    )
    rows = []
    cache: dict[str, pd.DataFrame] = {}
    for baseline_exp, feedback_exp in pairs:
        for experiment, method in ((baseline_exp, "one_shot"), (feedback_exp, "feedback")):
            if experiment not in cache:
                cache[experiment] = pd.read_csv(case_level_path(experiment))
            case_df = cache[experiment]
            for model in MODEL_ORDER:
                for setting in SETTING_ORDER:
                    subset = case_df[
                        (case_df["model"] == model)
                        & (case_df["prompt_setting"] == setting)
                        & case_df["first_success_index"].notna()
                    ]
                    value = float(subset["first_success_index"].mean()) if len(subset) else np.nan
                    rows.append(
                        {
                            "task_pair": feedback_exp,
                            "experiment": experiment,
                            "method": method,
                            "model": model,
                            "model_label": MODEL_LABEL[model],
                            "prompt_setting": setting,
                            "value": value,
                        }
                    )
    data = pd.DataFrame(rows)
    return add_average_rows(
        data,
        group_cols=("task_pair", "experiment", "method", "prompt_setting"),
        value_cols=("value",),
    )


def build_mfsa_setting_data(
    experiments: Sequence[str] = ("rq2_valid_feedback", "rq2_invalid_feedback"),
) -> pd.DataFrame:
    rows = []
    for experiment in experiments:
        case_df = pd.read_csv(case_level_path(experiment))
        for model in MODEL_ORDER:
            for setting in SETTING_ORDER:
                subset = case_df[
                    (case_df["model"] == model)
                    & (case_df["prompt_setting"] == setting)
                    & case_df["first_success_index"].notna()
                ]
                value = float(subset["first_success_index"].mean()) if len(subset) else np.nan
                rows.append(
                    {
                        "experiment": experiment,
                        "model": model,
                        "model_label": MODEL_LABEL[model],
                        "prompt_setting": setting,
                        "value": value,
                    }
                )
    data = pd.DataFrame(rows)
    return add_average_rows(
        data,
        group_cols=("experiment", "prompt_setting"),
        value_cols=("value",),
    )


def build_attempts_per_case_feedback_data(summary: pd.DataFrame) -> pd.DataFrame:
    pairs = (
        ("rq1_valid", "rq2_valid_feedback"),
        ("rq1_invalid", "rq2_invalid_feedback"),
    )
    rows = []
    for baseline_exp, feedback_exp in pairs:
        for experiment, method in ((baseline_exp, "one_shot"), (feedback_exp, "feedback")):
            subset = summary[summary["experiment"] == experiment]
            for _, row in subset.iterrows():
                rows.append(
                    {
                        "task_pair": feedback_exp,
                        "experiment": experiment,
                        "method": method,
                        "model": row["model"],
                        "model_label": MODEL_LABEL[row["model"]],
                        "prompt_setting": row["prompt_setting"],
                        "value": float(row["actual_calls_per_case"]),
                    }
                )
    return add_average_rows(
        pd.DataFrame(rows),
        group_cols=("task_pair", "experiment", "method", "prompt_setting"),
        value_cols=("value",),
    )


def plot_mfsa() -> pd.DataFrame:
    data = build_mfsa_data()
    task_pairs = ("rq2_valid_feedback", "rq2_invalid_feedback")
    xlim_by_task = {
        "rq2_valid_feedback": (0, 3),
        "rq2_invalid_feedback": (0, 30),
    }
    row_label = {
        "rq2_valid_feedback": "RQ2.1 Valid",
        "rq2_invalid_feedback": "RQ2.2 Invalid",
    }
    panel = 2.15
    fig, axes = plt.subplots(
        len(task_pairs),
        len(SETTING_ORDER),
        figsize=(panel * len(SETTING_ORDER) + 1.35, panel * len(task_pairs) + 1.35),
        squeeze=False,
    )
    for row_idx, task_pair in enumerate(task_pairs):
        for col_idx, setting in enumerate(SETTING_ORDER):
            ax = axes[row_idx][col_idx]
            subset = data[
                (data["task_pair"] == task_pair) & (data["prompt_setting"] == setting)
            ]
            plot_paired_pointline_panel(
                ax,
                subset,
                value_col="value",
                show_ylabels=(col_idx == 0),
                xlabel="MFSA (lower is better)" if row_idx == len(task_pairs) - 1 else "",
                title=SETTING_LABEL_INLINE[setting] if row_idx == 0 else "",
                xlim=xlim_by_task[task_pair],
            )
            if col_idx == 0:
                ax.text(
                    -0.42,
                    0.5,
                    row_label[task_pair],
                    rotation=90,
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                    fontsize=8,
                    fontweight="bold",
                )
    fig.suptitle("RQ2 Feedback Efficiency: Mean First-Success Attempt", y=0.985, fontsize=10, fontweight="bold")
    fig.legend(
        handles=method_legend(),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=2,
        frameon=False,
    )
    fig.subplots_adjust(left=0.20, right=0.99, top=0.86, bottom=0.16, wspace=0.12, hspace=0.28)
    save_all(fig, "rq2_mean_first_success_attempt_point_lines")
    plt.close(fig)
    return data


def plot_mfsa_setting_comparison(
    *,
    experiments: Sequence[str],
    stem: str,
    title: str,
) -> pd.DataFrame:
    data = build_mfsa_setting_data(experiments)
    xlim_by_experiment = {
        "rq1_valid": (0, 3),
        "rq1_invalid": (0, 30),
        "rq2_valid_feedback": (0, 3),
        "rq2_invalid_feedback": (0, 30),
    }
    panel = 2.35
    fig, axes = plt.subplots(
        1,
        len(experiments),
        figsize=(panel * len(experiments) + 1.3, panel + 1.15),
        squeeze=False,
    )
    for col_idx, experiment in enumerate(experiments):
        ax = axes[0][col_idx]
        subset = data[data["experiment"] == experiment]
        plot_setting_pointline_panel(
            ax,
            subset,
            value_col="value",
            show_ylabels=(col_idx == 0),
            xlabel="MFSA (lower is better)",
            title=TASK_LABEL[experiment],
            xlim=xlim_by_experiment[experiment],
        )
    fig.suptitle(
        title,
        y=0.98,
        fontsize=10,
        fontweight="bold",
    )
    fig.legend(
        handles=setting_legend(),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=3,
        frameon=False,
    )
    fig.subplots_adjust(left=0.16, right=0.99, top=0.78, bottom=0.24, wspace=0.18)
    save_all(fig, stem)
    plt.close(fig)
    return data


def export_plot_data(datasets: dict[str, pd.DataFrame]) -> None:
    out_dir = DATA_DIR / "figure_plot_data"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stem, data in datasets.items():
        data.to_csv(out_dir / f"{stem}.csv", index=False)


def main() -> None:
    setup_style()
    summary = read_summary()
    datasets: dict[str, pd.DataFrame] = {}

    datasets[RQ1_VALID_SPEC.stem] = plot_rq1_effectiveness(summary, RQ1_VALID_SPEC)
    datasets[RQ1_INVALID_SPEC.stem] = plot_rq1_effectiveness(summary, RQ1_INVALID_SPEC)
    datasets["rq1_token_overhead_input_output_total_point_lines"] = plot_token_overhead(
        summary,
        experiments=("rq1_valid", "rq1_invalid"),
        stem="rq1_token_overhead_input_output_total_point_lines",
        title="RQ1 Token Overhead Across Prompt Settings",
    )
    datasets["rq1_mfsa_setting_point_lines"] = plot_mfsa_setting_comparison(
        experiments=("rq1_valid", "rq1_invalid"),
        stem="rq1_mfsa_setting_point_lines",
        title="RQ1 MFSA Across Prompt Settings",
    )
    datasets["rq1_latency_overhead_point_lines"] = plot_single_metric_setting_grid(
        summary,
        experiments=("rq1_valid", "rq1_invalid"),
        value_col="elapsed_ms_per_generation_mean",
        transform=lambda value: float(value) / 1000.0,
        stem="rq1_latency_overhead_point_lines",
        title="RQ1 Latency Overhead Across Prompt Settings",
        xlabel="Latency / attempt (s)",
    )

    datasets[RQ2_VALID_SPEC.stem] = plot_feedback_effectiveness(summary, RQ2_VALID_SPEC)
    datasets[RQ2_INVALID_SPEC.stem] = plot_feedback_effectiveness(summary, RQ2_INVALID_SPEC)
    datasets[RQ2_VALID_SETTING_SPEC.stem] = plot_rq1_effectiveness(summary, RQ2_VALID_SETTING_SPEC)
    datasets[RQ2_INVALID_SETTING_SPEC.stem] = plot_rq1_effectiveness(summary, RQ2_INVALID_SETTING_SPEC)
    datasets["rq2_mean_first_success_attempt_point_lines"] = plot_mfsa()
    datasets["rq2_mfsa_setting_point_lines"] = plot_mfsa_setting_comparison(
        experiments=("rq2_valid_feedback", "rq2_invalid_feedback"),
        stem="rq2_mfsa_setting_point_lines",
        title="RQ2 MFSA Across Initial Prompt Settings",
    )
    datasets["rq2_llm_calls_per_case_point_lines"] = plot_single_metric_setting_grid(
        summary,
        experiments=("rq2_valid_feedback", "rq2_invalid_feedback"),
        value_col="actual_calls_per_case",
        transform=float,
        stem="rq2_llm_calls_per_case_point_lines",
        title="RQ2 Attempts per Case Across Initial Prompt Settings",
        xlabel="Attempts/Case",
        xlims={
            "rq2_valid_feedback": (0, 3),
            "rq2_invalid_feedback": (0, 30),
        },
    )
    datasets["rq2_token_overhead_input_output_total_point_lines"] = plot_token_overhead(
        summary,
        experiments=("rq2_valid_feedback", "rq2_invalid_feedback"),
        stem="rq2_token_overhead_input_output_total_point_lines",
        title="RQ2 Token Overhead Across Initial Prompt Settings",
    )
    datasets["rq2_latency_overhead_point_lines"] = plot_single_metric_setting_grid(
        summary,
        experiments=("rq2_valid_feedback", "rq2_invalid_feedback"),
        value_col="elapsed_ms_per_generation_mean",
        transform=lambda value: float(value) / 1000.0,
        stem="rq2_latency_overhead_point_lines",
        title="RQ2 Latency Overhead Across Initial Prompt Settings",
        xlabel="Latency / attempt (s)",
    )

    export_plot_data(datasets)
    print(f"Wrote figures to {FIGURE_DIR}")
    print(f"Wrote plot data to {DATA_DIR / 'figure_plot_data'}")


if __name__ == "__main__":
    main()
