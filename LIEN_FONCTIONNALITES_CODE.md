# Correspondance Complète entre les Fonctionnalités du TDR / Guide et le Code Source - BWAI 2026

Ce document établit le lien direct entre chaque exigence fonctionnelle du **TDR (Section 4 - Cas pratique 1)** et du **Guide (Modules A à L)**, les fichiers Python/SQL/HTML où elle est implémentée, les fonctions exactes appelées et la manière dont les composants s'intègrent entre eux.

---

## 1. Architecture Globale d'Intégration

Toutes les fonctionnalités suivent un flux en **4 couches** strictement séparées :

1. **Couche Données & Contraintes (`database/schema.sql` & `database/db.py`)** :
   Stocke localement en SQLite les 8 entités métier + les paramètres (`settings`), les erreurs d'import (`import_errors`) et le journal d'audit (`audit_logs`). Les contraintes `UNIQUE` au niveau SQL verrouillent physiquement les doublons.
2. **Couche Validation & Accès (`models/schemas.py` & `models/repositories.py`)** :
   Valide les entrées avec **Pydantic v2** avant toute écriture en base et exécute les requêtes SQL paramétrées pour alimenter les services et l'interface.
3. **Couche Cœur Métier & IA (`services/*.py`)** :
   Contient 100 % des règles déterministes (calculs, statuts, priorisation, séquence de relances, revue humaine, garde-fou pré-envoi, paiements simulés, reprise hors-ligne) et le classifieur NLP local (`scikit-learn`). Cette couche est indépendante de FastAPI, ce qui permet à `pytest` de la tester directement.
4. **Couche Exposition HTTP (`routes/api_routes.py`, `routes/web_routes.py`, `templates/*.html`)** :
   Expose les services via l'API REST FastAPI (`/api/...`, documentée sur `/docs`) et affiche le tableau de bord HTML/Jinja2 (`/`).

---

## 2. Tableau de Traçabilité : Fonctionnalité -> Fichier -> Fonction -> Test

| Module Guide / Exigence TDR | Fichier(s) Source | Fonction(s) / Classe(s) Clés | Route API / Web | Test(s) de Validation |
| :--- | :--- | :--- | :--- | :--- |
| **Module A** : Importation locale (CSV, Excel, Dict) | `services/importer.py`<br>`models/repositories.py` | `import_portfolio_file()`<br>`import_portfolio_dict()`<br>`_sanitize_records()` | `POST /api/import/upload`<br>`POST /api/system/reset-demo` | `test_excel_file_upload_endpoint_e2e` |
| **Module B** : Contrôle des données et rejet des lignes invalides / doublons | `models/schemas.py`<br>`services/importer.py` | `ClientImportSchema`<br>`InvoiceImportSchema`<br>`_record_import_error()` | `GET /api/import/errors` | `test_t12_ligne_import_invalide_et_doublon_non_integres_silencieusement` |
| **Module C** : Calcul déterministe du solde et des jours de retard | `services/calculator.py` | `compute_balance_and_delay()`<br>`recalculate_invoice()`<br>`recalculate_all_invoices()` | `POST /api/recalculate` | `test_t1_...`, `test_t2_...`, `test_t3_...`, `test_t4_...` |
| **Module D** : Qualification des 6 statuts métier des factures | `services/calculator.py` | `determine_invoice_status()` | `GET /api/invoices`<br>`GET /` | `test_t1` à `test_t6`, `test_expired_installment_plan_becomes_overdue` |
| **Module E** : Priorisation explicable (ancienneté, solde, historique) | `services/prioritizer.py` | `compute_priority_score()` | `GET /api/invoices`<br>`GET /` | `test_prioritizer_guide_formula`, `test_unknown_client_history_...` |
| **Brique IA** : Analyse NLP des messages clients et score de confiance | `services/ai_classifier.py` | `ClientMessageClassifier.classify()`<br>`classifier_singleton` | `POST /api/ai/classify` | `test_step2_ai.py` (5 tests) + `test_ai_classifier_holdout_test_set_accuracy` |
| **Module F** : Séquence graduée de relances sur canal simulé | `services/reminder_engine.py` | `determine_reminder_level()`<br>`prepare_reminder_for_invoice()` | `POST /api/reminders/prepare-all` | `test_step5_reminders_and_recovery.py` |
| **Module G** : Message personnalisé et mention Mobile Money fictive | `services/reminder_engine.py` | `build_personalized_message()` | `GET /api/reminders` | `test_t7_...`, `test_human_review_modify_...` |
| **Module H** : Validation humaine (litiges, gros montants, incertitudes IA) | `services/reminder_engine.py` | `prepare_reminder_for_invoice()`<br>`apply_human_review()` | `POST /api/review/{reminder_id}` | `test_t7_...`, `test_t5_disputed_invoice_...`, `test_human_review_modify_...` |
| **Module I** : Revérification juste avant l'envoi simulé (Garde-fou) | `services/reminder_engine.py` | `verify_and_simulate_send()` | `POST /api/reminders/{id}/send` | `test_t8_...`, `test_t11_relance_preparee_puis_facture_reglee_...` |
| **Module J** : Paiements simulés sans double comptabilisation | `services/payment_service.py`<br>`database/schema.sql` | `record_simulated_payment()`<br>`UNIQUE(invoice_id, reference_fictive)` | `POST /api/payments/simulate` | `test_t9_paiement_enregistre_deux_fois_...` |
| **Module K** : Travail hors connexion et reprise contrôlée sans doublon | `database/db.py`<br>`services/recovery_service.py` | `init_db()`<br>`resume_after_interruption()`<br>`UNIQUE(invoice_id, niveau_relance)` | `POST /api/system/recover` | `test_t10_double_clic_et_reprise_apres_interruption_sans_doublon` |
| **Module L** : Traçabilité et journal d'audit horodaté | `services/audit_logger.py`<br>`models/repositories.py` | `log_event()`<br>`list_audit_logs()` | `GET /api/audit-logs` | Vérifié transversalement dans les 25 tests |

