import json
import uuid

import pandas as pd

import app as appmod
import secrets_store as sec
from insights import classify, match_score


def unique_url(tag="x"):
    return f"https://www.rekrute.com/offre-emploi-{tag}-recrutement-acme-marrakech-{uuid.uuid4().int % 10**8}.html"


# ------------------------------------------------------------ accès et sécurité
def test_anonymous_is_redirected_to_login(client):
    r = client.get("/")
    assert r.status_code == 302 and "/login" in r.headers["Location"]


def test_api_requires_login(client):
    r = client.post("/api/favorite", json={"kind": "offer", "ref": "x"})
    assert r.status_code == 401


def test_post_without_csrf_token_is_rejected(client):
    r = client.post("/register", data={"name": "A", "email": "a@test.ma", "password": "motdepasse1"}, csrf=False)
    assert r.status_code == 403


def test_api_post_without_csrf_header_is_rejected(user):
    r = user.post("/api/favorite", json={"kind": "offer", "ref": "x"}, csrf=False)
    assert r.status_code == 403


def test_register_login_logout_flow(client):
    email = client.register()
    assert client.get("/").status_code == 200
    assert client.post("/logout").status_code == 302
    assert client.get("/").status_code == 302
    bad = client.post("/login", data={"email": email, "password": "mauvais"})
    assert bad.status_code == 200 and b"incorrect" in bad.data
    ok = client.post("/login", data={"email": email, "password": "motdepasse1"})
    assert ok.status_code == 302


def test_register_rejects_short_password(client):
    r = client.post("/register", data={"name": "A", "email": "short@test.ma", "password": "123"})
    assert r.status_code == 200 and b"8 caract" in r.data


def test_login_redirect_blocks_open_redirect(client):
    email = client.register()
    client.post("/logout")
    r = client.post("/login?next=//evil.com", data={"email": email, "password": "motdepasse1"})
    assert r.status_code == 302 and "evil.com" not in r.headers["Location"]


# ------------------------------------------------------------------ pages
def test_main_pages_render(user):
    for url in ["/", "/offres", "/entreprises", "/carte", "/candidatures", "/favoris", "/profil",
                "/statistiques", "/recherche", "/parametres", "/cv"]:
        assert user.get(url).status_code == 200, url


def test_offers_page_survives_bad_params(user):
    assert user.get("/offres?page=abc&tri=zzz").status_code == 200
    assert user.get("/offres?page=9999").status_code == 200


def test_offers_show_percentage_when_profile_filled(user):
    user.post("/profil", data={"name": "T", "title": "Data Analyst", "skills": "Python SQL", "bio": ""})
    html = user.get("/offres").data.decode()
    assert "%</span>" in html


def test_offer_card_status_menu_lists_all_statuses(user):
    html = user.get("/offres").data.decode()
    for s in ["Envoyé", "En attente", "Entretien", "Refusé", "Accepté"]:
        assert f">{s}</option>" in html, s


def test_stats_api_shape(user):
    d = user.get("/api/stats").get_json()
    for k in ("kpis", "offers_by_sector", "funnel", "timeline", "generated"):
        assert k in d
    assert d["kpis"]["offers"] > 0


# ------------------------------------------------------- correspondance / secteurs
def test_match_score_none_without_profile():
    assert match_score({"title": "", "skills": "", "bio": "", "sectors": ""}, "Comptable", "Finance & Assurance") is None


def test_match_score_rewards_common_words_and_sector():
    p = {"title": "Data Analyst", "skills": "Python", "bio": "", "sectors": "Informatique & IA"}
    high = match_score(p, "DATA ANALYST & AI AUTOMATION SPECIALIST", "Informatique & IA")
    low = match_score(p, "Caissier", "Commerce & Distribution")
    assert high >= 60 and low < 30 and 0 <= low <= high <= 100


def test_classify_known_titles():
    assert classify("Comptable Femme") == "Finance & Assurance"
    assert classify("Graphiste") == "Marketing & Communication"
    assert classify("Commercial Terrain") == "Commerce & Distribution"
    assert classify("xyz abc") == "Non classé"


# ----------------------------------------------------------- clés API chiffrées
def test_secrets_are_encrypted_and_masked(user):
    user.post("/parametres", data={"GOOGLE_MAPS_API_KEY": "AIzaSECRETKEY9999"})
    html = user.get("/parametres").data.decode()
    assert "AIzaSECRETKEY9999" not in html and "9999" in html
    import sqlite3
    raw = sqlite3.connect(appmod.DB_PATH).execute(
        "SELECT value FROM user_settings WHERE name='GOOGLE_MAPS_API_KEY' ORDER BY rowid DESC").fetchone()[0]
    assert b"AIza" not in bytes(raw)


