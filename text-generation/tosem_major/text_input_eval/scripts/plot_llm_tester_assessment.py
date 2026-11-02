from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Dict, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pymysql
import seaborn as sns
from matplotlib.patches import Patch
from pymysql.cursors import DictCursor
from scipy.stats import rankdata


PROJECT_ROOT = Path(
    "/Users/cuichenhui/Documents/local-repositories/"
    "llm-empirical-study-workspace/llm-empirical-study"
)

DEFAULT_SCORE_TABLE = "t_llm_tester_assessment_score"
DEFAULT_PROMPT_VERSION = "llm_tester_must_v1"
DEFAULT_SCORE_JUDGE_MODEL = ""
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "output" / "llm_tester_assessment"
DEFAULT_FIGURE_DIR = (
    PROJECT_ROOT
    / "论文及审稿意见"
    / "Android_Empirical_Study"
    / "figures"
    / "results"
)

FINAL_MODELS = (
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

MODEL_LABELS = {
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

PROMPT_SETTINGS = ("extracted", "xml", "vision")
PROMPT_VARIANT_BY_SETTING = {
    "extracted": "full",
    "xml": "xml",
    "vision": "vision",
}
SETTING_LABELS = {
    "extracted": "Extracted context",
    "xml": "UI hierarchy XML",
    "vision": "Screenshot vision",
}
TASK_LABELS = {
    "valid": "PPTR",
    "invalid": "Bug",
}
LIKERT_LABELS = {
    1: "1: Strongly Disagree",
    2: "2: Disagree",
    3: "3: Neutral",
    4: "4: Agree",
    5: "5: Strongly Agree",
}
LIKERT_COLORS = {
    1: "#f8d6d6",
    2: "#ead5ea",
    3: "#fbf9d6",
    4: "#d8e9f8",
    5: "#d9f8d8",
}
EXPECTED_CASES = {
    "valid": 115,
    "invalid": 37,
}
EXPECTED_TESTERS = 20


def get_conn():
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "101.200.47.200"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "must_user"),
        password=os.getenv("MYSQL_PASSWORD", "REDACTED_USE_ENV_MYSQL_PASSWORD"),
        database=os.getenv("MYSQL_DATABASE", "tosem_text_input_db"),
        charset="utf8mb4",
        cursorclass=DictCursor,
        autocommit=False,
    )


def placeholders(items: Sequence[Any]) -> str:
    return ",".join(["%s"] * len(items))


def fetch_scores(
    *,
    score_table: str,
    prompt_version: str,
    score_judge_model: str,
) -> pd.DataFrame:
    judge_filter_sql = ""
    params_tail: list[Any] = [prompt_version]
    if score_judge_model:
        judge_filter_sql = "AND judge_model = %s"
        params_tail = [score_judge_model, prompt_version]

    base_sql = f"""
        SELECT
            score_id,
            assessment_task,
            case_id,
            model,
            prompt_setting,
            prompt_variant,
            tester_id,
            judge_model,
            score
        FROM {score_table}
        WHERE assessment_task IN ('valid', 'invalid')
          AND model IN ({placeholders(FINAL_MODELS)})
          AND prompt_setting IN ({placeholders(PROMPT_SETTINGS)})
          {judge_filter_sql}
          AND prompt_version = %s
          AND score_id > %s
        ORDER BY score_id
        LIMIT %s
    """
    base_params = [*FINAL_MODELS, *PROMPT_SETTINGS, *params_tail]
    conn = get_conn()
    rows: list[Dict[str, Any]] = []
    last_score_id = 0
    batch_size = 5000
    try:
        while True:
            conn.ping(reconnect=True)
            with conn.cursor() as cursor:
                cursor.execute(base_sql, [*base_params, last_score_id, batch_size])
                batch = cursor.fetchall()
            if not batch:
                break
            rows.extend(batch)
            last_score_id = int(batch[-1]["score_id"])
            print(
                f"[FETCH_BATCH] rows={len(rows)} last_score_id={last_score_id}",
                flush=True,
            )
    finally:
        conn.close()

    df = pd.DataFrame(rows)
    if df.empty:
        raise RuntimeError("No LLM tester assessment scores were fetched.")

    df = df[
        df.apply(
            lambda row: row["prompt_variant"]
            == PROMPT_VARIANT_BY_SETTING.get(str(row["prompt_setting"])),
            axis=1,
        )
    ].copy()
    df["score"] = pd.to_numeric(df["score"], errors="coerce")
    df = df.dropna(subset=["score"])
    df["score"] = df["score"].astype(int)
    df = (
        df.sort_values("score_id")
        .drop_duplicates(
            subset=[
                "assessment_task",
                "case_id",
                "model",
                "prompt_setting",
                "prompt_variant",
                "tester_id",
            ],
            keep="last",
        )
        .copy()
    )
    df["model_label"] = df["model"].map(MODEL_LABELS)
    df["model_label"] = pd.Categorical(
        df["model_label"],
        categories=[MODEL_LABELS[m] for m in FINAL_MODELS],
        ordered=True,
    )
    return df


