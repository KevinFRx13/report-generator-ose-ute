"""
CLI entry point for the OSE/UTE bill processing pipeline.

Usage examples
--------------
# Process PDFs from a local folder (safest starting point)
python main.py process-folder data/bills/2026/05

# Fetch new bill emails from Outlook and process them
python main.py process-email

# Generate the PDF report for a given month (uses data already in the DB)
python main.py generate-report 2026-05

# Full pipeline: fetch from email -> process -> generate -> send
python main.py run 2026-05

# Full pipeline using a local folder instead of email
python main.py run 2026-05 --folder data/bills/2026/05

# Import historical data from CSV (see docs for format)
python main.py import-history history.csv

# Set up Windows Task Scheduler to run monthly
python main.py setup-scheduler
"""

import argparse
import csv
import os
import sys
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

from config.settings import Settings
from src.storage.database import Database, OseBill, UteBill
from src.parsers.bill_extractor import detect_utility, extract_ose_bill, extract_ute_bill
from src.reports.generator import generate_report
from src.email_sender import send_report


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_db() -> Database:
    return Database(Settings.db_path)


def _parse_year_month(ym: str) -> tuple[int, int]:
    try:
        dt = datetime.strptime(ym, "%Y-%m")
        return dt.year, dt.month
    except ValueError:
        print(f"[error] Formato de mes inválido '{ym}'. Use YYYY-MM (ej: 2026-05).")
        sys.exit(1)


def _process_pdf_file(pdf_path: Path, db: Database) -> bool:
    """Parse a single PDF, identify its type, and store it in the DB. Returns True if new."""
    try:
        utility = detect_utility(pdf_path)
        if utility == "OSE":
            bill = extract_ose_bill(pdf_path)
            is_new = db.upsert_ose_bill(bill)
        else:
            bill = extract_ute_bill(pdf_path)
            is_new = db.upsert_ute_bill(bill)

        status = "nueva" if is_new else "ya existia"
        print(f"  [{utility}] {pdf_path.name} -> {bill.location_name} ({status})")
        return is_new
    except ValueError as exc:
        print(f"  [advertencia] {pdf_path.name}: {exc}")
        return False
    except Exception as exc:
        print(f"  [error] {pdf_path.name}: {exc}")
        return False


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_process_folder(folder: Path, db: Database):
    """Scan a local folder for PDFs and import them into the database."""
    pdfs = list(folder.glob("**/*.pdf"))
    if not pdfs:
        print(f"[process-folder] No se encontraron PDFs en: {folder}")
        return
    print(f"[process-folder] Procesando {len(pdfs)} archivo(s) en {folder}…")
    new_count = sum(_process_pdf_file(p, db) for p in pdfs)
    print(f"[process-folder] Listo — {new_count} factura(s) nueva(s) importada(s).")


def cmd_process_email(db: Database):
    """Fetch bill PDFs from Outlook, import them, and send one notification per new bill."""
    from src.ingestion.outlook_reader import fetch_bill_emails
    from src.parsers.bill_extractor import detect_utility, extract_ose_bill, extract_ute_bill
    from src.email_sender import notify_ose_bill, notify_ute_bill

    print("[process-email] Buscando facturas en el correo…")
    pdfs = fetch_bill_emails()
    if not pdfs:
        print("[process-email] No se encontraron facturas nuevas.")
        return

    print(f"[process-email] {len(pdfs)} PDF(s) descargado(s). Procesando…")
    new_count = 0
    for p in pdfs:
        try:
            utility = detect_utility(p)
            if utility == "OSE":
                bill = extract_ose_bill(p)
                if db.upsert_ose_bill(bill):
                    print(f"  [OSE] {p.name} -> {bill.location_name} (nueva)")
                    notify_ose_bill(bill)
                    new_count += 1
                else:
                    print(f"  [OSE] {p.name} ya existia, omitido.")
            else:
                bill = extract_ute_bill(p)
                if db.upsert_ute_bill(bill):
                    print(f"  [UTE] {p.name} -> {bill.location_name} (nueva)")
                    notify_ute_bill(bill)
                    new_count += 1
                else:
                    print(f"  [UTE] {p.name} ya existia, omitido.")
        except ValueError as exc:
            print(f"  [advertencia] {p.name}: {exc}")
        except Exception as exc:
            print(f"  [error] {p.name}: {exc}")

    print(f"[process-email] Listo — {new_count} factura(s) nueva(s) importada(s) y notificada(s).")


