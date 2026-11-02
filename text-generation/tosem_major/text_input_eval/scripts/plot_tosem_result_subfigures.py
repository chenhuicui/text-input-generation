from __future__ import annotations

import os
import re
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tosem_major.text_input_eval.scripts import plot_tosem_result_figures as base


SUBFIGURE_DIR = (
    Path(os.environ["TOSEM_SUBFIGURE_DIR"])
    if "TOSEM_SUBFIGURE_DIR" in os.environ
    else base.FIGURE_DIR / "tosem_result_subfigures"
)
SINGLE_FIGSIZE = (3.35, 3.35)


def slug(text: str) -> str:
    text = text.lower().replace("@", "at")
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")


def save_svg(fig: plt.Figure, filename: str, manifest: list[dict[str, str]]) -> None:
    SUBFIGURE_DIR.mkdir(parents=True, exist_ok=True)
    path = SUBFIGURE_DIR / f"{filename}.svg"
    fig.savefig(path)
    manifest.append({"filename": path.name, "path": str(path)})
    plt.close(fig)


def make_single_axis() -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(1, 1, figsize=SINGLE_FIGSIZE)
    fig.subplots_adjust(left=0.30, right=0.97, top=0.80, bottom=0.24)
    return fig, ax


def add_setting_legend(fig: plt.Figure) -> None:
    fig.legend(
        handles=base.setting_legend(),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.035),
        ncol=3,
        frameon=False,
        handletextpad=0.4,
        columnspacing=0.8,
    )


def add_method_legend(fig: plt.Figure) -> None:
    fig.legend(
        handles=base.method_legend(),
        loc="lower center",
        bbox_to_anchor=(0.5, 0.035),
        ncol=2,
        frameon=False,
        handletextpad=0.4,
        columnspacing=0.8,
    )


def plot_setting_svg(
    data: pd.DataFrame,
    *,
    title: str,
    xlabel: str,
    xlim: tuple[float, float] | None,
    filename: str,
    manifest: list[dict[str, str]],
    log_scale: bool = False,
    token_ticks: bool = False,
) -> None:
    fig, ax = make_single_axis()
    base.plot_setting_pointline_panel(
        ax,
        data,
        value_col="value",
        show_ylabels=True,
        xlabel=xlabel,
        title=title,
        xlim=xlim,
        log_scale=log_scale,
    )
    if token_ticks and xlim:
        ticks = base.token_ticks(xlim)
        ax.set_xticks(ticks)
        ax.set_xticklabels([base.format_token_tick(tick) for tick in ticks])
        ax.tick_params(axis="x", labelsize=7)
    add_setting_legend(fig)
    save_svg(fig, filename, manifest)


def plot_paired_svg(
    data: pd.DataFrame,
    *,
    title: str,
    xlabel: str,
    xlim: tuple[float, float] | None,
    filename: str,
    manifest: list[dict[str, str]],
    log_scale: bool = False,
    token_ticks: bool = False,
) -> None:
    fig, ax = make_single_axis()
    base.plot_paired_pointline_panel(
        ax,
        data,
        value_col="value",
        show_ylabels=True,
        xlabel=xlabel,
        title=title,
        xlim=xlim,
        log_scale=log_scale,
    )
    if token_ticks and xlim:
        ticks = base.token_ticks(xlim)
        ax.set_xticks(ticks)
        ax.set_xticklabels([base.format_token_tick(tick) for tick in ticks])
        ax.tick_params(axis="x", labelsize=7)
    add_method_legend(fig)
    save_svg(fig, filename, manifest)


def build_single_metric_setting_data(
    summary: pd.DataFrame,
    *,
    experiments: tuple[str, ...],
    value_col: str,
    transform,
) -> pd.DataFrame:
    subset = summary[summary["experiment"].isin(experiments)].copy()
    subset["_plot_value"] = subset[value_col].map(transform)
    rows = []
    for _, row in subset.iterrows():
        rows.append(
            {
                "experiment": row["experiment"],
                "model": row["model"],
                "model_label": base.MODEL_LABEL[row["model"]],
                "prompt_setting": row["prompt_setting"],
                "value": float(row["_plot_value"]),
            }
        )
    return base.add_average_rows(
        pd.DataFrame(rows),
        group_cols=("experiment", "prompt_setting"),
        value_cols=("value",),
    )


