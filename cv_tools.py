"""Génération du CV en PDF (reportlab) et envoi par email (SMTP)."""
import io
import os
import re
import smtplib
import ssl
import unicodedata
from email.message import EmailMessage
from xml.sax.saxutils import escape

import requests
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.platypus import HRFlowable, KeepInFrame, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import logging

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
EMPTY_CV = {"full_name": "", "title": "", "email": "", "phone": "", "city": "Marrakech", "links": "",
            "summary": "", "experiences": [], "education": [], "projects": [], "skills": "", "languages": "",
            "style": "modern"}
STYLES = ("classic", "modern")


def _t(s) -> str:
    """Texte sûr pour les polices standard du PDF (latin-1/cp1252) et pour le XML de reportlab."""
    s = str(s or "").replace("\u2019", "'").replace("\u2018", "'").replace("\u2011", "-")
    return escape(s.encode("cp1252", "replace").decode("cp1252"))


# ---------------------------------------------------------------- analyse du texte saisi
def _norm(s: str) -> str:
    return re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", str(s).lower()).encode("ascii", "ignore").decode())


def split_items(text: str) -> list:
    """Découpe un texte en éléments : une ligne par élément, puces (- • *) retirées, et listes « aplaties »
    du type « Python - SQL - Pandas » (tiret entouré d'espaces) séparées aussi."""
    items = []
    for line in str(text or "").replace("\r", "").split("\n"):
        line = re.sub(r"^[\s\-\u2022\*\u00b7\u2013]+", "", line).strip()
        if not line:
            continue
        parts = re.split(r"\s+[-\u2013\u2022]\s+", line)
        items += [p.strip(" -\u2022*") for p in parts if p.strip(" -\u2022*")]
    return items


def parse_groups(text: str) -> list:
    """Transforme le champ « compétences » en groupes [{'title': str|None, 'items': [...]}].
    Comprend : texte collé avec des titres « ### », listes à puces, « Titre : » suivi de puces, ou simple liste
    séparée par des virgules."""
    text = str(text or "").replace("\r", "").strip()
    if not text:
        return []
    groups = []
    if "#" in text:                                    # « ### Titre - item - item ### Titre 2 - ... »
        for chunk in re.split(r"#{1,6}", text):
            chunk = chunk.strip()
            if not chunk:
                continue
            m = re.match(r"(.+?)(?:\n|\s+[-\u2013\u2022]\s+|$)", chunk)
            title = m.group(1).strip(" :-") if m else ""
            rest = chunk[len(m.group(0)):] if m else chunk
            groups.append({"title": title or None, "items": split_items(rest)})
    elif re.search(r"(^|\n)\s*[-\u2022*]", text) or re.search(r"\s[-\u2013\u2022]\s", text) or ":" in text.split("\n")[0]:
        cur = {"title": None, "items": []}
        for line in text.split("\n"):
            raw = line.strip()
            if not raw:
                continue
            if not re.match(r"^[-\u2022*\u2013]", raw) and raw.endswith(":") and len(raw) < 60:
                if cur["items"] or cur["title"]:
                    groups.append(cur)
                cur = {"title": raw.rstrip(":").strip(), "items": []}
            else:
                cur["items"] += split_items(raw)
        groups.append(cur)
    else:
        groups = [{"title": None, "items": [x.strip() for x in re.split(r"[,;\n]", text) if x.strip()]}]
    out = []
    for g in groups:
        if _norm(g["title"] or "") in ("competences", "langues", "skills", "languages"):
            g["title"] = None                           # titre redondant avec le titre de la section
        if g["items"]:
            out.append(g)
    return out


def split_language(item: str):
    """« Arabe : Langue maternelle » -> ('Arabe', 'Langue maternelle')."""
    m = re.match(r"^\s*([^:(\-\u2013]+?)\s*(?:[:\-\u2013]|\()\s*(.+?)\)?\s*$", item)
    return (m.group(1).strip(), m.group(2).strip()) if m else (item.strip(), "")


