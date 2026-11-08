
"""Stockage chiffré des clés API et réglages SMTP de chaque utilisateur.
Les valeurs sont chiffrées (Fernet) avec une clé dérivée de SECRET_KEY.
"""
import base64
import hashlib
import os

from cryptography.fernet import Fernet, InvalidToken

# nom -> (libellé, secret ?)
FIELDS = {
    "GOOGLE_MAPS_API_KEY": ("Clé API Google Places", True),
    "SMTP_HOST": ("Serveur SMTP", False),
    "SMTP_PORT": ("Port SMTP", False),
    "SMTP_USER": ("Utilisateur SMTP", False),
    "SMTP_PASSWORD": ("Mot de passe SMTP", True),
    "SMTP_FROM": ("Adresse d'expéditeur", False),
}


def _fernet() -> Fernet:
    secret = os.environ.get("SECRET_KEY", "dev-only-change-me").encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret).digest()))


def ensure_table(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS user_settings("
        "user_id INTEGER, name TEXT, value BLOB, "
        "PRIMARY KEY(user_id, name))"
    )


def set_value(conn, user_id: int, name: str, value: str):
    if name not in FIELDS:
        raise KeyError(name)

    conn.execute(
        "INSERT OR REPLACE INTO user_settings VALUES(?,?,?)",
        (user_id, name, _fernet().encrypt(value.strip().encode()))
    )


def delete_value(conn, user_id: int, name: str):
    if name not in FIELDS:
        raise KeyError(name)

    conn.execute(
        "DELETE FROM user_settings WHERE user_id=? AND name=?",
        (user_id, name)
    )


def get_value(conn, user_id: int, name: str, env_fallback: bool = True) -> str:
    """Valeur de l'utilisateur, sinon variable d'environnement du serveur."""
    if name not in FIELDS:
        return ""

    row = conn.execute(
        "SELECT value FROM user_settings WHERE user_id=? AND name=?",
        (user_id, name)
    ).fetchone()

    if row:
        try:
            return _fernet().decrypt(bytes(row[0])).decode()
        except InvalidToken:
            # SECRET_KEY différente : valeur chiffrée illisible.
            pass

    return os.environ.get(name, "") if env_fallback else ""


def source_of(conn, user_id: int, name: str) -> str:
    """'user' si enregistrée par l'utilisateur, 'server' si fournie par l'environnement."""
    if get_value(conn, user_id, name, env_fallback=False):
        return "user"

    return "server" if os.environ.get(name) else ""


def mask(value: str, secret: bool) -> str:
    if not value:
        return ""

    return ("•" * 8 + value[-4:]) if secret and len(value) > 8 else (
        "•" * 8 if secret else value
    )