def export_rq1_effectiveness(
    summary: pd.DataFrame,
    spec: base.EffectivenessSpec,
    manifest: list[dict[str, str]],
) -> None:
    data = base.build_effectiveness_data(summary, spec)
    for cutoff_label, metric in spec.metrics:
        subset = data[data["metric"] == metric]
        plot_setting_svg(
            subset,
            title=f"{base.TASK_LABEL[spec.experiment]} | {cutoff_label}",
            xlabel="Effectiveness (%)",
            xlim=spec.xlim,
            filename=f"{spec.stem}__{slug(cutoff_label)}",
            manifest=manifest,
        )


def export_token_overhead(
    summary: pd.DataFrame,
    *,
    experiments: tuple[str, ...],
    stem: str,
    manifest: list[dict[str, str]],
) -> None:
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
    data = base.build_overhead_data(summary, experiments, token_cols)
    for experiment in experiments:
        for metric in token_cols:
            subset = data[
                (data["experiment"] == experiment) & (data["metric"] == metric)
            ]
            plot_setting_svg(
                subset,
                title=f"{base.TASK_LABEL[experiment]} | {token_labels[metric]}",
                xlabel="Tokens / attempt",
                xlim=base.token_xlim_for_experiment(experiment),
                filename=f"{stem}__{experiment}__{slug(token_labels[metric])}",
                manifest=manifest,
                log_scale=True,
                token_ticks=True,
            )


def export_setting_metric(
    summary: pd.DataFrame,
    *,
    experiments: tuple[str, ...],
    value_col: str,
    transform,
    stem: str,
    xlabel: str,
    manifest: list[dict[str, str]],
    xlims: dict[str, tuple[float, float]] | None = None,
) -> None:
    data = build_single_metric_setting_data(
        summary,
        experiments=experiments,
        value_col=value_col,
        transform=transform,
    )
    for experiment in experiments:
        subset = data[data["experiment"] == experiment]
        xlim = xlims.get(experiment) if xlims else None
        if xlim is None and subset["value"].notna().any():
            high = float(subset["value"].max()) * 1.15
            xlim = (0, high if high > 0 else 1)
        plot_setting_svg(
            subset,
            title=base.TASK_LABEL[experiment],
            xlabel=xlabel,
            xlim=xlim,
            filename=f"{stem}__{experiment}",
            manifest=manifest,
        )


def export_feedback_effectiveness(
    summary: pd.DataFrame,
    spec: base.FeedbackSpec,
    manifest: list[dict[str, str]],
) -> None:
    data = base.build_feedback_effectiveness_data(summary, spec)
    for setting in base.SETTING_ORDER:
        for cutoff_label, metric in spec.metrics:
            subset = data[
                (data["prompt_setting"] == setting) & (data["metric"] == metric)
            ]
            plot_paired_svg(
                subset,
                title=f"{base.SETTING_LABEL_INLINE[setting]} | {cutoff_label}",
                xlabel="Effectiveness (%)",
                xlim=spec.xlim,
                filename=f"{spec.stem}__initial_{setting}__{slug(cutoff_label)}",
                manifest=manifest,
            )


