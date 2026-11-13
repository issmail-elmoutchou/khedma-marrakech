"""Connexion à la base de données.

- Par défaut : SQLite local (fichier DB_PATH), comme avant (tests, Docker, localhost).
- Si TURSO_DATABASE_URL est défini : base Turso (libSQL, compatible SQLite) via le réseau.
  Nécessaire sur Vercel, dont le disque n'est pas persistant.
Le SQL de l'application ne change pas : Turso comprend le même SQL que SQLite.
"""
import os
import sqlite3


def is_remote() -> bool:
    return bool(os.environ.get("TURSO_DATABASE_URL"))


class Row(tuple):
    """Ligne de résultat : accès par index (r[0]) ou par nom de colonne (r["colonne"]), comme sqlite3.Row."""

    def __new__(cls, values, cols):
        obj = tuple.__new__(cls, values)
        obj._cols = list(cols)
        return obj

    def __getitem__(self, key):
        if isinstance(key, str):
            return tuple.__getitem__(self, self._cols.index(key))
        return tuple.__getitem__(self, key)

    def keys(self):
        return list(self._cols)


class _Cursor:
    def __init__(self, cur):
        self._cur = cur

    def _wrap(self, values):
        cols = [d[0] for d in (self._cur.description or [])]
        return Row(values, cols)

    def fetchone(self):
        r = self._cur.fetchone()
        return None if r is None else self._wrap(r)

    def fetchall(self):
        return [self._wrap(r) for r in self._cur.fetchall()]

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def description(self):
        return self._cur.description

    @property
    def rowcount(self):
        return getattr(self._cur, "rowcount", -1)

    @property
    def lastrowid(self):
        return getattr(self._cur, "lastrowid", None)


class _RemoteConn:
    """Enveloppe autour du client libSQL pour offrir la même interface que sqlite3."""

    def __init__(self):
        try:
            import libsql
        except ImportError:                      # ancien nom du paquet
            import libsql_experimental as libsql
        self._c = libsql.connect(os.environ["TURSO_DATABASE_URL"],
                                 auth_token=os.environ.get("TURSO_AUTH_TOKEN", ""))

    def execute(self, sql, params=()):
        cur = self._c.cursor()
        cur.execute(sql, tuple(params))
        return _Cursor(cur)

    def executescript(self, script):
        for stmt in script.split(";"):
            if stmt.strip():
                self.execute(stmt)
        self.commit()

    def commit(self):
        self._c.commit()

    def rollback(self):
        try:
            self._c.rollback()
        except Exception:
            pass

    def close(self):
        try:
            self._c.close()
        except Exception:
            pass


def connect(path, timeout=None):
    if is_remote():
        return _RemoteConn()
    conn = sqlite3.connect(path, timeout=timeout) if timeout else sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn
