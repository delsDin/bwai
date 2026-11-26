# Analyse du backend et notation (TDR BWAI 2026, partie 8)

Périmètre : `recouvrement_bwai/` (Cas 1 : recouvrement des factures impayées).
Méthode : lecture du TDR (sections 4 et 8), lecture du code (services, routes, schéma, tests) et exécution de la suite de tests (25 tests, tous verts). L'application n'a pas été exécutée de bout en bout.

---

## 1. Couverture du socle obligatoire (section 4 du TDR)

| Exigence | État | Preuve / remarque |
|---|---|---|
| Import local, contrôle des champs et doublons | ✅ | `services/importer.py` : validation Pydantic, table `import_errors`, rejet des doublons et clients inconnus |
| Soldes et retards déterministes | ✅ | `services/calculator.py` |
| 6 statuts (non échue, échue, partiellement payée, soldée, contestée, échéancier) | ✅ | `CHECK` dans `schema.sql`, `determine_invoice_status` |
| Priorisation par heuristique documentée | ✅ | `services/prioritizer.py` : 0,40 retard + 0,35 solde + 0,25 historique, avec explication |
| Séquence graduée de relances, canal simulé | ✅ | Pré-échéance, `RELANCE_1`, `RELANCE_2` |
| Message personnalisé, référence Mobile Money fictive signalée | ✅ | `build_personalized_message` |
| Escalade humaine (litiges, gros montants, cas incertains) | ✅ | Seuil 500 000 FCFA, confiance IA < 0,75, historique inconnu, consentement absent |
| Revérification juste avant envoi, annulation si injustifiée | ✅ | `verify_and_simulate_send` |
| Pas de double comptabilisation ni double action | ✅ en séquentiel | Clés `UNIQUE`, anti-doublon de paiement, `ALREADY_SENT` |
| Fonctionnement local, reprise contrôlée | ✅ | SQLite, `resume_after_interruption` |
| Journal des décisions | ✅ | `audit_logs` |
| Tableau de bord | ⚠️ | Templates présents, non évalués en détail ; `static/css` et `static/js` vides |

**Conclusion : le socle fonctionnel est intégré.** Les écarts portent sur la robustesse, la sécurité, l'évaluation de l'IA et les livrables.

---

## 2. Notation selon la grille de 100 points

| Critère | Max | Note | Mérite | Manque |
|---|---|---|---|---|
| Utilité et parcours métier | 15 | **12** | Parcours complet : import → calcul → priorisation → relance → revue humaine → envoi simulé → paiement → journal. Démontrable via Swagger. Cas d'usage fidèle au TDR. | Parcours peu visible côté interface (CSS/JS vides). Pas de scénario de démonstration scripté. Pas de compte rendu de gain de temps. |
| Fiabilité métier | 25 | **17** | Calculs déterministes, date de référence paramétrable, statuts contraints en base. Clés d'unicité (`(invoice_id, reference_fictive)`, `(invoice_id, niveau_relance)`, `idempotency_key`). Gardes avant envoi complets. | Concurrence non gérée (transactions séparées, pas de `BEGIN IMMEDIATE`, WAL ni `busy_timeout`). `MIN_DAYS_BETWEEN_REMINDERS` défini mais jamais appliqué. Surpaiement masqué par `max(0, …)`. Date de paiement par défaut = constante, pas le réglage. Montants en `REAL`. `PARTIELLEMENT_PAYEE` ambigu. Aucun test concurrent. |
| Qualité de l'automatisation | 15 | **8** | IA bien ciblée (aide à la revue, ne décide rien). Seuil de confiance paramétrable. Tout litige ou paiement annoncé va en revue humaine. Fonctionne hors ligne. | Classifieur entraîné sur 19 phrases. Aucune évaluation mesurée (pas de jeu de test, précision, rappel ni matrice de confusion). Seuil 0,75 non calibré. Fiche IA absente. Pas de justification du choix technique face à des règles à mots-clés. |
| Sécurité et maîtrise des actions | 15 | **6** | Requêtes SQL paramétrées. Pas de secrets dans le code. Données fictives. Validations humaines sur les cas sensibles. | Aucune authentification ni autorisation (reset, paiements, approbations ouverts). Acteur codé en dur (« GESTIONNAIRE », « SYSTEME »). Pas de rôles ni de séparation des tâches. Journal modifiable (`UPDATE`/`DELETE` possibles). |
| Qualité technique et reproductibilité | 20 | **13** | Architecture claire (routes / services / models / database). 25 tests verts couvrant T1 à T12. Calculs reproductibles. Journalisation utile. | Pas de README. `requirements.txt` non épinglé, avec `httpx2` suspect et `pytest` en dépendance de production. Pas de dépôt git ni empreinte de commit. Base SQLite dans l'arbre du code. Pas de test de concurrence ni de reprise après coupure réelle. |
| Usage et présentation | 10 | **5** | Tableau de bord et page de détail facture. Descriptions Swagger. Documents internes d'étapes et de liaison fonctionnalités/code. | Pas de rapport (3 à 5 pages), pas de fiche IA, pas de vidéo, pas de comparaison à une méthode de référence, limites non formulées. |
| **Total** | **100** | **≈ 61** | | |

---

## 3. Détail des constats techniques

### Fiabilité
- `verify_and_simulate_send` lit le statut, puis recalcule, puis écrit dans trois transactions distinctes. Deux appels simultanés peuvent tous deux passer le contrôle `SIMULEE`.
- Les `UNIQUE` protègent l'insertion, mais l'`IntegrityError` n'est pas traduite en réponse métier.
- Le surpaiement est silencieux : le solde est ramené à 0 sans alerte ni avoir proposé.
- `record_simulated_payment` utilise `config.DEFAULT_REFERENCE_DATE` et non le réglage `reference_date` de la base.

### IA
- Corpus de 19 exemples, 4 classes, vectorisation TF-IDF 1-2 grammes, régression logistique.
- Les probabilités d'un modèle aussi peu entraîné ne sont pas fiables. Un seuil fixe donne une fausse impression de maîtrise.

### Sécurité
- Aucune dépendance d'authentification dans `routes/`. Chaque route est publique.
- L'`acteur` dans le journal est une chaîne constante : la piste d'audit ne dit pas qui a validé.

### Reproductibilité
- Pas de README, donc ni installation, ni lancement, ni scénarios documentés.
- Dépendances sans versions figées, résultats potentiellement différents selon l'environnement.

---

## 4. Suggestions pour se démarquer si toutes les équipes sont à égalité

Hypothèse : toutes les équipes livrent le socle, avec un tableau de bord correct et un classifieur simple. Le jury départagera sur la **preuve** (mesures, tests adverses) et sur la **maîtrise des cas limites**. Les suggestions sont classées par rapport effet/effort.

### A. Prouver la fiabilité (critère à 25 points, le plus lourd)
1. **Test de concurrence démontré en direct** : lancer 20 threads sur le même envoi ou le même paiement, afficher « 1 envoi, 19 doublons bloqués ». C'est exactement ce que le TDR demande et ce que peu d'équipes prouveront.
2. **Envoi atomique** : `UPDATE reminders SET statut_action='SIMULEE' WHERE reminder_id=? AND statut_action='APPROUVEE'` suivi d'un contrôle de `rowcount`, avec WAL et `busy_timeout`.
3. **Montants entiers en FCFA** (pas de flottants), et alerte de surpaiement avec proposition d'avoir.
4. **Tests de propriétés** (Hypothesis) : le solde n'est jamais négatif sans alerte, rejouer deux fois la même séquence donne le même état final, l'ordre des paiements n'a pas d'effet.
5. **Rejeu déterministe** : une commande qui rejoue tout le portefeuille à une date de référence donnée et produit un hachage de l'état final, identique à chaque exécution.
6. **Respect du délai minimal entre relances** et du plafond, avec journalisation du motif de suspension (action « suspendue » à afficher au tableau de bord).

