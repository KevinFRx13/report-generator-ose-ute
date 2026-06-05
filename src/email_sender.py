"""
Email delivery via Microsoft Graph API.

Two types of emails:
  1. Individual bill notification  — sent immediately when a new bill is imported.
  2. Monthly summary report        — sent on the 25th with the full PDF attached.
"""

import base64
from datetime import datetime
from pathlib import Path

import msal
import requests

from config.settings import Settings
from src.storage.database import OseBill, UteBill

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES     = ["https://graph.microsoft.com/Mail.Send"]

MONTHS_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _get_token() -> str:
    cache = msal.SerializableTokenCache()
    cache.deserialize(base64.b64decode(Settings.msal_token_cache()).decode())

    app = msal.PublicClientApplication(
        client_id=Settings.azure_client_id(),
        authority=f"https://login.microsoftonline.com/{Settings.azure_tenant_id()}",
        token_cache=cache,
    )

    accounts = app.get_accounts()
    if not accounts:
        raise RuntimeError(
            "Token cache vacio. Ejecuta setup_auth.py y actualiza el secret MSAL_TOKEN_CACHE."
        )

    result = app.acquire_token_silent(SCOPES, account=accounts[0])
    if not result or "access_token" not in result:
        raise RuntimeError(
            "Token expirado o revocado. Vuelve a ejecutar setup_auth.py y actualiza "
            f"el secret MSAL_TOKEN_CACHE. Error: {result.get('error_description') if result else 'sin resultado'}"
        )
    return result["access_token"]


def _send(token: str, sender: str, recipients: list[str],
          subject: str, html_body: str,
          pdf_path: Path | None = None, pdf_name: str | None = None) -> None:
    attachments = []
    if pdf_path:
        pdf_b64 = base64.b64encode(pdf_path.read_bytes()).decode()
        attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": pdf_name or pdf_path.name,
            "contentType": "application/pdf",
            "contentBytes": pdf_b64,
        })

    message = {
        "message": {
            "subject": subject,
            "body": {"contentType": "HTML", "content": html_body},
            "toRecipients": [{"emailAddress": {"address": a}} for a in recipients],
            "attachments": attachments,
        },
        "saveToSentItems": True,
    }
    resp = requests.post(
        f"{GRAPH_BASE}/users/{sender}/sendMail",
        json=message,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        timeout=60,
    )
    resp.raise_for_status()


def _fmt(value: float | None) -> str:
    if value is None:
        return "—"
    return f"$ {abs(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _kwh(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".") + " kWh"


def _dt(d) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


# ── Individual bill notification ───────────────────────────────────────────────

def notify_ose_bill(bill: OseBill) -> None:
    """Send a one-bill email notification for a newly imported OSE bill."""
    token     = _get_token()
    sender    = Settings.report_sender()
    recipients = Settings.report_recipients()

    subject = (
        f"Nueva factura OSE - {bill.location_name} - "
        f"Periodo {_dt(bill.period_start)} al {_dt(bill.period_end)}"
    )

    html = f"""
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;">
  <div style="background:#1a78c2;padding:14px 20px;border-radius:6px;">
    <h2 style="color:white;margin:0;">Nueva factura OSE — Agua potable</h2>
    <p style="color:#d6e8f8;margin:4px 0 0 0;">{bill.location_name}</p>
  </div>
  <table style="margin-top:16px;border-collapse:collapse;width:100%;max-width:500px;">
    <tr><td style="padding:6px 12px;color:#555;width:180px;">Factura N°</td>
        <td style="padding:6px 12px;font-weight:bold;">{bill.invoice_number}</td></tr>
    <tr style="background:#f4f6f8;">
        <td style="padding:6px 12px;color:#555;">Periodo de consumo</td>
        <td style="padding:6px 12px;">{_dt(bill.period_start)} al {_dt(bill.period_end)}</td></tr>
    <tr><td style="padding:6px 12px;color:#555;">Vencimiento</td>
        <td style="padding:6px 12px;">{_dt(bill.due_date)}</td></tr>
    <tr style="background:#f4f6f8;">
        <td style="padding:6px 12px;color:#555;">Consumo</td>
        <td style="padding:6px 12px;font-weight:bold;">{bill.consumption_m3:,.2f} m³</td></tr>
    <tr><td style="padding:6px 12px;color:#555;">Importe gravado</td>
        <td style="padding:6px 12px;">{_fmt(bill.amount_without_tax)}</td></tr>
    <tr style="background:#f4f6f8;">
        <td style="padding:6px 12px;color:#555;">IVA</td>
        <td style="padding:6px 12px;">{_fmt(bill.iva_amount)}</td></tr>
    <tr style="background:#d6e8f8;">
        <td style="padding:8px 12px;font-weight:bold;color:#1a78c2;">TOTAL</td>
        <td style="padding:8px 12px;font-weight:bold;font-size:16px;color:#1a78c2;">{_fmt(bill.total_amount)}</td></tr>
  </table>
  <p style="margin-top:20px;font-size:11px;color:#888;">
    Generado automaticamente el {datetime.now().strftime('%d/%m/%Y %H:%M')} — TIFOR LTDA.
  </p>
</body></html>
""".strip()

    _send(token, sender, recipients, subject, html)
    print(f"[email] Notificacion OSE enviada: {bill.invoice_number} - {bill.location_name}")


