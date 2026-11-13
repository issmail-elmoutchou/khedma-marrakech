"""Fonctions de collecte et de nettoyage des données, partagées par
l'application Streamlit (app.py) et le micro-service d'automatisation
(scraper_api/main.py) que n8n vient déclencher.
"""

import os
import re
from urllib.parse import urljoin

import pandas as pd
import requests
from bs4 import BeautifulSoup

REKRUTE_SEARCH_URL = "https://www.rekrute.com/en/offres.html"
PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"

JOBS_COLUMNS = ["poste", "localisation", "url"]
COMPANIES_COLUMNS = [
    "place_id", "nom", "adresse", "telephone", "site_web",
    "google_maps", "categorie", "note", "nombre_avis",
    "latitude", "longitude",
]


# ==========================================
# Collecte
# ==========================================
def scrape_rekrute(keyword: str = "", direct_url: str = ""):
    """Scrape les offres Rekrute depuis un mot-clé ou un lien de recherche."""

    headers = {"User-Agent": "Mozilla/5.0"}

    if direct_url:
        target_url = direct_url
        params = {}
    else:
        target_url = REKRUTE_SEARCH_URL
        params = {
            "keyword": keyword,
            "query": keyword
        }

    try:
        response = requests.get(
            target_url,
            params=params,
            headers=headers,
            timeout=30
        )
        response.raise_for_status()

    except Exception as exc:
        return pd.DataFrame(columns=JOBS_COLUMNS), str(exc)

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    resultats = []

    for bloc in soup.find_all(
        "li",
        class_="post-id"
    ):

        lien_elem = bloc.find(
            "a",
            class_="titreJob"
        )

        if not lien_elem:
            continue

        titre = lien_elem.get_text(
            " ",
            strip=True
        )

        href = lien_elem.get(
            "href",
            ""
        )

        # Extraire la ville depuis le titre
        localisation = "Inconnue"

        match = re.search(
            r"\|\s*([^(|]+?)\s*\(",
            titre
        )

        if match:
            localisation = match.group(1).strip()

        resultats.append(
            {
                "poste": titre,
                "localisation": localisation,
                "url": urljoin(
                    response.url,
                    href
                )
            }
        )

    df = pd.DataFrame(
        resultats,
        columns=JOBS_COLUMNS
    )

    if not df.empty:
        df = df.drop_duplicates(
            subset="url"
        ).reset_index(drop=True)

    return df, None


def search_places(query: str, api_key: str, region_code: str = "MA", language_code: str = "fr"):
    """Interroge l'API Google Places (Text Search) pour trouver des prestataires."""
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.location,places.googleMapsUri,places.websiteUri,"
            "places.nationalPhoneNumber,places.rating,places.userRatingCount,"
            "places.primaryTypeDisplayName"
        ),
    }
    data = {"textQuery": query, "languageCode": language_code, "regionCode": region_code}

    try:
        response = requests.post(PLACES_SEARCH_URL, headers=headers, json=data, timeout=30)
    except Exception as exc:
        return pd.DataFrame(columns=COMPANIES_COLUMNS), str(exc)

    if response.status_code != 200:
        return (
            pd.DataFrame(columns=COMPANIES_COLUMNS),
            f"Erreur API ({response.status_code}) : {response.text[:300]}",
        )

    places = response.json().get("places", [])
    rows = []
    for place in places:
        loc = place.get("location", {})
        rows.append(
            {
                "place_id": place.get("id"),
                "nom": place.get("displayName", {}).get("text"),
                "adresse": place.get("formattedAddress"),
                "telephone": place.get("nationalPhoneNumber"),
                "site_web": place.get("websiteUri"),
                "google_maps": place.get("googleMapsUri"),
                "categorie": place.get("primaryTypeDisplayName", {}).get("text"),
                "note": place.get("rating"),
                "nombre_avis": place.get("userRatingCount"),
                "latitude": loc.get("latitude"),
                "longitude": loc.get("longitude"),
            }
        )
    return pd.DataFrame(rows, columns=COMPANIES_COLUMNS), None


def get_env_api_key() -> str:
    return os.environ.get("GOOGLE_MAPS_API_KEY", "")


# ==========================================
# Nettoyage (correspond au bloc "Nettoyage" du tableau blanc)
# ==========================================
def clean_jobs_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.dropna(subset=["poste", "url"]).copy()
    df["poste"] = df["poste"].astype("string").str.strip()
    df["localisation"] = df["localisation"].astype("string").str.strip()
    df = df.drop_duplicates(subset="url").reset_index(drop=True)
    return df


def clean_companies_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df.drop_duplicates(subset="place_id").copy()
    for col in ["nom", "adresse"]:
        if col in df.columns:
            df[col] = df[col].astype("string").str.strip()
    for col in ["note", "nombre_avis", "latitude", "longitude"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.reset_index(drop=True)
    return df


def data_quality_report(df: pd.DataFrame) -> pd.DataFrame:
    """Retourne un tableau récapitulatif des problèmes détectés dans un
    DataFrame : valeurs manquantes par colonne (correspond au bloc
    'Lister les problèmes détectés' du tableau blanc)."""
    if df.empty:
        return pd.DataFrame(columns=["Colonne", "Valeurs manquantes", "% manquant"])
    report = pd.DataFrame(
        {
            "Colonne": df.columns,
            "Valeurs manquantes": df.isnull().sum().values,
        }
    )
    report["% manquant"] = (report["Valeurs manquantes"] / len(df) * 100).round(1)
    return report.sort_values("Valeurs manquantes", ascending=False).reset_index(drop=True)
