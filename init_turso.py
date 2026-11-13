"""Crée les tables sur Turso (à lancer UNE SEULE FOIS depuis votre PC).

PowerShell :
    $env:TURSO_DATABASE_URL="libsql://votre-base.turso.io"
    $env:TURSO_AUTH_TOKEN="votre-jeton"
    python init_turso.py
"""
import os

os.environ["INIT_DB"] = "1"
os.environ.setdefault("AUTO_REFRESH", "0")

import app  # noqa: E402  (l'import exécute init_db)

if app.dbconn.is_remote():
    print("OK : tables créées sur Turso.")
else:
    print("ATTENTION : TURSO_DATABASE_URL n'est pas défini, la base locale a été utilisée.")
