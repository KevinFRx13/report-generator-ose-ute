import json
import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).parent.parent
load_dotenv(ROOT / ".env")


def _require(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"Variable de entorno requerida no configurada: {key}\n"
            "Copie .env.example a .env y complete los valores."
        )
    return value


def _load_locations() -> dict:
    path = ROOT / "config" / "locations.json"
    with open(path, encoding="utf-8") as f:
        return json.load(f)


class Settings:
    # Paths
    root: Path = ROOT
    db_path: Path = ROOT / os.getenv("DB_PATH", "data/bills.db")
    bills_folder: Path = ROOT / os.getenv("BILLS_FOLDER", "data/bills")

    # OpenRouter
    @staticmethod
    def openrouter_api_key() -> str:
        return _require("OPENROUTER_API_KEY")

    # Azure / Graph API
    @staticmethod
    def azure_tenant_id() -> str:
        return _require("AZURE_TENANT_ID")

    @staticmethod
    def azure_client_id() -> str:
        return _require("AZURE_CLIENT_ID")

    @staticmethod
    def azure_client_secret() -> str:
        return _require("AZURE_CLIENT_SECRET")

    @staticmethod
    def msal_token_cache() -> str:
        return _require("MSAL_TOKEN_CACHE")

    # Email
    @staticmethod
    def bills_mailbox() -> str:
        return _require("BILLS_MAILBOX")

    @staticmethod
    def report_recipients() -> list[str]:
        raw = _require("REPORT_RECIPIENTS")
        return [r.strip() for r in raw.split(",") if r.strip()]

    @staticmethod
    def report_sender() -> str:
        return _require("REPORT_SENDER")

    @staticmethod
    def ose_email_sender() -> str:
        return os.getenv("OSE_EMAIL_SENDER", "")

    @staticmethod
    def ute_email_sender() -> str:
        return os.getenv("UTE_EMAIL_SENDER", "")

    # ── Account registries (independent lists) ────────────────────────────────

    @staticmethod
    def ose_accounts() -> list[dict]:
        """Return list of {account, name} for OSE, in display order."""
        return _load_locations().get("ose_accounts", [])

    @staticmethod
    def ute_accounts() -> list[dict]:
        """Return list of {account, name} for UTE, in display order."""
        return _load_locations().get("ute_accounts", [])

    @staticmethod
    def location_name_by_ose_account(account: str) -> str | None:
        for entry in Settings.ose_accounts():
            if entry.get("account") == account:
                return entry["name"]
        return None

    @staticmethod
    def location_name_by_ute_account(account: str) -> str | None:
        for entry in Settings.ute_accounts():
            if entry.get("account") == account:
                return entry["name"]
        return None
