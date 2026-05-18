# Paris Transport Tracker

Application web de suivi en temps reel et d'analyse historique des perturbations du reseau RATP (metro, RER, tramway) en Ile-de-France.

Le site RATP affiche le statut actuel des lignes mais ne conserve aucun historique. Ce projet collecte ces donnees toutes les 5 minutes et les stocke localement, permettant d'analyser les tendances sur le long terme : quelle ligne est la plus perturbee, a quelle heure, quel jour, etc.

## Fonctionnalites

### Suivi en temps reel
- Statut de 35 lignes (16 metro, 5 RER, 14 tram) collecte toutes les 5 minutes
- Carte du reseau avec indicateurs vert/rouge/orange
- Rafraichissement automatique

### Analyse historique
- Classement des pires lignes par taux de perturbation
- Graphiques par heure, jour de la semaine, evolution dans le temps
- Filtres par annee et par type de transport (metro/RER/tram)
- Heatmap heure x jour pour identifier les pires creneaux

### Donnees SNCF (2013-2026)
- Import automatique de 13 ans de donnees de ponctualite mensuelles
- Classement historique des RER et lignes Transilien
- Analyse saisonniere et comparaison par annee
- Source : data.sncf.com (open data)

### Mon trajet
- Selection de ses lignes quotidiennes
- Score de fiabilite personnalise sur 100
- Predictions de perturbation par creneau horaire
- Estimation du temps perdu par jour/semaine/annee
- Heatmap personnalisee

### Comparateur
- Comparaison de deux lignes cote a cote
- Graphiques superposes (par heure, par jour, evolution)
- Verdict automatique

### Remboursements
- Suivi manuel des campagnes de remboursement RATP/IDFM
- Alerte visuelle quand une campagne expire bientot (J-7)

## Stack technique

- **Backend** : Python 3.11 + Flask
- **Base de donnees** : SQLite (fichier local, zero config)
- **Scraping** : API PRIM d'Ile-de-France Mobilites (officielle, gratuite)
- **Frontend** : HTML + Tailwind CSS (CDN) + Chart.js
- **Scheduler** : APScheduler (collecte en arriere-plan)
- **Icones** : SVG officiels Wikimedia Commons

## Installation

### Prerequis
- Python 3.10+
- Un compte PRIM (gratuit) : https://prim.iledefrance-mobilites.fr/

### Mise en place

```bash
cd ratp-tracker
python -m venv venv

# Windows
venv\Scripts\activate

# Linux/Mac
source venv/bin/activate

pip install -r requirements.txt
python app.py
```

Ouvrir http://localhost:5555 dans le navigateur.

Au premier lancement, l'application demande une cle API PRIM. Pour l'obtenir :
1. Creer un compte sur prim.iledefrance-mobilites.fr
2. Aller dans "Mes jetons d'authentification" > onglet "API"
3. Cliquer "Generer mon jeton"
4. Coller le jeton dans la page de configuration

### Lancement rapide (Windows)

Double-cliquer sur `start.bat`.

### Demarrage automatique au boot (Windows)

