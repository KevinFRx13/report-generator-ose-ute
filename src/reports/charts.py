"""Generate matplotlib charts for the monthly PDF report."""

from pathlib import Path
from collections import defaultdict

import matplotlib
matplotlib.use("Agg")  # non-interactive backend, safe for scheduled scripts
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np

# ── Palette ───────────────────────────────────────────────────────────────────
_OSE_COLOR = "#1a78c2"
_UTE_COLOR = "#e6a817"
_LOC_PALETTE = ["#2E86AB", "#A23B72", "#F18F01", "#C73E1D", "#3B1F2B"]
_PVL_COLORS  = ["#e74c3c", "#3498db", "#2ecc71"]   # Punta, Valle, Llano

MONTH_NAMES_ES = [
    "", "Ene", "Feb", "Mar", "Abr", "May", "Jun",
    "Jul", "Ago", "Set", "Oct", "Nov", "Dic",
]


def _fmt_uyu(x, _pos):
    return f"${x:,.0f}".replace(",", ".")


def _fmt_kwh(x, _pos):
    return f"{x:,.0f}".replace(",", ".")


def _month_label(ym: str) -> str:
    year, month = ym.split("-")
    return f"{MONTH_NAMES_ES[int(month)]} {year[2:]}"


def _pivot(rows: list[dict], value_key: str) -> tuple[list[str], dict[str, list[float]]]:
    """
    Pivot rows into (sorted months list, location → values list).
    Keeps only the last 12 months; missing entries become 0.0.
    """
    all_months = sorted({r["month"] for r in rows})[-12:]
    by_loc: dict[str, dict[str, float]] = defaultdict(dict)
    for r in rows:
        if r["month"] in all_months:
            by_loc[r["location_name"]][r["month"]] = r[value_key] or 0.0
    return all_months, {
        loc: [m_map.get(m, 0.0) for m in all_months]
        for loc, m_map in by_loc.items()
    }


# ── Global (all-location) charts ──────────────────────────────────────────────

