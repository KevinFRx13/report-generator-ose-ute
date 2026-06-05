"""Sync bills.db with Azure Blob Storage for cloud deployment."""

import logging
import os
from pathlib import Path


def _blob_client(container: str):
    from azure.storage.blob import BlobServiceClient
    conn_str = os.environ["AZURE_STORAGE_CONNECTION_STRING"]
    svc = BlobServiceClient.from_connection_string(conn_str)
    return svc.get_blob_client(container=container, blob="bills.db")


def download_db(local_path: Path) -> bool:
    """Download bills.db from Azure Blob to local_path. Returns True on success."""
    container = os.environ.get("AZURE_BLOB_CONTAINER", "tifor-bills")
    try:
        blob = _blob_client(container)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(blob.download_blob().readall())
        logging.info("[blob] DB descargada -> %s", local_path)
        return True
    except Exception as exc:
        logging.error("[blob] No se pudo descargar la DB: %s", exc)
        return False


def upload_db(local_path: Path) -> bool:
    """Upload local bills.db to Azure Blob. Returns True on success."""
    container = os.environ.get("AZURE_BLOB_CONTAINER", "tifor-bills")
    try:
        blob = _blob_client(container)
        blob.upload_blob(local_path.read_bytes(), overwrite=True)
        logging.info("[blob] DB subida desde %s", local_path)
        return True
    except Exception as exc:
        logging.error("[blob] No se pudo subir la DB: %s", exc)
        return False