Le script `scripts\install_autostart.bat` (clic-droit > Executer en tant qu'administrateur) cree une tache planifiee `TransportTracker` qui lance `autostart.bat` au demarrage du PC, en SYSTEM, avec relance automatique en cas de plantage. Tournant en SYSTEM, les processes ne peuvent pas etre tues depuis un terminal utilisateur normal.

### Redemarrer apres une modification du code Python

Les changements HTML/Jinja sont rechargés tout seuls par Flask en mode debug. Les changements dans les fichiers `.py` (database.py, scraper.py, app.py...) demandent un redemarrage du process Python.

**PowerShell en mode administrateur** (Win+X > "Terminal (administrateur)") :

```powershell
schtasks /end /tn "TransportTracker"; Start-Sleep 1; Get-Process python -EA SilentlyContinue | Stop-Process -Force; Start-Sleep 1; schtasks /run /tn "TransportTracker"
```

Cette ligne :
1. Arrete la tache planifiee (tue autostart.bat)
2. Tue les python.exe survivants (sinon ils continuent a tenir le port 5555)
3. Relance la tache (autostart.bat repart, lance python avec le nouveau code)

Pour verifier que le port est bien repris par un nouveau process :

```powershell
Get-NetTCPConnection -LocalPort 5555 -ErrorAction SilentlyContinue | Select-Object OwningProcess
```

## Structure du projet

```
ratp-tracker/
  app.py              Serveur Flask, routes et API
  scraper.py          Collecte des donnees via API PRIM
  database.py         Schema SQLite et fonctions de requete
  sncf_data.py        Import des donnees SNCF open data
  config.py           Gestion de la configuration (cle API)
  demo.py             Generateur de donnees fictives pour test
  start.bat           Script de lancement Windows
  requirements.txt    Dependances Python
  data/               Base SQLite + config + logs (gitignore)
  static/icons/       Pictogrammes SVG des lignes
  templates/          Pages HTML (Jinja2)
    base.html         Layout commun, navbar, theme
    dashboard.html    Page d'accueil, stats temps reel
    map.html          Carte du reseau
    my_commute.html   Mon trajet personnalise
    compare.html      Comparateur de lignes
    punctuality.html  Donnees historiques SNCF
    reimbursements.html  Suivi des remboursements
    line_detail.html  Detail d'une ligne
    settings.html     Configuration de la cle API
```

## API

Tous les endpoints retournent du JSON.

### Temps reel
- `GET /api/current` - Statut actuel de toutes les lignes
- `GET /api/ranking?start=&end=` - Classement par taux de perturbation
- `GET /api/by-hour?line_type=&line_id=` - Perturbations par heure
- `GET /api/by-dow?line_type=&line_id=` - Perturbations par jour de la semaine
- `GET /api/by-date?line_type=&line_id=` - Perturbations par date
- `GET /api/line-history/<type>/<id>` - Historique d'une ligne
- `GET /api/summary` - Stats globales
- `GET /api/heatmap?line_type=&line_id=` - Heatmap heure x jour
- `GET /api/predict?line_type=&line_id=` - Donnees de prediction
- `POST /api/scrape` - Forcer une collecte

### SNCF
- `GET /api/sncf/ranking?service=&year=&month=` - Classement ponctualite
- `GET /api/sncf/history/<line>?service=` - Historique d'une ligne
- `GET /api/sncf/by-year?service=` - Moyennes par annee
- `GET /api/sncf/seasonal?service=` - Moyennes par mois (saisonnier)
- `GET /api/sncf/years` - Annees disponibles
- `POST /api/sncf/sync` - Mettre a jour les donnees SNCF

### Remboursements
- `GET /api/reimbursements` - Lister les campagnes
- `POST /api/reimbursements` - Ajouter une campagne (JSON body)
- `DELETE /api/reimbursements/<id>` - Supprimer une campagne

## Donnees

### Collecte temps reel
- Source : API PRIM (general-message, SIRI)
- Frequence : toutes les 5 minutes
- 35 lignes surveillees (metro 1-14, RER A-E, tram T1-T13)
- Statuts : normal, alerte, normal_trav (travaux)
- Stockage : table `line_status` dans SQLite

### Donnees historiques SNCF
- Source : data.sncf.com/explore/dataset/ponctualite-mensuelle-transilien
- Couverture : janvier 2013 a aujourd'hui
- 13 lignes : RER A-E + Transilien H, J, K, L, N, P, R, U
- Donnee : taux de ponctualite mensuel (%)
- Stockage : table `sncf_punctuality` dans SQLite

### Stockage
- Base SQLite locale : `data/ratp.db`
- Taille estimee : ~1 Mo/jour, ~365 Mo/an
- Logs : `data/scraper.log`
- Configuration : `data/config.json`

Tout le dossier `data/` est dans le `.gitignore`.

## Limites connues

- La collecte necessite que le PC soit allume et le script en cours d'execution
- L'API PRIM a une limite de ~1000 requetes/jour pour les nouveaux comptes (35 lignes x 12 collectes/heure x ~20h = ~8400, la limite semble plus souple en pratique)
- Les donnees historiques avant la date de premiere collecte n'existent pas (sauf les donnees SNCF mensuelles)
- Les bus et Noctilien ne sont pas suivis (trop de lignes pour le quota API)
- Les campagnes de remboursement doivent etre ajoutees manuellement (pas d'API disponible)

## Licence

Projet personnel. Donnees PRIM sous licence Ile-de-France Mobilites. Donnees SNCF sous licence Open Data SNCF. Pictogrammes sous licence Wikimedia Commons.
