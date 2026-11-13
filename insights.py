"""Classification par secteur, score de correspondance avec le profil
et résumé des offres. Sans dépendance lourde : tout tourne en local."""
import math
from difflib import SequenceMatcher
import os
import re
import unicodedata
from collections import Counter

import requests
from bs4 import BeautifulSoup

# ------------------------------------------------------------------ texte
STOP = set("""le la les un une des de du d l et ou en au aux a à pour par sur dans avec sans
chez the and for of to h f hf marrakech maroc homme femme poste offre recrutement""".split())


def norm(text) -> str:
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text.lower()).strip()


def stems(text) -> set:
    words = re.findall(r"[a-z0-9+#]{3,}", norm(text))
    return {w[:5] for w in words if w not in STOP}



# ---------------------------------------------------------------- secteurs
SECTORS = [
    ("Informatique & IA", [
        "intelligence artificielle", "machine learning", "data analyst",
        "data science", "developpement logiciel", "developpement web",
        "application mobile", "cybersecurite", "software company",
        "computer software", "societe informatique", "informatique",
        "developpeur", "programmation", "logiciel", "software",
        "web development", "web developer", "reseau informatique",
        "devops", "cloud computing", "automatisation", "robotique",
        "erp", "database", "base de donnees", "api", "hebergement web",
    ]),
    ("Santé", [
        "centre medical", "cabinet medical", "clinique", "pharmacie",
        "laboratoire medical", "hopital", "medecin", "dentaire",
        "veterinaire", "infirmier", "sante", "medical", "pharma",
    ]),
    ("Marketing & Communication", [
        "marketing digital", "agence marketing", "agence de communication",
        "digital marketing", "communication digitale", "marketing",
        "community manager", "referencement seo", "publicite",
        "branding", "identite visuelle", "relations publiques",
        "creation de contenu", "reseaux sociaux", "graphisme",
        "agence digitale", "communication", "seo", "influence",
    ]),
    ("Immobilier & Construction", [
        "agence immobiliere", "promotion immobiliere",
        "construction batiment", "travaux publics", "genie civil",
        "architecture", "architecte", "immobilier", "chantier",
        "construction", "btp", "vrd", "promoteur",
    ]),
    ("Éducation & Formation", [
        "centre de formation", "formation professionnelle",
        "etablissement scolaire", "ecole privee", "enseignement",
        "universite", "education", "formation", "formateur",
        "academy", "institut",
    ]),
    ("Industrie & Logistique", [
        "transport logistique", "supply chain", "chaine logistique",
        "industrie manufacturiere", "production industrielle",
        "maintenance industrielle", "controle qualite",
        "transport", "logistique", "industrie", "production",
        "agriculture", "agricole", "entrepot", "fabrication",
    ]),
    ("Finance & Assurance", [
        "cabinet comptable", "expert comptable", "services bancaires",
        "societe assurance", "assurance", "banque", "comptabilite",
        "comptable", "finance", "audit financier", "credit",
        "microfinance", "paie", "fiscalite",
    ]),
    ("Hôtellerie & Tourisme", [
        "agence de voyage", "agence de voyages", "tourisme",
        "hotel", "hotellerie", "riad", "restaurant", "restauration",
        "hebergement touristique", "voyage", "guide touristique",
        "maison d hotes", "palace",
    ]),
    ("Ressources humaines", [
        "ressources humaines", "agence de recrutement",
        "cabinet de recrutement", "recrutement", "interim",
        "gestion du personnel", "talent acquisition",
        "ressources humaines rh",
    ]),
    ("Commerce & Distribution", [
        "grande distribution", "commerce de gros", "commerce de detail",
        "magasin de vetements", "supermarche", "hypermarche",
        "centre commercial", "vente au detail", "grossiste",
        "distribution", "commerce", "retail", "magasin",
        "boutique", "e commerce", "ecommerce", "vendeur",
        "caissier", "supermarche",
    ]),
    ("Conseil & Audit", [
        "conseil en strategie", "cabinet de conseil",
        "conseil aux entreprises", "consulting", "consultant",
        "audit organisationnel", "conseil", "audit",
    ]),
]

SECTOR_NAMES = [name for name, _ in SECTORS] + ["Non classé"]



def classify(text: str) -> str:
    """Classe le texte selon les mots-clés spécifiques de chaque secteur."""
    t = norm(text)

    # Exceptions pour les entreprises vérifiées
    if "safe labs" in t:
        return "Informatique & IA"

    scores = {}

    for sector, keywords in SECTORS:
        score = 0

        for keyword in keywords:
            k = norm(keyword)
            pattern = r"(?<!\w)" + re.escape(k) + r"(?!\w)"

            if re.search(pattern, t):
                score += 1 + k.count(" ")

        scores[sector] = score

    if not scores:
        return "Non classé"

    best_sector = max(scores, key=scores.get)
    best_score = scores[best_sector]

    if best_score == 0:
        return "Non classé"

    winners = [
        sector for sector, score in scores.items()
        if score == best_score
    ]

    if len(winners) > 1:
        return "Non classé"

    return best_sector



# ----------------------------------------------------------- correspondance
# secteurs voisins : donnent quelques points même sans mot commun
ADJACENT = {
    "Informatique & IA": {"Marketing & Communication", "Finance & Assurance", "Conseil & Audit", "Éducation & Formation"},
    "Marketing & Communication": {"Commerce & Distribution", "Informatique & IA"},
    "Commerce & Distribution": {"Marketing & Communication", "Immobilier & Construction", "Finance & Assurance"},
    "Finance & Assurance": {"Ressources humaines", "Conseil & Audit", "Commerce & Distribution", "Informatique & IA"},
    "Ressources humaines": {"Finance & Assurance", "Éducation & Formation", "Conseil & Audit"},
    "Immobilier & Construction": {"Commerce & Distribution", "Industrie & Logistique"},
    "Industrie & Logistique": {"Immobilier & Construction", "Commerce & Distribution"},
    "Hôtellerie & Tourisme": {"Commerce & Distribution", "Marketing & Communication"},
    "Éducation & Formation": {"Ressources humaines", "Informatique & IA"},
    "Santé": {"Industrie & Logistique"},
    "Conseil & Audit": {"Finance & Assurance", "Informatique & IA", "Ressources humaines"},
}


