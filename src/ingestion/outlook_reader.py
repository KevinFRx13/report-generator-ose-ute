"""
Fetch OSE/UTE bill PDFs from an Outlook / Microsoft 365 mailbox
using the Microsoft Graph API with application-level permissions.

Setup (one-time):
  1. Azure portal → Azure Active Directory → App registrations → New registration
  2. Add Microsoft Graph *Application* permissions:
       Mail.Read   (read the bills mailbox)
  3. Grant admin consent for those permissions
  4. Create a client secret → save in .env as AZURE_CLIENT_SECRET
  5. Fill AZURE_TENANT_ID, AZURE_CLIENT_ID, BILLS_MAILBOX in .env
"""

import re
from pathlib import Path
from datetime import datetime, timedelta, timezone

import msal
import requests

from config.settings import Settings

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
SCOPES = ["https://graph.microsoft.com/.default"]


def _get_token() -> str:
    app = msal.ConfidentialClientApplication(
        client_id=Settings.azure_client_id(),
        client_credential=Settings.azure_client_secret(),
        authority=f"https://login.microsoftonline.com/{Settings.azure_tenant_id()}",
    )
    result = app.acquire_token_for_client(scopes=SCOPES)
    if "access_token" not in result:
        raise RuntimeError(
            f"Error al obtener token de Microsoft Graph: {result.get('error_description')}"
        )
    return result["access_token"]


def _graph_get(token: str, url: str, params: dict | None = None,
               extra_headers: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if extra_headers:
        headers.update(extra_headers)
    resp = requests.get(url, headers=headers, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def _graph_get_bytes(token: str, url: str) -> bytes:
    headers = {"Authorization": f"Bearer {token}"}
    resp = requests.get(url, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.content


def _safe_filename(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def fetch_bill_emails(since_days: int = 45) -> list[Path]:
    """
    Search the bills mailbox for emails from OSE/UTE senders received in the
    last `since_days` days.  Download PDF attachments to
    data/bills/YYYY/MM/ose/ or data/bills/YYYY/MM/ute/ depending on sender.
    Returns list of paths to downloaded PDFs.
    """
    base_dir = Settings.bills_folder / datetime.now().strftime("%Y/%m")

    token = _get_token()
    mailbox = Settings.bills_mailbox()
    ose_sender = Settings.ose_email_sender()
    ute_sender = Settings.ute_email_sender()

    since_dt = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    # Build OData filter — match either sender
    sender_filters = []
    if ose_sender:
        sender_filters.append(f"from/emailAddress/address eq '{ose_sender}'")
    if ute_sender:
        sender_filters.append(f"from/emailAddress/address eq '{ute_sender}'")

    if not sender_filters:
        print("[outlook] No hay remitentes de facturas configurados (OSE_EMAIL_SENDER / UTE_EMAIL_SENDER).")
        return []

    odata_filter = (
        f"receivedDateTime ge {since_dt} and hasAttachments eq true"
        f" and ({' or '.join(sender_filters)})"
    )

    # ConsistencyLevel + $count are required by Graph API for complex $filter
    # expressions that use navigation properties (from/emailAddress/address) or
    # combine conditions with 'or'. Without them the request returns HTTP 400
    # on many tenants.
    search_headers = {"ConsistencyLevel": "eventual"}
    url = f"{GRAPH_BASE}/users/{mailbox}/messages"
    params = {
        "$filter": odata_filter,
        "$select": "id,subject,from,receivedDateTime,hasAttachments",
        "$top": 50,
        "$count": "true",
    }

    downloaded: list[Path] = []
    while url:
        data = _graph_get(token, url, params, extra_headers=search_headers)
        messages = data.get("value", [])
        for msg in messages:
            msg_id = msg["id"]
            subject = msg.get("subject", "sin_asunto")
            sender = msg.get("from", {}).get("emailAddress", {}).get("address", "")
            received = msg.get("receivedDateTime", "")[:10]  # YYYY-MM-DD
            utility = "OSE" if ose_sender and ose_sender.lower() in sender.lower() else "UTE"
            output_dir = base_dir / utility.lower()
            output_dir.mkdir(parents=True, exist_ok=True)

            # Fetch attachments list
            att_url = f"{GRAPH_BASE}/users/{mailbox}/messages/{msg_id}/attachments"
            att_data = _graph_get(token, att_url)
            for att in att_data.get("value", []):
                if att.get("@odata.type") != "#microsoft.graph.fileAttachment":
                    continue
                filename: str = att.get("name", "attachment.pdf")
                if not filename.lower().endswith(".pdf"):
                    continue

                att_content_url = (
                    f"{GRAPH_BASE}/users/{mailbox}/messages/{msg_id}"
                    f"/attachments/{att['id']}/$value"
                )
                try:
                    pdf_bytes = _graph_get_bytes(token, att_content_url)
                except requests.HTTPError as exc:
                    if att.get("contentBytes"):
                        import base64
                        pdf_bytes = base64.b64decode(att["contentBytes"])
                    else:
                        print(f"[outlook] No se pudo descargar adjunto '{filename}': {exc}")
                        continue

                safe_name = _safe_filename(f"{received}_{filename}")
                dest = output_dir / safe_name
                if not dest.exists():
                    dest.write_bytes(pdf_bytes)
                    print(f"[outlook] Descargado: {utility} → {dest.name}")
                    downloaded.append(dest)
                else:
                    print(f"[outlook] Ya existe, omitido: {dest.name}")

        # Pagination
        url = data.get("@odata.nextLink")
        params = None  # nextLink already includes query params

    return downloaded
