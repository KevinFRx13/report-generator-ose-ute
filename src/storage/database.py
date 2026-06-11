"""SQLite persistence layer — bills history and location registry."""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class OseBill:
    location_name: str
    invoice_number: str
    emission_date: date
    due_date: date
    period_start: date
    period_end: date
    consumption_m3: float
    meter_reading_prev: float | None
    meter_reading_curr: float | None
    amount_without_tax: float
    iva_amount: float
    total_amount: float
    pdf_path: str | None = None


@dataclass
class UteBill:
    location_name: str
    invoice_number: str
    emission_date: date
    due_date: date
    period_start: date
    period_end: date
    energy_punta_kwh: float
    energy_valle_kwh: float
    energy_llano_kwh: float
    energy_total_kwh: float
    reactive_energy_kvarh: float | None
    reactive_charge: float | None  # negative = discount, positive = extra charge
    amount_without_tax: float
    iva_amount: float
    total_amount: float
    pdf_path: str | None = None
    # Demand / power fields (extracted from UTE bill's measurement table)
    power_punta_kw: float | None = None           # measured punta demand (kW)
    power_valle_kw: float | None = None           # measured valle demand (kW)
    power_llano_kw: float | None = None           # measured llano demand (kW)
    power_punta_contracted_kw: float | None = None  # contracted punta (horaria tariff)
    power_valle_contracted_kw: float | None = None
    power_llano_contracted_kw: float | None = None
    power_measured_kw: float | None = None        # measured demand (simple tariff)
    power_contracted_kw: float | None = None      # contracted demand (simple tariff)
    power_min_billable_kw: float | None = None    # minimum billable demand (simple tariff)


