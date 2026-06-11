"""
Extract structured data from OSE and UTE PDF bills via Anthropic Vision API.

Strategy:
  1. Render each PDF page as a PNG image.
  2. Send the images + a structured prompt to Claude.
  3. Parse the JSON response, applying locale-aware number parsing
     (Uruguayan format: dot = thousands, comma = decimal).
  4. Override account number with regex extraction (more reliable).
  5. Validate key fields (IVA ≈ 22% of gravado, kWh within sane range).
"""

import base64
import json
import re
from datetime import date
from pathlib import Path

import anthropic
import fitz  # PyMuPDF

from config.settings import Settings
from src.storage.database import OseBill, UteBill

_MODEL = "claude-haiku-4-5-20251001"


# ── Number parsing ─────────────────────────────────────────────────────────────

def _parse_num(value) -> float | None:
    """Parse a number in Spanish format (1.234,56) or plain (1234.56)."""
    if value is None:
        return None
    s = str(value).strip()
    s = re.sub(r'[$\s]', '', s)
    if not s or s.lower() in ('null', 'none', ''):
        return None

    if ',' in s and '.' in s:
        # Spanish format: 1.234,56 → 1234.56
        s = s.replace('.', '').replace(',', '.')
    elif ',' in s:
        s = s.replace(',', '.')
    elif s.count('.') > 1:
        # Model error: returned 3.668.680 instead of 3.668,680
        # Treat the last dot as decimal, remove the rest as thousands separators
        last = s.rfind('.')
        s = s[:last].replace('.', '') + '.' + s[last + 1:]

    return float(s)


# ── Prompts ────────────────────────────────────────────────────────────────────

_OSE_PROMPT = """
Eres un asistente que extrae datos de facturas de OSE (Obras Sanitarias del Estado, Uruguay).
Analiza el texto y devuelve ÚNICAMENTE un objeto JSON válido, sin bloques de código.

ESQUEMA (todos los valores numéricos como STRING, tal como aparecen en la factura):
{
  "numero_factura": "string — formato 'B NNNNNNN' (ej: 'B 8573710')",
  "fecha_emision": "YYYY-MM-DD",
  "fecha_vencimiento": "YYYY-MM-DD",
  "numero_cuenta": "string — 8 dígitos que aparecen ANTES de las fechas (NO el NUM. MEDIDOR)",
  "periodo_inicio": "YYYY-MM-DD",
  "periodo_fin": "YYYY-MM-DD",
  "consumo_m3": "string — CONSUMO M3 de la tabla de lecturas en el ADENDA (parte inferior de la factura). Es un número entero pequeño (ej: '54', '1', '13'). NO calcular de sub-ítems del detalle.",
  "lectura_anterior": "string o null — LEC. ANTERIOR de la tabla de lecturas en el ADENDA. Es un número entero del odómetro del medidor (ej: '631', '38'). NO es un precio ni un importe.",
  "lectura_actual": "string o null — LEC. ACTUAL de la tabla de lecturas en el ADENDA. Es un número entero del odómetro (ej: '685'). Usar null si TIPO DE LEC. = Est. (lectura estimada), ya que ese campo estará en blanco.",
  "importe_gravado": "string — la línea 'Importe Gravado' del RESUMEN DE TOTALES al final de la factura (inmediatamente después de 'Importe No Gravado'). Es la suma de todos los cargos gravados. NUNCA un ítem individual del detalle (Cargo Fijo, Consumo Básico, etc.). Ej: '13.565,95'",
  "iva": "string — 'IVA Tasa Básica' en UYU (ej: '2.984,51'). Es el 22% del importe gravado. SIEMPRE menor que importe_gravado. NO confundir con 'Ajuste por Redondeo' (número pequeño) NI con 'Total Monto' (mayor que el gravado).",
  "total": "string — valor exacto de 'Total Monto' del resumen de totales (ej: '16.550,46'). Es la suma de importe_gravado + iva. NO usar el importe del talón de cobro ($***X,XX) que puede estar redondeado."
}

REGLAS:
- Devuelve los importes EXACTAMENTE como aparecen (con punto y coma: '13.565,95').
- TODAS las fechas en la factura están en formato DD/MM/AAAA (día primero, luego mes, luego año).
  Ejemplo: '03/06/2026' = 3 de junio de 2026 = '2026-06-03'. NO es el 6 de marzo.
- fecha_emision y fecha_vencimiento aparecen junto a la cuenta: CUENTA → EMISIÓN → VENCIMIENTO.
- periodo_inicio y periodo_fin: leer el campo 'PERÍODO CONSUMO' del encabezado de la factura (formato 'DD/MM/AAAA-DD/MM/AAAA'). NO usar las fechas de los sub-períodos que aparecen en las descripciones de los ítems del DETALLE (ej: '13/12/2025 a 31/12/2025').
- importe_gravado está en el bloque de TOTALES, después de 'Importe No Gravado' y antes de 'IVA Tasa Básica'. Si algún ítem del DETALLE DE FACTURACIÓN tiene un importe similar, ignorarlo.
- INVARIANTE: importe_gravado > iva > 0 siempre (iva ≈ 22% del gravado, es decir, gravado ≈ 4,5× iva). Si iva > importe_gravado, hay un error de mapeo de filas.
- INVARIANTE: total = importe_gravado + iva. total es siempre el valor más grande de los tres.
- Si un campo no aparece, usa null.
""".strip()