def language_lines(text: str) -> list:
    out = []
    for g in parse_groups(text):
        for it in g["items"]:
            n, lvl = split_language(it)
            out.append(f"{n} - {lvl}" if lvl else n)
    return out


# ---------------------------------------------------------------------------------- PDF
def _tint(hex_color: str, amount: float = 0.92):
    c = colors.HexColor(hex_color)
    return colors.Color(c.red + (1 - c.red) * amount, c.green + (1 - c.green) * amount, c.blue + (1 - c.blue) * amount)


def _styles(accent):
    acc = colors.HexColor(accent)

    def st(name, **kw):
        return ParagraphStyle(name, fontName=kw.pop("font", "Helvetica"), alignment=TA_LEFT, **kw)
    return acc, {
        "name": st("n", font="Helvetica-Bold", fontSize=22, leading=26, textColor=acc),
        "sub": st("s", fontSize=12, leading=16, textColor=colors.HexColor("#444444")),
        "meta": st("m", fontSize=9, leading=13, textColor=colors.HexColor("#666666")),
        "h": st("h", font="Helvetica-Bold", fontSize=10.5, leading=14, textColor=acc, spaceBefore=11, spaceAfter=2),
        "body": st("b", fontSize=9.5, leading=13.5),
        "bold": st("bb", font="Helvetica-Bold", fontSize=10, leading=13.5, spaceBefore=5),
        "bullet": st("bl", fontSize=9.5, leading=13.5, leftIndent=10, bulletIndent=0),
        "gt": st("gt", font="Helvetica-Bold", fontSize=9, leading=12, spaceBefore=4, textColor=colors.HexColor("#333333")),
        "hw": st("hw", font="Helvetica-Bold", fontSize=22, leading=26, textColor=colors.white),
        "sw": st("sw", fontSize=12, leading=16, textColor=colors.Color(1, 1, 1, alpha=0.85)),
        "mw": st("mw", fontSize=9, leading=13, textColor=colors.Color(1, 1, 1, alpha=0.8)),
        "bullet_s": st("bls", fontSize=9, leading=12.5, leftIndent=9, bulletIndent=0),
    }


def _dated(head, period, S):
    return Paragraph(f"<b>{head}</b>" + (f" <font color='#777777'>({_t(period)})</font>" if period else ""), S["bold"])


def _blocks(cv, S, section, side_mode="inline"):
    """Fabrique les blocs de contenu ; `section(titre)` ajoute un titre de section et retourne la liste de flowables."""
    def skills():
        out = section("Compétences")
        for g in parse_groups(cv.get("skills")):
            if g["title"]:
                out.append(Paragraph(_t(g["title"]), S["gt"]))
            if side_mode == "list":
                out += [Paragraph(_t(i), S["bullet_s"], bulletText="\u2022") for i in g["items"]]
            else:
                out.append(Paragraph(" \u2022 ".join(_t(i) for i in g["items"]), S["body"]))
        return out if len(out) > 1 else []

    def languages():
        lines = language_lines(cv.get("languages"))
        if not lines:
            return []
        out = section("Langues")
        if side_mode == "list":
            out += [Paragraph(_t(l), S["bullet_s"], bulletText="\u2022") for l in lines]
        else:
            out.append(Paragraph("  |  ".join(_t(l) for l in lines), S["body"]))
        return out

    def profile():
        return (section("Profil") + [Paragraph(_t(cv["summary"]).replace("\n", "<br/>"), S["body"])]) if cv.get("summary") else []

    def experiences():
        if not cv.get("experiences"):
            return []
        out = section("Expérience professionnelle")
        for e in cv["experiences"]:
            head = _t(e.get("role")) + (f" - {_t(e.get('company'))}" if e.get("company") else "")
            out.append(_dated(head, e.get("period"), S))
            out += [Paragraph(_t(i), S["bullet"], bulletText="\u2022") for i in split_items(e.get("details"))]
        return out

    def projects():
        if not cv.get("projects"):
            return []
        out = section("Projets")
        for p in cv["projects"]:
            out.append(Paragraph(f"<b>{_t(p.get('name'))}</b>" + (f" <font color='#777777'>- {_t(p.get('link'))}</font>" if p.get("link") else ""), S["bold"]))
            out += [Paragraph(_t(i), S["bullet"], bulletText="\u2022") for i in split_items(p.get("description"))]
        return out

    def education():
        if not cv.get("education"):
            return []
        out = section("Formation")
        for e in cv["education"]:
            head = _t(e.get("degree")) + (f" - {_t(e.get('school'))}" if e.get("school") else "")
            out.append(_dated(head, e.get("period"), S))
        return out
    return dict(skills=skills, languages=languages, profile=profile, experiences=experiences,
                projects=projects, education=education)