def kendalls_w(matrix: pd.DataFrame) -> Tuple[float, int, int]:
    """Compute tie-corrected Kendall's W for rows=objects, columns=raters."""
    complete = matrix.dropna(axis=0, how="any")
    n, m = complete.shape
    if n < 2 or m < 2:
        return float("nan"), n, m

    ranked = np.zeros((n, m), dtype=float)
    tie_correction = 0.0
    for col_idx, tester in enumerate(complete.columns):
        values = complete[tester].to_numpy(dtype=float)
        ranked[:, col_idx] = rankdata(values, method="average")
        _, counts = np.unique(values, return_counts=True)
        tie_correction += float(np.sum(counts**3 - counts))

    rank_sums = ranked.sum(axis=1)
    s_value = float(np.sum((rank_sums - rank_sums.mean()) ** 2))
    denominator = float((m**2) * (n**3 - n) - m * tie_correction)
    if denominator <= 0:
        return float("nan"), n, m
    return float(12.0 * s_value / denominator), n, m


def build_kendall_summary(df: pd.DataFrame) -> pd.DataFrame:
    pivot = df.pivot_table(
        index=["assessment_task", "prompt_setting", "case_id", "model"],
        columns="tester_id",
        values="score",
        aggfunc="first",
    )
    w_value, complete_units, testers = kendalls_w(pivot)
    return pd.DataFrame(
        [
            {
                "scope": "overall",
                "kendalls_w": w_value,
                "complete_units": complete_units,
                "testers": testers,
                "total_units": len(pivot),
            }
        ]
    )


def build_completeness_summary(df: pd.DataFrame) -> pd.DataFrame:
    rows: list[Dict[str, Any]] = []
    for task in ("valid", "invalid"):
        for setting in PROMPT_SETTINGS:
            part = df[
                (df["assessment_task"] == task)
                & (df["prompt_setting"] == setting)
            ]
            expected = EXPECTED_CASES[task] * len(FINAL_MODELS) * EXPECTED_TESTERS
            rows.append(
                {
                    "assessment_task": task,
                    "prompt_setting": setting,
                    "rows": len(part),
                    "expected_rows": expected,
                    "missing_rows": expected - len(part),
                    "unique_testers": part["tester_id"].nunique(),
                    "unique_cases": part["case_id"].nunique(),
                    "unique_models": part["model"].nunique(),
                    "mean_score": part["score"].mean(),
                    "median_score": part["score"].median(),
                }
            )
    return pd.DataFrame(rows)