### B. Rendre l'IA mesurable (critère à 15 points)
1. **Jeu de test séparé** (60 à 100 messages en français, y compris expressions en fon ou yoruba translittérées, SMS abrégés, fautes, mélange français et langues locales), avec précision, rappel et matrice de confusion.
2. **Comparaison à une base simple** (règles à mots-clés) pour **justifier** l'apport de l'IA, ou admettre qu'elle ne l'améliore pas : cela rapporte des points de rigueur.
3. **Calibration du seuil** : courbe couverture / erreur pour choisir le seuil de revue, et pas 0,75 arbitraire.
4. **Cas critiques traités à part** : tout message avec négation (« je ne conteste pas ») ou montant promis passe en revue humaine. Un taux de faux négatifs sur les litiges égal à 0 sur le jeu de test est un argument fort.
5. **Fiche IA d'une page** : modèle, version, données, séparation, erreurs observées, limites. Résultats métier et IA présentés séparément, comme l'exige le TDR.

### C. Sécurité et maîtrise (critère à 15 points)
1. **Authentification légère avec 3 rôles** (gestionnaire, validateur, administrateur), jetons simples, acteur réel dans le journal.
2. **Quatre yeux** : celui qui prépare une relance de gros montant ne peut pas l'approuver.
3. **Journal infalsifiable** : hachage chaîné de chaque événement et triggers SQLite interdisant `UPDATE` et `DELETE` sur `audit_logs`. Un endpoint `/audit/verify` prouve l'intégrité.
4. **Masquage des contacts** dans les réponses et les journaux, minimisation des données.
5. **Mode « tout est bloqué »** (interrupteur d'arrêt d'urgence) qui suspend tout envoi simulé.

### D. Reproductibilité et technique (critère à 20 points)
1. **README complet** : installation en une commande, lancement, comptes de test, reproduction de chaque scénario.
2. **`make demo` / `make test`** (ou script équivalent) : réinitialisation des données, rejeu des scénarios, rapport de tests.
3. **Dépendances épinglées** (`requirements.lock`), déclaration des licences et des composants préexistants.
4. **Test de reprise réel** : tuer le processus au milieu d'un lot, relancer, démontrer l'absence de doublon.
5. **Tests de bout en bout du tableau de bord** et couverture de code affichée.
6. **Versionnage git** avec étiquette du livrable gelé.

### E. Utilité et présentation (critères à 15 + 10 points)
1. **Chiffrer le gain** : comparer à une méthode de référence (relance manuelle sur Excel) sur le même portefeuille : temps de traitement, relances erronées évitées (facture déjà payée, litige ignoré).
2. **Explicabilité à un clic** : pour chaque créance, l'écran montre pièces, événements, calcul du score et règles déclenchées.
3. **Scénario de démonstration scripté de 3 minutes** couvrant : facture déjà payée entre-temps, litige détecté, gros montant, doublon de paiement, coupure puis reprise.
4. **Section « limites et coûts »** dans le rapport : franchise sur ce que le prototype ne fait pas, coût estimé d'usage, étapes d'une expérimentation réelle.
5. **Adaptation locale** : messages SMS courts, rappels en WhatsApp avec modèle de lien Mobile Money fictif, fonctionnement dégradé hors ligne.

---

## 5. Plan d'action recommandé (ordre de priorité)

| # | Action | Gain estimé | Effort |
|---|---|---|---|
| 1 | Envoi et paiement atomiques + test de concurrence | +4 à 5 | Moyen |
| 2 | Authentification, rôles, acteur réel, quatre yeux | +4 à 5 | Moyen |
| 3 | Évaluation de l'IA, comparaison à une base simple, fiche IA | +4 | Moyen |
| 4 | README, dépendances épinglées, script de démonstration | +4 | Faible |
| 5 | Journal à hachage chaîné | +2 | Faible |
| 6 | Délai minimal entre relances, surpaiement, montants entiers | +2 | Faible |
| 7 | Rapport, comparaison à la référence, vidéo | +3 | Moyen |

Objectif réaliste : passer d'environ 61 à environ 85 points, avec une différenciation principalement sur la **preuve de fiabilité** (concurrence, rejeu, journal vérifiable) et la **mesure de l'IA**.
