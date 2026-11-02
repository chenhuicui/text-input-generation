from __future__ import annotations

from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[3]
SUMMARY_CSV = PROJECT_ROOT / "output" / "tosem_rq_metrics" / "rq_metric_summary.csv"
OUTPUT_DIR = PROJECT_ROOT / "output" / "tosem_rq_metrics"
FIGURE_DIR = (
    PROJECT_ROOT
    / "论文及审稿意见"
    / "Android_Empirical_Study"
    / "figures"
    / "results"
)

SETTING_ORDER = ("extracted", "xml", "vision")
SETTING_LABEL = {
    "extracted": "Extracted\ncontext",
    "xml": "UI hierarchy\nXML",
    "vision": "Screenshot\nvision",
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

TASKS = (
    {
        "experiment": "rq1_valid",
        "metric": "PPTR@3",
        "title": "RQ1.1: Page pass-through",
        "short_title": "RQ1.1 PPTR@3",
        "ylim": (0, 82),
        "delta_xlim": (-65, 8),
    },
    {
        "experiment": "rq1_invalid",
        "metric": "BugReveal@30",
        "title": "RQ1.2: Bug reveal",
        "short_title": "RQ1.2 BugReveal@30",
        "ylim": (35, 66),
        "delta_xlim": (-12, 12),
    },
)

RQ2_TASKS = (
    {
        "experiment": "rq2_valid_feedback",
        "metric": "PPTR@3",
        "title": "RQ2.1: Feedback for page pass-through",
        "short_title": "RQ2.1 PPTR@3",
        "ylim": (0, 90),
        "delta_xlim": (-10, 15),
    },
    {
        "experiment": "rq2_invalid_feedback",
        "metric": "BugReveal@30",
        "title": "RQ2.2: Feedback for bug reveal",
        "short_title": "RQ2.2 BugReveal@30",
        "ylim": (35, 82),
        "delta_xlim": (-12, 12),
    },
)

CUMULATIVE_TASKS = (
    {
        "experiment": "rq1_valid",
        "title": "RQ1.1 cumulative page pass-through",
        "cutoffs": (
            ("@1", "PPTR@1"),
            ("@2", "PPTR@2"),
            ("@3", "PPTR@3"),
        ),
        "xlim": (0, 82),
        "stem": "rq1_valid_setting_cumulative_three_point_lines",
    },
    {
        "experiment": "rq1_invalid",
        "title": "RQ1.2 cumulative bug reveal",
        "cutoffs": (
            ("@1", "BugReveal@1"),
            ("@5", "BugReveal@5"),
            ("@10", "BugReveal@10"),
            ("@30", "BugReveal@30"),
        ),
        "xlim": (15, 66),
        "stem": "rq1_invalid_setting_cumulative_three_point_lines",
    },
    {
        "experiment": "rq2_valid_feedback",
        "title": "RQ2.1 feedback-enhanced cumulative page pass-through",
        "cutoffs": (
            ("@1", "PPTR@1"),
            ("@2", "PPTR@2"),
            ("@3", "PPTR@3"),
        ),
        "xlim": (0, 90),
        "stem": "rq2_valid_feedback_setting_cumulative_three_point_lines",
    },
    {
        "experiment": "rq2_invalid_feedback",
        "title": "RQ2.2 feedback-enhanced cumulative bug reveal",
        "cutoffs": (
            ("@1", "BugReveal@1"),
            ("@5", "BugReveal@5"),
            ("@10", "BugReveal@10"),
            ("@30", "BugReveal@30"),
        ),
        "xlim": (15, 82),
        "stem": "rq2_invalid_feedback_setting_cumulative_three_point_lines",
    },
)

FEEDBACK_GAIN_TASKS = (
    {
        "baseline_experiment": "rq1_valid",
        "feedback_experiment": "rq2_valid_feedback",
        "title": "RQ2.1 feedback gain over one-shot across attempts",
        "paired_title": "RQ2.1 one-shot vs feedback by cutoff and initial setting",
        "paired_note": (
            "Rows denote the initial setting; feedback turns after @1 use "
            "text-only feedback prompts."
        ),
        "cutoffs": (
            ("@1", 1, "PPTR@1"),
            ("@2", 2, "PPTR@2"),
            ("@3", 3, "PPTR@3"),
        ),
        "ylim": (-15, 60),
        "effectiveness_xlim": (0, 90),
        "stem": "rq2_valid_feedback_gain_progression",
        "paired_stem": "rq2_valid_feedback_paired_stage_point_lines",
    },
    {
        "baseline_experiment": "rq1_invalid",
        "feedback_experiment": "rq2_invalid_feedback",
        "title": "RQ2.2 feedback gain over one-shot across attempts",
        "paired_title": "RQ2.2 one-shot vs feedback by cutoff and initial setting",
        "paired_note": (
            "Rows denote the initial setting; feedback turns after @1 use "
            "text-only feedback prompts."
        ),
        "cutoffs": (
            ("@1", 1, "BugReveal@1"),
            ("@5", 5, "BugReveal@5"),
            ("@10", 10, "BugReveal@10"),
            ("@30", 30, "BugReveal@30"),
        ),
        "ylim": (-15, 32),
        "effectiveness_xlim": (15, 82),
        "stem": "rq2_invalid_feedback_gain_progression",
        "paired_stem": "rq2_invalid_feedback_paired_stage_point_lines",
    },
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
            "axes.spines.top": False,
            "axes.spines.right": False,
        }
    )


