# Sistema de reportes OSE/UTE — TIFOR LTDA.

Genera y envía automáticamente un informe mensual consolidado de facturas de agua (OSE) y electricidad (UTE) para las 5 ubicaciones de la empresa.

---

## Flujo mensual

### 1. Descargar las facturas (manual)

Al recibir las facturas de OSE y UTE, guardarlas en:

```
data/bills/YYYY/MM/
```

Ejemplo para junio 2026:
```
data/bills/2026/06/factura_ose_casavalle.pdf
data/bills/2026/06/factura_ute_casavalle.pdf
...
```

### 2. Importar a la base de datos

```powershell
python main.py process-folder data/bills/2026/06
```

Verificar que todas las facturas fueron importadas correctamente (el comando muestra cuántas se procesaron).

> **Nota:** Si la extracción automática falla para alguna factura, los datos se pueden ingresar manualmente a la DB con SQLite.

### 3. Subir la DB actualizada a GitHub

```powershell
git add data/bills.db
git commit -m "facturas junio 2026"
git push
```

### 4. El informe se genera y envía automáticamente

GitHub Actions corre cada noche a las 23:55 (hora Uruguay). El último día del mes genera el PDF y lo envía por correo a los destinatarios configurados.

Para enviar el informe manualmente en cualquier momento:

```powershell
$env:FORCE_MONTHLY="true"; python main.py monthly
```

---

## Configuración inicial

### Requisitos

- Python 3.11+
- Cuenta Microsoft 365 (para envío de correos via Graph API)
- Repositorio en GitHub (para la automatización)

### Instalación

```powershell
pip install -r requirements.txt
```

### Variables de entorno

Copiar `.env.example` a `.env` y completar los valores:

```powershell
copy .env.example .env
```

| Variable | Descripción |
|----------|-------------|
| `AZURE_TENANT_ID` | ID del tenant de Microsoft 365 |
| `AZURE_CLIENT_ID` | ID de la app registrada en Azure AD |
| `MSAL_TOKEN_CACHE` | Cache de tokens (generado por `setup_auth.py`) |
| `REPORT_SENDER` | Correo remitente (debe existir en el tenant M365) |
| `REPORT_RECIPIENTS` | Destinatarios separados por coma |
| `DB_PATH` | Ruta a la base de datos (default: `data/bills.db`) |

### Configurar autenticación con Microsoft Graph

**Una sola vez** (o cuando el token expire):

1. En [portal.azure.com](https://portal.azure.com) → App registrations → `report-generator-ose-ute`:
   - API permissions → agregar `Mail.Send` (Delegated)
   - Authentication → Allow public client flows → **Sí**

2. Correr el script de autenticación:
   ```powershell
   python setup_auth.py
   ```
   Seguir las instrucciones (abre el navegador para login con cuenta M365).
   Copiar el valor que imprime al `.env` como `MSAL_TOKEN_CACHE`.

3. El token es válido ~90 días. Como se usa mensualmente, la ventana se renueva automáticamente. Si algún mes falla por autenticación, repetir el paso 2.

### Configurar GitHub Actions

1. Crear repositorio privado en GitHub
2. Subir el código:
   ```powershell
   git remote add origin https://github.com/TU_USUARIO/report-generator-ose-ute.git
   git push -u origin master
   ```
3. En GitHub → Settings → Secrets and variables → Actions, agregar:

   | Secret | Valor |
   |--------|-------|
   | `AZURE_TENANT_ID` | ID del tenant M365 |
   | `AZURE_CLIENT_ID` | ID de la app Azure AD |
   | `MSAL_TOKEN_CACHE` | Salida de `setup_auth.py` |
   | `REPORT_SENDER` | Correo remitente |
   | `REPORT_RECIPIENTS` | Destinatarios separados por coma |

El workflow corre automáticamente los días 28–31 de cada mes y actúa solo el último día.

Para dispararlo manualmente: GitHub → Actions → **Informe Mensual OSE/UTE** → Run workflow (opcionalmente especificar un mes en formato `YYYY-MM`).

---

## Comandos disponibles

```powershell
# Importar facturas desde una carpeta
python main.py process-folder data/bills/2026/06

# Generar el PDF de un mes (sin enviar)
python main.py generate-report 2026-06

# Generar y enviar (solo actúa el último día del mes)
python main.py monthly

# Generar y enviar forzando sin importar la fecha
$env:FORCE_MONTHLY="true"; python main.py monthly

# Importar historial desde CSV
python main.py import-history data/history_template_OSE.csv
```

---

## Estructura del proyecto

```
report-generator-ose-ute/
├── data/
│   ├── bills/          # PDFs de facturas (no se sube a GitHub)
│   ├── reports/        # PDFs generados (no se sube a GitHub)
│   └── bills.db        # Base de datos SQLite (se sube a GitHub)
├── src/
│   ├── parsers/        # Extracción de datos de PDFs
│   ├── reports/        # Generación del PDF (charts + layout)
│   ├── storage/        # Base de datos y sincronización
│   └── email_sender.py # Envío via Microsoft Graph API
├── config/
│   ├── settings.py     # Variables de entorno
│   └── locations.json  # Configuración de ubicaciones
├── .github/workflows/
│   └── monthly_report.yml  # Automatización GitHub Actions
├── main.py             # CLI principal
└── setup_auth.py       # Autenticación inicial con M365
```

---

## Roadmap

- [ ] Extracción automática de facturas via Anthropic Vision API (reemplaza el extractor actual)
- [ ] Ingesta automática desde correo (requiere buzón dedicado para facturas)