---

## 3. Explication Détaillée de l'Intégration Code par Fonctionnalité

### 3.1. Modules A & B : Importation Locale et Contrôle des Données (`services/importer.py` + `models/schemas.py`)

* **Exigence du TDR** : *« Importer localement le portefeuille, contrôler les champs et doublons [...] Signaler les lignes invalides sans les intégrer silencieusement. »*
* **Comment c'est codé** :
  1. Lorsqu'un fichier Excel (`.xlsx`) ou CSV est envoyé à `POST /api/import/upload` (`routes/api_routes.py`), la route sauvegarde temporairement le fichier et appelle `import_portfolio_file(file_path)` dans `services/importer.py`.
  2. `pandas.read_excel(..., sheet_name=None)` lit chaque feuille (`Clients`, `Factures`, `Paiements`, `Avoirs`, `Echeanciers`, `Contestations`) et la fonction `_sanitize_records()` convertit proprement toutes les cellules vides (`NaN`) en `None`.
  3. Dans `import_portfolio_dict()`, chaque ligne est instanciée dans son modèle Pydantic (`ClientImportSchema`, `InvoiceImportSchema`, `PaymentImportSchema`, etc. définis dans `models/schemas.py`).
  4. **Trois barrières de contrôle** s'exécutent sur chaque ligne :
     * **Barrière 1 (Validation Pydantic)** : Vérifie que les identifiants ne sont pas vides, que `montant_initial > 0`, que `date_echeance >= date_emission` (`@model_validator`) et que `nb_retards_historiques <= nb_factures_historiques`.
     * **Barrière 2 (Anti-doublon SQL)** : Vérifie via un `SELECT 1` si la clé primaire (`client_id`, `invoice_id`, `payment_id`) ou le couple `(invoice_id, reference_fictive)` existe déjà.
     * **Barrière 3 (Intégrité référentielle)** : Vérifie que le `client_id` d'une facture existe bien dans `clients`, et que l'`invoice_id` d'un paiement/avoir/litige existe bien dans `invoices`.
  5. Si l'une des 3 barrières échoue, la ligne **n'est pas insérée** : la fonction `_record_import_error()` l'enregistre dans la table SQLite `import_errors` avec le numéro de ligne, les données brutes en JSON et l'explication en français.
  6. À la fin de l'import, `recalculate_all_invoices()` est appelé automatiquement pour mettre à jour tous les soldes, retards, statuts et scores, puis `log_event()` inscrit le bilan dans `audit_logs`.