def test_secret_roundtrip_and_mask():
    import sqlite3
    c = sqlite3.connect(":memory:")
    sec.ensure_table(c)
    sec.set_value(c, 1, "SMTP_PASSWORD", "mon-mot-de-passe")
    assert sec.get_value(c, 1, "SMTP_PASSWORD") == "mon-mot-de-passe"
    assert sec.get_value(c, 2, "SMTP_PASSWORD", env_fallback=False) == ""
    assert sec.mask("abcdefghijkl", True).endswith("ijkl") and "abcdefgh" not in sec.mask("abcdefghijkl", True)


def test_settings_isolated_between_users(user, other_user):
    user.post("/parametres", data={"ANTHROPIC_API_KEY": "sk-ant-secret-1234"})
    assert "1234" not in other_user.get("/parametres").data.decode()


# --------------------------------------------------------------------- recherche
def _fake_rekrute(urls):
    def fake(keyword="", direct_url=""):
        return pd.DataFrame([{"poste": f"Poste {i} | Marrakech (Maroc)", "localisation": "Marrakech", "url": u}
                             for i, u in enumerate(urls)]), None
    return fake


def test_search_add_and_dedup(user, monkeypatch):
    urls = [unique_url("a"), unique_url("b")]
    monkeypatch.setattr(appmod, "scrape_rekrute", _fake_rekrute(urls))
    r = user.post("/api/search", json={"kind": "offers", "query": "data"}).get_json()
    assert len(r["items"]) == 2 and r["known"] == 0
    added = user.post("/api/search/add", json={"token": r["token"], "ids": [0, 1]}).get_json()
    assert added["added"] == 2
    again = user.post("/api/search", json={"kind": "offers", "query": "data"}).get_json()
    assert len(again["items"]) == 0 and again["known"] == 2
    assert user.post("/api/search/add", json={"token": r["token"], "ids": [0]}).status_code == 404  # jeton à usage unique


def test_search_rejects_non_rekrute_link(user):
    r = user.post("/api/search", json={"kind": "offers", "query": "http://evil.example/page"})
    assert r.status_code == 502 and "Rekrute" in r.get_json()["error"]


def test_company_search_needs_key(user):
    r = user.post("/api/search", json={"kind": "companies", "query": "agence ia"})
    assert r.status_code == 502 and "Google" in r.get_json()["error"]


def test_search_token_not_usable_by_other_user(user, other_user, monkeypatch):
    monkeypatch.setattr(appmod, "scrape_rekrute", _fake_rekrute([unique_url("c")]))
    tok = user.post("/api/search", json={"kind": "offers", "query": "data"}).get_json()["token"]
    assert other_user.post("/api/search/add", json={"token": tok, "ids": [0]}).status_code == 404


def test_saved_search_run(user, monkeypatch):
    monkeypatch.setattr(appmod, "scrape_rekrute", _fake_rekrute([unique_url("d")]))
    assert user.post("/api/saved", json={"kind": "offers", "query": "data analyst"}).status_code == 200
    sid = appmod.sqlite3.connect(appmod.DB_PATH).execute("SELECT MAX(id) FROM saved_searches").fetchone()[0]
    r = user.post(f"/api/saved/{sid}/run").get_json()
    assert r["status"] == "OK" and r["added"] == 1


# ------------------------------------------------------------------------ Kanban
def test_kanban_add_move_delete(user):
    assert user.post("/api/application/add", json={"poste": "Chef de projet", "entreprise": "ACME"}).status_code == 200
    html = user.get("/candidatures").data.decode()
    assert "Chef de projet" in html
    url = unique_url("k")
    assert user.post("/api/application", json={"url": url, "poste": "Dev", "statut": "Entretien"}).status_code == 200
    assert "Dev" in user.get("/candidatures").data.decode()
    assert user.post("/api/application", json={"url": url, "poste": "Dev", "statut": "Inconnu"}).status_code == 400
    user.post("/api/application/delete", json={"url": url})
    assert "Dev</strong>" not in user.get("/candidatures").data.decode()


def test_applications_isolated_between_users(user, other_user):
    url = unique_url("iso")
    user.post("/api/application", json={"url": url, "poste": "SecretJob", "statut": "Envoyé"})
    assert "SecretJob" not in other_user.get("/candidatures").data.decode()
    other_user.post("/api/application/delete", json={"url": url})
    assert "SecretJob" in user.get("/candidatures").data.decode()   # l'autre utilisateur n'a rien pu supprimer


def test_favorite_toggle(user):
    ref = unique_url("fav")
    assert user.post("/api/favorite", json={"kind": "offer", "ref": ref}).get_json()["active"] is True
    assert user.post("/api/favorite", json={"kind": "offer", "ref": ref}).get_json()["active"] is False
    assert user.post("/api/favorite", json={"kind": "bad", "ref": ref}).status_code == 400