# ── Database ──────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS ose_bills (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    location_name       TEXT    NOT NULL,
    invoice_number      TEXT    NOT NULL UNIQUE,
    emission_date       TEXT    NOT NULL,
    due_date            TEXT    NOT NULL,
    period_start        TEXT    NOT NULL,
    period_end          TEXT    NOT NULL,
    consumption_m3      REAL    NOT NULL,
    meter_reading_prev  REAL,
    meter_reading_curr  REAL,
    amount_without_tax  REAL    NOT NULL,
    iva_amount          REAL    NOT NULL,
    total_amount        REAL    NOT NULL,
    pdf_path            TEXT,
    created_at          TEXT    DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ute_bills (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    location_name       TEXT    NOT NULL,
    invoice_number      TEXT    NOT NULL UNIQUE,
    emission_date       TEXT    NOT NULL,
    due_date            TEXT    NOT NULL,
    period_start        TEXT    NOT NULL,
    period_end          TEXT    NOT NULL,
    energy_punta_kwh    REAL    NOT NULL,
    energy_valle_kwh    REAL    NOT NULL,
    energy_llano_kwh    REAL    NOT NULL,
    energy_total_kwh    REAL    NOT NULL,
    reactive_energy_kvarh REAL,
    reactive_charge     REAL,
    amount_without_tax  REAL    NOT NULL,
    iva_amount          REAL    NOT NULL,
    total_amount        REAL    NOT NULL,
    pdf_path            TEXT,
    created_at          TEXT    DEFAULT (datetime('now'))
);
"""


class Database:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self.db_path = db_path
        self._init_schema()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_schema(self):
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            # Migration: add power columns if they don't exist yet
            for col in [
                "power_punta_kw",           "power_valle_kw",           "power_llano_kw",
                "power_punta_contracted_kw","power_valle_contracted_kw","power_llano_contracted_kw",
                "power_measured_kw",        "power_contracted_kw",      "power_min_billable_kw",
            ]:
                try:
                    conn.execute(f"ALTER TABLE ute_bills ADD COLUMN {col} REAL")
                except Exception:
                    pass  # column already exists

    # ── OSE ───────────────────────────────────────────────────────────────────

    def upsert_ose_bill(self, bill: OseBill) -> bool:
        """Insert bill; return True if new, False if already existed."""
        sql = """
        INSERT OR IGNORE INTO ose_bills
            (location_name, invoice_number, emission_date, due_date,
             period_start, period_end, consumption_m3,
             meter_reading_prev, meter_reading_curr,
             amount_without_tax, iva_amount, total_amount, pdf_path)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        with self._conn() as conn:
            cur = conn.execute(sql, (
                bill.location_name,
                bill.invoice_number,
                bill.emission_date.isoformat(),
                bill.due_date.isoformat(),
                bill.period_start.isoformat(),
                bill.period_end.isoformat(),
                bill.consumption_m3,
                bill.meter_reading_prev,
                bill.meter_reading_curr,
                bill.amount_without_tax,
                bill.iva_amount,
                bill.total_amount,
                bill.pdf_path,
            ))
            return cur.rowcount > 0

    def get_ose_bills(self, location_name: str | None = None,
                      year: int | None = None) -> list[OseBill]:
        conditions, params = [], []
        if location_name:
            conditions.append("location_name = ?")
            params.append(location_name)
        if year:
            conditions.append("strftime('%Y', emission_date) = ?")
            params.append(str(year))
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM ose_bills {where} ORDER BY emission_date ASC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_ose(r) for r in rows]

    def get_ose_bill_for_month(self, location_name: str,
                               year: int, month: int) -> OseBill | None:
        """Return the OSE bill whose consumption period ENDS in the given month."""
        sql = """
        SELECT * FROM ose_bills
        WHERE location_name = ?
          AND strftime('%Y', period_end) = ?
          AND strftime('%m', period_end) = ?
        ORDER BY period_end DESC LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(sql, (location_name, str(year), f"{month:02d}")).fetchone()
        return _row_to_ose(row) if row else None

    # ── UTE ───────────────────────────────────────────────────────────────────

    def upsert_ute_bill(self, bill: UteBill) -> bool:
        sql = """
        INSERT OR IGNORE INTO ute_bills
            (location_name, invoice_number, emission_date, due_date,
             period_start, period_end,
             energy_punta_kwh, energy_valle_kwh, energy_llano_kwh, energy_total_kwh,
             reactive_energy_kvarh, reactive_charge,
             amount_without_tax, iva_amount, total_amount, pdf_path,
             power_punta_kw, power_valle_kw, power_llano_kw,
             power_punta_contracted_kw, power_valle_contracted_kw, power_llano_contracted_kw,
             power_measured_kw, power_contracted_kw, power_min_billable_kw)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """
        with self._conn() as conn:
            cur = conn.execute(sql, (
                bill.location_name,
                bill.invoice_number,
                bill.emission_date.isoformat(),
                bill.due_date.isoformat(),
                bill.period_start.isoformat(),
                bill.period_end.isoformat(),
                bill.energy_punta_kwh,
                bill.energy_valle_kwh,
                bill.energy_llano_kwh,
                bill.energy_total_kwh,
                bill.reactive_energy_kvarh,
                bill.reactive_charge,
                bill.amount_without_tax,
                bill.iva_amount,
                bill.total_amount,
                bill.pdf_path,
                bill.power_punta_kw,
                bill.power_valle_kw,
                bill.power_llano_kw,
                bill.power_punta_contracted_kw,
                bill.power_valle_contracted_kw,
                bill.power_llano_contracted_kw,
                bill.power_measured_kw,
                bill.power_contracted_kw,
                bill.power_min_billable_kw,
            ))
            return cur.rowcount > 0

    def get_ute_bills(self, location_name: str | None = None,
                      year: int | None = None) -> list[UteBill]:
        conditions, params = [], []
        if location_name:
            conditions.append("location_name = ?")
            params.append(location_name)
        if year:
            conditions.append("strftime('%Y', emission_date) = ?")
            params.append(str(year))
        where = ("WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = f"SELECT * FROM ute_bills {where} ORDER BY emission_date ASC"
        with self._conn() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [_row_to_ute(r) for r in rows]

    def get_ute_bill_for_month(self, location_name: str,
                               year: int, month: int) -> UteBill | None:
        """Return the UTE bill whose consumption period ENDS in the given month."""
        sql = """
        SELECT * FROM ute_bills
        WHERE location_name = ?
          AND strftime('%Y', period_end) = ?
          AND strftime('%m', period_end) = ?
        ORDER BY period_end DESC LIMIT 1
        """
        with self._conn() as conn:
            row = conn.execute(sql, (location_name, str(year), f"{month:02d}")).fetchone()
        return _row_to_ute(row) if row else None

    # ── Historical queries (last N months across all locations) ───────────────

    def get_ose_monthly_totals(self, months: int = 13) -> list[dict]:
        """Return per-location OSE totals grouped by consumption period end month."""
        sql = """
        SELECT location_name,
               strftime('%Y-%m', emission_date) AS month,
               SUM(total_amount)    AS total,
               SUM(consumption_m3)  AS consumption
        FROM ose_bills
        GROUP BY location_name, month
        ORDER BY month ASC
        """
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def get_ute_monthly_totals(self, months: int = 13) -> list[dict]:
        """Return per-location UTE totals grouped by consumption period end month."""
        sql = """
        SELECT location_name,
               strftime('%Y-%m', emission_date) AS month,
               SUM(total_amount)         AS total,
               SUM(energy_total_kwh)     AS kwh_total,
               SUM(energy_punta_kwh)     AS kwh_punta,
               SUM(energy_valle_kwh)     AS kwh_valle,
               SUM(energy_llano_kwh)     AS kwh_llano,
               SUM(reactive_charge)      AS reactive_charge
        FROM ute_bills
        GROUP BY location_name, month
        ORDER BY month ASC
        """
        with self._conn() as conn:
            rows = conn.execute(sql).fetchall()
        return [dict(r) for r in rows]

    def get_ute_power_history(self, location_name: str) -> list[dict]:
        """Return per-month power demand data for one UTE location (last 12 months)."""
        sql = """
        SELECT strftime('%Y-%m', emission_date) AS month,
               power_punta_kw, power_valle_kw, power_llano_kw,
               power_punta_contracted_kw, power_valle_contracted_kw, power_llano_contracted_kw,
               power_measured_kw, power_contracted_kw, power_min_billable_kw
        FROM ute_bills
        WHERE location_name = ?
        ORDER BY emission_date ASC
        """
        with self._conn() as conn:
            rows = conn.execute(sql, (location_name,)).fetchall()
        return [dict(r) for r in rows]

    def invoice_exists(self, invoice_number: str, utility: str) -> bool:
        table = "ose_bills" if utility == "OSE" else "ute_bills"
        with self._conn() as conn:
            row = conn.execute(
                f"SELECT 1 FROM {table} WHERE invoice_number = ?", (invoice_number,)
            ).fetchone()
        return row is not None

    def pdf_already_imported(self, pdf_path: str) -> bool:
        """Return True if this PDF path is already recorded in either table."""
        p = str(pdf_path).replace("\\", "/")
        with self._conn() as conn:
            for table in ("ose_bills", "ute_bills"):
                row = conn.execute(
                    f"SELECT 1 FROM {table} WHERE replace(pdf_path, '\\', '/') = ?", (p,)
                ).fetchone()
                if row:
                    return True
        return False


# ── Row → dataclass helpers ───────────────────────────────────────────────────

def _row_to_ose(row) -> OseBill:
    return OseBill(
        location_name=row["location_name"],
        invoice_number=row["invoice_number"],
        emission_date=date.fromisoformat(row["emission_date"]),
        due_date=date.fromisoformat(row["due_date"]),
        period_start=date.fromisoformat(row["period_start"]),
        period_end=date.fromisoformat(row["period_end"]),
        consumption_m3=row["consumption_m3"],
        meter_reading_prev=row["meter_reading_prev"],
        meter_reading_curr=row["meter_reading_curr"],
        amount_without_tax=row["amount_without_tax"],
        iva_amount=row["iva_amount"],
        total_amount=row["total_amount"],
        pdf_path=row["pdf_path"],
    )


def _row_to_ute(row) -> UteBill:
    return UteBill(
        location_name=row["location_name"],
        invoice_number=row["invoice_number"],
        emission_date=date.fromisoformat(row["emission_date"]),
        due_date=date.fromisoformat(row["due_date"]),
        period_start=date.fromisoformat(row["period_start"]),
        period_end=date.fromisoformat(row["period_end"]),
        energy_punta_kwh=row["energy_punta_kwh"],
        energy_valle_kwh=row["energy_valle_kwh"],
        energy_llano_kwh=row["energy_llano_kwh"],
        energy_total_kwh=row["energy_total_kwh"],
        reactive_energy_kvarh=row["reactive_energy_kvarh"],
        reactive_charge=row["reactive_charge"],
        amount_without_tax=row["amount_without_tax"],
        iva_amount=row["iva_amount"],
        total_amount=row["total_amount"],
        pdf_path=row["pdf_path"],
        power_punta_kw=row["power_punta_kw"],
        power_valle_kw=row["power_valle_kw"],
        power_llano_kw=row["power_llano_kw"],
        power_punta_contracted_kw=row["power_punta_contracted_kw"],
        power_valle_contracted_kw=row["power_valle_contracted_kw"],
        power_llano_contracted_kw=row["power_llano_contracted_kw"],
        power_measured_kw=row["power_measured_kw"],
        power_contracted_kw=row["power_contracted_kw"],
        power_min_billable_kw=row["power_min_billable_kw"],
    )