---

### 3.2. Modules C & D : Calcul Déterministe et Qualification des 6 Statuts (`services/calculator.py`)

* **Exigence du TDR** : *« Calculer de façon déterministe les soldes et retards. Distinguer les factures non échues, échues, partiellement payées, soldées, contestées et couvertes par un échéancier. »*
* **Comment c'est codé** :
  1. `recalculate_invoice(invoice_id)` dans `services/calculator.py` lit la `reference_date` dans la table `settings` (par défaut `2026-03-15`), garantissant que deux exécutions donnent toujours exactement le même résultat.
  2. Elle calcule en SQL la somme des paiements validés (`SUM(montant) FROM payments WHERE statut = 'VALIDE'`) et la somme des avoirs (`SUM(montant) FROM credit_notes`).
  3. Elle appelle la fonction pure `compute_balance_and_delay()` :
     * `solde_restant = round(max(0.0, montant_initial - total_paye - total_avoirs), 2)`
     * Si `solde_restant <= 0.0`, alors `jours_retard = 0`. Sinon, `jours_retard = max(0, (reference_date - date_echeance).days)`.
  4. Elle vérifie en base s'il existe une contestation ouverte (`disputes WHERE statut = 'OUVERT'`) ou un échéancier actif dont la prochaine échéance n'est pas dépassée (`installment_plans WHERE statut = 'ACTIF' AND date_prochaine_echeance >= reference_date`).
  5. Elle appelle `determine_invoice_status()` qui applique l'arbre de décision déterministe suivant :
     * `solde_restant <= 0` -> **`SOLDEE`**
     * `has_open_dispute == True` -> **`CONTESTEE`**
     * `has_active_installment_plan == True` -> **`COUVERTE_PAR_ECHEANCIER`**
     * `total_paye + total_avoirs > 0` -> **`PARTIELLEMENT_PAYEE`**
     * `reference_date > date_echeance` -> **`ECHUE`**
     * Sinon -> **`NON_ECHUE`**
  6. La table `invoices` conserve simultanément `statut_calcule` (le statut métier parmi les 6) et `statut_paiement` (`IMPAYEE`, `PARTIELLEMENT_PAYEE`, `SOLDEE`), répondant à la note de conception de la page 4 du Guide sur le chevauchement entre facture échue et partiellement payée.

---

### 3.3. Module E : Priorisation Explicable (`services/prioritizer.py`)

* **Exigence du TDR** : *« Prioriser les dossiers selon l'ancienneté, le solde et l'historique de retard disponible. Une heuristique documentée suffit, sans prétendre prédire le non-paiement. »*
* **Comment c'est codé** :
  1. `recalculate_invoice()` appelle `compute_priority_score()` dans `services/prioritizer.py`.
  2. **Règle d'exclusion** : Si `statut_calcule` est `SOLDEE`, `CONTESTEE` ou `COUVERTE_PAR_ECHEANCIER`, la fonction retourne immédiatement `score = 0.0`, `eligible_relance = False` et l'explication `"Non eligible au classement de relance"`.
  3. **Normalisation entre 0 et 1** :
     * `score_retard = min(jours_retard / 60.0, 1.0)`
     * `score_solde = min(solde_restant / high_amount_threshold, 1.0)` (seuil de 500 000 FCFA)
     * `score_historique` : Si `nb_factures_historiques == 0`, le client n'a pas d'historique connu -> `score_historique = 0.50`, le drapeau `historique_inconnu = True` est levé et le libellé affiche `"inconnu (0.50 par defaut -> revue conseillee)"`. Sinon, `nb_retards_historiques / nb_factures_historiques`.
  4. **Combinaison pondérée** :
     * `score_total = round(0.40 * score_retard + 0.35 * score_solde + 0.25 * score_historique, 3)`
  5. Le score et la chaîne `explication_score` (qui détaille la contribution de chaque critère) sont stockés dans la table `invoices` et utilisés par `models/repositories.py` (`ORDER BY i.eligible_relance DESC, i.score_priorite DESC`) pour trier l'affichage.

---

### 3.4. Brique IA : Classification NLP Locale et Gestion de l'Incertitude (`services/ai_classifier.py`)

