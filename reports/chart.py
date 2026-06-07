"""
reports/chart.py
CPOI Platform -- Score Trajectory Chart Generator

Produces a matplotlib chart of the OEI Composite Score over time for
embedding in the OEIL Monthly Brief. Returns a base64-encoded PNG string
suitable for inclusion in a Jinja2 template <img src="data:image/png;...">
tag and for decoding by ReportLab in the PDF builder.

Public interface:
  generate_trajectory_chart(score_history: list[dict]) -> str

Returns an empty string when fewer than two periods are present (no
trajectory to draw). The caller must handle the empty-string case and
render a placeholder message instead of an image.
"""

import base64
import io
import json
import logging
from datetime import datetime, timezone
from typing import Any

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
from matplotlib.patches import Rectangle

matplotlib.use("Agg")  # Non-interactive backend; no display required.

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload)


def _build_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(_JsonFormatter())
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


log = _build_logger("cpoi.reports.chart")

# ---------------------------------------------------------------------------
# Visual constants -- CP monochrome palette
# ---------------------------------------------------------------------------

_BAND_COLORS: list[tuple[float, float, tuple[float, float, float]]] = [
    (0,   20,  (0.94, 0.97, 0.94)),   # Low Risk       -- pale green
    (20,  40,  (0.97, 0.97, 0.90)),   # Moderate       -- pale yellow
    (40,  60,  (1.00, 0.95, 0.85)),   # Elevated       -- pale amber
    (60,  80,  (1.00, 0.88, 0.85)),   # High Risk      -- pale red
    (80,  100, (0.95, 0.82, 0.82)),   # Critical       -- deeper red
]

_BAND_LABELS: list[tuple[float, str]] = [
    (10,  "Low Risk"),
    (30,  "Moderate"),
    (50,  "Elevated"),
    (70,  "High Risk"),
    (90,  "Critical"),
]

_LINE_COLOR: str = "#1a1a2e"       # Near-black for the score line
_MARKER_COLOR: str = "#c0392b"     # Deep red for data point markers
_GRID_COLOR: str = "#cccccc"
_BAND_LINE_COLOR: str = "#999999"
_FONT_FAMILY: str = "DejaVu Sans"

# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def generate_trajectory_chart(
    score_history: list[dict[str, Any]],
) -> str:
    """
    Generate a score trajectory chart and return it as a base64 PNG string.

    The chart plots OEI Composite Score (y-axis, 0-100) against reporting
    period date (x-axis). Horizontal band reference lines are drawn at 20,
    40, 60, and 80. Background bands are colour-coded by risk classification.
    The most recent period's score is annotated with its value.

    Args:
        score_history: List of dicts, each containing at minimum:
                         'period_date'        -- ISO date string (YYYY-MM-DD)
                         'oei_composite_score' -- integer 0-100
                       Must be ordered oldest-to-newest. The caller is
                       responsible for ordering.

    Returns:
        str: Base64-encoded PNG image string (no data URI prefix).
             Empty string if fewer than 2 periods are present.

    Raises:
        ValueError: if any score value is outside [0, 100].
        KeyError:   if required keys are absent from any history entry.
    """
    if len(score_history) < 2:
        log.info(
            "Trajectory chart skipped: %d period(s) present, minimum 2 required.",
            len(score_history),
        )
        return ""

    # Validate and extract data.
    dates: list[str] = []
    scores: list[int] = []

    for entry in score_history:
        period = entry["period_date"]
        score = int(entry["oei_composite_score"])
        if not (0 <= score <= 100):
            raise ValueError(
                f"Score {score} for period '{period}' is outside the valid "
                "range [0, 100]."
            )
        dates.append(period)
        scores.append(score)

    x_indices = list(range(len(dates)))

    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=150)
    fig.patch.set_facecolor("white")
    ax.set_facecolor("white")

    # Draw risk band backgrounds.
    for low, high, color in _BAND_COLORS:
        ax.axhspan(low, high, facecolor=color, alpha=0.55, zorder=0)

    # Draw band boundary lines.
    for boundary in (20, 40, 60, 80):
        ax.axhline(
            y=boundary, color=_BAND_LINE_COLOR, linewidth=0.8,
            linestyle="--", zorder=1,
        )

    # Band labels on the right margin.
    for y_pos, label in _BAND_LABELS:
        ax.text(
            len(dates) - 0.5 + 0.05, y_pos, label,
            ha="left", va="center", fontsize=7,
            color="#555555", fontfamily=_FONT_FAMILY,
            clip_on=False,
        )

    # Score trajectory line.
    ax.plot(
        x_indices, scores,
        color=_LINE_COLOR, linewidth=2.2,
        marker="o", markersize=7,
        markerfacecolor=_MARKER_COLOR, markeredgecolor=_LINE_COLOR,
        markeredgewidth=1.2, zorder=3,
    )

    # Annotate each data point with its value.
    for i, (x, y) in enumerate(zip(x_indices, scores)):
        offset = 6 if y < 90 else -10
        ax.annotate(
            str(y),
            xy=(x, y),
            xytext=(0, offset),
            textcoords="offset points",
            ha="center", va="bottom",
            fontsize=8, fontweight="bold",
            color=_LINE_COLOR, fontfamily=_FONT_FAMILY,
        )

    # Axes configuration.
    ax.set_xlim(-0.5, len(dates) - 0.5)
    ax.set_ylim(0, 100)
    ax.set_xticks(x_indices)
    ax.set_xticklabels(dates, rotation=30, ha="right", fontsize=8, fontfamily=_FONT_FAMILY)
    ax.set_yticks([0, 20, 40, 60, 80, 100])
    ax.yaxis.set_major_formatter(mticker.FormatStrFormatter("%d"))
    ax.tick_params(axis="y", labelsize=8)
    ax.set_ylabel("OEI Composite Score", fontsize=9, fontfamily=_FONT_FAMILY, labelpad=8)
    ax.set_xlabel("Reporting Period", fontsize=9, fontfamily=_FONT_FAMILY, labelpad=8)
    ax.set_title(
        "OEI Composite Score Trajectory",
        fontsize=11, fontweight="bold",
        fontfamily=_FONT_FAMILY, pad=12,
    )

    ax.grid(axis="y", color=_GRID_COLOR, linewidth=0.5, zorder=2)
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)

    fig.tight_layout(rect=[0, 0, 0.88, 1])

    # Encode to base64.
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    encoded = base64.b64encode(buf.read()).decode("utf-8")

    log.info(
        "Trajectory chart generated: %d periods, %d bytes (base64).",
        len(dates),
        len(encoded),
    )
    return encoded