def export_feedback_token_comparison(
    summary: pd.DataFrame,
    manifest: list[dict[str, str]],
) -> None:
    metric_defs = (
        ("input_tokens", "prompt_tokens_per_call", "Input Tokens / Attempt", float),
        ("output_tokens", "completion_tokens_per_call", "Output Tokens / Attempt", float),
        ("total_tokens", "total_tokens_per_call", "Total Tokens / Attempt", float),
    )
    data = base.build_feedback_overhead_comparison_data(summary, metric_defs)
    for task_pair in ("rq2_valid_feedback", "rq2_invalid_feedback"):
        xlim = base.token_xlim_for_experiment(task_pair)
        for setting in base.SETTING_ORDER:
            for metric, _column, metric_label, _transform in metric_defs:
                subset = data[
                    (data["task_pair"] == task_pair)
                    & (data["prompt_setting"] == setting)
                    & (data["metric"] == metric)
                ]
                plot_paired_svg(
                    subset,
                    title=(
                        f"{base.TASK_LABEL[task_pair]} | "
                        f"{base.SETTING_LABEL_INLINE[setting]} | {metric_label}"
                    ),
                    xlabel="Tokens / attempt",
                    xlim=xlim,
                    filename=(
                        "rq2_feedback_token_comparison_point_lines"
                        f"__{task_pair}__initial_{setting}__{metric}"
                    ),
                    manifest=manifest,
                    log_scale=True,
                    token_ticks=True,
                )


def export_feedback_latency_comparison(
    summary: pd.DataFrame,
    manifest: list[dict[str, str]],
) -> None:
    metric_defs = (
        (
            "latency_s",
            "elapsed_ms_per_generation_mean",
            "Latency / Attempt (s)",
            lambda value: float(value) / 1000.0,
        ),
    )
    data = base.build_feedback_overhead_comparison_data(summary, metric_defs)
    for task_pair in ("rq2_valid_feedback", "rq2_invalid_feedback"):
        task_data = data[data["task_pair"] == task_pair]
        high = float(task_data["value"].max()) * 1.15
        xlim = (0, high if high > 0 else 1)
        for setting in base.SETTING_ORDER:
            subset = task_data[
                (task_data["prompt_setting"] == setting)
                & (task_data["metric"] == "latency_s")
            ]
            plot_paired_svg(
                subset,
                title=(
                    f"{base.TASK_LABEL[task_pair]} | "
                    f"{base.SETTING_LABEL_INLINE[setting]} | Latency"
                ),
                xlabel="Latency / attempt (s)",
                xlim=xlim,
                filename=(
                    "rq2_feedback_latency_comparison_point_lines"
                    f"__{task_pair}__initial_{setting}"
                ),
                manifest=manifest,
            )


def export_mfsa(manifest: list[dict[str, str]]) -> None:
    data = base.build_mfsa_data()
    xlim_by_task = {
        "rq2_valid_feedback": (0, 3),
        "rq2_invalid_feedback": (0, 30),
    }
    for task_pair in ("rq2_valid_feedback", "rq2_invalid_feedback"):
        for setting in base.SETTING_ORDER:
            subset = data[
                (data["task_pair"] == task_pair) & (data["prompt_setting"] == setting)
            ]
            plot_paired_svg(
                subset,
                title=f"{base.TASK_LABEL[task_pair]} | {base.SETTING_LABEL_INLINE[setting]}",
                xlabel="MFSA",
                xlim=xlim_by_task[task_pair],
                filename=f"rq2_mean_first_success_attempt_point_lines__{task_pair}__initial_{setting}",
                manifest=manifest,
            )


def export_mfsa_setting_comparison(
    *,
    experiments: tuple[str, ...],
    stem: str,
    manifest: list[dict[str, str]],
) -> None:
    data = base.build_mfsa_setting_data(experiments)
    xlim_by_experiment = {
        "rq1_valid": (0, 3),
        "rq1_invalid": (0, 30),
        "rq2_valid_feedback": (0, 3),
        "rq2_invalid_feedback": (0, 30),
    }
    for experiment in experiments:
        subset = data[data["experiment"] == experiment]
        plot_setting_svg(
            subset,
            title=f"{base.TASK_LABEL[experiment]} | MFSA",
            xlabel="MFSA (lower is better)",
            xlim=xlim_by_experiment[experiment],
            filename=f"{stem}__{experiment}",
            manifest=manifest,
        )


