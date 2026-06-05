"""
Build the monthly PDF report using ReportLab Platypus.

Structure:
  1. Encabezado  (título, período, fecha de generación)
  2. Resumen ejecutivo — totales OSE + UTE + gran total
  3. Sección OSE  — tabla resumen → detalle + gráficos por cuenta
  4. Sección UTE  — tabla resumen → detalle + gráficos por cuenta
  5. Análisis histórico — gráficos globales últimos 12 meses
"""

import tempfile
from datetime import date, datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from src.storage.database import Database, OseBill, UteBill
from src.reports.charts import (
    generate_charts,
    generate_ose_location_charts,
    generate_ute_location_charts,
)

# ── Colores ────────────────────────────────────────────────────────────────────
C_OSE_DARK   = colors.HexColor("#1a78c2")
C_OSE_MED    = colors.HexColor("#4a9fd4")
C_OSE_LIGHT  = colors.HexColor("#d6e8f8")
C_UTE_DARK   = colors.HexColor("#c47c00")
C_UTE_MED    = colors.HexColor("#e0a030")
C_UTE_LIGHT  = colors.HexColor("#fdf0d0")
C_HEADER_BG  = colors.HexColor("#1c3d5a")
C_ALT_ROW    = colors.HexColor("#f4f6f8")
C_TOTAL_BG   = colors.HexColor("#dce8f2")
C_DISCOUNT   = colors.HexColor("#1a7c40")
C_CHARGE     = colors.HexColor("#a32222")

MONTH_NAMES_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]
MONTH_NAMES_ES_CAP = [m.capitalize() for m in MONTH_NAMES_ES]

PAGE_W, PAGE_H = A4
MARGIN = 1.8 * cm
CONTENT_W = PAGE_W - 2 * MARGIN


# ── Formato de valores ─────────────────────────────────────────────────────────

def _uyu(value: float | None, show_sign: bool = False) -> str:
    if value is None:
        return "—"
    formatted = f"{abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    if show_sign and value < 0:
        return f"-$ {formatted}"
    return f"$ {formatted}"