def load_metrics(tasks: tuple[dict[str, object], ...]) -> pd.DataFrame:
    summary = pd.read_csv(SUMMARY_CSV)
    rows = []
    for task in tasks:
        subset = summary[
            (summary["experiment"] == task["experiment"])
            & (summary["model"].isin(MODEL_ORDER))
            & (summary["prompt_setting"].isin(SETTING_ORDER))
        ].copy()
        for _, row in subset.iterrows():
            rows.append(
                {
                    "experiment": task["experiment"],
                    "metric": task["metric"],
                    "task_title": task["short_title"],
                    "model": row["model"],
                    "model_label": MODEL_LABEL[row["model"]],
                    "prompt_setting": row["prompt_setting"],
                    "setting_label": SETTING_LABEL[row["prompt_setting"]].replace("\n", " "),
                    "rate_pct": float(row[task["metric"]]) * 100.0,
                    "success_count": float(row[f"{task['metric']}_count"]),
                    "attempts_per_case": float(row["actual_calls_per_case"]),
                    "total_tokens_per_attempt": float(row["total_tokens_per_call"]),
                    "latency_s_per_attempt": float(row["elapsed_ms_per_generation_mean"])
                    / 1000.0,
                }
            )

    data = pd.DataFrame(rows)

    avg_rows = []
    for (experiment, metric, task_title, prompt_setting), group in data.groupby(
        ["experiment", "metric", "task_title", "prompt_setting"], sort=False
    ):
        avg_rows.append(
            {
                "experiment": experiment,
                "metric": metric,
                "task_title": task_title,
                "model": "Average",
                "model_label": "Average",
                "prompt_setting": prompt_setting,
                "setting_label": SETTING_LABEL[prompt_setting].replace("\n", " "),
                "rate_pct": group["rate_pct"].mean(),
                "success_count": group["success_count"].mean(),
                "attempts_per_case": group["attempts_per_case"].mean(),
                "total_tokens_per_attempt": group["total_tokens_per_attempt"].mean(),
                "latency_s_per_attempt": group["latency_s_per_attempt"].mean(),
            }
        )
    data = pd.concat([data, pd.DataFrame(avg_rows)], ignore_index=True)

    extracted = data[data["prompt_setting"] == "extracted"][
        ["experiment", "model", "rate_pct", "total_tokens_per_attempt"]
    ].rename(
        columns={
            "rate_pct": "extracted_rate_pct",
            "total_tokens_per_attempt": "extracted_tokens_per_attempt",
        }
    )
    data = data.merge(extracted, on=["experiment", "model"], how="left")
    data["delta_vs_extracted_pp"] = data["rate_pct"] - data["extracted_rate_pct"]
    data["token_ratio_vs_extracted"] = (
        data["total_tokens_per_attempt"] / data["extracted_tokens_per_attempt"]
    )
    return data


def load_rq1() -> pd.DataFrame:
    return load_metrics(TASKS)


def load_rq2() -> pd.DataFrame:
    return load_metrics(RQ2_TASKS)


def metric_rate_pct(row: pd.Series, metric: str) -> float:
    if metric == "PPTR@2":
        return (float(row["PPTR@1"]) + float(row["NewPPTR_2"])) * 100.0
    return float(row[metric]) * 100.0


