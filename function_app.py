"""
Azure Functions entry point.

Timer trigger runs every day at 23:55 UTC.
The function generates and emails the monthly report only on the last day
of the month; on all other days it exits immediately.

Required Application Settings in the Function App:
  DB_PATH                         = /tmp/bills.db
  AZURE_STORAGE_CONNECTION_STRING = <connection string for the storage account>
  AZURE_BLOB_CONTAINER            = tifor-bills
  OPENROUTER_API_KEY              = <key>
  AZURE_TENANT_ID                 = <tenant>
  AZURE_CLIENT_ID                 = <client>
  AZURE_CLIENT_SECRET             = <secret>
  REPORT_SENDER                   = <sender mailbox>
  REPORT_RECIPIENTS               = mail1@...,mail2@...,mail3@...
"""

import logging
import os
from datetime import date, timedelta
from pathlib import Path

import azure.functions as func

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


@app.timer_trigger(
    schedule="0 55 23 * * *",  # every day at 23:55 UTC (NCRONTAB: s m h d M dow)
    arg_name="timer",
    run_on_startup=False,
    use_monitor=True,
)
def monthly_report(timer: func.TimerRequest) -> None:
    today = date.today()

    if (today + timedelta(days=1)).day != 1:
        logging.info("%s — no es el ultimo dia del mes, nada que hacer.", today)
        return

    logging.info("Ultimo dia de %02d/%d detectado — generando informe...", today.month, today.year)

    db_path = Path(os.environ.get("DB_PATH", "/tmp/bills.db"))

    from src.storage.blob_sync import download_db
    if not download_db(db_path):
        logging.error("No se pudo descargar la DB desde Azure Blob. Abortando.")
        return

    from src.storage.database import Database
    from src.reports.generator import generate_report
    from src.email_sender import send_report

    db = Database(db_path)
    pdf_path = Path("/tmp") / f"Informe_{today.year}_{today.month:02d}.pdf"
    generate_report(db, today.year, today.month, pdf_path)
    send_report(pdf_path, today.year, today.month)
    logging.info("Informe generado y enviado correctamente.")