def build_pdf(cv: dict, accent: str = "#1B2A49") -> bytes:
    acc, S = _styles(accent)
    modern = cv.get("style", "modern") == "modern"
    contact = " | ".join(_t(cv.get(k)) for k in ("email", "phone", "city", "links") if cv.get(k))
    buf = io.BytesIO()
    title = f"CV {cv.get('full_name', '')}"

    if not modern:
        def section(t):
            return [Paragraph(_t(t).upper(), S["h"]), HRFlowable(width="100%", thickness=0.8, color=acc, spaceAfter=3)]
        story = [Paragraph(_t(cv.get("full_name")) or "Votre nom", S["name"])]
        if cv.get("title"):
            story.append(Paragraph(_t(cv["title"]), S["sub"]))
        if contact:
            story.append(Paragraph(contact, S["meta"]))
        B = _blocks(cv, S, section, "inline")
        for key in ("profile", "education", "experiences", "projects", "skills", "languages"):
            story += B[key]()
        SimpleDocTemplate(buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm,
                          bottomMargin=16 * mm, title=title).build(story)
        return buf.getvalue()

    # ---- modèle moderne : bandeau + colonne latérale (une page, le contenu est réduit s'il dépasse)
    page_w, page_h = A4
    margin = 10 * mm
    inner_w = page_w - 2 * margin
    head_cells = [Paragraph(_t(cv.get("full_name")) or "Votre nom", S["hw"])]
    if cv.get("title"):
        head_cells.append(Paragraph(_t(cv["title"]), S["sw"]))
    if contact:
        head_cells.append(Paragraph(contact, S["mw"]))
    head = Table([[head_cells]], colWidths=[inner_w])
    head.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), acc), ("LEFTPADDING", (0, 0), (-1, -1), 10 * mm),
                              ("RIGHTPADDING", (0, 0), (-1, -1), 10 * mm), ("TOPPADDING", (0, 0), (-1, -1), 8 * mm),
                              ("BOTTOMPADDING", (0, 0), (-1, -1), 7 * mm)]))
    side_w = 62 * mm
    main_w = inner_w - side_w
    pad = 6 * mm

    def sec_side(t):
        return [Paragraph(_t(t).upper(), S["h"]), HRFlowable(width="100%", thickness=0.8, color=acc, spaceAfter=3)]

    def sec_main(t):
        return [Paragraph(_t(t).upper(), S["h"]), HRFlowable(width="100%", thickness=0.6, color=colors.HexColor("#CCCCCC"), spaceAfter=3)]
    SB = _blocks(cv, S, sec_side, "list")
    MB = _blocks(cv, S, sec_main, "inline")
    side = SB["skills"]() + SB["languages"]()
    main = MB["profile"]() + MB["experiences"]() + MB["projects"]() + MB["education"]()
    head_h = head.wrap(inner_w, page_h)[1]
    body_h = page_h - 2 * margin - head_h - 6 * mm      # le cadre de reportlab a 6 pt de marge interne de chaque côté
    body = Table([[KeepInFrame(side_w - 2 * pad, body_h - 2 * pad, side or [Spacer(1, 1)], mode="shrink"),
                   KeepInFrame(main_w - 2 * pad, body_h - 2 * pad, main or [Spacer(1, 1)], mode="shrink")]],
                 colWidths=[side_w, main_w], rowHeights=[body_h])
    body.setStyle(TableStyle([("BACKGROUND", (0, 0), (0, 0), _tint(accent)), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                              ("LEFTPADDING", (0, 0), (-1, -1), pad), ("RIGHTPADDING", (0, 0), (-1, -1), pad),
                              ("TOPPADDING", (0, 0), (-1, -1), pad - 2 * mm), ("BOTTOMPADDING", (0, 0), (-1, -1), pad)]))
    SimpleDocTemplate(buf, pagesize=A4, leftMargin=margin, rightMargin=margin, topMargin=margin,
                      bottomMargin=margin, title=title).build([head, body])
    return buf.getvalue()