def cmd_generate_report(year: int, month: int, db: Database) -> Path:
    """Generate the PDF report for the given month."""
    out_dir = Settings.root / "data" / "reports"
    out_path = out_dir / f"Informe_{year}_{month:02d}.pdf"
    print(f"[generate-report] Generando informe para {year}-{month:02d}…")
    generate_report(db, year, month, out_path)
    print(f"[generate-report] PDF guardado en: {out_path}")
    return out_path


def cmd_run(year: int, month: int, folder: Path | None, db: Database):
    """Full pipeline: import → generate monthly summary → send."""
    if folder:
        cmd_process_folder(folder, db)
    else:
        cmd_process_email(db)
    pdf_path = cmd_generate_report(year, month, db)
    print(f"[send-report] Enviando informe mensual {year}-{month:02d}…")
    send_report(pdf_path, year, month)
    print("[run] Pipeline completado.")


def cmd_sync_db():
    """Upload the local bills.db to Azure Blob Storage."""
    from src.storage.blob_sync import upload_db
    print("[sync-db] Subiendo bills.db a Azure Blob Storage...")
    if upload_db(Settings.db_path):
        print("[sync-db] Listo.")
    else:
        print("[sync-db] Error al subir la DB. Verificar AZURE_STORAGE_CONNECTION_STRING.")
        sys.exit(1)


def cmd_monthly(db: Database):
    """
    Generate and send the monthly report — but ONLY if today is the last day of
    the month. Designed to be called daily by Task Scheduler at 23:55; it exits
    silently on every day except the last one.
    Set FORCE_MONTHLY=true to bypass the date check (useful for manual/CI triggers).
    """
    today = date.today()
    force = os.environ.get("FORCE_MONTHLY", "").lower() in ("1", "true", "yes")
    if not force and (today + timedelta(days=1)).day != 1:
        print(f"[monthly] {today} — no es el ultimo dia del mes. Nada que hacer.")
        return
    year, month = today.year, today.month
    print(f"[monthly] Ultimo dia de {month:02d}/{year} detectado — generando informe...")
    pdf_path = cmd_generate_report(year, month, db)
    send_report(pdf_path, year, month)
    print("[monthly] Informe enviado. Completado.")


