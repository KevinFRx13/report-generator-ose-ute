# Report Generator — OSE/UTE (Servicios Públicos)

Generador automático de informes mensuales de facturas de agua (OSE) y electricidad (UTE) para empresas en Uruguay. Todo el código, comentarios y el PDF generado están en **español**.

## Stack

- **Python 3.12+**
- **Anthropic SDK** (`claude-haiku-4-5-20251001`) — extracción de datos de PDFs de facturas via Vision API
- **SQLite** (`data/bills.db`) — historial de facturas; el archivo se commitea al repo (no hay blob storage)
- **ReportLab Platypus** — generación del PDF del informe
- **Matplotlib** — gráficos históricos (últimos 12 meses)
- **MSAL + Microsoft Graph API** — lectura de facturas desde Outlook y envío del informe por email
- **GitHub Actions** — automatización mensual (cron `55 2 28-31 * *`); `FORCE_MONTHLY=true` para disparar manualmente

## Estructura de archivos clave

```
config/
  locations.json     # cuentas OSE/UTE agrupadas por empresa → { "companies": [{name, ose_accounts, ute_accounts}] }
  settings.py        # carga .env, expone métodos tipados; companies() + ose_accounts() flat + ute_accounts() flat

src/
  parsers/
    bill_extractor.py   # extrae OseBill / UteBill de PDFs via Anthropic Vision; prompts en español
  storage/
    database.py         # OseBill y UteBill dataclasses; upsert con INSERT OR IGNORE; migraciones con ALTER TABLE + try/except
    outlook_reader.py   # Graph API: busca emails OSE/UTE, descarga adjuntos PDF
  reports/
    charts.py           # gráficos matplotlib: globales (todas las ubicaciones) + por ubicación
    generator.py        # PDF ReportLab; itera por empresa → OSE section → UTE section → histórico (solo si >1 ubicación)
  email_sender.py       # Graph API: envía PDF del informe

main.py               # CLI: process-folder, generate-report, send-report, import-history, monthly
```

## Convenciones críticas

**Nunca llamar a la API de Anthropic para probar o depurar** — consume dinero. Solo se llama en producción al procesar facturas reales.

**No hardcodear configuración de negocio** en `locations.json` ni en código — lo que puede cambiar (potencias contratadas, tarifas) debe extraerse de las facturas via API.

**Estructura del PDF:**
- Una sección por empresa (encabezado solo si hay >1 empresa)
- Dentro de cada empresa: resumen ejecutivo → detalle OSE por ubicación → detalle UTE por ubicación → histórico global (solo si la empresa tiene >1 ubicación por servicio)
- Tablas y gráficos van en `KeepTogether` **separados**: la tabla no se parte a la mitad; todos los gráficos de una ubicación van juntos; pero tabla y gráficos pueden quedar en páginas distintas
- Los gráficos históricos del final fluyen libremente (sin PageBreak forzado)

**Patrón `locations.json` → `Settings`:**
```python
Settings.companies()      # lista de empresas con sus cuentas
Settings.ose_accounts()   # flat list de todas las cuentas OSE (para el extractor)
Settings.ute_accounts()   # flat list de todas las cuentas UTE (para el extractor)
Settings.location_name_by_ose_account(account)  # resolución de cuenta → nombre
```

**Patrón de migración de esquema:**
```python
for col in ["nueva_columna_1", "nueva_columna_2"]:
    try:
        conn.execute(f"ALTER TABLE tabla ADD COLUMN {col} REAL")
    except Exception:
        pass  # ya existe
```

**Detección dinámica de tipo de gráfico (UTE potencia):**
- `power_punta_kw` no nulo → gráfico horario (barras Punta/Valle/Llano vs contratada)
- `power_measured_kw` no nulo → gráfico simple (leída + contratada + mínimo facturable)
- Ninguno → sin gráfico de potencia

## Empresas configuradas

- **TIFOR** — 4 ubicaciones OSE + 4 UTE (Montevideo + Tacuarembó)
- **Manuel Boullosa** — 1 ubicación OSE + 1 UTE (Pestalozzi 3857) — cuentas pendientes

## Datos de potencia UTE (llenados manualmente)

Las columnas de potencia en `ute_bills` se llenan al procesar nuevas facturas via API. Para el historial existente se llenaron manualmente leyendo los PDFs con PyMuPDF (sin llamar a la API).

- **Cno. Casavalle - Planta** (Gran Consumidor): horaria 3 niveles — Punta contratada varía (200→41 kW desde oct-2025), Valle/Llano = 250 kW
- **Gral. Hornos** (Tarifa Horaria Estacional - Zafral): simple — contratada 40 kW, sin mínimo facturable separado
- **Tacuarembó** (General Simple): sin medición de demanda → sin gráfico de potencia
