# Recouvrement des factures impayées — Prototype BWAI 2026

**Équipe** : AlPha coDers  
**Membres** :
- DINLA Marcel
- ABDOULAYE Nydal
- KINHOU Ange
- FAGBEGNON Dimitri

Prototype développé pour le **cas pratique 1 « Recouvrement automatique des factures impayées »** du hackathon BWAI 2026.

Cette application web locale aide une PME béninoise à :
- importer son portefeuille client depuis Excel ou CSV en bloquant les doublons et les erreurs ;
- calculer de façon exacte et vérifiable les soldes restants et les jours de retard ;
- classer les créances par ordre de priorité avec une explication claire pour chaque dossier ;
- préparer des messages de relance adaptés au canal du client (WhatsApp, SMS ou Email) ;
- analyser les réponses des clients grâce à une intelligence artificielle locale et explicable ;
- confier toutes les décisions sensibles (litiges, gros montants, cas incertains) à une validation humaine ;
- conserver un journal d'audit horodaté et protégé par une chaîne d'empreintes SHA-256.

> [!IMPORTANT]
> Toutes les données sont fictives. Aucun secret ni clé d'API n'est utilisé. Les envois de messages (WhatsApp, SMS, Email), la réception de réponses clients et les règlements Mobile Money (`MOMO-DEMO-…`) sont intégralement simulés en local, sans aucune connexion externe.

## Sommaire