def cmd_import_history(csv_path: Path, db: Database):
    """
    Import historical bill data from a CSV file.

    OSE CSV columns (one header row):
      type,location_name,invoice_number,emission_date,due_date,period_start,
      period_end,consumption_m3,meter_reading_prev,meter_reading_curr,
      amount_without_tax,iva_amount,total_amount

    UTE CSV columns:
      type,location_name,invoice_number,emission_date,due_date,period_start,
      period_end,energy_punta_kwh,energy_valle_kwh,energy_llano_kwh,
      energy_total_kwh,reactive_energy_kvarh,reactive_charge,
      amount_without_tax,iva_amount,total_amount

    The 'type' column must be either 'OSE' or 'UTE'.
    Dates must be in YYYY-MM-DD format.
    """
    if not csv_path.exists():
        print(f"[import-history] Archivo no encontrado: {csv_path}")
        sys.exit(1)

    ose_count = ute_count = 0
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            bill_type = row.get("type", "").upper()
            try:
                if bill_type == "OSE":
                    bill = OseBill(
                        location_name=row["location_name"],
                        invoice_number=row["invoice_number"],
                        emission_date=date.fromisoformat(row["emission_date"]),
                        due_date=date.fromisoformat(row["due_date"]),
                        period_start=date.fromisoformat(row["period_start"]),
                        period_end=date.fromisoformat(row["period_end"]),
                        consumption_m3=float(row["consumption_m3"]),
                        meter_reading_prev=float(row["meter_reading_prev"]) if row.get("meter_reading_prev") else None,
                        meter_reading_curr=float(row["meter_reading_curr"]) if row.get("meter_reading_curr") else None,
                        amount_without_tax=float(row["amount_without_tax"]),
                        iva_amount=float(row["iva_amount"]),
                        total_amount=float(row["total_amount"]),
                    )
                    if db.upsert_ose_bill(bill):
                        ose_count += 1
                elif bill_type == "UTE":
                    bill = UteBill(
                        location_name=row["location_name"],
                        invoice_number=row["invoice_number"],
                        emission_date=date.fromisoformat(row["emission_date"]),
                        due_date=date.fromisoformat(row["due_date"]),
                        period_start=date.fromisoformat(row["period_start"]),
                        period_end=date.fromisoformat(row["period_end"]),
                        energy_punta_kwh=float(row["energy_punta_kwh"]),
                        energy_valle_kwh=float(row["energy_valle_kwh"]),
                        energy_llano_kwh=float(row["energy_llano_kwh"]),
                        energy_total_kwh=float(row["energy_total_kwh"]),
                        reactive_energy_kvarh=float(row["reactive_energy_kvarh"]) if row.get("reactive_energy_kvarh") else None,
                        reactive_charge=float(row["reactive_charge"]) if row.get("reactive_charge") else None,
                        amount_without_tax=float(row["amount_without_tax"]),
                        iva_amount=float(row["iva_amount"]),
                        total_amount=float(row["total_amount"]),
                    )
                    if db.upsert_ute_bill(bill):
                        ute_count += 1
                else:
                    print(f"[import-history] Tipo desconocido '{bill_type}', omitiendo fila.")
            except (KeyError, ValueError) as exc:
                print(f"[import-history] Error en fila {reader.line_num}: {exc}")

    print(f"[import-history] Importadas {ose_count} facturas OSE y {ute_count} facturas UTE.")


def _register_task(task_name: str, arguments: str,
                   trigger_xml: str, python: str, script: Path):
    """Helper: write XML, register with schtasks, delete temp file."""
    common = f"""  <Actions Context="Author">
    <Exec>
      <Command>{python}</Command>
      <Arguments>{arguments}</Arguments>
      <WorkingDirectory>{script.parent}</WorkingDirectory>
    </Exec>
  </Actions>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT2H</ExecutionTimeLimit>
    <Priority>7</Priority>
  </Settings>"""

    xml = (
        '<?xml version="1.0" encoding="UTF-16"?>\n'
        '<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">\n'
        + trigger_xml + "\n" + common + "\n</Task>"
    )
    xml_path = script.parent / "_task.xml"
    xml_path.write_text(xml, encoding="utf-16")
    try:
        subprocess.run(
            ["schtasks", "/Create", "/TN", task_name, "/XML", str(xml_path), "/F"],
            capture_output=True, text=True, check=True,
        )
        return True
    except subprocess.CalledProcessError as exc:
        print(f"  Error: {exc.stderr.strip()}")
        return False
    finally:
        xml_path.unlink(missing_ok=True)