def notify_ute_bill(bill: UteBill) -> None:
    """Send a one-bill email notification for a newly imported UTE bill."""
    token      = _get_token()
    sender     = Settings.report_sender()
    recipients = Settings.report_recipients()

    subject = (
        f"Nueva factura UTE - {bill.location_name} - "
        f"Periodo {_dt(bill.period_start)} al {_dt(bill.period_end)}"
    )

    reactive = bill.reactive_charge or 0.0
    r_color  = "#1a7c40" if reactive <= 0 else "#a32222"
    r_label  = "DESCUENTO" if reactive <= 0 else "CARGO ADICIONAL"
    r_str    = f"{_fmt(reactive)} ({r_label})"

    html = f"""
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;">
  <div style="background:#c47c00;padding:14px 20px;border-radius:6px;">
    <h2 style="color:white;margin:0;">Nueva factura UTE — Electricidad</h2>
    <p style="color:#fdf0d0;margin:4px 0 0 0;">{bill.location_name}</p>
  </div>
  <table style="margin-top:16px;border-collapse:collapse;width:100%;max-width:560px;">
    <tr><td style="padding:6px 12px;color:#555;width:200px;">Factura N°</td>
        <td style="padding:6px 12px;font-weight:bold;">{bill.invoice_number}</td></tr>
    <tr style="background:#fdf0d0;">
        <td style="padding:6px 12px;color:#555;">Periodo de consumo</td>
        <td style="padding:6px 12px;">{_dt(bill.period_start)} al {_dt(bill.period_end)}</td></tr>
    <tr><td style="padding:6px 12px;color:#555;">Vencimiento</td>
        <td style="padding:6px 12px;">{_dt(bill.due_date)}</td></tr>
    <tr style="background:#fdf0d0;">
        <td style="padding:6px 12px;color:#555;">Consumo activo total</td>
        <td style="padding:6px 12px;font-weight:bold;">{_kwh(bill.energy_total_kwh)}</td></tr>
    <tr><td style="padding:6px 12px;color:#555;padding-left:24px;">— Punta</td>
        <td style="padding:6px 12px;">{_kwh(bill.energy_punta_kwh)}</td></tr>
    <tr style="background:#fdf0d0;">
        <td style="padding:6px 12px;color:#555;padding-left:24px;">— Valle</td>
        <td style="padding:6px 12px;">{_kwh(bill.energy_valle_kwh)}</td></tr>
    <tr><td style="padding:6px 12px;color:#555;padding-left:24px;">— Llano</td>
        <td style="padding:6px 12px;">{_kwh(bill.energy_llano_kwh)}</td></tr>
    <tr style="background:#fdf0d0;">
        <td style="padding:6px 12px;color:#555;">Energia reactiva</td>
        <td style="padding:6px 12px;color:{r_color};font-weight:bold;">{r_str}</td></tr>
    <tr><td style="padding:6px 12px;color:#555;">Importe gravado</td>
        <td style="padding:6px 12px;">{_fmt(bill.amount_without_tax)}</td></tr>
    <tr style="background:#fdf0d0;">
        <td style="padding:6px 12px;color:#555;">IVA</td>
        <td style="padding:6px 12px;">{_fmt(bill.iva_amount)}</td></tr>
    <tr style="background:#fdf0d0;">
        <td style="padding:8px 12px;font-weight:bold;color:#c47c00;">TOTAL</td>
        <td style="padding:8px 12px;font-weight:bold;font-size:16px;color:#c47c00;">{_fmt(bill.total_amount)}</td></tr>
  </table>
  <p style="margin-top:20px;font-size:11px;color:#888;">
    Generado automaticamente el {datetime.now().strftime('%d/%m/%Y %H:%M')} — TIFOR LTDA.
  </p>
</body></html>
""".strip()

    _send(token, sender, recipients, subject, html)
    print(f"[email] Notificacion UTE enviada: {bill.invoice_number} - {bill.location_name}")


# ── Monthly summary report ─────────────────────────────────────────────────────

def send_report(pdf_path: Path, year: int, month: int) -> None:
    """Send the full monthly PDF summary report."""
    token      = _get_token()
    sender     = Settings.report_sender()
    recipients = Settings.report_recipients()
    month_name = MONTHS_ES[month].capitalize()

    html = f"""
<html><body style="font-family:Arial,sans-serif;font-size:14px;color:#222;">
  <div style="background:#1c3d5a;padding:18px 24px;border-radius:6px;">
    <h2 style="color:white;margin:0;">Informe mensual de servicios publicos</h2>
    <p style="color:#cce0f5;margin:6px 0 0 0;">{month_name} {year}</p>
  </div>
  <p style="margin-top:18px;">Estimado/a,</p>
  <p>Adjunto encontrara el informe consolidado de facturacion de agua (OSE) y
     electricidad (UTE) correspondiente a <strong>{month_name} {year}</strong>.</p>
  <p>El informe incluye resumen ejecutivo, detalle de cada factura y graficos
     comparativos de los ultimos 12 meses.</p>
  <p style="font-size:11px;color:#888;margin-top:24px;">
    Generado automaticamente el {datetime.now().strftime('%d/%m/%Y %H:%M')} — TIFOR LTDA.
  </p>
</body></html>
""".strip()

    subject     = f"Informe de servicios publicos - {month_name} {year}"
    pdf_name    = f"Informe_Servicios_{month_name}_{year}.pdf"
    _send(token, sender, recipients, subject, html, pdf_path, pdf_name)
    print(f"[email] Informe mensual enviado a: {', '.join(recipients)}")