def plot_one_boxplot(
    df: pd.DataFrame,
    *,
    task: str,
    setting: str,
    output_path_pdf: Path,
    output_path_png: Path,
) -> None:
    part = df[
        (df["assessment_task"] == task)
        & (df["prompt_setting"] == setting)
    ].copy()
    if part.empty:
        raise RuntimeError(f"No rows for task={task}, setting={setting}")

    sns.set_theme(style="whitegrid", context="paper", font_scale=1.05)
    fig, ax = plt.subplots(figsize=(8.6, 3.1))
    sns.boxplot(
        data=part,
        x="model_label",
        y="score",
        order=[MODEL_LABELS[m] for m in FINAL_MODELS],
        ax=ax,
        width=0.58,
        linewidth=0.9,
        fliersize=1.8,
        color="#9ecae1",
    )
    sns.stripplot(
        data=part.sample(min(len(part), 3000), random_state=7),
        x="model_label",
        y="score",
        order=[MODEL_LABELS[m] for m in FINAL_MODELS],
        ax=ax,
        color="#1f2937",
        size=1.2,
        alpha=0.12,
        jitter=0.22,
    )
    ax.set_title(f"{TASK_LABELS[task]} - {SETTING_LABELS[setting]}")
    ax.set_xlabel("")
    ax.set_ylabel("LLM tester score")
    ax.set_ylim(0.7, 5.3)
    ax.set_yticks([1, 2, 3, 4, 5])
    ax.tick_params(axis="x", rotation=30)
    ax.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(output_path_pdf, bbox_inches="tight")
    fig.savefig(output_path_png, dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_overview(df: pd.DataFrame, *, output_path_pdf: Path, output_path_png: Path) -> None:
    sns.set_theme(style="whitegrid", context="paper", font_scale=0.95)
    fig, axes = plt.subplots(2, 3, figsize=(16, 7.2), sharey=True)
    order = [MODEL_LABELS[m] for m in FINAL_MODELS]
    for row_idx, task in enumerate(("valid", "invalid")):
        for col_idx, setting in enumerate(PROMPT_SETTINGS):
            ax = axes[row_idx, col_idx]
            part = df[
                (df["assessment_task"] == task)
                & (df["prompt_setting"] == setting)
            ]
            sns.boxplot(
                data=part,
                x="model_label",
                y="score",
                order=order,
                ax=ax,
                width=0.55,
                linewidth=0.8,
                fliersize=1.3,
                color="#9ecae1",
            )
            ax.set_title(f"{TASK_LABELS[task]} - {SETTING_LABELS[setting]}")
            ax.set_xlabel("")
            ax.set_ylabel("LLM tester score" if col_idx == 0 else "")
            ax.set_ylim(0.7, 5.3)
            ax.set_yticks([1, 2, 3, 4, 5])
            ax.tick_params(axis="x", rotation=35, labelsize=8)
            ax.grid(axis="x", visible=False)
    fig.tight_layout()
    fig.savefig(output_path_pdf, bbox_inches="tight")
    fig.savefig(output_path_png, dpi=300, bbox_inches="tight")
    plt.close(fig)


def likert_distribution_by_model(part: pd.DataFrame) -> tuple[pd.DataFrame, pd.Series]:
    labels = [MODEL_LABELS[m] for m in FINAL_MODELS]
    counts = (
        part.groupby(["model_label", "score"], observed=False)
        .size()
        .unstack(fill_value=0)
        .reindex(index=labels, fill_value=0)
        .reindex(columns=[1, 2, 3, 4, 5], fill_value=0)
    )
    percentages = counts.div(counts.sum(axis=1), axis=0).fillna(0) * 100.0
    means = part.groupby("model_label", observed=False)["score"].mean().reindex(labels)
    return percentages, means


def draw_likert_distribution(
    ax: plt.Axes,
    part: pd.DataFrame,
    *,
    title: str,
    show_ylabel: bool,
    show_xlabel: bool,
    annotate_segments: bool,
) -> None:
    percentages, means = likert_distribution_by_model(part)
    labels = list(percentages.index)
    x = np.arange(len(labels))
    width = 0.72
    bottom = np.zeros(len(labels), dtype=float)

    for score in [1, 2, 3, 4, 5]:
        vals = percentages[score].to_numpy(dtype=float)
        ax.bar(
            x,
            vals,
            width,
            bottom=bottom,
            color=LIKERT_COLORS[score],
            edgecolor="#333333",
            linewidth=0.55,
        )
        if annotate_segments:
            for idx, value in enumerate(vals):
                if value >= 7.0:
                    ax.text(
                        x[idx],
                        bottom[idx] + value / 2.0,
                        f"{value:.1f}%",
                        ha="center",
                        va="center",
                        fontsize=6.5,
                        color="#111111",
                    )
        bottom += vals

    for idx, mean_score in enumerate(means.to_numpy(dtype=float)):
        if np.isnan(mean_score):
            continue
        y_pos = mean_score / 5.0 * 100.0
        ax.hlines(
            y_pos,
            x[idx] - width / 2.0,
            x[idx] + width / 2.0,
            colors="red",
            linestyles=(0, (4, 2)),
            linewidth=0.9,
            zorder=5,
        )
        ax.text(
            x[idx],
            min(y_pos + 1.5, 101.5),
            f"{mean_score:.2f}",
            ha="center",
            va="bottom",
            color="red",
            fontsize=7.5,
            fontweight="bold",
        )

    ax.set_title(title, fontsize=11, pad=8)
    ax.set_ylim(0, 104)
    ax.set_yticks(np.arange(0, 101, 20))
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=35, ha="right")
    if show_ylabel:
        ax.set_ylabel("Percentage distribution of testers' 5-point Likert scale evaluation (%)")
    else:
        ax.set_ylabel("")
    ax.set_xlabel("LLM" if show_xlabel else "")
    ax.grid(axis="y", linestyle="--", linewidth=0.5, alpha=0.65)
    ax.grid(axis="x", visible=False)