def _m3(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " m³"


def _kwh(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " kWh"


def _date(d: date | None) -> str:
    if d is None:
        return "—"
    return d.strftime("%d/%m/%Y")


# ── Estilos ────────────────────────────────────────────────────────────────────

def _build_styles() -> dict:
    return {
        "title": ParagraphStyle(
            "title", fontSize=16, textColor=colors.white,
            alignment=TA_CENTER, fontName="Helvetica-Bold", spaceAfter=2,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontSize=10, textColor=colors.HexColor("#cce0f5"),
            alignment=TA_CENTER, fontName="Helvetica",
        ),
        "section_ose": ParagraphStyle(
            "section_ose", fontSize=13, textColor=colors.white,
            fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=5,
            backColor=C_OSE_DARK, borderPadding=(5, 8, 5, 8),
        ),
        "section_ute": ParagraphStyle(
            "section_ute", fontSize=13, textColor=colors.white,
            fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=5,
            backColor=C_UTE_DARK, borderPadding=(5, 8, 5, 8),
        ),
        "section_hist": ParagraphStyle(
            "section_hist", fontSize=13, textColor=colors.white,
            fontName="Helvetica-Bold", spaceBefore=10, spaceAfter=5,
            backColor=C_HEADER_BG, borderPadding=(5, 8, 5, 8),
        ),
        "account_ose": ParagraphStyle(
            "account_ose", fontSize=10, textColor=C_OSE_DARK,
            fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=3,
        ),
        "account_ute": ParagraphStyle(
            "account_ute", fontSize=10, textColor=C_UTE_DARK,
            fontName="Helvetica-Bold", spaceBefore=8, spaceAfter=3,
        ),
        "no_bill": ParagraphStyle(
            "no_bill", fontSize=8, textColor=colors.grey,
            fontName="Helvetica-Oblique", leftIndent=8, spaceAfter=4,
        ),
        "summary_label": ParagraphStyle(
            "summary_label", fontSize=9, textColor=C_HEADER_BG,
            fontName="Helvetica-Bold", alignment=TA_CENTER,
            spaceBefore=12, spaceAfter=4,
        ),
    }


# ── Estilo de tablas ───────────────────────────────────────────────────────────

def _header_row_style(header_color) -> list:
    return [
        ("BACKGROUND",    (0, 0), (-1, 0), header_color),
        ("TEXTCOLOR",     (0, 0), (-1, 0), colors.white),
        ("FONTNAME",      (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE",      (0, 0), (-1, 0), 9),
        ("ALIGN",         (0, 0), (-1, -1), "CENTER"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("FONTSIZE",      (0, 1), (-1, -1), 9),
        ("TOPPADDING",    (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("GRID",          (0, 0), (-1, -1), 0.4, colors.HexColor("#c0cdd8")),
    ]


def _detail_ts() -> TableStyle:
    return TableStyle([
        ("FONTSIZE",      (0, 0), (-1, -1), 9),
        ("ALIGN",         (1, 0), (-1, -1), "RIGHT"),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW",     (0, 0), (-1, -2), 0.3, colors.HexColor("#e0e6eb")),
        ("FONTNAME",      (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME",      (2, 0), (2, -1), "Helvetica-Bold"),
    ])


# ── Helper: two charts side by side ───────────────────────────────────────────

def _side_by_side(path1: Path | None, path2: Path | None) -> Table:
    """Return a 2-column table with two compact chart images."""
    w = (CONTENT_W - 0.4 * cm) / 2
    h = w * 0.52

    def _img(p):
        if p and Path(p).exists():
            return Image(str(p), width=w, height=h)
        return Spacer(w, h)

    tbl = Table([[_img(path1), _img(path2)]], colWidths=[w + 0.2 * cm, w + 0.2 * cm])
    tbl.setStyle(TableStyle([
        ("VALIGN",        (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING",   (0, 0), (-1, -1), 0),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 0),
        ("TOPPADDING",    (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    return tbl


# ── Encabezado ─────────────────────────────────────────────────────────────────

def _build_header(year: int, month: int, styles: dict) -> list:
    tbl = Table(
        [
            [Paragraph("INFORME MENSUAL DE SERVICIOS PÚBLICOS", styles["title"])],
            [Paragraph(
                f"{MONTH_NAMES_ES_CAP[month]} {year}"
                f" &nbsp;—&nbsp; "
                f"<font size='8'>Generado el "
                f"{datetime.now().strftime('%d/%m/%Y %H:%M')}</font>",
                styles["subtitle"],
            )],
        ],
        colWidths=[CONTENT_W],
    )
    tbl.setStyle(TableStyle([
        ("BACKGROUND",    (0, 0), (-1, -1), C_HEADER_BG),
        ("VALIGN",        (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING",    (0, 0), (0, 0),   12),
        ("BOTTOMPADDING", (0, 0), (0, 0),   2),
        ("TOPPADDING",    (0, 1), (0, 1),   2),
        ("BOTTOMPADDING", (0, 1), (0, 1),   12),
        ("LEFTPADDING",   (0, 0), (-1, -1), 14),
        ("RIGHTPADDING",  (0, 0), (-1, -1), 14),
    ]))
    return [tbl, Spacer(1, 0.4 * cm)]


# ── Resumen ejecutivo ──────────────────────────────────────────────────────────

def _build_executive_summary(ose_names: list[str], ute_names: list[str],
                              ose_bills: dict, ute_bills: dict,
                              styles: dict) -> list:
    total_ose = sum(b.total_amount for b in ose_bills.values() if b)
    total_ute = sum(b.total_amount for b in ute_bills.values() if b)

    data = [
        ["Servicio", "Facturas recibidas", "Total del mes"],
        ["Agua (OSE)",
         f"{sum(1 for b in ose_bills.values() if b)} / {len(ose_names)}",
         _uyu(total_ose)],
        ["Electricidad (UTE)",
         f"{sum(1 for b in ute_bills.values() if b)} / {len(ute_names)}",
         _uyu(total_ute)],
        ["TOTAL GENERAL", "", _uyu(total_ose + total_ute)],
    ]
    col_w = [CONTENT_W * 0.45, CONTENT_W * 0.25, CONTENT_W * 0.30]
    tbl = Table(data, colWidths=col_w)
    ts = TableStyle(_header_row_style(C_HEADER_BG))
    ts.add("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, C_ALT_ROW])
    ts.add("BACKGROUND",    (0, -1), (-1, -1), C_TOTAL_BG)
    ts.add("FONTNAME",      (0, -1), (-1, -1), "Helvetica-Bold")
    ts.add("ALIGN",         (0, 1),  (0, -1),  "LEFT")
    tbl.setStyle(ts)
    return [Paragraph("Resumen ejecutivo", styles["summary_label"]), tbl, Spacer(1, 0.3 * cm)]


# ── Sección OSE ────────────────────────────────────────────────────────────────

def _build_ose_section(ose_names: list[str],
                       ose_bills: dict[str, OseBill | None],
                       ose_loc_charts: dict[str, dict],
                       styles: dict) -> list:
    content = [Paragraph("  Agua — OSE", styles["section_ose"])]

    rows = [["Cuenta / Ubicación", "Período", "Consumo (m³)", "Total (c/IVA)"]]
    for name in ose_names:
        b = ose_bills.get(name)
        if b:
            rows.append([name,
                         f"{_date(b.period_start)} – {_date(b.period_end)}",
                         _m3(b.consumption_m3), _uyu(b.total_amount)])
        else:
            rows.append([name, "Sin factura", "—", "—"])
    total_ose = sum(b.total_amount for b in ose_bills.values() if b)
    rows.append(["TOTAL OSE", "", "", _uyu(total_ose)])

    col_w = [CONTENT_W * 0.38, CONTENT_W * 0.28, CONTENT_W * 0.17, CONTENT_W * 0.17]
    tbl = Table(rows, colWidths=col_w)
    ts = TableStyle(_header_row_style(C_OSE_DARK))
    ts.add("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, C_OSE_LIGHT])
    ts.add("BACKGROUND",    (0, -1), (-1, -1), C_TOTAL_BG)
    ts.add("FONTNAME",      (0, -1), (-1, -1), "Helvetica-Bold")
    ts.add("ALIGN",         (0, 1),  (0, -1),  "LEFT")
    ts.add("ALIGN",         (2, 1),  (-1, -1), "RIGHT")
    tbl.setStyle(ts)
    content.append(tbl)
    content.append(Spacer(1, 0.3 * cm))

    for name in ose_names:
        b = ose_bills.get(name)
        content.append(Paragraph(f"Detalle: {name}", styles["account_ose"]))
        if not b:
            content.append(Paragraph("Sin factura para este período.", styles["no_bill"]))
            continue

        detail_rows = [
            ["Nº Factura", b.invoice_number, "Emisión", _date(b.emission_date)],
            ["Período", f"{_date(b.period_start)} – {_date(b.period_end)}",
             "Vencimiento", _date(b.due_date)],
            ["Consumo", _m3(b.consumption_m3),
             "Lecturas (ant. / act.)",
             f"{b.meter_reading_prev or '—'} / {b.meter_reading_curr or '—'}"],
            ["Importe gravado", _uyu(b.amount_without_tax), "IVA", _uyu(b.iva_amount)],
            ["", "", "TOTAL", _uyu(b.total_amount)],
        ]
        cw = [CONTENT_W * 0.22, CONTENT_W * 0.32, CONTENT_W * 0.25, CONTENT_W * 0.21]
        dt = Table(detail_rows, colWidths=cw)
        ts2 = _detail_ts()
        ts2.add("BACKGROUND", (0, 0), (-1, -1), C_OSE_LIGHT)
        ts2.add("FONTNAME",   (3, -1), (3, -1), "Helvetica-Bold")
        ts2.add("TEXTCOLOR",  (3, -1), (3, -1), C_OSE_DARK)
        dt.setStyle(ts2)
        content.append(dt)

        charts = ose_loc_charts.get(name, {})
        if charts.get("gasto") or charts.get("consumo"):
            content.append(Spacer(1, 0.15 * cm))
            content.append(_side_by_side(charts.get("gasto"), charts.get("consumo")))

        content.append(Spacer(1, 0.25 * cm))

    return content


# ── Sección UTE ────────────────────────────────────────────────────────────────

def _build_ute_section(ute_names: list[str],
                       ute_bills: dict[str, UteBill | None],
                       ute_loc_charts: dict[str, dict],
                       styles: dict) -> list:
    content = [Paragraph("  Electricidad — UTE", styles["section_ute"])]

    _cn  = ParagraphStyle("cn",  fontSize=9, fontName="Helvetica",      leading=11)
    _hdr = ParagraphStyle("hdr", fontSize=9, fontName="Helvetica-Bold",
                           textColor=colors.white, alignment=TA_CENTER, leading=11)

    def _reactive_str(charge: float | None) -> str:
        v = charge or 0.0
        if abs(v) < 0.01:
            return "—"
        fmt = f"{abs(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        return ("-$ " if v < 0 else "+$ ") + fmt

    rows = [["Cuenta / Ubicación", "Período",
             Paragraph("Importe<br/>gravado ($)", _hdr),
             "Reactiva", "Total (c/IVA)"]]
    for name in ute_names:
        b = ute_bills.get(name)
        if b:
            rows.append([Paragraph(name, _cn),
                         f"{_date(b.period_start)} – {_date(b.period_end)}",
                         _uyu(b.amount_without_tax),
                         _reactive_str(b.reactive_charge),
                         _uyu(b.total_amount)])
        else:
            rows.append([Paragraph(name, _cn), "Sin factura", "—", "—", "—"])
    total_ute = sum(b.total_amount for b in ute_bills.values() if b)
    rows.append(["TOTAL UTE", "", "", "", _uyu(total_ute)])

    col_w = [CONTENT_W * 0.33, CONTENT_W * 0.21,
             CONTENT_W * 0.16, CONTENT_W * 0.15, CONTENT_W * 0.15]
    tbl = Table(rows, colWidths=col_w)
    ts = TableStyle(_header_row_style(C_UTE_DARK))
    ts.add("ROWBACKGROUNDS", (0, 1), (-1, -2), [colors.white, C_UTE_LIGHT])
    ts.add("BACKGROUND",    (0, -1), (-1, -1), C_TOTAL_BG)
    ts.add("FONTNAME",      (0, -1), (-1, -1), "Helvetica-Bold")
    ts.add("ALIGN",         (0, 1),  (0, -1),  "LEFT")
    ts.add("ALIGN",         (2, 1),  (-1, -1), "RIGHT")
    tbl.setStyle(ts)
    content.append(tbl)
    content.append(Spacer(1, 0.3 * cm))

    for name in ute_names:
        b = ute_bills.get(name)
        content.append(Paragraph(f"Detalle: {name}", styles["account_ute"]))
        if not b:
            content.append(Paragraph("Sin factura para este período.", styles["no_bill"]))
            continue

        reactive = b.reactive_charge or 0.0
        r_label = "DESCUENTO" if reactive < 0 else "CARGO ADICIONAL"
        r_color = C_DISCOUNT if reactive < 0 else C_CHARGE

        detail_rows = [
            ["Nº Factura", b.invoice_number, "Emisión", _date(b.emission_date)],
            ["Período", f"{_date(b.period_start)} – {_date(b.period_end)}",
             "Vencimiento", _date(b.due_date)],
            ["Consumo Activo Total", _kwh(b.energy_total_kwh), "", ""],
            ["  Punta", _kwh(b.energy_punta_kwh), "  Valle", _kwh(b.energy_valle_kwh)],
            ["  Llano", _kwh(b.energy_llano_kwh), "", ""],
            [f"Energía Reactiva ({r_label})",
             f"{b.reactive_energy_kvarh:,.2f} kVArh".replace(",", "X").replace(".", ",").replace("X", ".") if b.reactive_energy_kvarh else "—",
             "Cargo reactivo",
             _uyu(reactive, show_sign=True)],
            ["Importe gravado", _uyu(b.amount_without_tax), "IVA", _uyu(b.iva_amount)],
            ["", "", "TOTAL", _uyu(b.total_amount)],
        ]
        cw = [CONTENT_W * 0.28, CONTENT_W * 0.26,
              CONTENT_W * 0.24, CONTENT_W * 0.22]
        dt = Table(detail_rows, colWidths=cw)
        ts2 = _detail_ts()
        ts2.add("BACKGROUND", (0, 0), (-1, -1), C_UTE_LIGHT)
        ts2.add("FONTNAME",   (3, -1), (3, -1), "Helvetica-Bold")
        ts2.add("TEXTCOLOR",  (3, -1), (3, -1), C_UTE_DARK)
        ts2.add("TEXTCOLOR",  (3, 5),  (3, 5),  r_color)
        ts2.add("FONTNAME",   (3, 5),  (3, 5),  "Helvetica-Bold")
        dt.setStyle(ts2)
        content.append(dt)

        charts = ute_loc_charts.get(name, {})
        if charts:
            content.append(Spacer(1, 0.15 * cm))
            if charts.get("pvl") or charts.get("reactiva"):
                content.append(_side_by_side(charts.get("gasto"),       charts.get("reactiva")))
                content.append(_side_by_side(charts.get("consumo_kwh"), charts.get("pvl")))
            else:
                content.append(_side_by_side(charts.get("gasto"), charts.get("consumo_kwh")))

        content.append(Spacer(1, 0.25 * cm))

    return content


# ── Gráficos históricos globales ───────────────────────────────────────────────

def _build_charts_section(chart_paths: dict[str, Path], styles: dict) -> list:
    content = [
        PageBreak(),
        Paragraph("  Análisis histórico — últimos 12 meses", styles["section_hist"]),
    ]
    chart_order = [
        ("ose_gasto",          "Gasto mensual en agua (OSE)"),
        ("ose_consumo",        "Consumo de agua mensual (m³)"),
        ("ute_gasto",          "Gasto mensual en electricidad (UTE)"),
        ("ute_consumo_activo", "Consumo eléctrico activo mensual (kWh)"),
    ]
    for key, _ in chart_order:
        path = chart_paths.get(key)
        if path and path.exists():
            content.append(Image(str(path), width=CONTENT_W, height=CONTENT_W * 0.38))
            content.append(Spacer(1, 0.45 * cm))
    return content


# ── Punto de entrada ───────────────────────────────────────────────────────────

def generate_report(db: Database, year: int, month: int, out_path: Path) -> Path:
    from config.settings import Settings

    ose_names = [e["name"] for e in Settings.ose_accounts()]
    ute_names = [e["name"] for e in Settings.ute_accounts()]

    ose_bills: dict[str, OseBill | None] = {
        name: db.get_ose_bill_for_month(name, year, month) for name in ose_names
    }
    ute_bills: dict[str, UteBill | None] = {
        name: db.get_ute_bill_for_month(name, year, month) for name in ute_names
    }

    ose_hist = db.get_ose_monthly_totals()
    ute_hist = db.get_ute_monthly_totals()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)

        global_charts = generate_charts(ose_hist, ute_hist, tmp_path)

        ose_loc_charts = {
            name: generate_ose_location_charts(name, ose_hist, tmp_path)
            for name in ose_names
        }
        ute_loc_charts = {
            name: generate_ute_location_charts(name, ute_hist, tmp_path)
            for name in ute_names
        }

        out_path.parent.mkdir(parents=True, exist_ok=True)
        doc = SimpleDocTemplate(
            str(out_path), pagesize=A4,
            leftMargin=MARGIN, rightMargin=MARGIN,
            topMargin=MARGIN, bottomMargin=MARGIN,
        )
        styles = _build_styles()
        story: list = []
        story += _build_header(year, month, styles)
        story += _build_executive_summary(ose_names, ute_names, ose_bills, ute_bills, styles)
        story += _build_ose_section(ose_names, ose_bills, ose_loc_charts, styles)
        story += [Spacer(1, 0.3 * cm)]
        story += _build_ute_section(ute_names, ute_bills, ute_loc_charts, styles)
        story += _build_charts_section(global_charts, styles)
        doc.build(story)

    return out_path