* **Exigence du TDR** : *« L'analyse de messages et la priorisation assistée par l'IA peuvent aider les équipes métiers, à condition de conserver des calculs vérifiables et des validations humaines. »* (Critère barème : 15 points + Fiche IA).
* **Comment c'est codé** :
  1. `ClientMessageClassifier` dans `services/ai_classifier.py` encapsule un pipeline `scikit-learn` (`TfidfVectorizer(ngram_range=(1, 2))` + `LogisticRegression(C=2.0, class_weight="balanced")`).
  2. Il s'entraîne localement en mémoire en moins de 50 millisecondes (100 % hors-ligne, aucune clé API externe).
  3. Lorsqu'une facture possède un `dernier_message_client` (ex: réponse WhatsApp ou SMS du client), `classify()` calcule les probabilités des 4 classes (`LITIGE`, `DEMANDE_ECHEANCIER`, `ANNONCE_PAIEMENT`, `RETARD_SIMPLE`) via `predict_proba()`.
  4. **Mécanisme de reprise humaine sur incertitude** :
     * Si la probabilité maximale `confiance < 0.75` (`DEFAULT_AI_CONFIDENCE_THRESHOLD`), la fonction renvoie `requiert_revue_humaine = True` avec le motif `"Cas incertain IA (confiance X% < seuil 75%)"`.
     * Si la classe prédite est `LITIGE`, `DEMANDE_ECHEANCIER` ou `ANNONCE_PAIEMENT`, elle renvoie également `requiert_revue_humaine = True` pour qu'un humain vérifie avant toute relance.
  5. Ce résultat est intégré à deux endroits : dans `recalculate_invoice()` (qui stocke `ai_categorie` et `ai_confiance` sur la facture pour l'afficher sur le tableau de bord) et dans `prepare_reminder_for_invoice()` (qui bloque la relance en file de revue humaine).

---

### 3.5. Modules F, G, H : Relances Graduées, Message Personnalisé et File de Revue Humaine (`services/reminder_engine.py`)

* **Exigence du TDR** : *« Paramétrer une séquence graduée de relances courtoises sur un canal simulé au choix [...] Préparer un message personnalisé avec référence, échéance et solde vérifiés. Un lien ou une référence mobile money doit être fictif et signalé comme tel. Transmettre les litiges, gros montants selon un seuil configuré et cas incertains à une personne avant toute suite. »*
* **Comment c'est codé** :
  1. `determine_reminder_level()` sélectionne le niveau selon l'ancienneté du retard :
     * `RAPPEL_PRE_ECHEANCE` (0 à 3 jours avant échéance),
     * `RELANCE_1` (1 à 14 jours de retard),
     * `RELANCE_2` (15 jours de retard et plus).
  2. `build_personalized_message()` assemble le texte courtois avec le nom du client, la référence de facture, la date d'échéance, le solde exact vérifié en FCFA et la mention obligatoire :
     `Reference Mobile Money fictive (SIMULATION UNIQUEMENT) : MOMO-DEMO-{invoice_id}. [...] Ceci est un message de demonstration.`
  3. `prepare_reminder_for_invoice()` évalue les **5 règles de mise en revue humaine** (`reasons_for_review`) :
     * Facture au statut `CONTESTEE` (Test T5),
     * `solde_restant >= 500 000 FCFA` (Test T7),
     * Message client signalé par la brique IA (`confiance < 0.75` ou intention sensible),
     * Historique client inconnu (`nb_factures_historiques == 0`),
     * Consentement absent ou canal incompatible (Test T8).
  4. Si au moins une raison est présente, la relance est insérée dans la table `reminders` avec `statut_action = 'EN_ATTENTE_VALIDATION'` et `requiert_revue_humaine = 1`. Sinon, elle est créée au statut `APPROUVEE`.
  5. Le gestionnaire humain utilise `apply_human_review()` (via `POST /api/review/{reminder_id}`) pour appliquer l'une des 4 décisions : `APPROUVER`, `MODIFIER` (qui remplace `contenu_message` par le texte édité), `SUSPENDRE` ou `REJETER`.

---

### 3.6. Module I : Garde-fou de Revérification Juste Avant l'Envoi Simulé (`services/reminder_engine.py`)

* **Exigence du TDR** : *« Revérifier le solde, les statuts, les préférences et les limites juste avant l'envoi simulé. Annuler une relance devenue injustifiée. »*
* **Comment c'est codé** :
  Dans `verify_and_simulate_send(reminder_id)` (appelée par `POST /api/reminders/{reminder_id}/send`) :
  1. **Contrôle anti-double envoi** : Si `statut_action == 'SIMULEE'`, retourne immédiatement `ALREADY_SENT` (`duplicate_prevented = True`, Test T10).
  2. **Recalcul en temps réel** : Appelle `recalculate_invoice(invoice_id)` juste avant l'envoi pour obtenir le solde et le statut à la seconde près.
  3. **Garde-fou A (Solde & Statut - Test T11)** : Si le nouveau solde est `<= 0` ou si le statut est devenu `SOLDEE`, `CONTESTEE` ou `COUVERTE_PAR_ECHEANCIER`, la relance passe automatiquement à `statut_action = 'ANNULEE'` en base et un événement `ANNULATION_PRE_ENVOI` est écrit dans `audit_logs`.
  4. **Garde-fou B (Préférence & Consentement - Test T8)** : Si `consentement_contact == 0` ou si le canal demandé diffère du `canal_prefere` du client, la relance passe à `ANNULEE` (`BLOCAGE_CANAL_INCOMPATIBLE`).
  5. **Garde-fou C (Validation humaine - Test T7)** : Si la relance est encore en `EN_ATTENTE_VALIDATION`, l'envoi est refusé (`PENDING_HUMAN_REVIEW`).
  6. **Garde-fou D (Plafond de relances)** : Vérifie que le nombre de relances déjà `SIMULEE` sur cette facture n'atteint pas `MAX_REMINDERS_PER_INVOICE` (3).
  7. Si les 4 garde-fous passent, la relance est marquée `SIMULEE` avec la date/heure d'envoi et journalisée.

---

### 3.7. Modules J, K, L : Paiements Simulés Idempotents, Reprise Hors-Ligne et Audit (`payment_service.py`, `recovery_service.py`, `audit_logger.py`)

* **Exigence du TDR** : *« Les reprises, doublons et traitements concurrents ne doivent produire ni double comptabilisation ni double action. Maintenir les données et la file de travail localement en cas de faible connectivité. Permettre une reprise contrôlée sans doublon, intégrer les paiements simulés et tracer les décisions, validations et changements de statut. »*
* **Comment c'est codé** :
  1. **Paiements simulés (`services/payment_service.py`)** :
     * `record_simulated_payment()` interroge d'abord `SELECT payment_id FROM payments WHERE invoice_id = ? AND reference_fictive = ?` (doublé par la contrainte SQL `UNIQUE(invoice_id, reference_fictive)` dans `database/schema.sql`).
     * Si la référence existe déjà, le paiement est ignoré (`DUPLICATE_IGNORED`), le solde n'est pas modifié et une trace `DOUBLON_PAIEMENT_BLOQUE` est ajoutée au journal (Test T9).
     * Si le paiement est nouveau, il est inséré, `recalculate_invoice()` met à jour le solde, et si le solde tombe à `0`, un `UPDATE reminders SET statut_action = 'ANNULEE'` annule immédiatement toutes les relances en attente sur cette facture (Test T11).
  2. **Reprise après interruption (`services/recovery_service.py`)** :
     * Toutes les données résident dans le fichier SQLite local `database/recouvrement.db`.
     * Après une coupure électrique ou un redémarrage, `resume_after_interruption()` recalcule les factures et relance `prepare_all_reminders()`. Grâce à la clé d'idempotence et à la contrainte SQL `UNIQUE(invoice_id, niveau_relance)` sur la table `reminders`, toutes les relances déjà créées avant la coupure sont détectées (`ALREADY_EXISTS`, `duplicate_prevented = True`) et seules les actions manquantes sont créées (Test T10).
  3. **Journal d'audit (`services/audit_logger.py`)** :
     * Chaque service appelle `log_event(acteur, action, objet_type, objet_id, ancienne_valeur, nouvelle_valeur, motif)` qui insère une ligne horodatée en UTC dans `audit_logs`, restituée sur le tableau de bord et via `GET /api/audit-logs`.