def likert_legend_handles() -> list[Patch]:
    return [
        Patch(facecolor=LIKERT_COLORS[score], edgecolor="#333333", label=LIKERT_LABELS[score])
        for score in [1, 2, 3, 4, 5]
    ]


def plot_likert_six_panel(
    df: pd.DataFrame,
    *,
    output_path_pdf: Path,
    output_path_png: Path,
) -> None:
    sns.set_theme(style="whitegrid", context="paper", font_scale=1.0)
    fig, axes = plt.subplots(2, 3, figsize=(18.5, 8.4), sharey=True)
    for row_idx, task in enumerate(("valid", "invalid")):
        for col_idx, setting in enumerate(PROMPT_SETTINGS):
            part = df[
                (df["assessment_task"] == task)
                & (df["prompt_setting"] == setting)
            ]
            draw_likert_distribution(
                axes[row_idx, col_idx],
                part,
                title=f"{TASK_LABELS[task]} - {SETTING_LABELS[setting]}",
                show_ylabel=col_idx == 0,
                show_xlabel=row_idx == 1,
                annotate_segments=False,
            )
    fig.legend(
        handles=likert_legend_handles(),
        loc="upper center",
        ncol=5,
        frameon=False,
        bbox_to_anchor=(0.5, 1.015),
    )
    fig.tight_layout(rect=(0, 0, 1, 0.965))
    fig.savefig(output_path_pdf, bbox_inches="tight")
    fig.savefig(output_path_png, dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_likert_task_panel(
    df: pd.DataFrame,
    *,
    output_path_pdf: Path,
    output_path_png: Path,
) -> None:
    sns.set_theme(style="whitegrid", context="paper", font_scale=1.05)
    fig, axes = plt.subplots(1, 2, figsize=(15.5, 4.9), sharey=True)
    for col_idx, task in enumerate(("valid", "invalid")):
        part = df[df["assessment_task"] == task]
        draw_likert_distribution(
            axes[col_idx],
            part,
            title=TASK_LABELS[task],
            show_ylabel=col_idx == 0,
            show_xlabel=True,
            annotate_segments=True,
        )
    fig.legend(
        handles=likert_legend_handles(),
        loc="upper center",
        ncol=5,
        frameon=False,
        bbox_to_anchor=(0.5, 1.04),
    )
    fig.tight_layout(rect=(0, 0, 1, 0.92))
    fig.savefig(output_path_pdf, bbox_inches="tight")
    fig.savefig(output_path_png, dpi=300, bbox_inches="tight")
    plt.close(fig)


def write_markdown_summary(
    *,
    completeness_df: pd.DataFrame,
    kendall_df: pd.DataFrame,
    figure_dir: Path,
    md_path: Path,
) -> None:
    lines = [
        "# LLM Tester Assessment Figures and Kendall's W",
        "",
        "## Data Completeness",
        "",
        completeness_df.to_markdown(index=False, floatfmt=".3f"),
        "",
        "## Kendall's W",
        "",
        "Overall agreement across all PPTR and Bug assessment units:",
        "",
        kendall_df.to_markdown(index=False, floatfmt=".4f"),
        "",
        "## Figures",
        "",
        f"- Overview: `{figure_dir / 'rq3_llm_tester_boxplots_overview.pdf'}`",
        f"- Likert six-panel distribution: `{figure_dir / 'rq3_llm_tester_likert_6panels.pdf'}`",
        f"- Likert task-level distribution: `{figure_dir / 'rq3_llm_tester_likert_task_panels.pdf'}`",
    ]
    for task in ("valid", "invalid"):
        for setting in PROMPT_SETTINGS:
            lines.append(
                f"- {TASK_LABELS[task]} / {setting}: "
                f"`{figure_dir / f'rq3_llm_tester_boxplot_{task}_{setting}.pdf'}`"
            )
    md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--score-table", default=DEFAULT_SCORE_TABLE)
    parser.add_argument("--prompt-version", default=DEFAULT_PROMPT_VERSION)
    parser.add_argument(
        "--score-judge-model",
        default=DEFAULT_SCORE_JUDGE_MODEL,
        help=(
            "Optional judge_model filter. Empty means merge scores across judge models "
            "and keep the latest score per analytical slot."
        ),
    )
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    parser.add_argument("--figure-dir", default=str(DEFAULT_FIGURE_DIR))
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    figure_dir = Path(args.figure_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    figure_dir.mkdir(parents=True, exist_ok=True)

    print("[FETCH] reading LLM tester assessment scores", flush=True)
    df = fetch_scores(
        score_table=args.score_table,
        prompt_version=args.prompt_version,
        score_judge_model=args.score_judge_model,
    )
    raw_csv = output_dir / "llm_tester_assessment_scores.csv"
    df.to_csv(raw_csv, index=False)
    print(f"[FETCH_DONE] rows={len(df)} csv={raw_csv}", flush=True)

    completeness_df = build_completeness_summary(df)
    completeness_csv = output_dir / "llm_tester_assessment_completeness.csv"
    completeness_df.to_csv(completeness_csv, index=False)
    print(f"[COMPLETENESS] {completeness_csv}", flush=True)
    print(completeness_df.to_string(index=False), flush=True)

    kendall_df = build_kendall_summary(df)
    kendall_csv = output_dir / "llm_tester_assessment_kendalls_w.csv"
    kendall_df.to_csv(kendall_csv, index=False)
    print(f"[KENDALL] {kendall_csv}", flush=True)
    print(kendall_df.to_string(index=False), flush=True)

    for task in ("valid", "invalid"):
        for setting in PROMPT_SETTINGS:
            stem = f"rq3_llm_tester_boxplot_{task}_{setting}"
            pdf_path = figure_dir / f"{stem}.pdf"
            png_path = figure_dir / f"{stem}.png"
            plot_one_boxplot(
                df,
                task=task,
                setting=setting,
                output_path_pdf=pdf_path,
                output_path_png=png_path,
            )
            print(f"[PLOT] {pdf_path}", flush=True)

    overview_pdf = figure_dir / "rq3_llm_tester_boxplots_overview.pdf"
    overview_png = figure_dir / "rq3_llm_tester_boxplots_overview.png"
    plot_overview(df, output_path_pdf=overview_pdf, output_path_png=overview_png)
    print(f"[PLOT] {overview_pdf}", flush=True)

    likert_6_pdf = figure_dir / "rq3_llm_tester_likert_6panels.pdf"
    likert_6_png = figure_dir / "rq3_llm_tester_likert_6panels.png"
    plot_likert_six_panel(df, output_path_pdf=likert_6_pdf, output_path_png=likert_6_png)
    print(f"[PLOT] {likert_6_pdf}", flush=True)

    likert_task_pdf = figure_dir / "rq3_llm_tester_likert_task_panels.pdf"
    likert_task_png = figure_dir / "rq3_llm_tester_likert_task_panels.png"
    plot_likert_task_panel(df, output_path_pdf=likert_task_pdf, output_path_png=likert_task_png)
    print(f"[PLOT] {likert_task_pdf}", flush=True)

    md_path = output_dir / "llm_tester_assessment_summary.md"
    write_markdown_summary(
        completeness_df=completeness_df,
        kendall_df=kendall_df,
        figure_dir=figure_dir,
        md_path=md_path,
    )
    print(f"[DONE] summary={md_path}", flush=True)


if __name__ == "__main__":
    main()
