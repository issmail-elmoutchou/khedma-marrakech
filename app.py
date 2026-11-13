"""khdma Marrakech — application web (Flask) : connexion, tableau de bord,
offres en cartes, recommandations selon le profil, résumé d'offres, carte,
suivi des candidatures et favoris."""
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import socket
from collections import Counter
import re
import sqlite3

import dbconn
import threading
import time
import uuid
from datetime import datetime
from datetime import date
from functools import wraps
from urllib.parse import urlparse
import smtplib
from email.message import EmailMessage
from itsdangerous import URLSafeTimedSerializer

import pandas as pd
import requests
import insights  # noqa: F401  (ajouté dans votre version)
from flask import (Flask, abort, flash, g, jsonify, redirect, render_template,
                   request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

from data_sources import clean_companies_df, clean_jobs_df, scrape_rekrute, search_places
import secrets_store as sec
from cv_tools import EMAIL_RE, EMPTY_CV, build_pdf, find_emails, send_application, smtp_config, test_smtp
from insights import SECTOR_NAMES, STOP, classify, match_score, norm, summarize_offer

BASE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(BASE, "data"))
DB_PATH = os.environ.get("DB_PATH", os.path.join(DATA_DIR, "career.db"))
STATUTS = ["Envoyé", "En attente", "Entretien", "Refusé", "Accepté"]
PER_PAGE = 12

app = Flask(__name__)
app.config.update(
    SECRET_KEY=os.environ.get("SECRET_KEY", "dev-only-change-me"),
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",   # mettre COOKIE_SECURE=1 derrière HTTPS
)


# ------------------------------------------------------------------ protection CSRF
def csrf_token() -> str:
    """Jeton secret lié à la session ; ajouté automatiquement aux formulaires et aux fetch() par static/csrf.js."""
    if "csrf" not in session:
        session["csrf"] = secrets.token_hex(16)
    return session["csrf"]


app.jinja_env.globals["csrf_token"] = csrf_token
app.jinja_env.globals["statuts"] = STATUTS   # visible aussi dans les macros importées (_macros.html)


@app.before_request
def csrf_protect():
    if request.path.startswith("/api/n8n/"):   # authentifié par jeton Bearer (pas de cookie => pas de CSRF)
        return
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        sent = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or ""
        if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
            if request.path.startswith("/api/"):
                return jsonify(error="Session expirée : rechargez la page."), 403
            return "Session expirée ou requête non autorisée. Rechargez la page et réessayez.", 403


# ---------------------------------------------------------------- base de données
def db():
    if "db" not in g:
        g.db = dbconn.connect(DB_PATH)
    return g.db


@app.teardown_appcontext
def close_db(_):
    conn = g.pop("db", None)
    if conn:
        conn.close()


