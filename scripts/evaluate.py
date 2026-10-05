#!/usr/bin/env python3
"""Evaluate Rhythm's rule on simulated households and write docs/evaluation.md (+ a chart).

    python scripts/evaluate.py            # about a minute
    python scripts/evaluate.py --quick    # fewer households, a few seconds, prints only (no files written)

All data is SYNTHETIC. The chart needs matplotlib (pip install matplotlib); without it,
only the Markdown report is written.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from datetime import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from rhythm.config import Settings
from rhythm.evaluation import evaluate, margin_sweep

MARGINS = [0, 5, 10, 15, 20, 30, 45, 60]
DOCS = Path(__file__).resolve().parents[1] / "docs"
BLUE, ORANGE = "#2a78d6", "#eb6834"  # categorical slots 1 and 2, validated for colour-blind separation


def write_chart(rows: list[dict], path: Path) -> bool:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.ticker  # noqa: F401
    except ImportError:
        return False
    fig, axes = plt.subplots(1, 2, figsize=(10, 3.8), dpi=150)
    panels = (
        ("false_alerts_per_month", "False alerts per household per month", None),
        ("detection_rate", "Share of true changes caught", (0, 1.05)),
    )
    for ax, (key, title, ylim) in zip(axes, panels):
        for with_floor, color, label in ((True, BLUE, "with minimum wait"), (False, ORANGE, "without")):
            series = [r for r in rows if r["floor"] is with_floor]
            xs = [r["margin"] for r in series]
            ys = [r[key] for r in series]
            ax.plot(xs, ys, color=color, linewidth=2, marker="o", markersize=5, label=label)
        ax.axvline(15, color="#b0afaa", linewidth=1, linestyle="--")
        if ylim:
            ax.set_ylim(*ylim)
        low, high = ax.get_ylim()
        at_top = key == "false_alerts_per_month"  # keep the label clear of the lines in each panel
        ax.text(
            15.8,
            high - 0.03 * (high - low) if at_top else low + 0.03 * (high - low),
            "default 15 min",
            color="#52514e",
            fontsize=8,
            va="top" if at_top else "bottom",
        )
        ax.set_title(title, fontsize=10, color="#0b0b0b", loc="left")
        ax.set_xlabel("Margin after her usual time (minutes)", fontsize=9, color="#52514e")
        ax.grid(axis="y", color="#e8e7e3", linewidth=0.8)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        ax.tick_params(colors="#52514e", labelsize=8)
    axes[1].yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0))
    for ax in axes:
        ax.legend(frameon=False, fontsize=8, loc="center right")
    fig.suptitle("Rhythm on simulated households (synthetic data)", fontsize=11, x=0.01, ha="left")
    fig.tight_layout()
    fig.savefig(path, facecolor="#fcfcfb")
    return True


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--quick", action="store_true", help="20 households, print only (does not overwrite docs)")
    args = parser.parse_args()
    households = 20 if args.quick else 60
    base = replace(Settings.from_env(), ring_access_token="")  # pure simulation, no Ring connection
    default = evaluate(base, households=households)
    careful = evaluate(
        replace(
            base,
            max_margin_minutes=5,
            minimum_wait_weekday=time(9, 30),
            minimum_wait_weekend=time(10, 30),
            fallback_weekday=time(9, 30),
            fallback_weekend=time(10, 30),
        ),
        households=households,
    )
    rows = margin_sweep(base, MARGINS, households=households)
    chart = False if args.quick else write_chart(rows, DOCS / "evaluation.png")

    lines = [
        "# Evaluation on simulated households",
        "",
        "**All data here is synthetic.** It shows how Rhythm's rule behaves on realistic routines; it is not",
        "a measurement on real people. Regenerate it with `python scripts/evaluate.py`.",
        "",
        "## Setup",
        "",
        f"- {households} simulated households, 16 weeks each; the first 14 days are the silent learning period.",
        "- Each household has its own weekday wake time (06:30–08:30), a weekend time 20–90 min later,",
        "  10–25 min of day-to-day noise, and ordinary sleep-ins (5% of days, 30–90 min late).",
        "- Distractions: night events on 20% of days (01:00–04:30) and doorbell presses on 15% of days.",
        '- One announced 3–6 day trip per household (marked with "She\'s away").',
        "- True changes the family would want to hear about: late mornings (1 in 40 days, first activity",
        "  2–4 h after her usual time) and silent days (1 in 120 days, no activity at all).",
        "- The rule is the app's own code (`rhythm/rules.py`), run with the default settings.",
        "",
        "## Results",
        "",
        "| Measure | Standard (default) | Careful |",
        "|---|---|---|",
        f"| False alerts per household per month | {default['false_alerts_per_month']:.2f} "
        f"({default['false_alerts']} in {default['normal_days']} normal days) | {careful['false_alerts_per_month']:.2f} |",
        f"| Silent days caught | {default['silent_caught']} of {default['silent_total']} "
        f"| {careful['silent_caught']} of {careful['silent_total']} |",
        f"| Late mornings caught | {default['late_caught']} of {default['late_total']} "
        f"| {careful['late_caught']} of {careful['late_total']} |",
        f"| Alerts during announced trips | {default['away_alerts']} | {careful['away_alerts']} |",
        f"| Median time from her usual time to the alert | {default['median_minutes_after_usual']:.0f} min "
        f"| {careful['median_minutes_after_usual']:.0f} min |",
        "",
        "## Margin and minimum wait",
        "",
        "| Margin (min) | Minimum wait | False alerts / month | Changes caught |",
        "|---|---|---|---|",
    ]
    for row in rows:
        lines.append(
            f"| {row['margin']} | {'yes' if row['floor'] else 'no'} | {row['false_alerts_per_month']:.2f} "
            f"| {row['detection_rate']:.0%} |"
        )
    lines += [
        "",
        (
            "![Margin versus false alerts and detection](evaluation.png)"
            if chart
            else "_Install matplotlib to draw the chart._"
        ),
        "",
        "## What this shows",
        "",
        "- The minimum wait (10:00 weekdays, 11:00 weekends) is what keeps false alerts near zero: without it,",
        "  small margins alert on ordinary sleep-ins.",
        "- Silent days are always caught. Late mornings are caught when they run past the minimum wait;",
        "  an early riser who gets up two hours late but still before 10:00 is not reported. That is the",
        "  deliberate trade-off: Rhythm prefers missing a mild delay to crying wolf.",
        '- The "Careful" sensitivity lowers the minimum wait by 30 minutes for families who want earlier notice.',
        "- Night events and doorbell presses never hid a silent morning, because they are not counted as her activity.",
        "- Limits: the routines are invented, real households vary in ways this model does not capture,",
        "  and the thresholds are not clinically calibrated.",
        "",
    ]
    if args.quick:
        print("\n".join(lines[16:25]))
        print("Quick run: docs/evaluation.md was not changed.")
        return
    (DOCS / "evaluation.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[16:25]))
    print(f"Wrote {DOCS / 'evaluation.md'}" + (f" and {DOCS / 'evaluation.png'}" if chart else ""))


if __name__ == "__main__":
    main()