def export_attempts_per_case_setting_comparison(
    summary: pd.DataFrame,
    *,
    experiments: tuple[str, ...],
    stem: str,
    manifest: list[dict[str, str]],
) -> None:
    data = build_single_metric_setting_data(
        summary,
        experiments=experiments,
        value_col="actual_calls_per_case",
        transform=float,
    )
    xlim_by_experiment = {
        "rq2_valid_feedback": (0, 3),
        "rq2_invalid_feedback": (0, 30),
    }
    for experiment in experiments:
        subset = data[data["experiment"] == experiment]
        plot_setting_svg(
            subset,
            title=f"{base.TASK_LABEL[experiment]} | Attempts/Case",
            xlabel="Attempts/Case",
            xlim=xlim_by_experiment[experiment],
            filename=f"{stem}__{experiment}",
            manifest=manifest,
        )


def main() -> None:
    base.setup_style()
    summary = base.read_summary()
    manifest: list[dict[str, str]] = []

    export_rq1_effectiveness(summary, base.RQ1_VALID_SPEC, manifest)
    export_rq1_effectiveness(summary, base.RQ1_INVALID_SPEC, manifest)
    export_token_overhead(
        summary,
        experiments=("rq1_valid", "rq1_invalid"),
        stem="rq1_token_overhead_input_output_total_point_lines",
        manifest=manifest,
    )
    export_mfsa_setting_comparison(
        experiments=("rq1_valid", "rq1_invalid"),
        stem="rq1_mfsa_setting_point_lines",
        manifest=manifest,
    )
    export_setting_metric(
        summary,
        experiments=("rq1_valid", "rq1_invalid"),
        value_col="elapsed_ms_per_generation_mean",
        transform=lambda value: float(value) / 1000.0,
        stem="rq1_latency_overhead_point_lines",
        xlabel="Latency / attempt (s)",
        manifest=manifest,
    )

    export_feedback_effectiveness(summary, base.RQ2_VALID_SPEC, manifest)
    export_feedback_effectiveness(summary, base.RQ2_INVALID_SPEC, manifest)
    export_feedback_token_comparison(summary, manifest)
    export_feedback_latency_comparison(summary, manifest)
    export_rq1_effectiveness(summary, base.RQ2_VALID_SETTING_SPEC, manifest)
    export_rq1_effectiveness(summary, base.RQ2_INVALID_SETTING_SPEC, manifest)
    export_mfsa(manifest)
    export_mfsa_setting_comparison(
        experiments=("rq2_valid_feedback", "rq2_invalid_feedback"),
        stem="rq2_mfsa_setting_point_lines",
        manifest=manifest,
    )
    export_setting_metric(
        summary,
        experiments=("rq2_valid_feedback", "rq2_invalid_feedback"),
        value_col="actual_calls_per_case",
        transform=float,
        stem="rq2_llm_calls_per_case_point_lines",
        xlabel="Attempts/Case",
        manifest=manifest,
        xlims={
            "rq2_valid_feedback": (0, 3),
            "rq2_invalid_feedback": (0, 30),
        },
    )
    export_token_overhead(
        summary,
        experiments=("rq2_valid_feedback", "rq2_invalid_feedback"),
        stem="rq2_token_overhead_input_output_total_point_lines",
        manifest=manifest,
    )
    export_setting_metric(
        summary,
        experiments=("rq2_valid_feedback", "rq2_invalid_feedback"),
        value_col="elapsed_ms_per_generation_mean",
        transform=lambda value: float(value) / 1000.0,
        stem="rq2_latency_overhead_point_lines",
        xlabel="Latency / attempt (s)",
        manifest=manifest,
    )

    manifest_df = pd.DataFrame(manifest)
    manifest_df.to_csv(SUBFIGURE_DIR / "manifest.csv", index=False)
    print(f"Wrote {len(manifest)} SVG subfigures to {SUBFIGURE_DIR}")


if __name__ == "__main__":
    main()