_UTE_PROMPT = """
Eres un asistente que extrae datos de facturas de UTE (Administración Nacional de Usinas y
Trasmisiones Eléctricas, Uruguay). Analiza el texto y devuelve ÚNICAMENTE un objeto JSON válido,
sin bloques de código.

ESQUEMA (todos los valores numéricos como STRING, tal como aparecen en la factura):
{
  "numero_factura": "string — formato 'B NNNNNNN' (ej: 'B 8074269')",
  "fecha_emision": "YYYY-MM-DD — la fecha que aparece DESPUÉS del número 'B XXXXXXX', NO la fecha anterior a él",
  "fecha_vencimiento": "YYYY-MM-DD — 'Próx. Vencimiento' o la segunda fecha después del número 'B XXXXXXX'",
  "numero_cuenta": "string — Nº de Cuenta de 10 dígitos que aparece ANTES del 'B XXXXXXX'. NO el Acuerdo de Servicio.",
  "periodo_inicio": "YYYY-MM-DD — inicio del Período de Consumo",
  "periodo_fin": "YYYY-MM-DD — fin del Período de Consumo",
  "consumo_punta_kwh": "string — kWh TOTALES en Punta de la sección 'CARGO ENERGÍA MENSUAL'. Si hay varias sublíneas de Punta (ej: 'días hábiles' y 'NO hábiles'), SUMARLAS. NO leer de la tabla de lecturas (esa tabla muestra energía bruta del medidor, no la energía facturada por tramo). Si tarifa simple, usar '0'.",
  "consumo_valle_kwh": "string — kWh TOTALES en Valle de la sección 'CARGO ENERGÍA MENSUAL'. Mismo criterio: sumar sublíneas si las hay. NO leer de la tabla de lecturas. Si tarifa simple, usar '0'.",
  "consumo_llano_kwh": "string — kWh TOTALES en Llano de la sección 'CARGO ENERGÍA MENSUAL'. Mismo criterio: sumar sublíneas si las hay. NO leer de la tabla de lecturas. Si tarifa simple, usar '0'.",
  "consumo_activo_total_kwh": "string — total kWh activos. Para tarifa simple es el único valor de consumo activo (ej: '187'). Para tarifas horarias es la suma Punta+Valle+Llano del encabezado ('Consumo Activo (kWh)').",
  "consumo_reactivo_kvarh": "string o null — leer el campo 'Consumo Reactiva (kVArh)' del encabezado de la factura (ej: '2505,6'). NO sumar filas individuales de la tabla de lecturas.",
  "cargo_reactivo_total": "string o null — suma de TODAS las líneas de energía reactiva en UYU (puede ser negativo si son descuentos, ej: '-3.409,50')",
  "importe_gravado": "string — 'Importe Gravado 22%' en UYU (ej: '195.823,39')",
  "iva": "string — 'IVA Tasa Básica 22%' en UYU (ej: '43.081,15')",
  "total": "string — 'Total' en UYU (ej: '238.905,00')",
  "potencia_punta_medida_kw": "string o null — columna 'Total de Medida' de la fila 'Potencia Punta' (Gran Consumidor) o 'Potencia' (Mediano Consumidor) en la tabla de lecturas. Usar SIEMPRE el valor de la columna 'Total de Medida', NO la lectura anterior ni la lectura actual. Ej: '52,40' o '48,76'.",
  "potencia_valle_medida_kw": "string o null — columna 'Total de Medida' de la fila 'Potencia Valle' en la tabla de lecturas. Valor en kW. Ej: '31,16'.",
  "potencia_llano_medida_kw": "string o null — columna 'Total de Medida' fila 'Potencia Llano' en kW. Solo Gran Consumidor (tarifa horaria con 3 niveles separados: Punta, Valle, Llano). Para Mediano Consumidor no existe esta fila → null.",
  "potencia_punta_contratada_kw": "string o null — potencia contratada para Punta en kW, de la primera tabla de la factura. Solo tarifas horarias.",
  "potencia_valle_contratada_kw": "string o null — potencia contratada para Valle en kW. Solo tarifas horarias.",
  "potencia_llano_contratada_kw": "string o null — potencia contratada para Llano en kW. Solo tarifas horarias.",
  "potencia_medida_kw": "string o null — para tarifas con demanda simple (no horaria), la demanda máxima medida en kW del período, de la columna 'Total de Medida'. Null para tarifas horarias o si no aplica.",
  "potencia_contratada_kw": "string o null — para tarifas con demanda simple, la potencia contratada en kW de la primera tabla. Null para tarifas horarias o si no aplica.",
  "potencia_minimo_facturable_kw": "string o null — el mínimo facturable de demanda en kW indicado en la factura. Puede ser 0. Null si no aparece."
}

REGLAS CRÍTICAS:
- Devuelve los importes y kWh EXACTAMENTE como aparecen en la factura (ej: '2.454,840', no 2454840).
  En Uruguay, el punto es separador de miles y la coma es separador decimal. Ej: '3.668,680' = tres mil seiscientos sesenta y ocho punto sesenta y ocho (3668.68 kWh), NO tres millones.
  El separador decimal siempre es la COMA. NUNCA uses punto como decimal en los valores numéricos del JSON.
- TODAS las fechas en la factura están en formato DD/MM/AAAA (día primero, luego mes, luego año).
  Ejemplo: '03/06/2026' = 3 de junio de 2026 = '2026-06-03'. NO es el 6 de marzo.
- El orden en el texto es: Nº Cuenta → Fecha cobro → 'B XXXXXX' → Fecha Emisión → Fecha Próx.Vcto.
  La fecha_emision es la que viene DESPUÉS del 'B XXXXXX'.
- Para tarifas simples (General Simple, sin Punta/Valle/Llano):
  Los valores 'Activa: anterior / actual / consumo' son lecturas del medidor, NO kWh por tramo.
  consumo_activo_total_kwh = el valor de consumo activo (tercera columna de la tabla Activa).
  consumo_punta_kwh = consumo_valle_kwh = consumo_llano_kwh = '0'.
- consumo_reactivo_kvarh: usar el campo 'Consumo Reactiva (kVArh)' del encabezado del PDF, no la tabla de lecturas (que puede tener múltiples filas Q1/Q4).
- cargo_reactivo_total es la suma de todas las líneas "Energía Reactiva" y "Potencia Reactiva" del DETALLE DE FACTURACIÓN (puede ser negativo).
- IVA Tasa Básica es ~22% del Importe Gravado.
""".strip()