def load_cumulative_rq1(task: dict[str, object]) -> pd.DataFrame:
    summary = pd.read_csv(SUMMARY_CSV)
    subset = summary[
        (summary["experiment"] == task["experiment"])
        & (summary["model"].isin(MODEL_ORDER))
        & (summary["prompt_setting"].isin(SETTING_ORDER))
    ].copy()

    rows = []
    for _, row in subset.iterrows():
        for cutoff_label, metric in task["cutoffs"]:
            rows.append(
                {
                    "experiment": task["experiment"],
                    "cutoff_label": cutoff_label,
                    "metric": metric,
                    "model": row["model"],
                    "model_label": MODEL_LABEL[row["model"]],
                    "prompt_setting": row["prompt_setting"],
                    "setting_label": SETTING_LABEL[row["prompt_setting"]].replace("\n", " "),
                    "rate_pct": metric_rate_pct(row, metric),
                }
            )

    data = pd.DataFrame(rows)
    avg_rows = []
    for (cutoff_label, metric, prompt_setting), group in data.groupby(
        ["cutoff_label", "metric", "prompt_setting"], sort=False
    ):
        avg_rows.append(
            {
                "experiment": task["experiment"],
                "cutoff_label": cutoff_label,
                "metric": metric,
                "model": "Average",
                "model_label": "Average",
                "prompt_setting": prompt_setting,
                "setting_label": SETTING_LABEL[prompt_setting].replace("\n", " "),
                "rate_pct": group["rate_pct"].mean(),
            }
        )
    return pd.concat([data, pd.DataFrame(avg_rows)], ignore_index=True)


def save_all(fig: plt.Figure, stem: str) -> None:
    FIGURE_DIR.mkdir(parents=True, exist_ok=True)
    for suffix in ("pdf", "png", "svg"):
        fig.savefig(FIGURE_DIR / f"{stem}.{suffix}", bbox_inches="tight")


def plot_delta_dotplot(data: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.8), sharey=True)
    y_labels = ["Average"] + [MODEL_LABEL[model] for model in MODEL_ORDER]
    y_positions = np.arange(len(y_labels))
    model_keys = ["Average"] + list(MODEL_ORDER)
    colors = {"xml": "#0072B2", "vision": "#D55E00"}
    markers = {"xml": "s", "vision": "o"}
    labels = {"xml": "XML - Extracted", "vision": "Vision - Extracted"}

    for ax, task in zip(axes, TASKS):
        subset = data[data["experiment"] == task["experiment"]]
        ax.axvline(0, color="#444444", linewidth=0.8, linestyle="--", zorder=0)
        ax.grid(axis="x", color="#d9d9d9", linewidth=0.6)

        for yi, model in zip(y_positions, model_keys):
            vals = []
            for setting in ("xml", "vision"):
                value = subset[
                    (subset["model"] == model) & (subset["prompt_setting"] == setting)
                ]["delta_vs_extracted_pp"].iloc[0]
                vals.append(value)
            ax.plot(vals, [yi, yi], color="#bbbbbb", linewidth=0.7, zorder=1)

        for setting in ("xml", "vision"):
            xs = [
                subset[
                    (subset["model"] == model) & (subset["prompt_setting"] == setting)
                ]["delta_vs_extracted_pp"].iloc[0]
                for model in model_keys
            ]
            ax.scatter(
                xs,
                y_positions,
                s=34,
                marker=markers[setting],
                facecolor=colors[setting],
                edgecolor="white",
                linewidth=0.5,
                label=labels[setting],
                zorder=3,
            )

        ax.set_title(task["short_title"])
        ax.set_xlim(task["delta_xlim"])
        ax.set_xlabel("Difference from Extracted Context (pp)")
        ax.set_yticks(y_positions)
        ax.set_yticklabels(y_labels)

        avg_values = subset[
            (subset["model"] == "Average")
            & (subset["prompt_setting"].isin(("xml", "vision")))
        ]
        for _, row in avg_values.iterrows():
            ax.annotate(
                f"{row['delta_vs_extracted_pp']:+.1f}",
                (row["delta_vs_extracted_pp"], 0),
                textcoords="offset points",
                xytext=(4, -8 if row["prompt_setting"] == "xml" else 5),
                ha="left",
                va="center",
                fontsize=7,
                color=colors[row["prompt_setting"]],
            )

    axes[0].invert_yaxis()
    axes[0].legend(
        loc="lower center",
        bbox_to_anchor=(1.05, -0.28),
        ncol=2,
        frameon=False,
    )
    fig.suptitle(
        "Setting-level effectiveness gaps in RQ1",
        x=0.52,
        y=1.02,
        fontsize=10,
        fontweight="bold",
    )
    fig.tight_layout()
    save_all(fig, "rq1_setting_delta_dotplot")
    plt.close(fig)


