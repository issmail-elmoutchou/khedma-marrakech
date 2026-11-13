"""Configuration pytest : base SQLite temporaire, planificateur désactivé, aucun appel réseau."""
import os
import sys
import tempfile
import uuid

import pytest

_tmp = tempfile.mkdtemp()
os.environ.update(DB_PATH=os.path.join(_tmp, "test.db"), AUTO_REFRESH="0", SECRET_KEY="test-secret-key")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as appmod  # noqa: E402


@pytest.fixture()
def app():
    appmod.app.config.update(TESTING=True)
    return appmod.app


class Client:
    """Client de test qui gère le jeton CSRF comme le ferait le navigateur."""

    def __init__(self, flask_client):
        self.c = flask_client
        with self.c.session_transaction() as s:
            s["csrf"] = "tok-" + uuid.uuid4().hex

    @property
    def token(self):
        """Comme le navigateur : après connexion/déconnexion la session est neuve, le jeton revient avec la page suivante."""
        with self.c.session_transaction() as s:
            tok = s.get("csrf")
        if not tok:
            self.c.get("/login")   # le rendu de la page crée un nouveau jeton (balise <meta name="csrf-token">)
            with self.c.session_transaction() as s:
                tok = s["csrf"]
        return tok

    def get(self, *a, **k):
        return self.c.get(*a, **k)

    def post(self, url, data=None, json=None, csrf=True, **k):
        headers = {"X-CSRF-Token": self.token} if csrf else {}
        if json is not None:
            return self.c.post(url, json=json, headers=headers, **k)
        return self.c.post(url, data={**(data or {}), **({"csrf_token": self.token} if csrf else {})}, **k)

    def register(self, name="Test User", email=None, password="motdepasse1"):
        email = email or f"{uuid.uuid4().hex[:10]}@test.ma"
        r = self.post("/register", data={"name": name, "email": email, "password": password})
        assert r.status_code == 302, r.data[:300]
        return email


@pytest.fixture()
def client(app):
    return Client(app.test_client())


@pytest.fixture()
def user(client):
    client.register()
    return client


@pytest.fixture()
def other_user(app):
    c = Client(app.test_client())
    c.register()
    return c
