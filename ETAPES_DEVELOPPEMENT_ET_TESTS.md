# Guide Détaillé des 6 Étapes de Développement et Stratégie de Tests - BWAI 2026

Ce document explique en détail chacune des **6 étapes de développement** réalisées pour le prototype du **Cas pratique 1 (Recouvrement automatique des factures impayées)**, l'ordre logique d'écriture des fichiers, les tests exécutés à chaque palier dans `.venv`, ainsi qu'une analyse complète de la couverture des **25 tests automatisés**.

---

## 1. Vue d'Ensemble du Cycle de Construction

Le développement suit une approche **Bottom-Up (du cœur métier vers l'interface)** afin que chaque brique soit testée isolément avant d'être connectée à la suivante :

```text
[Étape 0 : Socle SQLite + Config + Schémas Pydantic + Audit Logger]
       |
       v
[Étape 1 : Moteur de Calcul Déterministe + 6 Statuts + Priorisation]  --> Tests T1 à T6 (7 tests)
       |
       v
[Étape 2 : Brique IA NLP Locale (TF-IDF + LogReg) + Seuil 0.75]       --> Tests IA (5 tests)
       |
       v
[Étape 3 : Importateur CSV/Excel/Dict + Rejet explicite anomalies]    --> Test T12 (1 test multi-cas)
       |
       v
[Étape 4 : Service de Paiements Simulés Idempotents]                  --> Test T9 (1 test)
       |
       v
[Étape 5 : Moteur de Relances + Revue Humaine + Garde-fou + Reprise]  --> Tests T7, T8, T10, T11 (4 tests)
       |
       v
[Étape 6 : Repositories + API REST FastAPI + Interface Web Jinja2]    --> Test E2E + 6 Tests Avancés (7 tests)
```

---

## 2. Détail des 6 Étapes Développées

### Étape 0 : Socle de Configuration, Base SQLite Locale, Schémas Pydantic et Journal d'Audit

* **Objectif** : Poser les fondations techniques garantissant le fonctionnement 100 % hors connexion (Module K), la validation des formats (Module B), l'idempotence physique en base de données (Section 9 du Guide) et la traçabilité (Module L).
* **Fichiers écrits** :
  1. `config.py` : Centralise la date de référence fixe (`2026-03-15`), le seuil de gros montant (`500 000 FCFA`), le seuil de confiance IA (`0.75`), les poids du score (`0.40 / 0.35 / 0.25`) et les limites de fréquence des relances.
  2. `database/schema.sql` : Définit les 10 tables SQLite (`settings`, `clients`, `invoices`, `payments`, `credit_notes`, `installment_plans`, `disputes`, `reminders`, `import_errors`, `audit_logs`). Intègre les contraintes physiques anti-doublon `UNIQUE(invoice_id, reference_fictive)` sur `payments` et `UNIQUE(invoice_id, niveau_relance)` sur `reminders`.
  3. `database/db.py` : Gère les connexions SQLite locales avec `PRAGMA foreign_keys = ON` et transactions atomiques (`commit` / `rollback`).
  4. `models/schemas.py` : Définit les modèles **Pydantic v2** qui contrôlent les types, les montants strictement positifs (`gt=0`), la cohérence des dates (`date_echeance >= date_emission`) et la cohérence de l'historique client (`nb_retards <= nb_factures`).
  5. `services/audit_logger.py` : Enregistre chaque action (`SYSTEME`, `GESTIONNAIRE`, `IA`) avec horodatage UTC, ancienne valeur, nouvelle valeur et motif.

---

### Étape 1 : Moteur de Calcul Déterministe, Qualification des 6 Statuts et Priorisation (Modules C, D, E)

* **Objectif** : Garantir une fiabilité financière absolue (25 points du barème TDR) sans jamais utiliser d'IA générative pour les calculs arithmétiques.
* **Fichiers écrits** :
  1. `services/calculator.py` :
     * `compute_balance_and_delay()` : Calcule de manière déterministe :
       * `solde_restant = max(0.0, montant_initial - total_paye - total_avoirs)`
       * `jours_retard = 0` si `solde_restant == 0`, sinon `max(0, date_reference - date_echeance)`.
     * `determine_invoice_status()` : Qualifie chaque facture parmi les **6 statuts obligatoires** avec un ordre de priorité de sécurité strict :
       1. `SOLDEE` (`solde_restant == 0`)
       2. `CONTESTEE` (litige ouvert)
       3. `COUVERTE_PAR_ECHEANCIER` (plan actif non dépassé)
       4. `PARTIELLEMENT_PAYEE` (solde > 0 et au moins un paiement ou avoir imputé)
       5. `ECHUE` (solde > 0, aucun paiement, date d'échéance dépassée)
       6. `NON_ECHUE` (solde > 0, aucun paiement, date d'échéance non dépassée)
     * `recalculate_invoice()` et `recalculate_all_invoices()` : Agrègent les paiements, avoirs, litiges et échéanciers depuis SQLite et mettent à jour la table `invoices`.
  2. `services/prioritizer.py` :
     * `compute_priority_score()` : Implémente l'heuristique explicable du Guide :
       * `score_retard = min(jours_retard / 60, 1.0)`
       * `score_solde = min(solde_restant / 500000, 1.0)`
       * `score_historique = nb_retards / nb_factures` (ou `0.50` avec mention `"inconnu"` si `nb_factures == 0`)
       * `score_total = 0.40 * score_retard + 0.35 * score_solde + 0.25 * score_historique`
     * Exclut automatiquement du classement de relance les factures `SOLDEE`, `CONTESTEE` et `COUVERTE_PAR_ECHEANCIER`.
* **Fichier de test** : `tests/test_step1_calculations.py` (**7 tests** validant T1, T2, T3, T4, T5, T6 et la formule de score).

---

### Étape 2 : Brique IA Locale de Classification des Messages Clients (Module H / Fiche IA)

* **Objectif** : Apporter une automatisation IA ciblée, mesurable et 100 % hors-ligne (15 points du barème TDR) avec gestion explicite de l'incertitude.
* **Fichier écrit** :
  1. `services/ai_classifier.py` :
     * Pipeline `scikit-learn` combinant `TfidfVectorizer(ngram_range=(1, 2))` et `LogisticRegression(class_weight="balanced")`.
     * Classe tout message retour d'un client en 4 intentions : `LITIGE`, `DEMANDE_ECHEANCIER`, `ANNONCE_PAIEMENT`, `RETARD_SIMPLE`.
     * Extrait la probabilité (`predict_proba`) comme **score de confiance** entre `0.0` et `1.0`.
     * Déclenche automatiquement `requiert_revue_humaine = True` si :
       * `confiance < 0.75` (cas incertain), ou
       * la catégorie prédite est `LITIGE`, `DEMANDE_ECHEANCIER` ou `ANNONCE_PAIEMENT`.
* **Fichier de test** : `tests/test_step2_ai.py` (**5 tests** vérifiant message vide, litige, demande d'échéancier, annonce de paiement et message ambigu sous le seuil de 75 %).

---

### Étape 3 : Importation Locale et Contrôle Strict des Données (Modules A & B)

* **Objectif** : Permettre l'importation d'un portefeuille multi-entités (Excel `.xlsx`, `.csv` ou dictionnaire) tout en rejetant et en expliquant chaque ligne invalide ou doublon sans jamais l'intégrer silencieusement (Test T12).
* **Fichier écrit** :
  1. `services/importer.py` :
     * `import_portfolio_dict()` et `import_portfolio_file()` : Parcourent les 6 entités (`clients`, `invoices`, `payments`, `credit_notes`, `installment_plans`, `disputes`).
     * Valident chaque ligne avec les schémas Pydantic de `models/schemas.py`.
     * Vérifient l'absence de doublon d'identifiant primaire et l'existence des clés étrangères (`client_id`, `invoice_id`).
     * Enregistrent toute anomalie dans la table `import_errors` avec le numéro de ligne, les données brutes et le motif exact en français.
     * Déclenchent `recalculate_all_invoices()` et journalisent le bilan dans `audit_logs`.
* **Fichier de test** : `tests/test_step3_import.py` (**1 test complet** vérifiant simultanément le rejet d'un doublon client, d'un montant négatif, d'une date d'échéance antérieure à l'émission et d'un client inconnu - Test T12).

---

### Étape 4 : Paiements Simulés Idempotents (Module J)

* **Objectif** : Permettre d'enregistrer un règlement fictif Mobile Money ou virement, mettre à jour le solde et le statut sans jamais comptabiliser deux fois le même paiement (Test T9), et annuler les relances en attente si la facture devient soldée (Test T11).
* **Fichier écrit** :
  1. `services/payment_service.py` :
     * `record_simulated_payment()` : Vérifie si le couple `(invoice_id, reference_fictive)` existe déjà dans `payments`.
     * Si oui : retourne `status = "DUPLICATE_IGNORED"`, `duplicate = True`, conserve le solde intact et trace la tentative de doublon dans `audit_logs`.
     * Si non : insère le paiement, appelle `recalculate_invoice()`, passe automatiquement au statut `ANNULEE` toutes les relances non envoyées si `solde_restant == 0`, et trace l'opération.
* **Fichier de test** : `tests/test_step4_payments.py` (**1 test** validant le Test T9 sur deux appels successifs avec la même référence Mobile Money).

---

### Étape 5 : Moteur de Relances Graduées, Revue Humaine, Garde-fou Pré-Envoi et Reprise (Modules F, G, H, I, K)

* **Objectif** : Orchestrer la préparation des messages, bloquer les dossiers sensibles en file de revue humaine, revérifier toutes les conditions juste avant l'envoi simulé et supporter une reprise après coupure sans doublon.
* **Fichiers écrits** :
  1. `services/reminder_engine.py` :
     * `determine_reminder_level()` : Choisit entre `RAPPEL_PRE_ECHEANCE` (J-3 à J0), `RELANCE_1` (1 à 14 jours de retard) et `RELANCE_2` (15 jours et plus).
     * `build_personalized_message()` : Injecte le nom du client, la référence de facture, l'échéance, le solde vérifié et la mention explicite `Reference Mobile Money fictive (SIMULATION UNIQUEMENT) : MOMO-DEMO-{invoice_id}`.
     * `prepare_reminder_for_invoice()` :
       * Ignore les factures `SOLDEE`, `COUVERTE_PAR_ECHEANCIER` ou `NON_ECHUE`.
       * Vérifie l'idempotence `(invoice_id, niveau_relance)` pour bloquer tout double clic (Test T10).
       * Place en `EN_ATTENTE_VALIDATION` tout dossier `CONTESTEE` (T5), tout solde `>= 500 000 FCFA` (T7), toute alerte/incertitude IA (`confiance < 0.75`), tout client sans historique ou sans consentement.
     * `apply_human_review()` : Permet au gestionnaire d'`APPROUVER`, `MODIFIER`, `SUSPENDRE` ou `REJETER` une relance avec motif obligatoire tracé dans `audit_logs`.
     * `verify_and_simulate_send()` (**Garde-fou Module I**) :
       * Bloque si déjà `SIMULEE` (Test T10).
       * **Recalcule en temps réel** la facture juste avant l'envoi.
       * Si la facture est devenue `SOLDEE`, `CONTESTEE` ou `COUVERTE_PAR_ECHEANCIER` entre la préparation et l'envoi -> **annule automatiquement la relance** (`CANCELLED_PRE_SEND`, Test T11).
       * Si le canal demandé ne correspond pas au `canal_prefere` du client ou si `consentement_contact == 0` -> **bloque et annule l'envoi** (`BLOCKED_CHANNEL_PREFERENCE`, Test T8).
       * Si la relance est toujours en `EN_ATTENTE_VALIDATION` -> refuse l'envoi tant qu'un humain n'a pas validé (Test T7).
  2. `services/recovery_service.py` :
     * `resume_after_interruption()` : Recalcule le portefeuille après redémarrage et reprend la préparation des relances en ignorant toutes celles déjà présentes en base SQLite (Test T10).
* **Fichier de test** : `tests/test_step5_reminders_and_recovery.py` (**4 tests** validant T7, T8, T10 et T11).

---

### Étape 6 : Repositories, Routes API REST FastAPI, Interface Web, Excel de Démo et Tests Avancés

* **Objectif** : Exposer toute la logique métier via une API REST documentée (`/docs`), une interface Web locale et un fichier Excel réel `data/demo_portfolio.xlsx` prêt pour la démonstration de 3 à 4 minutes devant le jury.
* **Fichiers écrits** :
  1. `models/repositories.py` : Requêtes d'agrégation pour les KPIs du tableau de bord (`get_dashboard_kpis`), détail complet d'un dossier (`get_invoice_detail`), listes filtrées et réinitialisation du portefeuille fictif de démonstration (`reset_and_load_demo_portfolio`).
  2. `routes/api_routes.py` : 15 endpoints REST FastAPI (`/api/dashboard`, `/api/invoices`, `/api/import/upload`, `/api/reminders/prepare-all`, `/api/review/{id}`, `/api/reminders/{id}/send`, `/api/payments/simulate`, `/api/ai/classify`, `/api/system/recover`, `/api/system/reset-demo`, `/api/audit-logs`).
  3. `routes/web_routes.py`, `templates/dashboard.html` et `templates/invoice_detail.html` : Interface Web claire affichant les KPIs, la liste priorisée avec explication du score et badge IA, la file de validation humaine, la file d'envoi simulé et le journal d'audit.
  4. `data/demo_portfolio.xlsx` : Fichier Excel multi-onglets (`Clients`, `Factures`, `Paiements`, `Avoirs`, `Echeanciers`, `Contestations`) contenant le portefeuille fictif complet ainsi qu'une ligne d'erreur volontaire pour démontrer le contrôle d'import en direct devant le jury.
* **Fichier de test** : `tests/test_step6_api_e2e.py` (**7 tests** incluant le parcours E2E et les 6 tests complémentaires).

---

## 3. Matrice Complète des 25 Tests Automatisés

| N° | Catégorie / Code Guide | Fonction de test | Ce qui est vérifié |
| :-: | :--- | :--- | :--- |
| 1 | **T1** (Calcul & Statut) | `test_t1_facture_non_echue_solde_positif` | Facture non échue -> statut `NON_ECHUE`, 0 jour de retard |
| 2 | **T2** (Calcul & Statut) | `test_t2_facture_echue_aucun_paiement` | Facture échue sans paiement -> solde exact et jours de retard exacts |
| 3 | **T3** (Calcul & Statut) | `test_t3_paiement_partiel_et_avoir` | Déduction déterministe des paiements et avoirs -> statut `PARTIELLEMENT_PAYEE` |
| 4 | **T4** (Calcul & Statut) | `test_t4_paiement_total` | Solde nul -> statut `SOLDEE`, inéligible à la relance |
| 5 | **T5** (Calcul & Statut) | `test_t5_facture_contestee` | Contestation ouverte -> statut `CONTESTEE`, inéligible à la relance automatique |
| 6 | **T6** (Calcul & Statut) | `test_t6_echeancier_actif` | Échéancier actif -> statut `COUVERTE_PAR_ECHEANCIER` |
| 7 | **Priorisation** (Section 5) | `test_prioritizer_guide_formula` | Formule exacte `0.40 * retard + 0.35 * solde + 0.25 * historique` |
| 8 | **Brique IA** (NLP) | `test_ai_classify_empty_message` | Message vide -> `AUCUN_MESSAGE`, pas de revue humaine |
| 9 | **Brique IA** (NLP) | `test_ai_classify_clear_dispute` | Détection automatique d'un litige client -> revue humaine |
| 10 | **Brique IA** (NLP) | `test_ai_classify_installment_request` | Détection d'une demande d'échéancier -> revue humaine |
| 11 | **Brique IA** (NLP) | `test_ai_classify_payment_claim` | Détection d'une annonce de paiement Mobile Money -> revue humaine |
| 12 | **Brique IA** (Incertitude) | `test_ai_classify_uncertain_message_triggers_human_review` | Confiance `< 0.75` -> envoi automatique en file de revue humaine |
| 13 | **T12** (Contrôle Import) | `test_t12_ligne_import_invalide_et_doublon_non_integres_silencieusement` | Rejet explicite des doublons, montants négatifs, dates incohérentes et clients inconnus |
| 14 | **T9** (Idempotence Paiement) | `test_t9_paiement_enregistre_deux_fois_une_seule_comptabilisation` | Double paiement avec même référence -> une seule comptabilisation |
| 15 | **T7** (Seuil Gros Montant) | `test_t7_montant_superieur_au_seuil_validation_humaine_obligatoire` | Montant `>= 500 000 FCFA` -> bloqué en revue humaine jusqu'à approbation |
| 16 | **T8** (Préférence Canal) | `test_t8_preference_de_contact_incompatible_aucune_simulation_envoi` | Canal différent de la préférence client -> envoi bloqué et annulé |
| 17 | **T10** (Anti-doublon & Reprise) | `test_t10_double_clic_et_reprise_apres_interruption_sans_doublon` | Double clic et reprise après interruption -> aucune double relance |
| 18 | **T11** (Garde-fou Pré-Envoi) | `test_t11_relance_preparee_puis_facture_reglee_annule_la_relance` | Facture payée après préparation de la relance -> annulation automatique avant envoi |
| 19 | **E2E FastAPI** (API + Web) | `test_api_health_and_reset_demo_and_web_dashboard` | Parcours complet HTTP REST + rendu HTML du tableau de bord |
| 20 | **Import Excel `.xlsx`** | `test_excel_file_upload_endpoint_e2e` | Upload HTTP réel du fichier `data/demo_portfolio.xlsx` via `POST /api/import/upload` |
| 21 | **Échéancier Dépassé** | `test_expired_installment_plan_becomes_overdue` | Un échéancier dont la date est dépassée ne protège plus la facture (redevient `ECHUE`) |
| 22 | **T5 Complet (File Revue)** | `test_t5_disputed_invoice_goes_to_human_review_queue` | Une facture contestée crée une tâche bloquée en file de revue humaine |
| 23 | **Historique Inconnu** | `test_unknown_client_history_flagged_for_human_review` | Client avec `0` facture antérieure -> mention `"inconnu"` et envoi en revue humaine |
| 24 | **Actions Humaines Complètes** | `test_human_review_modify_suspend_and_reject_actions` | Vérifie `MODIFIER` (texte édité), `SUSPENDRE` et `REJETER` (envoi interdit) |
| 25 | **Évaluation Fiche IA** | `test_ai_classifier_holdout_test_set_accuracy` | Évalue le modèle NLP sur un jeu de test séparé (12 phrases inédites) avec Accuracy >= 80 % |