def plot_effectiveness_slope(data: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.2))
    x = np.arange(len(SETTING_ORDER))
    cmap = plt.get_cmap("tab10")
    model_colors = {model: cmap(i) for i, model in enumerate(MODEL_ORDER)}

    for ax, task in zip(axes, TASKS):
        subset = data[data["experiment"] == task["experiment"]]
        for model in MODEL_ORDER:
            model_data = (
                subset[subset["model"] == model]
                .set_index("prompt_setting")
                .loc[list(SETTING_ORDER)]
            )
            ax.plot(
                x,
                model_data["rate_pct"],
                marker="o",
                markersize=3.2,
                linewidth=1.0,
                alpha=0.82,
                color=model_colors[model],
                label=MODEL_LABEL[model],
            )

        avg_data = (
            subset[subset["model"] == "Average"]
            .set_index("prompt_setting")
            .loc[list(SETTING_ORDER)]
        )
        ax.plot(
            x,
            avg_data["rate_pct"],
            marker="D",
            markersize=4.2,
            linewidth=2.2,
            color="#111111",
            label="Average",
            zorder=5,
        )
        for xi, value in zip(x, avg_data["rate_pct"]):
            ax.annotate(
                f"{value:.1f}",
                (xi, value),
                textcoords="offset points",
                xytext=(0, 6),
                ha="center",
                fontsize=7,
                color="#111111",
            )

        ax.set_title(task["short_title"])
        ax.set_xticks(x)
        ax.set_xticklabels([SETTING_LABEL[s] for s in SETTING_ORDER])
        ax.set_ylabel("Effectiveness (%)")
        ax.set_ylim(task["ylim"])
        ax.grid(axis="y", color="#d9d9d9", linewidth=0.6)

    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        labels,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.18),
        ncol=5,
        frameon=False,
    )
    fig.suptitle(
        "RQ1 effectiveness under three prompting settings",
        y=1.03,
        fontsize=10,
        fontweight="bold",
    )
    fig.tight_layout()
    save_all(fig, "rq1_setting_effectiveness_slope")
    plt.close(fig)


def plot_three_point_lines(data: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.8), sharey=True)
    y_labels = ["Average"] + [MODEL_LABEL[model] for model in MODEL_ORDER]
    model_keys = ["Average"] + list(MODEL_ORDER)
    y_positions = np.arange(len(y_labels))
    colors = {
        "extracted": "#222222",
        "xml": "#0072B2",
        "vision": "#D55E00",
    }
    markers = {"extracted": "D", "xml": "s", "vision": "o"}
    labels = {
        "extracted": "Extracted Context",
        "xml": "UI Hierarchy XML",
        "vision": "Screenshot Vision",
    }

    for ax, task in zip(axes, TASKS):
        subset = data[data["experiment"] == task["experiment"]]
        ax.grid(axis="x", color="#d9d9d9", linewidth=0.6)

        for yi, model in zip(y_positions, model_keys):
            values = []
            for setting in SETTING_ORDER:
                value = subset[
                    (subset["model"] == model) & (subset["prompt_setting"] == setting)
                ]["rate_pct"].iloc[0]
                values.append(value)
            ax.plot(
                values,
                [yi] * len(values),
                color="#b8b8b8",
                linewidth=0.8,
                zorder=1,
            )

        for setting in SETTING_ORDER:
            xs = [
                subset[
                    (subset["model"] == model) & (subset["prompt_setting"] == setting)
                ]["rate_pct"].iloc[0]
                for model in model_keys
            ]
            ax.scatter(
                xs,
                y_positions,
                s=34,
                marker=markers[setting],
                facecolor=colors[setting],
                edgecolor="white",
                linewidth=0.5,
                label=labels[setting],
                zorder=3,
            )

        ax.set_title(task["short_title"])
        ax.set_xlim(task["ylim"])
        ax.set_xlabel("Effectiveness (%)")
        ax.set_yticks(y_positions)
        ax.set_yticklabels(y_labels)

    axes[0].invert_yaxis()
    axes[0].legend(
        loc="lower center",
        bbox_to_anchor=(1.05, -0.28),
        ncol=3,
        frameon=False,
    )
    fig.suptitle(
        "RQ1 effectiveness per LLM across three prompting settings",
        x=0.52,
        y=1.02,
        fontsize=10,
        fontweight="bold",
    )
    fig.tight_layout()
    save_all(fig, "rq1_setting_three_point_lines")
    plt.close(fig)