def smtp_config(get) -> dict:
    """Construit la configuration SMTP et vérifie les champs obligatoires."""
    cfg = {
        "host": (get("SMTP_HOST") or "").strip(),
        "port": (get("SMTP_PORT") or "587").strip(),
        "user": (get("SMTP_USER") or "").strip(),
        "password": get("SMTP_PASSWORD") or "",
        "from": (get("SMTP_FROM") or "").strip(),
    }

    if not cfg["host"] or not cfg["user"] or not cfg["password"]:
        return {}

    try:
        port = int(cfg["port"])
        if not 1 <= port <= 65535:
            return {}
    except (TypeError, ValueError):
        return {}

    cfg["port"] = port
    return cfg


def _connect(cfg: dict):
    ctx = ssl.create_default_context()
    port = int(cfg.get("port") or 587)
    if port == 465:
        s = smtplib.SMTP_SSL(cfg["host"], port, context=ctx, timeout=30)
    else:
        s = smtplib.SMTP(cfg["host"], port, timeout=30)
        s.starttls(context=ctx)
    s.login(cfg["user"], cfg["password"])
    return s


def test_smtp(cfg: dict):
    """Vérifie connexion + identifiants sans envoyer de message."""
    with _connect(cfg):
        pass


def send_application(to: str, subject: str, body: str, sender_name: str, reply_to: str,
                     pdf: bytes, filename: str, cfg: dict):
    """Envoie le CV en pièce jointe. Le Reply-To est l'email du candidat : le recruteur lui répond directement."""
    if not cfg:
        raise RuntimeError("SMTP non configuré")
    msg = EmailMessage()  # lève une erreur si un en-tête contient un saut de ligne (anti-injection)
    msg["From"] = f"{sender_name} <{cfg.get('from') or cfg['user']}>"
    msg["To"] = to
    msg["Reply-To"] = reply_to
    msg["Subject"] = subject
    msg.set_content(body)
    msg.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    with _connect(cfg) as s:
        s.send_message(msg)

def find_emails(url: str) -> list:
    """Cherche des adresses email publiques sur un site web."""

    found = []

    if not url:
        return []

    urls = [
        url,
        url.rstrip("/") + "/contact"
    ]

    for u in urls:
        try:
            r = requests.get(
                u,
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=20
            )

            if r.ok:
                found += EMAIL_RE.findall(r.text)
            else:
                logger.warning(
                    "Email search HTTP %s: %s",
                    r.status_code,
                    u
                )

        except requests.exceptions.RequestException:
            logger.exception(
                "Email search connection error: %s",
                u
            )

    bad = (
        ".png", ".jpg", ".jpeg", ".gif",
        ".webp", ".svg", "sentry",
        "example.", "wixpress", "@2x"
    )

    out = []

    for e in found:
        e = e.lower()

        if not any(b in e for b in bad) and e not in out:
            out.append(e)

    return out[:5]