def _bar_chart(months: list[str], series: dict[str, list[float]],
               title: str, ylabel: str, formatter,
               colors: list[str], out_path: Path,
               show_total: bool = True):
    if not months:
        return
    fig, ax = plt.subplots(figsize=(13, 4.5))
    n_locs, n_months = len(series), len(months)
    x = np.arange(n_months)
    bar_width = min(0.7 / max(n_locs, 1), 0.18)
    totals = [0.0] * n_months
    for i, (loc, values) in enumerate(series.items()):
        offset = (i - (n_locs - 1) / 2) * bar_width
        ax.bar(x + offset, values, bar_width, label=loc,
               color=colors[i % len(colors)], zorder=2)
        totals = [t + v for t, v in zip(totals, values)]
    if show_total and n_locs > 1:
        ax.plot(x, totals, "k--o", linewidth=1.5, markersize=4, label="Total", zorder=3)
    ax.set_title(title, fontsize=12, fontweight="bold", pad=10)
    ax.set_ylabel(ylabel, fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([_month_label(m) for m in months],
                       rotation=45, ha="right", fontsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(formatter))
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.3, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


def _reactive_chart(months: list[str], series: dict[str, list[float]],
                    out_path: Path):
    if not months:
        return
    fig, ax = plt.subplots(figsize=(13, 4.0))
    n_locs = len(series)
    x = np.arange(len(months))
    bar_width = min(0.7 / max(n_locs, 1), 0.18)
    for i, (loc, values) in enumerate(series.items()):
        offset = (i - (n_locs - 1) / 2) * bar_width
        ax.bar(x + offset, values, bar_width,
               color=["#c0392b" if v > 0 else "#27ae60" for v in values],
               label=loc, zorder=2)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title("Cargo por energía reactiva ($ UYU)\nVerde = descuento  /  Rojo = cargo adicional",
                 fontsize=11, fontweight="bold", pad=10)
    ax.set_ylabel("$ UYU", fontsize=9)
    ax.set_xticks(x)
    ax.set_xticklabels([_month_label(m) for m in months],
                       rotation=45, ha="right", fontsize=8)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_uyu))
    ax.legend(fontsize=8, loc="upper left")
    ax.grid(axis="y", alpha=0.3, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(out_path, dpi=150, bbox_inches="tight")
    plt.close(fig)


# ── Small per-location charts ─────────────────────────────────────────────────

def _small_bar(months: list[str], values: list[float],
               title: str, ylabel: str, formatter, color: str,
               out_path: Path):
    """Single-series compact bar chart."""
    if not months:
        return
    fig, ax = plt.subplots(figsize=(6, 2.8))
    x = np.arange(len(months))
    ax.bar(x, values, 0.65, color=color, zorder=2)
    ax.set_title(title, fontsize=9, fontweight="bold", pad=5)
    ax.set_ylabel(ylabel, fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels([_month_label(m) for m in months],
                       rotation=45, ha="right", fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(formatter))
    ax.grid(axis="y", alpha=0.3, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)


def _small_grouped_bar(months: list[str], series: dict[str, list[float]],
                       title: str, ylabel: str, formatter,
                       colors: list[str], out_path: Path):
    """Multi-series compact grouped bar chart."""
    if not months:
        return
    fig, ax = plt.subplots(figsize=(6, 2.8))
    n = len(series)
    x = np.arange(len(months))
    bar_w = min(0.7 / max(n, 1), 0.22)
    for i, (label, vals) in enumerate(series.items()):
        offset = (i - (n - 1) / 2) * bar_w
        ax.bar(x + offset, vals, bar_w, label=label,
               color=colors[i % len(colors)], zorder=2)
    ax.set_title(title, fontsize=9, fontweight="bold", pad=5)
    ax.set_ylabel(ylabel, fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels([_month_label(m) for m in months],
                       rotation=45, ha="right", fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(formatter))
    ax.legend(fontsize=7, loc="upper left")
    ax.grid(axis="y", alpha=0.3, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)


def _small_reactive(months: list[str], values: list[float],
                    title: str, out_path: Path):
    """Compact reactive chart — green discount / red charge."""
    if not months:
        return
    fig, ax = plt.subplots(figsize=(6, 2.8))
    x = np.arange(len(months))
    ax.bar(x, values, 0.65,
           color=["#c0392b" if v > 0 else "#27ae60" for v in values],
           zorder=2)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_title(title, fontsize=9, fontweight="bold", pad=5)
    ax.set_ylabel("$ UYU", fontsize=7)
    ax.set_xticks(x)
    ax.set_xticklabels([_month_label(m) for m in months],
                       rotation=45, ha="right", fontsize=7)
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(_fmt_uyu))
    ax.grid(axis="y", alpha=0.3, zorder=0)
    ax.spines[["top", "right"]].set_visible(False)
    plt.tight_layout()
    fig.savefig(out_path, dpi=130, bbox_inches="tight")
    plt.close(fig)


def generate_ose_location_charts(location_name: str, ose_rows: list[dict],
                                  output_dir: Path) -> dict[str, Path]:
    """Generate gasto + consumo charts for one OSE location."""
    loc_rows = [r for r in ose_rows if r["location_name"] == location_name]
    months = sorted({r["month"] for r in loc_rows})[-12:]
    by_m = {r["month"]: r for r in loc_rows}
    val = lambda m, k: (by_m.get(m) or {}).get(k) or 0.0  # noqa: E731

    safe = location_name.replace(" ", "_").replace(".", "").replace("/", "_")
    paths: dict[str, Path] = {}
    if not months:
        return paths

    p = output_dir / f"ose_loc_gasto_{safe}.png"
    _small_bar(months, [val(m, "total") for m in months],
               "Gasto mensual ($)", "$ UYU", _fmt_uyu, _OSE_COLOR, p)
    paths["gasto"] = p

    p = output_dir / f"ose_loc_consumo_{safe}.png"
    _small_bar(months, [val(m, "consumption") for m in months],
               "Consumo (m³)", "m³",
               lambda x, _: f"{x:.1f}", _OSE_COLOR, p)
    paths["consumo"] = p

    return paths


def generate_ute_location_charts(location_name: str, ute_rows: list[dict],
                                  output_dir: Path) -> dict[str, Path]:
    """Generate gasto, consumo kWh, Punta/Valle/Llano, and reactiva charts for one UTE location."""
    loc_rows = [r for r in ute_rows if r["location_name"] == location_name]
    months = sorted({r["month"] for r in loc_rows})[-12:]
    by_m = {r["month"]: r for r in loc_rows}
    val = lambda m, k: (by_m.get(m) or {}).get(k) or 0.0  # noqa: E731

    safe = location_name.replace(" ", "_").replace(".", "").replace("/", "_")
    paths: dict[str, Path] = {}
    if not months:
        return paths

    p = output_dir / f"ute_loc_gasto_{safe}.png"
    _small_bar(months, [val(m, "total") for m in months],
               "Gasto mensual ($)", "$ UYU", _fmt_uyu, _UTE_COLOR, p)
    paths["gasto"] = p

    p = output_dir / f"ute_loc_kwh_{safe}.png"
    _small_bar(months, [val(m, "kwh_total") for m in months],
               "Consumo activo (kWh)", "kWh", _fmt_kwh, _UTE_COLOR, p)
    paths["consumo_kwh"] = p

    pvl_series = {
        "Punta": [val(m, "kwh_punta") for m in months],
        "Valle": [val(m, "kwh_valle") for m in months],
        "Llano": [val(m, "kwh_llano") for m in months],
    }
    if any(v for series in pvl_series.values() for v in series):
        p = output_dir / f"ute_loc_pvl_{safe}.png"
        _small_grouped_bar(months, pvl_series, "Punta / Valle / Llano (kWh)", "kWh",
                           _fmt_kwh, _PVL_COLORS, p)
        paths["pvl"] = p

    reactiva_vals = [val(m, "reactive_charge") for m in months]
    if any(abs(v) > 0 for v in reactiva_vals):
        p = output_dir / f"ute_loc_reactiva_{safe}.png"
        _small_reactive(months, reactiva_vals, "Cargo reactivo ($)", p)
        paths["reactiva"] = p

    return paths


# ── Public entry point ────────────────────────────────────────────────────────

def generate_charts(ose_rows: list[dict], ute_rows: list[dict],
                    output_dir: Path) -> dict[str, Path]:
    """Generate all global (all-location) charts; returns chart_name → file path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}

    ose_months, ose_spending = _pivot(ose_rows, "total")
    if ose_months:
        p = output_dir / "ose_gasto.png"
        _bar_chart(ose_months, ose_spending,
                   "Gasto por período de consumo – Agua (OSE)",
                   "$ UYU", _fmt_uyu, _LOC_PALETTE, p)
        paths["ose_gasto"] = p

        _, ose_m3 = _pivot(ose_rows, "consumption")
        p = output_dir / "ose_consumo.png"
        _bar_chart(ose_months, ose_m3,
                   "Consumo de agua por período (m³)",
                   "m³", lambda x, _: f"{x:.1f}",
                   _LOC_PALETTE, p)
        paths["ose_consumo"] = p

    ute_months, ute_spending = _pivot(ute_rows, "total")
    if ute_months:
        p = output_dir / "ute_gasto.png"
        _bar_chart(ute_months, ute_spending,
                   "Gasto por período de consumo – Electricidad (UTE)",
                   "$ UYU", _fmt_uyu, _LOC_PALETTE, p)
        paths["ute_gasto"] = p

        _, ute_kwh = _pivot(ute_rows, "kwh_total")
        p = output_dir / "ute_consumo_activo.png"
        _bar_chart(ute_months, ute_kwh,
                   "Consumo eléctrico activo por período (kWh)",
                   "kWh", _fmt_kwh, _LOC_PALETTE, p)
        paths["ute_consumo_activo"] = p

        _, ute_reactive = _pivot(ute_rows, "reactive_charge")
        p = output_dir / "ute_reactiva.png"
        _reactive_chart(ute_months, ute_reactive, p)
        paths["ute_reactiva"] = p

    return paths