def plot_cumulative_three_point_lines(task: dict[str, object]) -> pd.DataFrame:
    data = load_cumulative_rq1(task)
    cutoffs = list(task["cutoffs"])
    fig_width = 7.2 if len(cutoffs) == 3 else 8.2
    fig, axes = plt.subplots(1, len(cutoffs), figsize=(fig_width, 3.8), sharey=True)
    if len(cutoffs) == 1:
        axes = [axes]

    y_labels = ["Average"] + [MODEL_LABEL[model] for model in MODEL_ORDER]
    model_keys = ["Average"] + list(MODEL_ORDER)
    y_positions = np.arange(len(y_labels))
    colors = {
        "extracted": "#222222",
        "xml": "#0072B2",
        "vision": "#D55E00",
    }
    markers = {"extracted": "D", "xml": "s", "vision": "o"}
    labels = {
        "extracted": "Extracted Context",
        "xml": "UI Hierarchy XML",
        "vision": "Screenshot Vision",
    }

    for ax, (cutoff_label, metric) in zip(axes, cutoffs):
        subset = data[data["metric"] == metric]
        ax.grid(axis="x", color="#d9d9d9", linewidth=0.6)

        for yi, model in zip(y_positions, model_keys):
            values = []
            for setting in SETTING_ORDER:
                value = subset[
                    (subset["model"] == model) & (subset["prompt_setting"] == setting)
                ]["rate_pct"].iloc[0]
                values.append(value)
            ax.plot(
                values,
                [yi] * len(values),
                color="#b8b8b8",
                linewidth=0.8,
                zorder=1,
            )

        for setting in SETTING_ORDER:
            xs = [
                subset[
                    (subset["model"] == model) & (subset["prompt_setting"] == setting)
                ]["rate_pct"].iloc[0]
                for model in model_keys
            ]
            ax.scatter(
                xs,
                y_positions,
                s=32,
                marker=markers[setting],
                facecolor=colors[setting],
                edgecolor="white",
                linewidth=0.5,
                label=labels[setting],
                zorder=3,
            )

        ax.set_title(cutoff_label)
        ax.set_xlim(task["xlim"])
        ax.set_xlabel("Effectiveness (%)")
        ax.set_yticks(y_positions)
        ax.set_yticklabels(y_labels)

    axes[0].invert_yaxis()
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=3,
        frameon=False,
    )
    fig.suptitle(
        task["title"],
        x=0.52,
        y=1.02,
        fontsize=10,
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.15, right=0.98, top=0.80, bottom=0.25, wspace=0.25)
    save_all(fig, task["stem"])
    plt.close(fig)
    return data


def format_token_tick(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:g}k"
    return f"{value:g}"


def plot_overhead_three_point_lines(
    data: pd.DataFrame,
    tasks: tuple[dict[str, object], ...],
    value_col: str,
    title: str,
    xlabel: str,
    stem: str,
    xlim: tuple[float, float],
    log_scale: bool = False,
    xlims_by_experiment: dict[str, tuple[float, float]] | None = None,
) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(7.2, 3.5), sharey=True)
    y_labels = [MODEL_LABEL[model] for model in MODEL_ORDER]
    model_keys = list(MODEL_ORDER)
    y_positions = np.arange(len(y_labels))
    colors = {
        "extracted": "#222222",
        "xml": "#0072B2",
        "vision": "#D55E00",
    }
    markers = {"extracted": "D", "xml": "s", "vision": "o"}
    labels = {
        "extracted": "Extracted Context",
        "xml": "UI Hierarchy XML",
        "vision": "Screenshot Vision",
    }

    for ax, task in zip(axes, tasks):
        subset = data[
            (data["experiment"] == task["experiment"])
            & (data["model"].isin(MODEL_ORDER))
        ]
        ax.grid(axis="x", color="#d9d9d9", linewidth=0.6)

        for yi, model in zip(y_positions, model_keys):
            values = []
            for setting in SETTING_ORDER:
                value = subset[
                    (subset["model"] == model) & (subset["prompt_setting"] == setting)
                ][value_col].iloc[0]
                values.append(value)
            ax.plot(
                values,
                [yi] * len(values),
                color="#b8b8b8",
                linewidth=0.8,
                zorder=1,
            )

        for setting in SETTING_ORDER:
            xs = [
                subset[
                    (subset["model"] == model) & (subset["prompt_setting"] == setting)
                ][value_col].iloc[0]
                for model in model_keys
            ]
            ax.scatter(
                xs,
                y_positions,
                s=34,
                marker=markers[setting],
                facecolor=colors[setting],
                edgecolor="white",
                linewidth=0.5,
                label=labels[setting],
                zorder=3,
            )

        ax.set_title(task["short_title"])
        ax.set_xlim(
            xlims_by_experiment.get(str(task["experiment"]), xlim)
            if xlims_by_experiment
            else xlim
        )
        if log_scale:
            ax.set_xscale("log")
            ticks = [300, 1000, 3000, 10000, 30000, 100000]
            ax.set_xticks(ticks)
            ax.set_xticklabels([format_token_tick(tick) for tick in ticks])
        ax.set_xlabel(xlabel)
        ax.set_yticks(y_positions)
        ax.set_yticklabels(y_labels)

    axes[0].invert_yaxis()
    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.02),
        ncol=3,
        frameon=False,
    )
    fig.suptitle(
        title,
        x=0.52,
        y=1.02,
        fontsize=10,
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.15, right=0.98, top=0.78, bottom=0.25, wspace=0.22)
    save_all(fig, stem)
    plt.close(fig)