# ── Regex account extraction ───────────────────────────────────────────────────

def _regex_ute_account(text: str) -> str | None:
    m = re.search(r'(?<!\d)(\d{10})(?!\d)\n\d{2}/\d{2}/\d{4}', text)
    return m.group(1) if m else None


def _regex_ose_account(text: str) -> str | None:
    m = re.search(r'(?<!\d)(\d{8})(?!\d)\n\d{2}/\d{2}/\d{4}', text)
    return m.group(1) if m else None


# ── Anthropic client ───────────────────────────────────────────────────────────

def _get_client() -> anthropic.Anthropic:
    return anthropic.Anthropic(api_key=Settings.anthropic_api_key())


def _extract_text(pdf_path: Path) -> str:
    doc = fitz.open(str(pdf_path))
    return "\n".join(page.get_text() for page in doc).strip()


def _pdf_to_image_blocks(pdf_path: Path) -> list[dict]:
    doc = fitz.open(str(pdf_path))
    blocks = []
    for page in doc:
        pix    = page.get_pixmap(matrix=fitz.Matrix(2, 2))
        img_b64 = base64.b64encode(pix.tobytes("png")).decode()
        blocks.append({
            "type": "image",
            "source": {
                "type":       "base64",
                "media_type": "image/png",
                "data":       img_b64,
            },
        })
    return blocks