def _fuzzy_word(profile_words: set, title_words: set) -> float:
    """Meilleure ressemblance (0-1) entre un mot du profil et un mot du titre (tolère les variantes : analyste/analyst)."""
    best = 0.0
    for p in profile_words:
        for t in title_words:
            best = max(best, SequenceMatcher(None, p, t).ratio())
    return best


def match_score(profile: dict, offer_title: str, offer_sector: str):
    """Pourcentage de correspondance 0-100 entre le profil et une offre.
    Retourne None si le profil est vide."""
    text = " ".join([profile.get("title", ""), profile.get("skills", ""), profile.get("bio", "")]).strip()
    prefs = {p for p in (profile.get("sectors") or "").split(",") if p}
    if not text and not prefs:
        return None
    common = stems(text) & stems(offer_title)
    score = min(60, 30 * len(common))
    if not common:  # pas de mot identique : on tolère les variantes proches (analyste / analyst)
        words = lambda t: set(re.findall(r"[a-z]{4,}", norm(t))) - STOP
        r = _fuzzy_word(words(text), words(offer_title))
        if r >= 0.7:
            score += round(25 * r)
    inferred = classify(text) if text else "Non classé"
    if offer_sector in prefs:
        score += 30
    elif offer_sector == inferred:
        score += 20
    elif offer_sector in ADJACENT.get(inferred, set()) or any(offer_sector in ADJACENT.get(p, set()) for p in prefs):
        score += 10
    return min(100, score)


# ----------------------------------------------------------------- résumé
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_offer_text(url: str) -> str:
    r = requests.get(url, headers=HEADERS, timeout=20)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    for tag in soup(["script", "style", "nav", "footer", "header", "form", "noscript"]):
        tag.decompose()
    best = ""
    for node in soup.select('[class*="description"], [class*="contentbloc"], [class*="blc"], '
                            '[id*="description"], article, main'):
        txt = node.get_text("\n", strip=True)
        if len(txt) > len(best) and len(txt) < 12000:
            best = txt
    if len(best) < 200:
        best = (soup.body or soup).get_text("\n", strip=True)
    lines, seen = [], set()
    for line in best.split("\n"):
        line = line.strip(" •-–\t")
        if len(line) >= 25 and line not in seen:
            seen.add(line)
            lines.append(line)
    return "\n".join(lines)[:8000]


def extract_facts(text: str) -> list:
    facts, t = [], text
    m = re.search(r"\b(CDI|CDD|Stage|Freelance|Intérim|Interim|ANAPEC|Alternance)\b", t, re.I)
    if m:
        facts.append("Contrat : " + m.group(1).upper())
    m = re.search(r"bac\s*\+\s*\d", t, re.I)
    if m:
        facts.append("Niveau : " + m.group(0).replace(" ", "").capitalize())
    m = re.search(r"(\d+)\s*(?:à|a|-)?\s*(\d+)?\s*ans?\s*(?:d['’]\s*)?exp", t, re.I)
    if m:
        facts.append("Expérience : " + (f"{m.group(1)}-{m.group(2)}" if m.group(2) else m.group(1)) + " an(s)")
    langs = [l for l in ["Français", "Anglais", "Arabe", "Allemand", "Espagnol"] if re.search(l[:5], t, re.I)]
    if langs:
        facts.append("Langues : " + ", ".join(langs))
    return facts


def extractive_summary(text: str, n: int = 4) -> list:
    sentences = [s.strip() for s in re.split(r"(?<=[.!?;:])\s+|\n", text) if 40 <= len(s.strip()) <= 280]
    if not sentences:
        return []
    freq = Counter(w for w in re.findall(r"[a-z]{4,}", norm(text)) if w not in STOP)
    scored = []
    for i, s in enumerate(sentences):
        words = [w for w in re.findall(r"[a-z]{4,}", norm(s)) if w not in STOP]
        if words:
            scored.append((sum(freq[w] for w in words) / math.pow(len(words), 0.6), i, s))
    top = sorted(scored, reverse=True)[:n]
    return [s for _, _, s in sorted(top, key=lambda x: x[1])]


def claude_summary(text: str, key: str = ""):
    key = key or os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        return None
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": key, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6"),
                "max_tokens": 500,
                "messages": [{"role": "user", "content":
                    "Résume cette offre d'emploi en 4 à 5 puces courtes en français (missions, profil, "
                    "conditions). Une puce par ligne, commençant par '- '. Pas d'introduction.\n\n" + text[:6000]}],
            },
            timeout=40,
        )
        r.raise_for_status()
        out = r.json()["content"][0]["text"]
        bullets = [l.lstrip("-• ").strip() for l in out.splitlines() if l.strip().startswith(("-", "•"))]
        return bullets or None
    except Exception:
        return None


def summarize_offer(url: str, api_key: str = "") -> dict:
    text = fetch_offer_text(url)
    if not text:
        raise ValueError("Description introuvable sur la page de l'offre.")
    bullets = claude_summary(text, api_key)
    return {"points": bullets or extractive_summary(text), "facts": extract_facts(text),
            "engine": "claude" if bullets else "local"}