def init_db():
    if not dbconn.is_remote():
        os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    conn = dbconn.connect(DB_PATH)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS users(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, email TEXT UNIQUE, password_hash TEXT,
        title TEXT DEFAULT '', skills TEXT DEFAULT '', bio TEXT DEFAULT '', sectors TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS favorites(
        user_id INTEGER, kind TEXT, ref TEXT, PRIMARY KEY(user_id, kind, ref));
    CREATE TABLE IF NOT EXISTS candidatures(
        user_id INTEGER, url TEXT, poste TEXT, statut TEXT, date_maj TEXT, notes TEXT DEFAULT '',
        PRIMARY KEY(user_id, url));
    CREATE TABLE IF NOT EXISTS cvs(user_id INTEGER PRIMARY KEY, data TEXT, updated TEXT);
    CREATE TABLE IF NOT EXISTS sent_mails(
        user_id INTEGER, url TEXT, to_email TEXT, subject TEXT, sent_at TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS user_settings(user_id INTEGER, name TEXT, value BLOB, PRIMARY KEY(user_id, name));
    CREATE TABLE IF NOT EXISTS saved_searches(
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, kind TEXT, query TEXT, auto INTEGER DEFAULT 1,
        last_run TEXT, last_added INTEGER DEFAULT 0, last_status TEXT DEFAULT '');
    CREATE TABLE IF NOT EXISTS search_results(
        token TEXT PRIMARY KEY, user_id INTEGER, kind TEXT, payload TEXT, created TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS summaries(url TEXT PRIMARY KEY, payload TEXT);
    CREATE TABLE IF NOT EXISTS extra_offers(url TEXT PRIMARY KEY, poste TEXT, localisation TEXT, added_at TEXT);
    CREATE TABLE IF NOT EXISTS api_tokens(
        token_hash TEXT PRIMARY KEY, user_id INTEGER UNIQUE, created TEXT DEFAULT CURRENT_TIMESTAMP);
    CREATE TABLE IF NOT EXISTS extra_companies(
        place_id TEXT PRIMARY KEY, nom TEXT, adresse TEXT, telephone TEXT, site_web TEXT, google_maps TEXT,
        categorie TEXT, note REAL, nombre_avis REAL, latitude REAL, longitude REAL);
    """)
    try:
        conn.execute("ALTER TABLE candidatures ADD COLUMN entreprise TEXT DEFAULT ''")
    except Exception:   # colonne déjà présente
        pass
    try:
        conn.execute("ALTER TABLE extra_offers ADD COLUMN added_at TEXT")
    except Exception:   # colonne déjà présente
        pass
    # anciens statuts -> nouveaux statuts
    conn.execute("UPDATE candidatures SET statut='Envoyé' WHERE statut='Postulé'")
    conn.execute("UPDATE candidatures SET statut='En attente' WHERE statut='À suivre'")
    conn.commit()
    conn.close()


# --------------------------------------------------------------------- données
def _clean_title(poste: str) -> str:
    return re.split(r"\s+\|\s+", str(poste))[0].strip()


def _company_from_url(url: str) -> str:
    m = re.search(r"recrutement-(.+?)-\d+\.html", url)
    if not m:
        return ""
    slug = re.sub(r"-(marrakech|maroc|casablanca|agadir)(-.*)?$", "", m.group(1))
    return slug.replace("-", " ").title()


def query_df(sql: str) -> pd.DataFrame:
    """Résultat d'une requête SQL en DataFrame (fonctionne avec SQLite local comme avec Turso)."""
    cur = db().execute(sql)
    cols = [d[0] for d in cur.description]
    return pd.DataFrame([tuple(r) for r in cur.fetchall()], columns=cols)


def load_offers() -> pd.DataFrame:
    try:
        df = pd.read_csv(os.path.join(DATA_DIR, "offres_rekrute_marrakech.csv"))
    except Exception:
        df = pd.DataFrame(columns=["poste", "localisation", "url"])
    extra = query_df("SELECT poste, localisation, url FROM extra_offers")
    df = clean_jobs_df(pd.concat([df, extra], ignore_index=True))
    if df.empty:
        return df.assign(titre="", entreprise="", secteur="")
    df["titre"] = df["poste"].map(_clean_title)
    df["entreprise"] = df["url"].map(_company_from_url)
    df["secteur"] = (df["titre"] + " " + df["entreprise"]).map(classify)  # le nom de l'entreprise aide pour les titres génériques
    return df


def load_companies() -> pd.DataFrame:
    try:
        df = pd.read_csv(os.path.join(DATA_DIR, "entreprises_IA_Marrakech.csv"))
    except Exception:
        df = pd.DataFrame()
    extra = query_df("SELECT * FROM extra_companies")
    df = clean_companies_df(pd.concat([df, extra], ignore_index=True))
    if df.empty:
        return df
    df["secteur"] = (
    df["nom"].fillna("").astype(str)
    + " "
    + df["categorie"].fillna("").astype(str)
).map(classify)
    df["domaine"] = df["site_web"].map(lambda u: urlparse(u).netloc.replace("www.", "") if isinstance(u, str) else "")
    df["initiales"] = df["nom"].map(lambda n: "".join(w[0] for w in re.findall(r"[A-Za-zÀ-ÿ0-9]+", str(n))[:2]).upper())
    df["note"] = df["note"].astype(float)
    return df


def records(df: pd.DataFrame):
    return df.where(df.notna(), None).to_dict("records")


# ---------------------------------------------------------------------- session
def current_user():
    if "uid" not in session:
        return None
    if not hasattr(g, "user"):
        g.user = db().execute("SELECT * FROM users WHERE id=?", (session["uid"],)).fetchone()
    return g.user


def login_required(view):
    @wraps(view)
    def wrapped(*a, **kw):
        if not current_user():
            if request.path.startswith("/api/"):
                return jsonify(error="Connexion requise"), 401
            return redirect(url_for("login", next=request.path))
        return view(*a, **kw)
    return wrapped


def profile_of(user) -> dict:
    return {k: user[k] or "" for k in ("title", "skills", "bio", "sectors")}


def favorites_of(user, kind) -> set:
    rows = db().execute("SELECT ref FROM favorites WHERE user_id=? AND kind=?", (user["id"], kind))
    return {r["ref"] for r in rows}


def statuses_of(user) -> dict:
    rows = db().execute("SELECT url, statut FROM candidatures WHERE user_id=?", (user["id"],))
    return {r["url"]: r["statut"] for r in rows}


@app.template_filter("dictupdate")
def dictupdate(d, page):
    from urllib.parse import urlencode
    return urlencode({**d, "page": page})


def _cv_phone() -> str:
    u = current_user()
    if not u:
        return ""
    row = db().execute("SELECT data FROM cvs WHERE user_id=?", (u["id"],)).fetchone()
    return json.loads(row["data"]).get("phone", "") if row else ""


@app.context_processor
def inject():
    return {"user": current_user(), "cv_phone": _cv_phone(), "statuts": STATUTS,  "all_sectors": SECTOR_NAMES, "active": request.endpoint}


def paginate(items: list, page: int):
    pages = max(1, -(-len(items) // PER_PAGE))
    page = min(max(page, 1), pages)
    return items[(page - 1) * PER_PAGE: page * PER_PAGE], page, pages


def scored_offers(user):
    df = load_offers()
    items = records(df)
    prof = profile_of(user)
    for o in items:
        o["match"] = match_score(prof, o["titre"], o["secteur"])
    return items


# ------------------------------------------------------------------------- auth
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        row = db().execute("SELECT * FROM users WHERE email=?", (request.form["email"].strip().lower(),)).fetchone()
        if row and check_password_hash(row["password_hash"], request.form["password"]):
            session.clear()
            session["uid"] = row["id"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        flash("Email ou mot de passe incorrect.", "error")
    return render_template("login.html", mode="login")


serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

@app.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        row = db().execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        
        if row:
            token = serializer.dumps(email, salt='password-reset-salt')
            reset_url = url_for('reset_password', token=token, _external=True)
            
            config = smtp_config() 
            
            if config:
                msg = EmailMessage()
                msg.set_content(f"Bonjour,\n\nCliquez sur le lien suivant pour réinitialiser votre mot de passe :\n{reset_url}\n\nSi vous n'avez pas demandé cela, veuillez ignorer cet email.")
                msg['Subject'] = "Réinitialisation de mot de passe - Khedma Marrakech"
                msg['From'] = config.get("user")
                msg['To'] = email
                
                try:
                    server = smtplib.SMTP(config.get("host"), int(config.get("port", 587)))
                    server.starttls()
                    server.login(config.get("user"), config.get("password"))
                    server.send_message(msg)
                    server.quit()
                    flash("Un email de réinitialisation a été envoyé à votre adresse.", "info")
                except Exception as e:
                    print(f"Erreur d'envoi d'email: {e}")
                    flash("Erreur lors de l'envoi de l'email.", "error")
            else:
                flash("Configuration SMTP introuvable.", "error")
        else:
            flash("Aucun compte associé à cet email.", "error")
            
        return redirect(url_for("login"))
        
    return render_template("forgot_password.html", mode="forgot")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name, email, pw = request.form["name"].strip(), request.form["email"].strip().lower(), request.form["password"]
        if not name or "@" not in email or len(pw) < 8:
            flash("Renseignez votre nom, un email valide et un mot de passe d'au moins 8 caractères.", "error")
        elif db().execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            flash("Un compte existe déjà avec cet email.", "error")
        else:
            cur = db().execute("INSERT INTO users(name,email,password_hash) VALUES(?,?,?)",
                               (name, email, generate_password_hash(pw)))
            db().commit()
            session.clear()
            session["uid"] = cur.lastrowid
            flash("Bienvenue ! Complétez votre profil pour recevoir des offres adaptées.", "ok")
            return redirect(url_for("profile"))
    return render_template("login.html", mode="register")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------------ pages
@app.route("/")
@login_required
def dashboard():
    user = current_user()
    offers = scored_offers(user)
    companies = load_companies()
    sectors = pd.Series([o["secteur"] for o in offers]).value_counts()
    chart = [{"label": k, "n": int(v)} for k, v in sectors.items()]
    top_offers = sorted([o for o in offers if o["match"]], key=lambda o: -o["match"])[:3] or offers[:3]
    prefs = {p for p in (user["sectors"] or "").split(",") if p}
    comp = records(companies)
    comp.sort(key=lambda c: (c["secteur"] in prefs, (c["note"] or 0) * ((c["nombre_avis"] or 0) ** 0.3)), reverse=True)
    apps_n = db().execute("SELECT COUNT(*) FROM candidatures WHERE user_id=?",
                          (user["id"],)).fetchone()[0]
    return render_template(
        "dashboard.html", chart=chart, max_n=max([c["n"] for c in chart] or [1]),
        top_offers=top_offers, companies=comp[:4], n_offers=len(offers), n_companies=len(comp),
        n_sectors=len([c for c in chart if c["label"] != "Non classé"]), n_apps=apps_n,
        favs=favorites_of(user, "company"), ofavs=favorites_of(user, "offer"), st=statuses_of(user),
        profile_empty=not any(profile_of(user).values()),
    )


@app.route("/offres")
@login_required
def offers():
    user = current_user()
    items = scored_offers(user)
    q, sector, sort = request.args.get("q", ""), request.args.get("secteur", ""), request.args.get("tri", "match")
    if q:
        items = [o for o in items if norm(q) in norm(o["titre"] + " " + o["entreprise"])]
    if sector:
        items = [o for o in items if o["secteur"] == sector]
    if sort == "titre":
        items.sort(key=lambda o: norm(o["titre"]))
    else:
        sort = "match"
        items.sort(key=lambda o: -(o["match"] or 0))
    page_arg = request.args.get("page", "1")
    page_items, page, pages = paginate(items, int(page_arg) if page_arg.isdigit() else 1)
    return render_template("offers.html", items=page_items, total=len(items), page=page, pages=pages,
                           q=q, sector=sector, sort=sort, favs=favorites_of(user, "offer"), st=statuses_of(user),
                           has_profile=any(profile_of(user).values()))


@app.route("/entreprises")
@login_required
def companies():
    user = current_user()
    items = records(load_companies())
    q, sector = request.args.get("q", ""), request.args.get("secteur", "")
    if q:
        items = [c for c in items if norm(q) in norm(f"{c['nom']} {c['categorie']} {c['adresse']}")]
    if sector:
        items = [c for c in items if c["secteur"] == sector]
    items.sort(key=lambda c: -((c["note"] or 0) * ((c["nombre_avis"] or 0) ** 0.3)))
    page_items, page, pages = paginate(items, int(request.args.get("page", 1) or 1))
    return render_template("companies.html", items=page_items, total=len(items), page=page, pages=pages,
                           q=q, sector=sector, favs=favorites_of(user, "company"))


@app.route("/carte")
@login_required
def map_view():
    pts = [c for c in records(load_companies()) if c["latitude"] and c["longitude"]]
    slim = [{k: c[k] for k in ("nom", "adresse", "latitude", "longitude", "note", "site_web", "secteur")} for c in pts]
    return render_template(
        "map.html", points=json.dumps(slim, default=str), has_points=bool(slim),
        tiles_url=os.environ.get("MAP_TILES_URL", "https://tile.openstreetmap.org/{z}/{x}/{y}.png"),
        tiles_attr=os.environ.get("MAP_TILES_ATTRIBUTION", "© les contributeurs d'OpenStreetMap"))

@app.route("/candidatures")
@login_required
def applications():
    user = current_user()
    rows = db().execute("SELECT * FROM candidatures WHERE user_id=? ORDER BY date_maj DESC", (user["id"],)).fetchall()
    cols = {s: [dict(r) for r in rows if r["statut"] == s] for s in STATUTS}
    return render_template("applications.html", cols=cols, total=len(rows))


@app.route("/favoris")
@login_required
def favorites():
    user = current_user()
    ofav, cfav = favorites_of(user, "offer"), favorites_of(user, "company")
    offers_ = [o for o in scored_offers(user) if o["url"] in ofav]
    comps = [c for c in records(load_companies()) if c["place_id"] in cfav]
    return render_template("favorites.html", offers=offers_, companies=comps, favs=cfav, ofavs=ofav,
                           st=statuses_of(user))


@app.route("/profil", methods=["GET", "POST"])
@login_required
def profile():
    user = current_user()
    if request.method == "POST":
        sectors = ",".join(s for s in request.form.getlist("sectors") if s in SECTOR_NAMES)
        db().execute("UPDATE users SET name=?, title=?, skills=?, bio=?, sectors=? WHERE id=?",
                     (request.form.get("name", user["name"]).strip(), request.form.get("title", "").strip(),
                      request.form.get("skills", "").strip(), request.form.get("bio", "").strip(), sectors, user["id"]))
        db().commit()
        flash("Profil enregistré. Vos recommandations sont à jour.", "ok")
        return redirect(url_for("profile"))
    return render_template("profile.html", chosen=set((user["sectors"] or "").split(",")))


# ============================================================ Recherche (offres / entreprises)
COMPANY_COLS = ["place_id", "nom", "adresse", "telephone", "site_web", "google_maps", "categorie",
                "note", "nombre_avis", "latitude", "longitude"]
REKRUTE_RE = re.compile(r"^https?://([a-z0-9-]+\.)*rekrute\.com(/|$)", re.I)


def known_offer_urls(conn) -> set:
    urls = {r[0] for r in conn.execute("SELECT url FROM extra_offers")}
    try:
        urls |= set(pd.read_csv(os.path.join(DATA_DIR, "offres_rekrute_marrakech.csv"))["url"].dropna())
    except Exception:
        pass
    return urls


def known_place_ids(conn) -> set:
    ids = {r[0] for r in conn.execute("SELECT place_id FROM extra_companies")}
    try:
        ids |= set(pd.read_csv(os.path.join(DATA_DIR, "entreprises_IA_Marrakech.csv"))["place_id"].dropna())
    except Exception:
        pass
    return ids


def run_search(conn, user_id: int, kind: str, query: str):
    """Lance une recherche externe. Retourne (liste de dicts, message d'erreur ou None)."""
    if kind == "offers":
        direct = query if query.lower().startswith("http") else ""
        if direct and not REKRUTE_RE.match(direct):   # seuls les liens Rekrute sont suivis (évite le SSRF)
            return [], "Seuls les liens du site Rekrute sont acceptés."
        df, err = scrape_rekrute(keyword="" if direct else query, direct_url=direct)
        items = [] if err else df.to_dict("records")
    else:
        key = sec.get_value(conn, user_id, "GOOGLE_MAPS_API_KEY")
        if not key:
            return [], "Ajoutez votre clé API Google Places dans Paramètres pour chercher des entreprises."
        df, err = search_places(query, key)
        items = [] if err else records(df)
    return items, err


def new_items_only(conn, kind: str, items: list) -> list:
    if kind == "offers":
        known = known_offer_urls(conn)
        return [i for i in items if i.get("url") not in known]
    known = known_place_ids(conn)
    return [i for i in items if i.get("place_id") and i["place_id"] not in known]


def store_items(conn, kind: str, items: list) -> int:
    added = 0
    for i in items:
        if kind == "offers":
            cur = conn.execute("INSERT OR IGNORE INTO extra_offers(url, poste, localisation, added_at) "
                               "VALUES(?,?,?,datetime('now'))", (i["url"], i["poste"], i.get("localisation") or "Inconnue"))
        else:
            cur = conn.execute("INSERT OR IGNORE INTO extra_companies VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                               tuple(i.get(c) for c in COMPANY_COLS))
        added += cur.rowcount
    conn.commit()
    return added



def display_item(kind: str, i: dict, idx: int) -> dict:
    def safe_text(value, default=""):
        if value is None:
            return default
        try:
            if pd.isna(value):
                return default
        except (TypeError, ValueError):
            pass
        return str(value)

    if kind == "offers":
        url = safe_text(i.get("url"))
        poste = safe_text(i.get("poste"), "Offre sans titre")
        localisation = safe_text(i.get("localisation"))
        titre = _clean_title(poste)
        ent = _company_from_url(url)

        return {
            "id": idx,
            "title": titre or "Offre sans titre",
            "sub": " · ".join(x for x in (ent, localisation) if x),
            "chip": classify(titre + " " + ent),
            "link": url
        }

    nom = safe_text(i.get("nom"), "Sans nom")
    categorie = safe_text(i.get("categorie"))
    adresse = safe_text(i.get("adresse"))
    note_value = i.get("note")

    try:
        note = "" if note_value is None or pd.isna(note_value) else f"{float(note_value)}/5"
    except (TypeError, ValueError):
        note = ""

    site = safe_text(i.get("site_web"))
    maps = safe_text(i.get("google_maps"))
    link = site or maps

    sector = classify(f"{nom} {categorie}")

    return {
        "id": idx,
        "title": nom,
        "sub": " · ".join(x for x in (note, adresse) if x),
        "chip": sector ,
        "link": link
    }


@app.route("/recherche")
@login_required
def search_page():
    user = current_user()
    saved = db().execute("SELECT * FROM saved_searches WHERE user_id=? ORDER BY id DESC", (user["id"],)).fetchall()
    return render_template("search.html", saved=saved, kind=request.args.get("type", "offers"),
                           has_google=bool(sec.get_value(db(), user["id"], "GOOGLE_MAPS_API_KEY")),
                           interval=os.environ.get("AUTO_REFRESH_HOURS", "6"))


@app.post("/api/search")
@login_required
def api_search():
    d, user = request.get_json(force=True), current_user()
    kind, query = d.get("kind"), (d.get("query") or "").strip()
    if kind not in ("offers", "companies") or not (2 <= len(query) <= 300):
        return jsonify(error="Saisissez au moins 2 caractères."), 400
    db().execute("DELETE FROM search_results WHERE created < datetime('now','-1 day')")
    recent = db().execute("SELECT COUNT(*) FROM search_results WHERE user_id=? AND created > datetime('now','-1 hour')",
                          (user["id"],)).fetchone()[0]
    if recent >= 30:
        return jsonify(error="Limite de 30 recherches par heure atteinte. Réessayez plus tard."), 429
    items, err = run_search(db(), user["id"], kind, query)
    if err:
        return jsonify(error=err), 502
    fresh = new_items_only(db(), kind, items)
    token = uuid.uuid4().hex
    db().execute("INSERT INTO search_results(token,user_id,kind,payload) VALUES(?,?,?,?)",
                 (token, user["id"], kind, json.dumps(fresh, default=str)))
    db().commit()
    
    displayed_items = [
    display_item(kind, item, index)
    for index, item in enumerate(fresh)
]

# Remplacer les valeurs NaN et infinies avant de créer le JSON.
    displayed_items = json.loads(
    json.dumps(displayed_items, ensure_ascii=False, allow_nan=False, default=str)
)

    return jsonify(
    token=token,
    items=displayed_items,
    known=len(items) - len(fresh)
)



@app.post("/api/search/add")
@login_required
def api_search_add():
    d, user = request.get_json(force=True), current_user()
    row = db().execute("SELECT kind, payload FROM search_results WHERE token=? AND user_id=?",
                       (d.get("token"), user["id"])).fetchone()
    if not row:
        return jsonify(error="Résultats expirés : relancez la recherche."), 404
    items = json.loads(row["payload"])
    chosen = [items[i] for i in {int(x) for x in d.get("ids", []) if str(x).isdigit()} if i < len(items)]
    added = store_items(db(), row["kind"], chosen)   # les données viennent du serveur, jamais du navigateur
    db().execute("DELETE FROM search_results WHERE token=?", (d.get("token"),))
    db().commit()
    return jsonify(added=added)


@app.post("/api/saved")
@login_required
def api_saved_add():
    d, user = request.get_json(force=True), current_user()
    kind, query = d.get("kind"), (d.get("query") or "").strip()
    if kind not in ("offers", "companies") or not (2 <= len(query) <= 300):
        abort(400)
    if db().execute("SELECT COUNT(*) FROM saved_searches WHERE user_id=?", (user["id"],)).fetchone()[0] >= 10:
        return jsonify(error="Maximum 10 recherches enregistrées."), 400
    db().execute("INSERT INTO saved_searches(user_id,kind,query,auto) VALUES(?,?,?,1)", (user["id"], kind, query))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/saved/<int:sid>/<action>")
@login_required
def api_saved_action(sid, action):
    user = current_user()
    row = db().execute("SELECT * FROM saved_searches WHERE id=? AND user_id=?", (sid, user["id"])).fetchone()
    if not row or action not in ("delete", "toggle", "run"):
        abort(404)
    if action == "delete":
        db().execute("DELETE FROM saved_searches WHERE id=?", (sid,))
    elif action == "toggle":
        db().execute("UPDATE saved_searches SET auto=1-auto WHERE id=?", (sid,))
    else:
        db().commit()
        execute_saved(db(), row)
        row = db().execute("SELECT last_added, last_status FROM saved_searches WHERE id=?", (sid,)).fetchone()
        return jsonify(added=row["last_added"], status=row["last_status"])
    db().commit()
    return jsonify(ok=True)


def execute_saved(conn, row):
    """Relance une recherche enregistrée et ajoute automatiquement les nouveautés."""
    items, err = run_search(conn, row["user_id"], row["kind"], row["query"])
    fresh = [] if err else new_items_only(conn, row["kind"], items)
    added = 0 if err else store_items(conn, row["kind"], fresh)
    if added and row["kind"] == "offers":
        notify_n8n(conn, row["user_id"], fresh)
    conn.execute("UPDATE saved_searches SET last_run=datetime('now'), last_added=?, last_status=? WHERE id=?",
                 (added, err or "OK", row["id"]))
    conn.commit()


def run_due_searches():
    """Exécute les recherches automatiques arrivées à échéance. Le 'claim' en base garantit qu'une seule
    instance (même avec plusieurs workers gunicorn) exécute une recherche donnée."""
    hours = float(os.environ.get("AUTO_REFRESH_HOURS", "6"))
    delay = f"-{int(hours * 60)} minutes"
    conn = dbconn.connect(DB_PATH, timeout=30)
    try:
        for r in conn.execute("SELECT * FROM saved_searches WHERE auto=1 AND (last_run IS NULL OR last_run <= datetime('now', ?))",
                              (delay,)).fetchall():
            claimed = conn.execute("UPDATE saved_searches SET last_run=datetime('now') WHERE id=? AND "
                                   "(last_run IS NULL OR last_run <= datetime('now', ?))", (r["id"], delay)).rowcount
            conn.commit()
            if claimed:
                execute_saved(conn, r)
    finally:
        conn.close()


def scheduler_loop():
    time.sleep(30)
    while True:
        try:
            run_due_searches()
        except Exception as exc:   # le planificateur ne doit jamais s'arrêter
            app.logger.warning("Actualisation automatique : %s", exc)
        time.sleep(600)


# ================================================================== n8n (automatisation)
def webhook_url_ok(url: str):
    """Évite le SSRF : le serveur n'appelle que des adresses publiques, sauf si N8N_ALLOW_PRIVATE=1
    (nécessaire quand n8n tourne en local ou dans le même docker-compose)."""
    from urllib.parse import urlparse
    p = urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        return False, "URL invalide (http ou https attendu)."
    if os.environ.get("N8N_ALLOW_PRIVATE") == "1":
        return True, ""
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == "https" else 80))}
    except OSError:
        return False, "Nom de domaine introuvable."
    if not addrs or not all(ipaddress.ip_address(a).is_global for a in addrs):
        return False, "Adresse privée refusée. Si n8n tourne en local ou dans Docker, définissez N8N_ALLOW_PRIVATE=1."
    return True, ""


def notify_n8n(conn, user_id: int, fresh_offers: list):
    """Envoie à n8n (webhook) les nouvelles offres dont la correspondance dépasse le seuil de l'utilisateur."""
    url = sec.get_value(conn, user_id, "N8N_WEBHOOK_URL")
    if not url or not fresh_offers:
        return False
    ok, why = webhook_url_ok(url)
    if not ok:
        app.logger.warning("Webhook n8n ignoré : %s", why)
        return False
    u = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    prof = {k: u[k] or "" for k in ("title", "skills", "bio", "sectors")}
    threshold = int(sec.get_value(conn, user_id, "N8N_MIN_MATCH") or 0)
    offers_ = []
    for i in fresh_offers:
        titre, ent = _clean_title(i["poste"]), _company_from_url(i["url"])
        sector = classify(titre + " " + ent)
        m = match_score(prof, titre, sector)
        if (m or 0) >= threshold:
            offers_.append({"titre": titre, "entreprise": ent, "localisation": i.get("localisation"),
                            "secteur": sector, "match": m, "url": i["url"]})
    if not offers_:
        return False
    try:
        requests.post(url, json={"event": "new_offers", "app": "khedma", "user": u["name"], "count": len(offers_),
                                 "offers": offers_}, timeout=10, allow_redirects=False)
        return True
    except requests.RequestException as exc:
        app.logger.warning("Webhook n8n : %s", exc)
        return False


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def token_required(view):
    """Authentification des appels de n8n : en-tête « Authorization: Bearer <jeton> »."""
    @wraps(view)
    def wrapped(*a, **kw):
        h = request.headers.get("Authorization", "")
        tok = h[7:].strip() if h.lower().startswith("bearer ") else request.headers.get("X-API-Key", "")
        row = db().execute("SELECT user_id FROM api_tokens WHERE token_hash=?", (_hash(tok),)).fetchone() if tok else None
        if not row:
            return jsonify(error="Jeton invalide ou absent."), 401
        g.api_user = db().execute("SELECT * FROM users WHERE id=?", (row["user_id"],)).fetchone()
        return view(*a, **kw)
    return wrapped


@app.post("/api/settings/n8n_token")
@login_required
def api_n8n_token_create():
    """Génère un jeton (affiché UNE seule fois ; seul son hash est conservé). L'ancien jeton est révoqué."""
    user = current_user()
    token = "kh_" + secrets.token_urlsafe(32)
    db().execute("DELETE FROM api_tokens WHERE user_id=?", (user["id"],))
    db().execute("INSERT INTO api_tokens(token_hash, user_id) VALUES(?,?)", (_hash(token), user["id"]))
    db().commit()
    return jsonify(token=token)


@app.post("/api/settings/n8n_token/revoke")
@login_required
def api_n8n_token_revoke():
    db().execute("DELETE FROM api_tokens WHERE user_id=?", (current_user()["id"],))
    db().commit()
    return jsonify(ok=True)


@app.get("/api/n8n/offers")
@token_required
def api_n8n_offers():
    """Offres avec leur % de correspondance. ?min_match=50&since_hours=24&limit=50"""
    user = g.api_user
    try:
        min_match = max(0, min(100, int(request.args.get("min_match", 0))))
        limit = max(1, min(200, int(request.args.get("limit", 50))))
        since = request.args.get("since_hours")
        since = max(1, min(24 * 30, int(since))) if since else None
    except ValueError:
        return jsonify(error="Paramètres numériques invalides."), 400
    items = scored_offers(user)
    recent = None
    if since:
        recent = {r[0] for r in db().execute("SELECT url FROM extra_offers WHERE added_at >= datetime('now', ?)",
                                              (f"-{since} hours",))}
    out = [{"titre": o["titre"], "entreprise": o["entreprise"], "localisation": o["localisation"],
            "secteur": o["secteur"], "match": o["match"], "url": o["url"]}
           for o in items if (o["match"] or 0) >= min_match and (recent is None or o["url"] in recent)]
    out.sort(key=lambda o: -(o["match"] or 0))
    return jsonify(count=len(out), offers=out[:limit])


@app.get("/api/n8n/summary")
@token_required
def api_n8n_summary():
    """Résumé pour un rapport quotidien (message Telegram / email construit dans n8n)."""
    user = g.api_user
    items = scored_offers(user)
    top = sorted([o for o in items if o["match"]], key=lambda o: -o["match"])[:3]
    apps_by = {s: 0 for s in STATUTS}
    for r in db().execute("SELECT statut, COUNT(*) c FROM candidatures WHERE user_id=? GROUP BY statut", (user["id"],)):
        apps_by[r["statut"]] = r["c"]
    new24 = db().execute("SELECT COUNT(*) FROM extra_offers WHERE added_at >= datetime('now','-24 hours')").fetchone()[0]
    return jsonify(user=user["name"], offers_total=len(items), offers_new_24h=new24,
                   companies_total=len(load_companies()), applications=apps_by,
                   top_matches=[{"titre": o["titre"], "entreprise": o["entreprise"], "match": o["match"], "url": o["url"]} for o in top])


@app.post("/api/n8n/refresh")
@token_required
def api_n8n_refresh():
    """Relance les recherches enregistrées de l'utilisateur (celles non exécutées depuis 5 min)."""
    user = g.api_user
    rows = db().execute("SELECT * FROM saved_searches WHERE user_id=? AND (last_run IS NULL OR last_run <= datetime('now','-5 minutes'))",
                        (user["id"],)).fetchall()
    details = []
    for r in rows:
        execute_saved(db(), r)
        res = db().execute("SELECT last_added, last_status FROM saved_searches WHERE id=?", (r["id"],)).fetchone()
        details.append({"kind": r["kind"], "query": r["query"], "added": res["last_added"], "status": res["last_status"]})
    return jsonify(ran=len(details), added=sum(d["added"] for d in details), details=details)


# ============================================================ Paramètres (clés API)
def _settings_view(user_id):
    rows = []
    for name, (label, secret) in sec.FIELDS.items():
        val = sec.get_value(db(), user_id, name, env_fallback=False)
        rows.append({"name": name, "label": label, "secret": secret, "source": sec.source_of(db(), user_id, name),
                     "masked": sec.mask(val, secret) if val else "", "value": "" if secret else val})
    return rows


@app.route("/parametres", methods=["GET", "POST"])
@login_required
def settings_page():
    user = current_user()
    if request.method == "POST":
        for name in sec.FIELDS:
            val = request.form.get(name, "").strip()
            if request.form.get("del_" + name):
                sec.delete_value(db(), user["id"], name)
            elif val:
                if name == "SMTP_PORT" and not val.isdigit():
                    flash("Le port SMTP doit être un nombre.", "error")
                    continue
                if name == "N8N_MIN_MATCH" and not (val.isdigit() and 0 <= int(val) <= 100):
                    flash("Le seuil doit être un nombre entre 0 et 100.", "error")
                    continue
                if name == "N8N_WEBHOOK_URL" and not webhook_url_ok(val)[0]:
                    flash("Webhook n8n refusé : " + webhook_url_ok(val)[1], "error")
                    continue
                sec.set_value(db(), user["id"], name, val)
        db().commit()
        flash("Paramètres enregistrés.", "ok")
        return redirect(url_for("settings_page"))
    has_token = bool(db().execute("SELECT 1 FROM api_tokens WHERE user_id=?", (user["id"],)).fetchone())
    return render_template("settings.html", rows=_settings_view(user["id"]), has_token=has_token)


@app.post("/api/settings/test")
@login_required

def api_settings_test():
    what, user = request.get_json(force=True).get("what"), current_user()

    if what == "google":
        key = sec.get_value(db(), user["id"], "GOOGLE_MAPS_API_KEY")
        if not key:
            return jsonify(ok=False, message="Aucune clé enregistrée."), 400

        df, err = search_places("agence Marrakech", key)
        return jsonify(
            ok=not err,
            message=err or f"Clé valide ({len(df)} résultat(s) de test)."
        )

    if what == "smtp":
        cfg = smtp_config(
            lambda n: sec.get_value(db(), user["id"], n)
        )

        if not cfg:
            return jsonify(
                ok=False,
                message="Renseignez le serveur, l'utilisateur et le mot de passe SMTP."
            ), 400

        try:
            test_smtp(cfg)
            return jsonify(
                ok=True,
                message="Connexion SMTP réussie."
            )
        except Exception as exc:
            return jsonify(
                ok=False,
                message=f"Échec : {exc}"
            )

    abort(400)


# -------------------------------------------------------------------------- API
@app.post("/api/favorite")
@login_required
def api_favorite():
    d = request.get_json(force=True)
    kind, ref, user = d.get("kind"), d.get("ref"), current_user()
    if kind not in ("offer", "company") or not ref:
        abort(400)
    cur = db().execute("DELETE FROM favorites WHERE user_id=? AND kind=? AND ref=?", (user["id"], kind, ref))
    on = cur.rowcount == 0
    if on:
        db().execute("INSERT INTO favorites VALUES(?,?,?)", (user["id"], kind, ref))
    db().commit()
    return jsonify(active=on)


@app.post("/api/application")
@login_required
def api_application():
    d, user = request.get_json(force=True), current_user()
    if d.get("statut") not in STATUTS or not d.get("url"):
        abort(400)
    db().execute("""INSERT INTO candidatures(user_id,url,poste,statut,date_maj,entreprise) VALUES(?,?,?,?,?,?)
        ON CONFLICT(user_id,url) DO UPDATE SET statut=excluded.statut, date_maj=excluded.date_maj""",
                 (user["id"], d["url"], d.get("poste", ""), d["statut"], date.today().isoformat(),
                  d.get("entreprise", "")))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/application/notes")
@login_required
def api_notes():
    d, user = request.get_json(force=True), current_user()
    db().execute("UPDATE candidatures SET notes=? WHERE user_id=? AND url=?", (d.get("notes", "")[:1000], user["id"], d.get("url")))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/application/add")
@login_required
def api_application_add():
    d, user = request.get_json(force=True), current_user()
    poste = (d.get("poste") or "").strip()[:200]
    if not poste:
        abort(400)
    db().execute("INSERT INTO candidatures(user_id,url,poste,statut,date_maj,entreprise) VALUES(?,?,?,?,?,?)",
                 (user["id"], "manual:" + uuid.uuid4().hex, poste, d.get("statut") if d.get("statut") in STATUTS else STATUTS[0],
                  date.today().isoformat(), (d.get("entreprise") or "").strip()[:200]))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/application/delete")
@login_required
def api_application_delete():
    d, user = request.get_json(force=True), current_user()
    db().execute("DELETE FROM candidatures WHERE user_id=? AND url=?", (user["id"], d.get("url")))
    db().commit()
    return jsonify(ok=True)


# ----------------------------------------------------------------------- Studio CV
def load_cv(user) -> dict:
    row = db().execute("SELECT data FROM cvs WHERE user_id=?", (user["id"],)).fetchone()
    if row:
        return {**EMPTY_CV, **json.loads(row["data"])}
    return {**EMPTY_CV, "full_name": user["name"], "email": user["email"], "title": user["title"],
            "skills": user["skills"], "summary": user["bio"]}


def clean_cv(d: dict) -> dict:
    cv = {k: str(d.get(k, ""))[:300] for k in ("full_name", "title", "email", "phone", "city", "links")}
    cv["skills"] = str(d.get("skills", ""))[:3000]
    cv["languages"] = str(d.get("languages", ""))[:800]
    cv["summary"] = str(d.get("summary", ""))[:1500]
    cv["style"] = d.get("style") if d.get("style") in ("classic", "modern") else "modern"
    cv["experiences"] = [{k: str(e.get(k, ""))[:900] for k in ("role", "company", "period", "details")}
                         for e in (d.get("experiences") or [])[:10] if isinstance(e, dict)]
    cv["education"] = [{k: str(e.get(k, ""))[:300] for k in ("degree", "school", "period")}
                       for e in (d.get("education") or [])[:8] if isinstance(e, dict)]
    cv["projects"] = [{k: str(e.get(k, ""))[:700] for k in ("name", "link", "description")}
                      for e in (d.get("projects") or [])[:8] if isinstance(e, dict)]
    return cv


@app.route("/cv")
@login_required
def studio_cv():
    return render_template("cv.html", cv=load_cv(current_user()))


@app.post("/api/cv")
@login_required
def api_cv():
    cv = clean_cv(request.get_json(force=True))
    db().execute("INSERT OR REPLACE INTO cvs VALUES(?,?,?)",
                 (current_user()["id"], json.dumps(cv, ensure_ascii=False), date.today().isoformat()))
    db().commit()
    return jsonify(ok=True)


def cv_pdf_bytes(user) -> bytes:
    return build_pdf(load_cv(user), os.environ.get("CV_ACCENT", "#1B2A49"))


@app.route("/cv.pdf")
@login_required
def cv_pdf():
    user = current_user()
    name = re.sub(r"[^A-Za-z0-9_-]+", "_", user["name"]) or "CV"
    return app.response_class(cv_pdf_bytes(user), mimetype="application/pdf",
                              headers={"Content-Disposition": f'attachment; filename="CV_{name}.pdf"'})


@app.post("/api/apply")
@login_required
def api_apply():
    """Envoie le CV (PDF) par email à une entreprise ou pour une offre, puis ajoute la candidature au Kanban (« Envoyé »)."""
    d, user = request.get_json(force=True), current_user()
    to, subject, body = (d.get("to") or "").strip(), (d.get("subject") or "").strip(), (d.get("body") or "").strip()
    if not EMAIL_RE.fullmatch(to) or not subject or not body:
        return jsonify(error="Renseignez un email valide, un objet et un message."), 400
    if not db().execute("SELECT 1 FROM cvs WHERE user_id=?", (user["id"],)).fetchone():
        return jsonify(error="Enregistrez d'abord votre CV dans Studio CV."), 400
    cfg = smtp_config(lambda n: sec.get_value(db(), user["id"], n))
    if not cfg:
        return jsonify(error="L'envoi par email n'est pas configuré. Ajoutez vos réglages SMTP dans Paramètres, "
                             "ou téléchargez le CV et envoyez-le depuis votre messagerie.", fallback=True), 503
    sent_today = db().execute("SELECT COUNT(*) FROM sent_mails WHERE user_id=? AND date(sent_at)=date('now')",
                              (user["id"],)).fetchone()[0]
    if sent_today >= 20:
        return jsonify(error="Limite de 20 envois par jour atteinte."), 429
    try:
        send_application(to, subject[:200], body[:5000], user["name"], user["email"], cv_pdf_bytes(user),
                         f"CV_{re.sub(r'[^A-Za-z0-9_-]+', '_', user['name'])}.pdf", cfg)
    except Exception as exc:
        return jsonify(error=f"L'envoi a échoué : {exc}"), 502
    url = d.get("url") or "manual:" + uuid.uuid4().hex
    db().execute("INSERT INTO sent_mails(user_id,url,to_email,subject) VALUES(?,?,?,?)", (user["id"], url, to, subject))
    db().execute("""INSERT INTO candidatures(user_id,url,poste,statut,date_maj,entreprise) VALUES(?,?,?,?,?,?)
        ON CONFLICT(user_id,url) DO UPDATE SET statut='Envoyé', date_maj=excluded.date_maj""",
                 (user["id"], url, (d.get("poste") or subject)[:200], "Envoyé", date.today().isoformat(),
                  (d.get("entreprise") or "")[:200]))
    db().commit()
    return jsonify(ok=True)


@app.get("/api/find_email")
@login_required
def api_find_email():
    """Cherche un email public sur la page d'une offre ou le site d'une entreprise connue de l'application."""
    kind, ref = request.args.get("kind"), request.args.get("ref", "")
    if kind == "offer" and ref in set(load_offers()["url"]):
        target = ref
    elif kind == "company":
        comp = load_companies()
        hit = comp[comp["place_id"] == ref] if not comp.empty else comp
        target = hit["site_web"].iloc[0] if len(hit) and isinstance(hit["site_web"].iloc[0], str) else ""
    else:
        abort(404)
    return jsonify(emails=find_emails(target) if target else [])


# ------------------------------------------------------------------ statistiques
@app.route("/statistiques")
@login_required
def stats_page():
    return render_template("stats.html")


@app.get("/api/stats")
@login_required
def api_stats():
    user = current_user()
    offers_ = scored_offers(user)
    comp = load_companies()

    def count(series):
        vc = pd.Series(list(series)).value_counts()
        return {"labels": [str(k) for k in vc.index], "values": [int(v) for v in vc.values]}

    words = Counter(w for o in offers_ for w in re.findall(r"[a-z]{4,}", norm(o["titre"])) if w not in STOP
                    and w not in {"responsable", "charge", "chargee"} | set())
    top_words = words.most_common(8)
    buckets = {"Aucune": 0, "1-29 %": 0, "30-59 %": 0, "60-100 %": 0}
    for o in offers_:
        m = o["match"] or 0
        buckets["Aucune" if m == 0 else "1-29 %" if m < 30 else "30-59 %" if m < 60 else "60-100 %"] += 1
    ratings = comp["note"].dropna() if not comp.empty else pd.Series(dtype=float)
    rating_b = pd.cut(ratings, [0, 2, 3, 4, 4.5, 5], labels=["≤ 2", "2-3", "3-4", "4-4,5", "4,5-5"], include_lowest=True).value_counts().sort_index()
    funnel = {s: 0 for s in STATUTS}
    timeline = {}
    for r in db().execute("SELECT statut, date_maj FROM candidatures WHERE user_id=?", (user["id"],)):
        funnel[r["statut"]] = funnel.get(r["statut"], 0) + 1
        timeline[r["date_maj"]] = timeline.get(r["date_maj"], 0) + 1
    top_co = comp.dropna(subset=["note"]).assign(score=lambda d: d["note"] * (d["nombre_avis"].fillna(0) + 1) ** 0.3)\
        .nlargest(8, "score") if not comp.empty else comp
    return jsonify(
        generated=datetime.now().strftime("%H:%M:%S"),
        kpis={"offers": len(offers_), "companies": len(comp),
              "sectors": len({o["secteur"] for o in offers_} - {"Non classé"}),
              "applications": db().execute("SELECT COUNT(*) FROM candidatures WHERE user_id=?", (user["id"],)).fetchone()[0],
              "avg_rating": round(float(ratings.mean()), 2) if len(ratings) else None,
              "with_site": int(comp["site_web"].notna().sum()) if not comp.empty else 0},
        offers_by_sector=count(o["secteur"] for o in offers_),
        companies_by_sector=count(comp["secteur"]) if not comp.empty else {"labels": [], "values": []},
        top_words={"labels": [w.capitalize() for w, _ in top_words], "values": [n for _, n in top_words]},
        ratings={"labels": [str(i) for i in rating_b.index], "values": [int(v) for v in rating_b.values]},
        match={"labels": list(buckets), "values": list(buckets.values())},
        funnel={"labels": list(funnel), "values": list(funnel.values())},
        timeline={"labels": sorted(timeline), "values": [timeline[k] for k in sorted(timeline)]},
        top_companies={"labels": [(str(n)[:20] + "…") if len(str(n)) > 20 else str(n) for n in top_co["nom"]] if len(top_co) else [],
                       "values": [float(x) for x in top_co["note"]] if len(top_co) else []},
    )


@app.get("/healthz")
def healthz():
    return "ok"



@app.get("/api/summary")
@login_required
def api_summary():
    url = request.args.get("url", "")

    if url not in set(load_offers()["url"]):
        abort(404)

    row = db().execute(
        "SELECT payload FROM summaries WHERE url = ?",
        (url,)
    ).fetchone()

    if row:
        return jsonify(json.loads(row["payload"]))

    try:
        # Résumé automatique local, sans API payante
        payload = summarize_offer(url, None)
    except Exception as exc:
        app.logger.warning("Erreur résumé local : %s", exc)
        return jsonify(
            error="Impossible de résumer cette offre. Ouvrez le lien pour la consulter."
        ), 502

    if not payload.get("points"):
        return jsonify(
            error="La page ne contient pas de description exploitable."
        ), 422

    db().execute(
        "INSERT OR REPLACE INTO summaries(url, payload) VALUES(?, ?)",
        (url, json.dumps(payload, ensure_ascii=False))
    )
    db().commit()

    return jsonify(payload)



@app.get("/api/cron/refresh")
def api_cron_refresh():
    """Appelé par Vercel Cron (le thread d'actualisation ne tourne pas en serverless)."""
    secret = os.environ.get("CRON_SECRET", "")
    sent = request.headers.get("Authorization", "")
    if not secret or not hmac.compare_digest(sent, f"Bearer {secret}"):
        return jsonify(error="non autorisé"), 401
    run_due_searches()
    return jsonify(ok=True)


# Sur Turso, les tables sont créées une seule fois avec init_turso.py (pas à chaque démarrage serverless).
if not dbconn.is_remote() or os.environ.get("INIT_DB") == "1":
    init_db()

if os.environ.get("AUTO_REFRESH", "1") == "1":
    threading.Thread(target=scheduler_loop, daemon=True, name="auto-refresh").start()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5000)))