def cmd_setup_scheduler():
    """
    Register one Windows Task Scheduler task:

      OSE_UTE_Informe_Mensual — runs 'monthly' every day at 23:55.
      The 'monthly' command checks whether today is the last day of the month
      and only generates + emails the report when it is; on all other days it
      exits immediately without doing anything.

    Must be run as Administrator.
    """
    script = Path(__file__).resolve()
    python = sys.executable
    today  = date.today()

    trigger = """  <Triggers>
    <CalendarTrigger>
      <StartBoundary>{y}-{m:02d}-{d:02d}T23:55:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay><DaysInterval>1</DaysInterval></ScheduleByDay>
    </CalendarTrigger>
  </Triggers>""".format(y=today.year, m=today.month, d=today.day)

    ok = _register_task(
        task_name="OSE_UTE_Informe_Mensual",
        arguments=f"{script} monthly",
        trigger_xml=trigger,
        python=python, script=script,
    )

    if ok:
        print("[scheduler] 'OSE_UTE_Informe_Mensual' registrada.")
        print("[scheduler] Se ejecuta diariamente a las 23:55.")
        print("[scheduler] Solo actua el ultimo dia del mes: genera el PDF y lo envia por email.")
        print("[scheduler] Listo.")
    else:
        print("[scheduler] Error al registrar la tarea. Ejecute este script como Administrador.")


# ── Argument parser ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="Sistema de reportes de facturas OSE/UTE",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # process-folder
    pf = sub.add_parser("process-folder", help="Importar PDFs desde una carpeta local")
    pf.add_argument("folder", type=Path)

    # process-email
    sub.add_parser("process-email", help="Descargar e importar facturas desde Outlook")

    # generate-report
    gr = sub.add_parser("generate-report", help="Generar PDF para un mes dado")
    gr.add_argument("month", metavar="YYYY-MM")

    # run
    run_p = sub.add_parser("run", help="Pipeline completo (importar + generar + enviar)")
    run_p.add_argument("month", metavar="YYYY-MM",
                       help="Mes a procesar, o 'auto' para el mes anterior")
    run_p.add_argument("--folder", type=Path, default=None,
                       help="Usar carpeta local en lugar de correo")

    # import-history
    ih = sub.add_parser("import-history", help="Importar historial desde CSV")
    ih.add_argument("csv_file", type=Path)

    # sync-db
    sub.add_parser("sync-db", help="Subir bills.db a Azure Blob Storage")

    # monthly
    sub.add_parser("monthly", help="Generar y enviar informe solo si hoy es el ultimo dia del mes")

    # setup-scheduler
    sub.add_parser("setup-scheduler", help="Registrar tarea en el Programador de Windows")

    # clear-db
    sub.add_parser("clear-db", help="Eliminar toda la base de datos y empezar de cero")

    args = parser.parse_args()
    db = _get_db()

    if args.command == "process-folder":
        cmd_process_folder(args.folder, db)

    elif args.command == "process-email":
        cmd_process_email(db)

    elif args.command == "generate-report":
        year, month = _parse_year_month(args.month)
        cmd_generate_report(year, month, db)

    elif args.command == "run":
        if args.month == "auto":
            # Use the previous month
            today = date.today()
            if today.month == 1:
                year, month = today.year - 1, 12
            else:
                year, month = today.year, today.month - 1
        else:
            year, month = _parse_year_month(args.month)
        cmd_run(year, month, args.folder, db)

    elif args.command == "import-history":
        cmd_import_history(args.csv_file, db)

    elif args.command == "sync-db":
        cmd_sync_db()

    elif args.command == "monthly":
        cmd_monthly(db)

    elif args.command == "setup-scheduler":
        cmd_setup_scheduler()

    elif args.command == "clear-db":
        db_path = Settings.db_path
        if db_path.exists():
            confirm = input(f"Esto eliminara TODOS los datos de {db_path}. Escriba 'SI' para confirmar: ")
            if confirm.strip().upper() == "SI":
                db_path.unlink()
                print(f"[clear-db] Base de datos eliminada. Se creara de nuevo al proxima importacion.")
            else:
                print("[clear-db] Cancelado.")
        else:
            print("[clear-db] La base de datos no existe.")


if __name__ == "__main__":
    main()