def load_feedback_gain(task: dict[str, object]) -> pd.DataFrame:
    summary = pd.read_csv(SUMMARY_CSV)
    rows = []
    for model in MODEL_ORDER:
        for setting in SETTING_ORDER:
            baseline = summary[
                (summary["experiment"] == task["baseline_experiment"])
                & (summary["model"] == model)
                & (summary["prompt_setting"] == setting)
            ]
            feedback = summary[
                (summary["experiment"] == task["feedback_experiment"])
                & (summary["model"] == model)
                & (summary["prompt_setting"] == setting)
            ]
            if baseline.empty or feedback.empty:
                continue
            baseline_row = baseline.iloc[0]
            feedback_row = feedback.iloc[0]
            for cutoff_label, attempt, metric in task["cutoffs"]:
                baseline_rate = metric_rate_pct(baseline_row, metric)
                feedback_rate = metric_rate_pct(feedback_row, metric)
                rows.append(
                    {
                        "baseline_experiment": task["baseline_experiment"],
                        "feedback_experiment": task["feedback_experiment"],
                        "cutoff_label": cutoff_label,
                        "attempt": attempt,
                        "metric": metric,
                        "model": model,
                        "model_label": MODEL_LABEL[model],
                        "prompt_setting": setting,
                        "setting_label": SETTING_LABEL[setting].replace("\n", " "),
                        "one_shot_rate_pct": baseline_rate,
                        "feedback_rate_pct": feedback_rate,
                        "gain_pp": feedback_rate - baseline_rate,
                    }
                )

    data = pd.DataFrame(rows)
    avg_rows = []
    for (cutoff_label, attempt, metric, setting), group in data.groupby(
        ["cutoff_label", "attempt", "metric", "prompt_setting"], sort=False
    ):
        avg_rows.append(
            {
                "baseline_experiment": task["baseline_experiment"],
                "feedback_experiment": task["feedback_experiment"],
                "cutoff_label": cutoff_label,
                "attempt": attempt,
                "metric": metric,
                "model": "Average",
                "model_label": "Average",
                "prompt_setting": setting,
                "setting_label": SETTING_LABEL[setting].replace("\n", " "),
                "one_shot_rate_pct": group["one_shot_rate_pct"].mean(),
                "feedback_rate_pct": group["feedback_rate_pct"].mean(),
                "gain_pp": group["gain_pp"].mean(),
            }
        )
    return pd.concat([data, pd.DataFrame(avg_rows)], ignore_index=True)