1. [Fonctionnalités couvertes](#1-fonctionnalités-couvertes)
2. [Installation](#2-installation)
3. [Configuration](#3-configuration)
4. [Lancement et ouverture avec Uvicorn](#4-lancement-et-ouverture-avec-uvicorn)
5. [Comptes de test et rôles](#5-comptes-de-test-et-rôles)
6. [Scénario de démonstration en 3 à 4 minutes](#6-scénario-de-démonstration-en-3-à-4-minutes)
7. [Format des fichiers d'import](#7-format-des-fichiers-dimport)
8. [Règles de calcul, statuts et priorisation](#8-règles-de-calcul-statuts-et-priorisation)
9. [Brique IA et comparaison avec les autres modèles](#9-brique-ia-et-comparaison-avec-les-autres-modèles)
10. [Architecture du projet](#10-architecture-du-projet)
11. [Routes de l'API REST](#11-routes-de-lapi-rest)
12. [Tests automatisés](#12-tests-automatisés)
13. [Travail hors connexion et reprise après coupure](#13-travail-hors-connexion-et-reprise-après-coupure)
14. [Dépendances et licences](#14-dépendances-et-licences)
15. [Limites connues](#15-limites-connues)
16. [Documentation du dépôt](#16-documentation-du-dépôt)

## 1. Fonctionnalités couvertes

| Module du cahier des charges | Comportement dans l'application |
|---|---|
| **A. Importation locale** | Import de fichiers Excel à 6 feuilles ou CSV, téléchargement d'un gabarit Excel prêt à remplir, bilan des lignes acceptées et rejetées. |
| **B. Contrôle des données** | Vérification des champs obligatoires, des dates, des montants positifs, de la devise (`FCFA`), des doublons, des références inconnues et de la chronologie des paiements. Boutons pour **corriger et réintégrer** ou **exclure** chaque ligne rejetée. |
| **C. Calcul fiable** | Calcul déterministe en arithmétique décimale exacte (`Decimal`, arrondi commercial au centime) à partir d'une date de référence configurable. |
| **D. Statuts des factures** | Prise en compte des 6 statuts demandés : `NON_ECHUE`, `ECHUE`, `PARTIELLEMENT_PAYEE`, `SOLDEE`, `CONTESTEE`, `COUVERTE_PAR_ECHEANCIER`, avec distinction claire des dossiers hors classement (`Hors classement (Soldée)` et `Hors classement (Suspendue)`). |
| **E. Priorisation** | Classement explicable combinant le retard (40 %), le solde restant (35 %) et l'historique client (25 %). |
| **F. Relances graduées** | Trois niveaux (`RAPPEL_PRE_ECHEANCE` optionnel à J-3, `RELANCE_1` de 1 à 14 jours, `RELANCE_2` dès 15 jours) sur le canal préféré du client. |
| **G. Message personnalisé** | Génération adaptée au canal (SMS court, WhatsApp ou Email avec objet) incluant le solde vérifié et une référence Mobile Money fictive clairement signalée. |
| **H. Validation humaine** | Mise en attente des litiges, des montants atteignant le seuil configuré, des messages clients sensibles ou incertains et des nouveaux clients sans historique. Décisions : approuver, modifier, suspendre ou rejeter, avec respect du principe des quatre yeux. |
| **I. Vérification avant envoi** | Recalcul du solde et du statut dans une transaction verrouillée juste avant l'envoi simulé, contrôle du canal, du consentement, du délai minimal de 7 jours, du plafond de 3 relances et de l'arrêt d'urgence. |
| **J. Paiements simulés** | Enregistrement sans double comptabilisation, annulation automatique des relances devenues inutiles, détection des surpaiements avec proposition d'avoir. |
| **K. Travail hors connexion** | Base SQLite locale en mode WAL, cache automatique de session et reprise sans doublon après interruption. |
| **L. Traçabilité et exports** | Journal horodaté chaîné en SHA-256 protégé contre toute modification, vérification d'intégrité en un clic, exports CSV et JSON du portefeuille (avec contacts masqués) et export CSV du journal. |

## 2. Installation

### Prérequis
- Python **3.11 ou version supérieure** (testé avec Python 3.14.4)
- Fonctionne sous Linux, macOS et Windows
- Aucune connexion Internet ni clé d'API externe requise après l'installation des bibliothèques Python

### Commandes d'installation

```bash
git clone https://github.com/delsDin/bwai.git recouvrement_bwai
cd recouvrement_bwai

python3 -m venv .venv
source .venv/bin/activate        # Sous Windows : .venv\Scripts\activate

pip install -r requirements.txt
```

Aucun fichier `.env` n'est nécessaire : toute la configuration de démonstration est prête à l'emploi dans `config.py`.

## 3. Configuration

Conformément au cahier des charges (*« sans secret ni donnée sensible »*), tous les paramètres sont centralisés en clair dans le fichier [config.py](config.py).

| Paramètre dans `config.py` | Valeur par défaut | Rôle métier |
|---|---|---|
| `DEFAULT_REFERENCE_DATE` | `2026-03-15` | Date de référence fixe permettant de reproduire exactement les mêmes calculs de retard. |
| `DEFAULT_HIGH_AMOUNT_THRESHOLD` | `500000.0` | Montant en FCFA à partir duquel la validation par un superviseur humain est obligatoire. |
| `DEFAULT_AI_CONFIDENCE_THRESHOLD` | `0.75` | Seuil de confiance IA en dessous duquel le message client est envoyé en revue humaine. |
| `DEFAULT_CHANNEL` | `WHATSAPP` | Canal simulé par défaut (`WHATSAPP`, `SMS` ou `EMAIL`). |
| `MIN_DAYS_BETWEEN_REMINDERS` | `7` | Nombre minimal de jours à respecter entre deux relances envoyées sur une même facture. |
| `MAX_REMINDERS_PER_INVOICE` | `3` | Nombre maximal de relances autorisées par facture. |
| `WEIGHT_DELAY / BALANCE / HISTORY` | `0.40 / 0.35 / 0.25` | Poids respectifs du retard, du solde et de l'historique dans le score de priorité. |
| `REQUIRE_AUTH` | `True` | Active la vérification obligatoire des jetons d'opérateur sur les routes de modification. |

Ces paramètres (ainsi que la préparation automatique des relances et l'activation des rappels pré-échéance) peuvent aussi être modifiés directement depuis l'interface web grâce au bouton **Paramètres métier**.

## 4. Lancement et ouverture avec Uvicorn

### Étape 1 : Démarrer le serveur Uvicorn

Dans un terminal placé dans le dossier `recouvrement_bwai`, activer l'environnement virtuel puis lancer la commande `uvicorn` :

```bash
source .venv/bin/activate        # Sous Windows : .venv\Scripts\activate
uvicorn app:app --reload --host 127.0.0.1 --port 8000
```

Détail de la commande :
- `app:app` indique à Uvicorn de charger l'objet FastAPI `app` situé dans le fichier `app.py`.
- `--reload` redémarre automatiquement le serveur si un fichier de code est modifié.
- `--host 127.0.0.1 --port 8000` ouvre le serveur en local sur le port `8000`.

Lorsque le terminal affiche `Application startup complete`, la base SQLite locale est initialisée et le modèle IA est entraîné.

### Étape 2 : Ouvrir l'application dans le navigateur

Ouvrir un navigateur web (Chrome, Firefox, Edge ou Safari) et saisir l'une des adresses suivantes :
- **Tableau de bord principal** : http://127.0.0.1:8000/
- **Fiche détaillée d'une facture** : http://127.0.0.1:8000/invoices/FAC-2026-002
- **Documentation interactive de l'API (Swagger)** : http://127.0.0.1:8000/docs

Au premier démarrage, le tableau de bord s'ouvre à vide pour permettre de tester l'importation en direct. Vous pouvez soit cliquer sur **Charger la démo**, soit importer le fichier `data/demo_portfolio.xlsx` depuis l'onglet **Imports**.

## 5. Comptes de test et rôles

Pour faciliter la démonstration devant le jury, le menu déroulant **Opérateur** en haut à droite de l'écran permet de changer de profil en un clic. Chaque action envoie automatiquement le jeton correspondant dans l'en-tête `X-API-Token`.

| Opérateur | Rôle | Jeton de test (`config.py`) | Permissions principales |
|---|---|---|---|
| **Koffi** | `GESTIONNAIRE` | `tok-gestionnaire-koffi` | Importer un portefeuille, corriger une ligne rejetée, préparer et envoyer une relance standard, enregistrer un paiement simulé, signaler un litige, simuler un message client entrant. |
| **Amina** | `VALIDATEUR` | `tok-validateur-amina` | Toutes les actions du gestionnaire, plus approuver/modifier/suspendre/rejeter en revue humaine, émettre un avoir, accorder un échéancier, résoudre un litige et modifier les paramètres métier. |
| **Boris** | `VALIDATEUR` | `tok-validateur-boris` | Second validateur permettant de démontrer le principe des quatre yeux lorsqu'Amina a elle-même préparé une relance sensible. |
| **Direction** | `ADMIN` | `tok-admin-direction` | Toutes les actions, plus activer l'arrêt d'urgence, charger la démo, remettre la base à zéro et restaurer le cache. |

## 6. Scénario de démonstration en 3 à 4 minutes

1. **Importer et contrôler** : sélectionner l'opérateur **Koffi**, ouvrir l'onglet **Imports** et importer `data/demo_portfolio.xlsx`. Montrer que 17 lignes sont acceptées et que la facture `FAC-ERR-DEMO` (montant `-45000`) est isolée dans les rejets.
2. **Corriger une anomalie** : cliquer sur **Corriger** sur la ligne rejetée, remplacer `-45000` par `45000` et valider. La facture est immédiatement intégrée et la correction est tracée.
3. **Expliquer les calculs et le classement** : dans l'onglet **Portefeuille**, montrer l'encours, les retards et le classement priorisé. Ouvrir `FAC-2026-003` pour montrer le calcul exact du solde (`250 000 - 80 000 de paiement - 20 000 d'avoir = 150 000 FCFA`) et expliquer pourquoi les dossiers soldés ou contestés portent la mention **Hors classement**.
4. **Simuler une réponse WhatsApp d'un client** : sur une facture échue, cliquer sur **Simuler message WhatsApp entrant** et saisir une contestation. Montrer que l'IA détecte le litige, affiche les mots explicatifs et place aussitôt la relance en **Revue humaine**.
5. **Valider à quatre yeux** : passer sur le profil **Amina (Validateur)**, ouvrir l'onglet **Revue humaine**, examiner la relance de gros montant `FAC-2026-007` (780 000 FCFA), adapter ou approuver le message.
6. **Simuler un paiement avant l'envoi** : encaisser le solde de `FAC-2026-003` (150 000 FCFA), puis tenter un second paiement identique (bloqué comme doublon) et vérifier que la relance devenue inutile a été automatiquement annulée.
7. **Montrer les preuves et le journal** : ouvrir l'onglet **Audit & IA**, observer le comparatif face à la méthode manuelle Excel, cliquer sur **Vérifier le journal** (chaîne SHA-256 intacte) et sur **Tester 20 threads simultanés** (`1 action exécutée, 19 doublons bloqués`).

## 7. Format des fichiers d'import

Un gabarit Excel prêt à l'emploi peut être téléchargé depuis l'onglet **Imports** (`GET /api/import/template.xlsx`). Il comprend six feuilles :
- **`Clients`** : `client_id`, `nom_affiche`, `contact_simule`, `canal_prefere` (`WHATSAPP`, `SMS`, `EMAIL`), `consentement_contact` (`1` ou `0`), `nb_factures_historiques`, `nb_retards_historiques`.
- **`Factures`** : `invoice_id`, `client_id`, `date_emission`, `date_echeance`, `montant_initial`, `devise` (`FCFA` ou `XOF`), `dernier_message_client`.
- **`Paiements`** : `payment_id`, `invoice_id`, `date_paiement`, `montant`, `reference_fictive`.
- **`Avoirs`** : `credit_note_id`, `invoice_id`, `date_avoir`, `montant`, `motif`.
- **`Echeanciers`** : `plan_id`, `invoice_id`, `date_prochaine_echeance`, `montant_prevu`, `statut`.
- **`Contestations`** : `dispute_id`, `invoice_id`, `motif`, `date_ouverture`, `statut`.

L'import accepte aussi des fichiers `.csv` par entité.

## 8. Règles de calcul, statuts et priorisation

### Formules déterministes
- **Solde restant** : `max(0, montant initial - paiements validés - avoirs)` calculé en `Decimal` exact.
- **Trop-perçu (surpaiement)** : `max(0, paiements validés + avoirs - montant initial)`. Lorsqu'un règlement dépasse le solde, la facture passe à `SOLDEE` et une alerte propose l'émission d'un avoir.
- **Jours de retard** : `max(0, date de référence - date d'échéance)`, ramené à `0` dès que la facture est soldée.

### Priorité des 6 statuts métier
1. `SOLDEE` (solde nul)
2. `CONTESTEE` (litige ouvert)
3. `COUVERTE_PAR_ECHEANCIER` (échéancier actif dont la prochaine date n'est pas dépassée)
4. `PARTIELLEMENT_PAYEE` (au moins un paiement ou avoir imputé, solde positif)
5. `ECHUE` (date d'échéance dépassée, aucun règlement)
6. `NON_ECHUE` (date d'échéance non atteinte, aucun règlement)

### Formule de priorisation explicable
Pour les factures éligibles aux relances :
`Score = 0,40 × min(retard / 60, 1) + 0,35 × min(solde / seuil_gros_montant, 1) + 0,25 × score_historique`

Lorsque le client n'a aucune facture antérieure (`0/0`), la composante historique prend la valeur prudente `0,50` et la relance est envoyée en revue humaine.

## 9. Brique IA et comparaison avec les autres modèles

### Fonctionnement et rôle strict
L'IA (`services/ai_classifier.py`) ne calcule aucun montant et n'envoie aucune relance. Elle lit le dernier message du client pour le classer parmi 4 intentions (`LITIGE`, `DEMANDE_ECHEANCIER`, `ANNONCE_PAIEMENT`, `RETARD_SIMPLE`) et bloquer en revue humaine tout dossier sensible ou incertain.

Son pipeline associe un normaliseur d'abréviations SMS béninoises, une vectorisation `TF-IDF (1-2 grammes)`, une `LogisticRegression` équilibrée et une calibration de température du Softmax ($T = 0,55$).
- **Jeu d'entraînement** : `data/train.csv` (250 messages fictifs).
- **Jeu de test séparé** : `data/test.csv` (100 messages inédits, 0 doublon avec l'entraînement).
- **Sécurité métier** : **100 % des 82 messages sensibles** et **100 % des erreurs du modèle** sur le jeu de test sont orientés vers la revue humaine (**0 envoi automatique abusif**).

### Comparaison expérimentale avec les autres familles de modèles (Arbres, Forêts, Boosting, Naive Bayes, SVM et Règles)

Six familles d'algorithmes d'apprentissage automatique et une base de règles par mots-clés ont été comparées sur nos données (commande de reproduction : `python -m services.ai_classifier`) :

| Famille de modèle | F1-macro (Validation croisée 5 plis sur `train.csv`) | Exactitude (Validation croisée 5 plis) | Exactitude sur `test.csv` (100 cas) | F1-macro sur `test.csv` | Rappel sur la classe `LITIGE` (`test.csv`) |
|---|---:|---:|---:|---:|---:|
| **LogisticRegression (C=2.0, balanced) [Retenu]** | **79,74 % (± 2,80 %)** | **81,60 %** | **89,00 %** | **89,85 %** | **95,83 % (23/24)** |
| LinearSVC + Calibration de Platt | 79,73 % (± 3,37 %) | 81,60 % | 89,00 % | 89,85 % | 95,83 % (23/24) |
| Forêt aléatoire (RandomForest, 100 arbres) | 65,66 % (± 4,66 %) | 65,20 % | 77,00 % | 77,27 % | 54,17 % (13/24) |
| Gradient Boosting (100 arbres) | 64,76 % (± 4,70 %) | 64,80 % | 72,00 % | 72,36 % | 54,17 % (13/24) |
| Multinomial Naive Bayes (alpha=0.5) | 67,36 % (± 3,71 %) | 72,00 % | 69,00 % | 70,68 % | 100,00 % (sur-prédit LITIGE) |
| Arbre de décision (DecisionTree, profondeur 15) | 54,99 % (± 6,91 %) | 54,80 % | 62,00 % | 62,73 % | 66,67 % (16/24) |
| Règles par mots-clés (Baseline sans IA) | Non applicable | Non applicable | 36,00 % | 35,80 % | 79,17 % (19/24) |

### Pourquoi la Régression Logistique surpasse-t-elle les arbres de décision sur nos messages ?
Un message WhatsApp ou SMS est très court (10 à 15 mots) au sein d'un vocabulaire de plus de 1 500 mots. Un arbre de décision teste un seul mot à chaque branche : si le client emploie un synonyme absent du sommet de l'arbre, l'arbre se trompe (62 % pour un arbre seul, 77 % pour une forêt de 100 arbres). À l'inverse, la régression logistique additionne les indices de tous les mots présents dans la phrase en même temps, ce qui lui donne **89 % d'exactitude (+12 à +27 points devant les arbres)** tout en permettant d'afficher à l'opérateur les mots exacts qui ont pesé dans la décision.

Tous les détails figurent dans [FICHE_IA.md](FICHE_IA.md).

## 10. Architecture du projet

```
recouvrement_bwai/
├── app.py                              Application FastAPI et démarrage
├── config.py                           Paramètres métier et comptes de test (sans secret)
├── LICENSE                             Licence MIT et licences des bibliothèques
├── README.md                           Guide d'installation et d'utilisation
├── FICHE_IA.md                         Documentation et évaluation du modèle IA
├── RAPPORT_PROJET_BWAI2026.md          Rapport complet du projet (3 à 5 pages)
├── AMELIORATIONS_ET_FONCTIONNALITES.md Détail des améliorations techniques et métier
├── GUIDE_CODE_ET_DOSSIERS.md           Explication simple des dossiers et du code
├── GUIDE_INTERFACE_ET_BOUTONS.md       Guide visuel des écrans et boutons de l'interface
├── PRESENTATION_DIAPOSITIVES.md        Plan détaillé des diapositives pour le jury
├── database/
│   ├── schema.sql                      Schéma SQLite, clés uniques et triggers d'audit
│   └── db.py                           Connexions WAL et transactions BEGIN IMMEDIATE
├── models/
│   ├── schemas.py                      Schémas de validation Pydantic
│   └── repositories.py                 Opérations en base, exports, gabarit et cache
├── services/
│   ├── importer.py                     Import Excel/CSV et contrôles de cohérence
│   ├── calculator.py                   Calculs Decimal exacts, statuts et rejeu SHA-256
│   ├── prioritizer.py                  Calcul du score de priorité explicable
│   ├── reminder_engine.py              Relances graduées, revue humaine et pré-envoi
│   ├── payment_service.py              Paiements simulés idempotents et surpaiements
│   ├── recovery_service.py             Reprise après coupure hors connexion
│   ├── ai_classifier.py                Classifieur IA explicable et calibré
│   ├── benchmark_service.py            Comparatif méthode manuelle et test 20 threads
│   ├── audit_logger.py                 Journal chaîné SHA-256
│   └── security.py                     Rôles RBAC, quatre yeux et masquage des contacts
├── routes/
│   ├── web_routes.py                   Pages HTML Jinja2
│   └── api_routes.py                   Routes API REST
├── frontend/
│   ├── templates/                      dashboard.html et invoice_detail.html
│   └── static/                         Feuille de style app.css et logique app.js
├── data/                               demo_portfolio.xlsx, train.csv, test.csv
├── notebooks/                          Notebook de reproduction de l'évaluation IA
└── tests/                              50 tests automatisés pytest
```

## 11. Routes de l'API REST

| Méthode | Route | Rôle requis | Fonction |
|---|---|---|---|
| `GET` | `/api/dashboard`, `/api/invoices`, `/api/invoices/{id}` | Public (lecture) | Indicateurs, liste priorisée et détail d'un dossier |
| `GET` | `/api/import/template.xlsx` | Public | Télécharger le gabarit Excel à 6 feuilles |
| `POST` | `/api/import/upload` | `GESTIONNAIRE` | Importer un fichier Excel ou CSV |
| `POST` / `DELETE` | `/api/import/errors/{id}/resolve`, `/api/import/errors/{id}` | `GESTIONNAIRE` | Corriger et réintégrer ou exclure une ligne rejetée |
| `POST` | `/api/reminders/prepare-all`, `/api/reminders/prepare/{id}` | `GESTIONNAIRE` | Préparer les relances de façon idempotente |
| `POST` | `/api/review/{reminder_id}` | `VALIDATEUR` | Approuver, modifier, suspendre ou rejeter (4 yeux) |
| `POST` | `/api/reminders/{reminder_id}/send` | `GESTIONNAIRE` | Revérifier le dossier et simuler l'envoi |
| `POST` | `/api/payments/simulate` | `GESTIONNAIRE` | Enregistrer un paiement Mobile Money simulé |
| `POST` | `/api/webhooks/whatsapp-incoming` | Webhook local | Simuler la réception d'un message WhatsApp/SMS entrant |
| `POST` | `/api/invoices/{id}/incoming-message` | `GESTIONNAIRE` | Simuler un message client entrant sur une facture |
| `POST` | `/api/invoices/{id}/ai-override` | `GESTIONNAIRE` | Requalifier humainement un message client (Active Learning) |
| `POST` | `/api/disputes`, `/api/disputes/{id}/resolve` | `GESTIONNAIRE` / `VALIDATEUR` | Ouvrir ou résoudre une contestation |
| `POST` | `/api/credit-notes`, `/api/installment-plans` | `VALIDATEUR` | Émettre un avoir ou activer un échéancier |
| `GET` / `POST` | `/api/settings` | Lecture / `VALIDATEUR` | Lire ou modifier les paramètres métier à chaud |
| `GET` | `/api/export/portfolio.csv`, `/api/export/portfolio.json`, `/api/export/audit.csv` | Public (masqué) | Exporter le portefeuille priorisé et le journal d'audit |
| `GET` | `/api/benchmark`, `/api/ai/evaluation` | Public | Comparatif méthode de référence et métriques IA |
| `POST` | `/api/system/replay`, `/api/system/concurrency-proof` | `GESTIONNAIRE` | Rejeu SHA-256 et test en direct de 20 threads simultanés |
| `POST` | `/api/system/recover` | `VALIDATEUR` | Reprise contrôlée après interruption |
| `GET` | `/api/audit-logs`, `/api/audit-logs/verify` | Public | Consulter et vérifier la chaîne SHA-256 du journal |
| `POST` | `/api/system/reset-demo`, `/api/system/clear`, `/api/system/cache/restore`, `/api/system/emergency-stop` | `ADMIN` | Administration, remise à zéro et arrêt d'urgence |

## 12. Tests automatisés

Lancer la suite complète :

```bash
source .venv/bin/activate
pytest -v
```

Les **50 tests automatisés** couvrent les 12 cas obligatoires du guide (`T1` à `T12`), la concurrence à 20 threads simultanés, les invariants comptables en `Decimal`, l'évaluation hold-out de l'IA et le comparatif des 6 modèles ML, le contrôle d'accès RBAC, le principe des quatre yeux, l'immuabilité du journal SHA-256, les exports et la réception simulée de messages WhatsApp.

## 13. Travail hors connexion et reprise après coupure

L'application fonctionne sans Internet grâce à SQLite en mode WAL et à un fichier de cache local (`database/app_state_cache.json`). En cas d'arrêt brusque ou de coupure de courant, la réouverture de l'application restaure l'état après vérification de la chaîne SHA-256 du journal, et l'action **Reprise hors ligne** reprend uniquement les dossiers non traités sans jamais créer de doublon.

## 14. Dépendances et licences

Le code source du prototype est distribué sous licence **MIT** (voir le fichier [LICENSE](LICENSE)). Toutes les bibliothèques utilisées sont open source :

| Bibliothèque | Version testée | Licence | Rôle |
|---|---|---|---|
| FastAPI / Starlette | 0.165.0 / 1.10.0 | MIT / BSD-3-Clause | Serveur web et API REST |
| Uvicorn | 0.54.0 | BSD-3-Clause | Serveur ASGI |
| Jinja2 | 3.1.6 | BSD-3-Clause | Rendu des pages HTML |
| Pydantic | 2.14.4 | MIT | Validation des données |
| python-multipart / python-dotenv | 0.0.34 / 1.2.4 | Apache-2.0 / BSD-3-Clause | Upload de fichiers |
| pandas / openpyxl | 3.1.1 / 3.1.5 | BSD-3-Clause / MIT | Lecture et génération de fichiers Excel et CSV |
| scikit-learn / NumPy / SciPy | 1.9.1 / 2.5.5 / 1.18.1 | BSD-3-Clause | Classification IA locale et métriques |
| pytest / httpx | 9.2.0 / 0.28.1 | MIT / BSD-3-Clause | Exécution des tests automatisés |

## 15. Limites connues

- **Jetons de démonstration** : le sélecteur d'opérateur intégré à l'interface facilite les tests du jury mais devra être remplacé par une authentification nominative par session en usage réel.
- **Communications et règlements simulés** : conformément au cahier des charges, aucun message réel n'est transmis aux opérateurs télécoms ou à WhatsApp.
- **Corpus IA synthétique** : le modèle est entraîné sur 250 messages fictifs en français. Les messages en langues nationales (fon, yoruba, mina) ne sont pas traduits mais sont détectés comme hors vocabulaire et confiés à un gestionnaire humain.
- **Base locale mono-poste** : SQLite est idéal pour un poste local hors connexion ; un déploiement multi-agences nécessitera PostgreSQL.

## 16. Documentation du dépôt

- [RAPPORT_PROJET_BWAI2026.md](RAPPORT_PROJET_BWAI2026.md) : rapport de synthèse (3 à 5 pages) pour le jury.
- [FICHE_IA.md](FICHE_IA.md) : fiche technique et résultats détaillés du modèle IA.
- [GUIDE_CODE_ET_DOSSIERS.md](GUIDE_CODE_ET_DOSSIERS.md) : explication simple de chaque dossier et des parties clés du code.
- [GUIDE_INTERFACE_ET_BOUTONS.md](GUIDE_INTERFACE_ET_BOUTONS.md) : présentation visuelle des parties et boutons de l'interface.
- [PRESENTATION_DIAPOSITIVES.md](PRESENTATION_DIAPOSITIVES.md) : structure et contenu page par page des diapositives de soutenance.
- [AMELIORATIONS_ET_FONCTIONNALITES.md](AMELIORATIONS_ET_FONCTIONNALITES.md) : présentation des améliorations intégrées.
- [LICENSE](LICENSE) : licence du projet et déclaration des composants tiers.
