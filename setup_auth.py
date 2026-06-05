"""
One-time authentication setup for Microsoft Graph API (delegated flow).

Run this script ONCE locally after registering the app in Azure AD:
  python setup_auth.py

It will open a browser for login. After completing it, the script prints
a base64-encoded token cache. Store that value as the GitHub Secret
'MSAL_TOKEN_CACHE'.

Re-run this script if authentication ever stops working (the token cache
is valid for ~90 days; since the report runs monthly the window resets
automatically, but an admin password change or explicit revocation would
require re-running this).
"""

import base64
import os
import sys

from dotenv import load_dotenv
import msal

load_dotenv()

TENANT_ID = os.environ.get("AZURE_TENANT_ID")
CLIENT_ID  = os.environ.get("AZURE_CLIENT_ID")
SCOPES     = ["https://graph.microsoft.com/Mail.Send"]

if not TENANT_ID or not CLIENT_ID:
    print("[error] AZURE_TENANT_ID y AZURE_CLIENT_ID deben estar en .env")
    sys.exit(1)

cache = msal.SerializableTokenCache()
app   = msal.PublicClientApplication(
    CLIENT_ID,
    authority=f"https://login.microsoftonline.com/{TENANT_ID}",
    token_cache=cache,
)

# Try silent auth first (useful if re-running the script)
accounts = app.get_accounts()
result   = None
if accounts:
    result = app.acquire_token_silent(SCOPES, account=accounts[0])

if not result:
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        print(f"[error] No se pudo iniciar el flujo: {flow.get('error_description')}")
        sys.exit(1)
    print("\n" + "=" * 62)
    print(flow["message"])
    print("=" * 62 + "\n")
    input("Presiona Enter DESPUÉS de completar el login en el navegador...")
    result = app.acquire_token_by_device_flow(flow)

if "access_token" not in result:
    print(f"[error] {result.get('error_description', result.get('error', 'desconocido'))}")
    sys.exit(1)

encoded = base64.b64encode(cache.serialize().encode()).decode()

print("\n✅ Autenticacion exitosa!")
print("\nCopia este valor como GitHub Secret con nombre  MSAL_TOKEN_CACHE :")
print("-" * 70)
print(encoded)
print("-" * 70)
print("\nTambien podés agregarlo a tu .env local para pruebas:")
print(f"MSAL_TOKEN_CACHE={encoded}\n")