def plot_feedback_gain_progression(task: dict[str, object]) -> pd.DataFrame:
    data = load_feedback_gain(task)
    fig, axes = plt.subplots(1, 3, figsize=(7.8, 3.4), sharey=True)
    model_keys = list(MODEL_ORDER)
    cmap = plt.get_cmap("tab10")
    model_colors = {model: cmap(i) for i, model in enumerate(MODEL_ORDER)}
    setting_titles = {
        "extracted": "Initial\nExtracted Context",
        "xml": "Initial\nUI Hierarchy XML",
        "vision": "Initial\nScreenshot Vision",
    }

    for ax, setting in zip(axes, SETTING_ORDER):
        subset = data[data["prompt_setting"] == setting]
        ax.axhline(0, color="#555555", linewidth=0.8, linestyle="--", zorder=0)
        ax.grid(axis="y", color="#d9d9d9", linewidth=0.6)

        for model in model_keys:
            model_data = subset[subset["model"] == model].sort_values("attempt")
            ax.plot(
                model_data["attempt"],
                model_data["gain_pp"],
                marker="o",
                markersize=3.0,
                linewidth=1.0,
                alpha=0.8,
                color=model_colors[model],
                label=MODEL_LABEL[model],
            )

        avg_data = subset[subset["model"] == "Average"].sort_values("attempt")
        ax.plot(
            avg_data["attempt"],
            avg_data["gain_pp"],
            marker="D",
            markersize=4.2,
            linewidth=2.2,
            color="#111111",
            label="Average",
            zorder=5,
        )
        ax.set_title(setting_titles[setting])
        ax.set_ylim(task["ylim"])
        ax.set_xticks([attempt for _, attempt, _ in task["cutoffs"]])
        ax.set_xlabel("Attempt cutoff")
        ax.set_ylabel("Feedback - One-shot (pp)")

    handles, legend_labels = axes[0].get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="lower center",
        bbox_to_anchor=(0.5, -0.02),
        ncol=5,
        frameon=False,
    )
    fig.suptitle(
        task["title"],
        y=1.02,
        fontsize=10,
        fontweight="bold",
    )
    fig.subplots_adjust(left=0.10, right=0.99, top=0.78, bottom=0.30, wspace=0.26)
    save_all(fig, task["stem"])
    plt.close(fig)
    return data


def plot_feedback_paired_stage_point_lines(task: dict[str, object]) -> pd.DataFrame:
    data = load_feedback_gain(task)
    cutoffs = list(task["cutoffs"])
    ncols = len(cutoffs)
    fig_width = 8.0 if ncols == 3 else 9.4
    fig, axes = plt.subplots(
        len(SETTING_ORDER),
        ncols,
        figsize=(fig_width, 6.2),
        sharey=False,
    )
    y_labels = ["Average"] + [MODEL_LABEL[model] for model in MODEL_ORDER]
    model_keys = ["Average"] + list(MODEL_ORDER)
    y_positions = np.arange(len(y_labels))
    setting_titles = {
        "extracted": "Extracted Context",
        "xml": "UI Hierarchy XML",
        "vision": "Screenshot Vision",
    }
    method_style = {
        "one_shot": {
            "label": "One-shot",
            "marker": "D",
            "color": "#555555",
            "size": 27,
        },
        "feedback": {
            "label": "Feedback",
            "marker": "o",
            "color": "#D55E00",
            "size": 31,
        },
    }

    for row_idx, setting in enumerate(SETTING_ORDER):
        for col_idx, (cutoff_label, _attempt, metric) in enumerate(cutoffs):
            ax = axes[row_idx][col_idx]
            subset = data[
                (data["prompt_setting"] == setting) & (data["metric"] == metric)
            ]
            ax.grid(axis="x", color="#d9d9d9", linewidth=0.55)

            for yi, model in zip(y_positions, model_keys):
                row = subset[subset["model"] == model].iloc[0]
                xs = [row["one_shot_rate_pct"], row["feedback_rate_pct"]]
                ax.plot(xs, [yi, yi], color="#b8b8b8", linewidth=0.8, zorder=1)

            for method, value_col in (
                ("one_shot", "one_shot_rate_pct"),
                ("feedback", "feedback_rate_pct"),
            ):
                xs = [
                    subset[subset["model"] == model][value_col].iloc[0]
                    for model in model_keys
                ]
                style = method_style[method]
                ax.scatter(
                    xs,
                    y_positions,
                    s=style["size"],
                    marker=style["marker"],
                    facecolor=style["color"],
                    edgecolor="white",
                    linewidth=0.45,
                    label=style["label"],
                    zorder=3,
                )

            if row_idx == 0:
                ax.set_title(cutoff_label)
            if col_idx == 0:
                ax.set_yticks(y_positions)
                ax.set_yticklabels(y_labels)
                ax.tick_params(axis="y", labelleft=True)
                ax.text(
                    -0.42,
                    0.5,
                    setting_titles[setting],
                    transform=ax.transAxes,
                    rotation=90,
                    ha="center",
                    va="center",
                    fontsize=8,
                    fontweight="bold",
                )
            else:
                ax.set_yticks(y_positions)
                ax.set_yticklabels([])
                ax.tick_params(axis="y", labelleft=False)
            if row_idx == len(SETTING_ORDER) - 1:
                ax.set_xlabel("Effectiveness (%)")
            ax.set_xlim(task["effectiveness_xlim"])
            ax.set_ylim(len(y_labels) - 0.5, -0.5)

    handles, legend_labels = axes[0][0].get_legend_handles_labels()
    fig.legend(
        handles,
        legend_labels,
        loc="lower center",
        bbox_to_anchor=(0.5, 0.01),
        ncol=2,
        frameon=False,
    )
    fig.suptitle(
        task["paired_title"],
        y=0.995,
        fontsize=10,
        fontweight="bold",
    )
    fig.text(
        0.5,
        0.935,
        str(task["paired_note"]),
        ha="center",
        va="center",
        fontsize=7,
        color="#444444",
    )
    fig.subplots_adjust(
        left=0.22,
        right=0.99,
        top=0.86,
        bottom=0.14,
        wspace=0.12,
        hspace=0.30,
    )
    save_all(fig, task["paired_stem"])
    plt.close(fig)
    return data