# --------------------------------------------------------------------- CV et envoi
def _cv():
    return {"full_name": "Sara L", "title": "Data Analyst", "email": "s@test.ma", "summary": "é à ç",
            "experiences": [{"role": "Stagiaire", "company": "ACME", "period": "2025", "details": "- SQL\n- Python"}],
            "education": [{"degree": "Master", "school": "UCA", "period": "2024"}], "skills": "Python"}


def test_cv_save_and_pdf(user):
    assert user.post("/api/cv", json=_cv()).status_code == 200
    r = user.get("/cv.pdf")
    assert r.status_code == 200 and r.data.startswith(b"%PDF") and r.mimetype == "application/pdf"


def test_cv_ignores_unexpected_fields(user):
    user.post("/api/cv", json={**_cv(), "is_admin": True, "evil": "x"})
    stored = json.loads(appmod.sqlite3.connect(appmod.DB_PATH).execute(
        "SELECT data FROM cvs ORDER BY rowid DESC LIMIT 1").fetchone()[0])
    assert "is_admin" not in stored and "evil" not in stored


def test_apply_requires_cv_then_smtp(user, monkeypatch):
    payload = {"to": "rh@acme.ma", "subject": "Candidature", "body": "Bonjour", "url": unique_url("ap"), "poste": "Dev"}
    assert user.post("/api/apply", json=payload).status_code == 400            # pas de CV
    user.post("/api/cv", json=_cv())
    r = user.post("/api/apply", json=payload)
    assert r.status_code == 503 and r.get_json()["fallback"] is True          # pas de SMTP configuré


def test_apply_sends_and_moves_to_envoye(user, monkeypatch):
    sent = []
    monkeypatch.setattr(appmod, "send_application", lambda *a, **k: sent.append(a))
    user.post("/api/cv", json=_cv())
    user.post("/parametres", data={"SMTP_HOST": "smtp.test", "SMTP_USER": "me@test.ma", "SMTP_PASSWORD": "pw"})
    url = unique_url("ok")
    r = user.post("/api/apply", json={"to": "rh@acme.ma", "subject": "Candidature", "body": "Bonjour", "url": url, "poste": "Dev"})
    assert r.status_code == 200 and len(sent) == 1 and sent[0][0] == "rh@acme.ma"
    assert "Dev" in user.get("/candidatures").data.decode()


def test_apply_rejects_invalid_email_and_header_injection(user):
    user.post("/api/cv", json=_cv())
    for bad in ("pas-un-email", "a@b.ma\nBcc: x@y.ma"):
        r = user.post("/api/apply", json={"to": bad, "subject": "S", "body": "B"})
        assert r.status_code == 400


# ---------------------------------------------------------------------- résumé
def test_summary_rejects_unknown_url(user):
    assert user.get("/api/summary?url=http://evil.example/x").status_code == 404


# ------------------------------------------------------------------------- n8n
def _token(user):
    return user.post("/api/settings/n8n_token").get_json()["token"]


def test_n8n_requires_valid_token(client, app):
    c = app.test_client()
    assert c.get("/api/n8n/summary").status_code == 401
    assert c.get("/api/n8n/summary", headers={"Authorization": "Bearer faux"}).status_code == 401


def test_n8n_token_flow_without_csrf_and_revoke(user, app):
    tok = _token(user)
    anon = app.test_client()                       # aucun cookie, aucun jeton CSRF : seul le Bearer compte
    h = {"Authorization": f"Bearer {tok}"}
    r = anon.get("/api/n8n/summary", headers=h)
    assert r.status_code == 200 and "offers_total" in r.get_json()
    assert anon.get("/api/n8n/summary", headers={"X-API-Key": tok}).status_code == 200
    assert user.post("/api/settings/n8n_token/revoke").status_code == 200
    assert anon.get("/api/n8n/summary", headers=h).status_code == 401


def test_n8n_new_token_revokes_old(user, app):
    old, new = _token(user), _token(user)
    anon = app.test_client()
    assert anon.get("/api/n8n/summary", headers={"Authorization": f"Bearer {old}"}).status_code == 401
    assert anon.get("/api/n8n/summary", headers={"Authorization": f"Bearer {new}"}).status_code == 200


def test_n8n_token_is_stored_hashed(user):
    tok = _token(user)
    import sqlite3
    rows = sqlite3.connect(appmod.DB_PATH).execute("SELECT token_hash FROM api_tokens").fetchall()
    assert all(tok not in r[0] for r in rows)


