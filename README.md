# khedma marrakech

Application web Flask (remplace l'interface Streamlit) : connexion, tableau de bord,
offres en cartes, recommandations selon le profil, résumé d'offres, carte, candidatures et favoris.

## Lancer en local
```bash
python -m venv .venv && source .venv/bin/activate   # Windows : .venv\Scripts\activate
pip install -r requirements.txt
python app.py            # http://127.0.0.1:5000
```
Créez un compte, puis remplissez **Profil** (poste, compétences, secteurs) : les offres
reçoivent un pourcentage de correspondance et les meilleures apparaissent sur le tableau de bord.

## Studio CV, envoi et Kanban
- **Studio CV** (`/cv`) : formulaire avec aperçu en direct, export PDF (`/cv.pdf`).
- **Postuler avec mon CV** (sur chaque offre) et **Envoyer mon CV** (sur chaque entreprise) : envoi du PDF par email
  via SMTP, avec recherche d'email public. La candidature passe automatiquement en « Envoyé ».
  Sans SMTP configuré, l'app propose le téléchargement du CV et l'ouverture de votre messagerie.
- **Kanban** (`/candidatures`) : Envoyé, En attente, Entretien, Refusé, Accepté ; glisser-déposer, notes, ajout manuel.
- Le PDF utilise des polices latines : les accents français fonctionnent, pas l'écriture arabe.

## Variables d'environnement (voir `.env.example`)
- `SECRET_KEY` : obligatoire en production.
- `GOOGLE_MAPS_API_KEY` : pour chercher de nouvelles entreprises (Google Places).
- `ANTHROPIC_API_KEY` : optionnel. Sans clé, « Résumer » utilise un résumé automatique local ;
  avec clé, le résumé est rédigé par Claude.

## Changer les couleurs
Toutes les couleurs sont en haut de `static/style.css` (variables `--wine`, `--saffron`, `--bg`…).

## Recherche, clés API et actualisation automatique
- **Recherche** (`/recherche`) : cherche de nouvelles offres (mot-clé ou lien Rekrute) et de nouvelles entreprises
  (Google Places). Les résultats sont prévisualisés ; seules les nouveautés sont proposées, vous choisissez lesquelles ajouter.
- **Recherches enregistrées** : relancées automatiquement toutes les `AUTO_REFRESH_HOURS` heures (6 par défaut) ;
  les nouveautés sont ajoutées seules. `AUTO_REFRESH=0` désactive le planificateur.
- **Paramètres** (`/parametres`) : chaque utilisateur enregistre sa clé Google Places, sa clé Anthropic et ses réglages SMTP.
  Les valeurs sont chiffrées (Fernet, clé dérivée de `SECRET_KEY`) et jamais renvoyées au navigateur.
  Attention : si vous changez `SECRET_KEY`, les clés déjà enregistrées deviennent illisibles et doivent être ressaisies.
  Les variables `.env` servent de valeurs par défaut.
- **Graphiques** : la page Statistiques et le tableau de bord se mettent à jour toutes les 30 s sans recharger la page.
- Les offres et entreprises ajoutées sont partagées entre tous les utilisateurs de l'instance.

## Automatisation avec n8n
Deux sens, au choix :
1. **L'application prévient n8n** : quand une recherche enregistrée trouve de nouvelles offres au-dessus de votre seuil, elle envoie un webhook (Paramètres > Automatisation n8n).
2. **n8n interroge l'application** avec un jeton API (créé dans Paramètres, affiché une seule fois) :
   - `GET /api/n8n/offers?min_match=50&since_hours=24` : offres et % de correspondance
   - `GET /api/n8n/summary` : résumé pour un rapport quotidien
   - `POST /api/n8n/refresh` : relance vos recherches enregistrées
   (en-tête `Authorization: Bearer <jeton>`)

Mise en route avec Docker : `docker compose up -d --build`, puis ouvrir n8n sur http://localhost:5678, importer les 3 fichiers de `n8n/`
(Workflows > Import from file), choisir vos identifiants Telegram, remplacer `REMPLACER_PAR_VOTRE_CHAT_ID` et `COLLEZ_VOTRE_JETON_ICI`,
activer les workflows, puis coller l'URL **production** du webhook (`http://n8n:5678/webhook/khedma-offres`) dans Paramètres.
Hors Docker, remplacez `http://web:8000` par `http://host.docker.internal:5000` et définissez `N8N_ALLOW_PRIVATE=1`.
Note : ces workflows n'ont pas pu être testés dans n8n lui-même ; si l'import signale un nœud, recréez-le à la main avec les mêmes réglages.

## Sécurité et tests
- **CSRF** : toutes les requêtes POST exigent un jeton lié à la session. `static/csrf.js` l'ajoute automatiquement aux formulaires et aux `fetch()`.
- Derrière HTTPS, définissez `COOKIE_SECURE=1` pour que le cookie de session ne circule qu'en HTTPS.
- **Tests** : `pip install -r requirements-dev.txt` puis `python -m pytest -q` (32 tests : accès, CSRF, offres, recherche, Kanban, CV, envoi, clés chiffrées, isolation entre utilisateurs). Aucun appel réseau.

## Docker
```bash
cp .env.example .env      # puis définissez SECRET_KEY (et SMTP / clés API si besoin)
docker compose up -d --build
```
Application sur http://localhost:8000. La base SQLite est stockée dans le volume `khedma_db` (comptes, CV, candidatures).
Sauvegarde : `docker compose cp web:/app/storage/career.db ./career-backup.db`.
Logs : `docker compose logs -f web`. Arrêt : `docker compose down` (ajoutez `-v` pour supprimer aussi les données).

## Statistiques
La page `/statistiques` affiche 8 graphiques (offres et entreprises par secteur, mots les plus demandés,
notes Google, correspondance au profil, entreprises les mieux notées, candidatures par statut et dans le temps).

## Production
`gunicorn app:app`. Utilisez un hébergeur avec disque persistant (Render, Railway, PythonAnywhere, VPS) :
la base SQLite (`data/career.db`) ne persiste pas sur Vercel. Pour Vercel, remplacez SQLite par Postgres/Supabase.

## Structure
`app.py` routes et API · `insights.py` secteurs, correspondance, résumé · `data_sources.py` scraping Rekrute et Google Places (inchangé) ·
`templates/` pages · `static/` CSS et JS · `data/` CSV de départ.