def main() -> None:
    setup_style()
    data = load_rq1()
    rq2_data = load_rq2()
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    data.sort_values(
        by=["experiment", "model", "prompt_setting"],
        key=lambda col: col.map(
            {
                **{model: i for i, model in enumerate(["Average", *MODEL_ORDER])},
                **{setting: i for i, setting in enumerate(SETTING_ORDER)},
            }
        ).fillna(col),
    ).to_csv(OUTPUT_DIR / "rq1_setting_gap_plot_data.csv", index=False)
    rq2_data.sort_values(
        by=["experiment", "model", "prompt_setting"],
        key=lambda col: col.map(
            {
                **{model: i for i, model in enumerate(["Average", *MODEL_ORDER])},
                **{setting: i for i, setting in enumerate(SETTING_ORDER)},
            }
        ).fillna(col),
    ).to_csv(OUTPUT_DIR / "rq2_setting_gap_plot_data.csv", index=False)
    plot_delta_dotplot(data)
    plot_effectiveness_slope(data)
    plot_three_point_lines(data)
    cumulative_data = [plot_cumulative_three_point_lines(task) for task in CUMULATIVE_TASKS]
    pd.concat(cumulative_data, ignore_index=True).to_csv(
        OUTPUT_DIR / "rq_setting_cumulative_plot_data.csv", index=False
    )
    feedback_gain_data = [
        plot_feedback_gain_progression(task) for task in FEEDBACK_GAIN_TASKS
    ]
    for task in FEEDBACK_GAIN_TASKS:
        plot_feedback_paired_stage_point_lines(task)
    pd.concat(feedback_gain_data, ignore_index=True).to_csv(
        OUTPUT_DIR / "rq2_feedback_gain_progression_plot_data.csv", index=False
    )
    plot_overhead_three_point_lines(
        data,
        TASKS,
        "total_tokens_per_attempt",
        "RQ1 token overhead per LLM across prompting settings",
        "Tokens per attempt (log scale)",
        "rq1_token_overhead_three_point_lines",
        (200, 150000),
        log_scale=True,
    )
    plot_overhead_three_point_lines(
        data,
        TASKS,
        "latency_s_per_attempt",
        "RQ1 latency overhead per LLM across prompting settings",
        "Latency per attempt (s)",
        "rq1_latency_overhead_three_point_lines",
        (0, 105),
    )
    plot_overhead_three_point_lines(
        rq2_data,
        RQ2_TASKS,
        "attempts_per_case",
        "RQ2 attempts per case across initial prompting settings",
        "Attempts per case",
        "rq2_attempts_per_case_three_point_lines",
        (0, 30),
        xlims_by_experiment={
            "rq2_valid_feedback": (0, 3),
            "rq2_invalid_feedback": (0, 30),
        },
    )
    plot_overhead_three_point_lines(
        rq2_data,
        RQ2_TASKS,
        "total_tokens_per_attempt",
        "RQ2 token overhead per LLM across prompting settings",
        "Tokens per attempt (log scale)",
        "rq2_token_overhead_three_point_lines",
        (200, 150000),
        log_scale=True,
    )
    plot_overhead_three_point_lines(
        rq2_data,
        RQ2_TASKS,
        "latency_s_per_attempt",
        "RQ2 latency overhead per LLM across prompting settings",
        "Latency per attempt (s)",
        "rq2_latency_overhead_three_point_lines",
        (0, 120),
    )
    print(f"Wrote plot data to {OUTPUT_DIR / 'rq1_setting_gap_plot_data.csv'}")
    print(f"Wrote figures to {FIGURE_DIR}")


if __name__ == "__main__":
    main()