def _parse_json_response(raw: str) -> dict:
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if match:
        raw = match.group()
    return json.loads(raw)


def _call_model(pdf_path: Path, prompt: str) -> dict:
    client  = _get_client()
    content = _pdf_to_image_blocks(pdf_path) + [{"type": "text", "text": prompt}]
    message = client.messages.create(
        model=_MODEL,
        max_tokens=1024,
        messages=[{"role": "user", "content": content}],
    )
    return _parse_json_response(message.content[0].text)


# ── Validation ─────────────────────────────────────────────────────────────────

_OSE_REQUIRED = ("numero_factura", "fecha_emision", "fecha_vencimiento",
                 "numero_cuenta", "periodo_inicio", "periodo_fin",
                 "consumo_m3", "importe_gravado", "iva", "total")
_UTE_REQUIRED = ("numero_factura", "fecha_emision", "fecha_vencimiento",
                 "numero_cuenta", "periodo_inicio", "periodo_fin",
                 "consumo_activo_total_kwh", "importe_gravado", "iva", "total")


def _validate_required(data: dict, required: tuple, pdf_name: str) -> None:
    missing = [f for f in required if not data.get(f)]
    if missing:
        raise ValueError(f"Campos faltantes: {', '.join(missing)} ({pdf_name})")


def _validate_iva(gravado: float, iva: float, pdf_name: str) -> None:
    if gravado <= 0:
        return
    expected = gravado * 0.22
    ratio = abs(iva - expected) / expected
    if ratio > 0.15:
        raise ValueError(
            f"IVA sospechoso: gravado={gravado:,.2f}, iva={iva:,.2f} "
            f"(esperado ~{expected:,.2f}, diferencia {ratio:.0%}). ({pdf_name})"
        )


def _validate_kwh(value: float, field: str, pdf_name: str) -> None:
    if value > 500_000:
        raise ValueError(
            f"{field}={value:,.0f} kWh parece incorrecto (>500.000). ({pdf_name})"
        )


# ── Public API ─────────────────────────────────────────────────────────────────

def _parse_date(value) -> date | None:
    if not value:
        return None
    return date.fromisoformat(str(value))


def extract_ose_bill(pdf_path: Path) -> OseBill:
    data = _call_model(pdf_path, _OSE_PROMPT)

    text       = _extract_text(pdf_path)
    regex_acct = _regex_ose_account(text)
    if regex_acct and Settings.location_name_by_ose_account(regex_acct):
        data["numero_cuenta"] = regex_acct

    _validate_required(data, _OSE_REQUIRED, pdf_path.name)

    location_name = Settings.location_name_by_ose_account(str(data["numero_cuenta"]))
    if location_name is None:
        raise ValueError(
            f"Cuenta OSE '{data['numero_cuenta']}' no encontrada en config/locations.json."
        )

    gravado = _parse_num(data["importe_gravado"])
    iva     = _parse_num(data["iva"])
    total   = _parse_num(data["total"])
    consumo = _parse_num(data["consumo_m3"])

    _validate_iva(gravado, iva, pdf_path.name)

    return OseBill(
        location_name=location_name,
        invoice_number=data["numero_factura"],
        emission_date=_parse_date(data["fecha_emision"]),
        due_date=_parse_date(data["fecha_vencimiento"]),
        period_start=_parse_date(data["periodo_inicio"]),
        period_end=_parse_date(data["periodo_fin"]),
        consumption_m3=consumo,
        meter_reading_prev=_parse_num(data.get("lectura_anterior")),
        meter_reading_curr=_parse_num(data.get("lectura_actual")),
        amount_without_tax=gravado,
        iva_amount=iva,
        total_amount=total,
        pdf_path=str(pdf_path),
    )