def test_n8n_offers_filters(user, app):
    user.post("/profil", data={"name": "T", "title": "Data Analyst", "skills": "Python SQL", "bio": "", "sectors": "Informatique & IA"})
    h = {"Authorization": f"Bearer {_token(user)}"}
    anon = app.test_client()
    allr = anon.get("/api/n8n/offers", headers=h).get_json()
    high = anon.get("/api/n8n/offers?min_match=60", headers=h).get_json()
    assert allr["count"] > high["count"] >= 1 and all(o["match"] >= 60 for o in high["offers"])
    assert anon.get("/api/n8n/offers?min_match=abc", headers=h).status_code == 400


def test_webhook_url_guard(monkeypatch):
    monkeypatch.delenv("N8N_ALLOW_PRIVATE", raising=False)
    assert appmod.webhook_url_ok("http://8.8.8.8/hook")[0] is True
    for bad in ("http://127.0.0.1:5678/x", "http://10.0.0.5/x", "http://169.254.169.254/latest", "file:///etc/passwd", "ftp://8.8.8.8/x"):
        assert appmod.webhook_url_ok(bad)[0] is False, bad
    monkeypatch.setenv("N8N_ALLOW_PRIVATE", "1")
    assert appmod.webhook_url_ok("http://localhost:5678/webhook/x")[0] is True


def test_settings_rejects_private_webhook(user, monkeypatch):
    monkeypatch.delenv("N8N_ALLOW_PRIVATE", raising=False)
    r = user.post("/parametres", data={"N8N_WEBHOOK_URL": "http://127.0.0.1:5678/x"}, follow_redirects=True)
    assert b"refus" in r.data


def test_saved_search_notifies_n8n(user, monkeypatch):
    monkeypatch.delenv("N8N_ALLOW_PRIVATE", raising=False)
    user.post("/profil", data={"name": "T", "title": "Data Analyst", "skills": "Python", "bio": "", "sectors": "Informatique & IA"})
    user.post("/parametres", data={"N8N_WEBHOOK_URL": "http://8.8.8.8/hook", "N8N_MIN_MATCH": "0"})
    url = unique_url("n8n")
    monkeypatch.setattr(appmod, "scrape_rekrute", lambda keyword="", direct_url="": (pd.DataFrame(
        [{"poste": "Data Analyst | Marrakech (Maroc)", "localisation": "Marrakech", "url": url}]), None))
    sent = []
    monkeypatch.setattr(appmod.requests, "post", lambda u, json=None, **k: sent.append((u, json)) or type("R", (), {"status_code": 200})())
    user.post("/api/saved", json={"kind": "offers", "query": "data"})
    sid = appmod.sqlite3.connect(appmod.DB_PATH).execute("SELECT MAX(id) FROM saved_searches").fetchone()[0]
    assert user.post(f"/api/saved/{sid}/run").get_json()["status"] == "OK"
    assert len(sent) == 1 and sent[0][0] == "http://8.8.8.8/hook"
    payload = sent[0][1]
    assert payload["event"] == "new_offers" and payload["count"] == 1 and payload["offers"][0]["url"] == url
    assert payload["offers"][0]["match"] >= 60


def test_n8n_threshold_filters_notification(user, monkeypatch):
    monkeypatch.delenv("N8N_ALLOW_PRIVATE", raising=False)
    user.post("/profil", data={"name": "T", "title": "Data Analyst", "skills": "Python", "bio": "", "sectors": "Informatique & IA"})
    user.post("/parametres", data={"N8N_WEBHOOK_URL": "http://8.8.8.8/hook", "N8N_MIN_MATCH": "90"})
    monkeypatch.setattr(appmod, "scrape_rekrute", lambda keyword="", direct_url="": (pd.DataFrame(
        [{"poste": "Caissier | Marrakech (Maroc)", "localisation": "Marrakech", "url": unique_url("low")}]), None))
    sent = []
    monkeypatch.setattr(appmod.requests, "post", lambda u, json=None, **k: sent.append(json))
    user.post("/api/saved", json={"kind": "offers", "query": "caisse"})
    sid = appmod.sqlite3.connect(appmod.DB_PATH).execute("SELECT MAX(id) FROM saved_searches").fetchone()[0]
    user.post(f"/api/saved/{sid}/run")
    assert sent == []          # offre sous le seuil : aucune notification


def test_n8n_refresh_endpoint(user, app, monkeypatch):
    monkeypatch.setattr(appmod, "scrape_rekrute", lambda keyword="", direct_url="": (pd.DataFrame(
        [{"poste": "Poste X | Marrakech (Maroc)", "localisation": "Marrakech", "url": unique_url("rf")}]), None))
    user.post("/api/saved", json={"kind": "offers", "query": "poste x"})
    r = app.test_client().post("/api/n8n/refresh", headers={"Authorization": f"Bearer {_token(user)}"}).get_json()
    assert r["ran"] >= 1 and r["added"] >= 1