def extract_ute_bill(pdf_path: Path) -> UteBill:
    data = _call_model(pdf_path, _UTE_PROMPT)

    text       = _extract_text(pdf_path)
    regex_acct = _regex_ute_account(text)
    if regex_acct and Settings.location_name_by_ute_account(regex_acct):
        data["numero_cuenta"] = regex_acct

    _validate_required(data, _UTE_REQUIRED, pdf_path.name)

    location_name = Settings.location_name_by_ute_account(str(data["numero_cuenta"]))
    if location_name is None:
        raise ValueError(
            f"Cuenta UTE '{data['numero_cuenta']}' no encontrada en config/locations.json."
        )

    punta     = _parse_num(data.get("consumo_punta_kwh")) or 0.0
    valle     = _parse_num(data.get("consumo_valle_kwh")) or 0.0
    llano     = _parse_num(data.get("consumo_llano_kwh")) or 0.0
    total_kwh = _parse_num(data["consumo_activo_total_kwh"])
    gravado   = _parse_num(data["importe_gravado"])
    iva       = _parse_num(data["iva"])
    total     = _parse_num(data["total"])

    for field, val in [("Punta", punta), ("Valle", valle),
                       ("Llano", llano), ("Total kWh", total_kwh)]:
        _validate_kwh(val, field, pdf_path.name)

    _validate_iva(gravado, iva, pdf_path.name)

    return UteBill(
        location_name=location_name,
        invoice_number=data["numero_factura"],
        emission_date=_parse_date(data["fecha_emision"]),
        due_date=_parse_date(data["fecha_vencimiento"]),
        period_start=_parse_date(data["periodo_inicio"]),
        period_end=_parse_date(data["periodo_fin"]),
        energy_punta_kwh=punta,
        energy_valle_kwh=valle,
        energy_llano_kwh=llano,
        energy_total_kwh=total_kwh,
        reactive_energy_kvarh=_parse_num(data.get("consumo_reactivo_kvarh")),
        reactive_charge=_parse_num(data.get("cargo_reactivo_total")),
        amount_without_tax=gravado,
        iva_amount=iva,
        total_amount=total,
        pdf_path=str(pdf_path),
        power_punta_kw=_parse_num(data.get("potencia_punta_medida_kw")),
        power_valle_kw=_parse_num(data.get("potencia_valle_medida_kw")),
        power_llano_kw=_parse_num(data.get("potencia_llano_medida_kw")),
        power_punta_contracted_kw=_parse_num(data.get("potencia_punta_contratada_kw")),
        power_valle_contracted_kw=_parse_num(data.get("potencia_valle_contratada_kw")),
        power_llano_contracted_kw=_parse_num(data.get("potencia_llano_contratada_kw")),
        power_measured_kw=_parse_num(data.get("potencia_medida_kw")),
        power_contracted_kw=_parse_num(data.get("potencia_contratada_kw")),
        power_min_billable_kw=_parse_num(data.get("potencia_minimo_facturable_kw")),
    )


def detect_utility(pdf_path: Path) -> str:
    """Identify OSE or UTE: parent folder → filename → text content → ask model."""
    parent = pdf_path.parent.name.lower()
    if parent == "ose":
        return "OSE"
    if parent == "ute":
        return "UTE"

    name = pdf_path.name.lower()
    if "ose" in name:
        return "OSE"
    if "ute" in name:
        return "UTE"

    raw = pdf_path.read_bytes()
    if b"Sanitarias" in raw or b"ose.com.uy" in raw:
        return "OSE"
    if b"Usinas" in raw or b"ute.com.uy" in raw:
        return "UTE"

    client  = _get_client()
    content = _pdf_to_image_blocks(pdf_path) + [{
        "type": "text",
        "text": "Esta factura es de OSE o UTE? Solo responde 'OSE' o 'UTE'.",
    }]
    message = client.messages.create(
        model=_MODEL,
        max_tokens=10,
        messages=[{"role": "user", "content": content}],
    )
    answer = message.content[0].text.strip().upper()
    if "OSE" in answer:
        return "OSE"
    if "UTE" in answer:
        return "UTE"
    raise ValueError(
        f"No se pudo determinar el tipo: {pdf_path.name}\n"
        "Solucion: colocar el PDF en una subcarpeta 'ose' o 'ute'."
    )
