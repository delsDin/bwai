# Fiche IA — Classification des messages clients et routage vers la revue humaine

**Projet** : Recouvrement automatique des factures impayées (Cas pratique 1 — BWAI 2026)  
**Composant** : `services/ai_classifier.py` (`ClientMessageClassifier`)  
**Commande de reproduction de l'évaluation** : `python -m services.ai_classifier` ou `pytest tests/test_step8_ai_evaluation.py`

---

## 1. Modèle, version et justification du choix technique

- **Architecture** : Pipeline `scikit-learn` (>= 1.5) combinant :
  1. `TfidfVectorizer(ngram_range=(1, 2), lowercase=True, strip_accents="unicode")` — extraction des unigrammes et bigrammes normalisés (insensible aux accents omis dans les SMS/WhatsApp).
  2. `LogisticRegression(C=2.0, class_weight="balanced", random_state=42, max_iter=1000)` — classifieur linéaire probabiliste multi-classes à pondération équilibrée.
- **Justification métier et technique** :
  - **100 % hors-ligne et sans coût d'API** : adapté aux PME béninoises à connectivité variable (aucune dépendance cloud ni clé API externe).
  - **Déterministe et reproductible** (`random_state=42`) : temps d'entraînement < 50 ms, inférence < 1 ms par message.
  - **Séparation stricte Calculs / IA** : conformément au TDR, ce modèle **ne calcule aucun solde, aucun retard et ne décide d'aucun envoi**. Il sert exclusivement de filet de sécurité pour qualifier l'intention du dernier message client et orienter les dossiers sensibles ou incertains vers la **file de revue humaine**.

---

## 2. Tâche et définition des 4 classes

Le modèle classe chaque message client (SMS, WhatsApp, courriel) parmi 4 intentions métier :

| Classe | Définition métier | Action du moteur de relance |
|---|---|---|
| `LITIGE` | Contestation de la facture (montant, livraison incomplète, produit endommagé, prestation non terminée). | **Blocage automatique** → File de revue humaine |
| `DEMANDE_ECHEANCIER` | Reconnaissance de la dette avec demande d'étalement, d'acompte ou de report d'échéance. | **Blocage automatique** → File de revue humaine |
| `ANNONCE_PAIEMENT` | Annonce d'un règlement déjà effectué (Mobile Money, MTN MoMo, Moov/Flooz, virement, espèces) ou imminent. | **Blocage automatique** → File de revue humaine |
| `RETARD_SIMPLE` | Excuse ou reconnaissance d'oubli sans contestation ni demande d'échéancier. | Envoi autorisé **uniquement si** confiance $\ge 0{,}75$ (sinon revue humaine) |

---

## 3. Données et séparation stricte des jeux (Train / Hold-out Test)

Les données sont **100 % synthétiques**, rédigées manuellement en français avec les réalités du recouvrement au Bénin (montants en FCFA, MTN MoMo, Moov Money, Flooz, SBEE, Yango, livraisons, abréviations SMS, tournures locales et négations). Aucune donnée personnelle réelle n'est utilisée. La méthodologie complète est documentée dans `data/methode_generation_jeux.md`.

| Jeu de données | Fichier | Taille | Répartition par classe | Cas ambigus (`ambigu=oui`) | Chevauchement Train/Test |
|---|---|---|---|---|---|
| **Entraînement** | `data/train.csv` | **250** | `LITIGE`: 100, `DEMANDE_ECHEANCIER`: 50, `ANNONCE_PAIEMENT`: 50, `RETARD_SIMPLE`: 50 | 29 (11,6 %) | **0 message commun** (0,0 %) |
| **Test Hold-out** | `data/test.csv` | **100** | `ANNONCE_PAIEMENT`: 32, `DEMANDE_ECHEANCIER`: 26, `LITIGE`: 24, `RETARD_SIMPLE`: 18 | 12 (12,0 %) | **0 message commun** (0,0 %) |

Le jeu `data/test.csv` a été construit après le jeu d'entraînement avec des contextes métiers distincts (pharmacie, tailleur, traiteur, SBEE, coopérative, frais de scolarité) et des plages de montants différentes pour éviter toute fuite de données.

---

## 4. Méthode d'évaluation et résultats quantitatifs (IA seule vs Baseline)

### 4.1 Comparaison globale sur `data/test.csv` (100 messages inédits)

| Approche évaluée | Accuracy globale | F1-score Macro | F1-score Pondéré |
|---|---|---|---|
| Baseline déterministe par mots-clés | 36,00 % | 0,36 | 0,36 |
| **Modèle IA (`TF-IDF + LogisticRegression`)** | **89,00 %** (+53,0 pts) | **0,90** (+0,54) | **0,89** (+0,53) |

- **Accuracy sur messages standards (`ambigu=non`, 88 cas)** : **88,64 %** (78 / 88)
- **Accuracy sur messages ambigus (`ambigu=oui`, 12 cas)** : **91,67 %** (11 / 12)

### 4.2 Métriques détaillées par classe sur `data/test.csv`

| Classe | Précision | Rappel (Recall) | F1-score | Support (nb messages) |
|---|---|---|---|---|
| `LITIGE` | 0,7667 (76,7 %) | **0,9583 (95,8 %)** | 0,8519 | 24 |
| `DEMANDE_ECHEANCIER` | 0,9231 (92,3 %) | **0,9231 (92,3 %)** | 0,9231 | 26 |
| `ANNONCE_PAIEMENT` | 0,9259 (92,6 %) | 0,7812 (78,1 %) | 0,8475 | 32 |
| `RETARD_SIMPLE` | **1,0000 (100,0 %)** | 0,9444 (94,4 %) | 0,9714 | 18 |
| **Moyenne Macro** | **0,9039** | **0,9018** | **0,8985** | **100** |

### 4.3 Matrice de confusion sur `data/test.csv`

| Classe réelle \ Classe prédite | `LITIGE` | `DEMANDE_ECHEANCIER` | `ANNONCE_PAIEMENT` | `RETARD_SIMPLE` |
|---|---:|---:|---:|---:|
| **`LITIGE` (24)** | **23** | 1 | 0 | **0** |
| **`DEMANDE_ECHEANCIER` (26)** | 1 | **24** | 1 | **0** |
| **`ANNONCE_PAIEMENT` (32)** | 6 | 1 | **25** | **0** |
| **`RETARD_SIMPLE` (18)** | 0 | 0 | 1 | **17** |

---

## 5. Séparation des résultats IA, calibration du seuil et garanties Métier

Le TDR impose de présenter séparément la performance brute de l'IA, la calibration du seuil de confiance et la sécurité du parcours métier :

### 5.1 Règle de routage à 3 niveaux (`services/ai_classifier.py`)

Un dossier est envoyé en revue humaine obligatoire (`requiert_revue_humaine = True`) dès qu'au moins une des conditions suivantes est vraie :
1. **Détection hors-vocabulaire (`hors_vocabulaire = True`)** : si aucun terme ou bigramme du message client n'appartient au lexique d'entraînement (`nb_termes_reconnus == 0`, ex. message en langue locale fon/yoruba/mina ou argot inédit), le message est immédiatement routé en revue humaine.
2. **Catégorie sensible** : la classe prédite est `LITIGE`, `DEMANDE_ECHEANCIER` ou `ANNONCE_PAIEMENT`.
3. **Garde-fou lexical critique sur `RETARD_SIMPLE`** (`detect_critical_patterns`) : même si le modèle prédit `RETARD_SIMPLE`, toute présence d'une **négation** (*« ne … pas »*, *« jamais »*, *« aucun »*, *« pas reçu »*) ou d'un **montant / échéance chiffré** (*`25000`*, *`fcfa`*, *`tranches`*, *`fois`*) force la revue humaine.
4. **Seuil de confiance IA** : le score $\max_c P(c \mid x)$ est strictement inférieur au seuil configuré (`ai_confidence_threshold`).

### 5.1 bis Explicabilité locale (White-Box AI) et Reprise Humaine (`POST /api/invoices/{id}/ai-override`)

- **Attribution explicable par terme (`termes_explicatifs`)** : pour chaque message classé, le pipeline calcule en $< 0{,}2\text{ ms}$ la contribution linéaire exacte $w_{c,j} \times \text{TF-IDF}_j(x)$ de chaque mot/bigramme présent dans le message, ainsi que la distribution complète des probabilités (`probabilites_classes`) et la marge d'incertitude `marge_top1_top2 = P(c_1) - P(c_2)`.
- **Reprise humaine persistante** : via la fiche dossier (`POST /api/invoices/{invoice_id}/ai-override`), un opérateur humain peut confirmer ou corriger la qualification IA d'un message client ; la décision humaine (`ai_confiance = 1.0`) est préservée lors des recalculs ultérieurs et tracée dans le journal SHA-256 (`REPRISE_HUMAINE_QUALIFICATION_IA`).

### 5.2 Calibration du seuil : Courbe Couverture / Erreur sur `data/test.csv`

Plutôt que de choisir un seuil arbitraire, la méthode `calibrate_thresholds()` balaie la grille de seuils de `0.35` à `0.75` sur les 100 messages *hold-out* :

| Seuil $\tau$ | Couverture IA ($P_{\max} \ge \tau$) | Précision IA au-dessus de $\tau$ | Erreurs IA non filtrées ($P_{\max} \ge \tau$) | Erreurs IA interceptées ($P_{\max} < \tau$) | Taux de revue humaine Métier | Faux négatifs critiques envoyés auto |
|---:|---:|---:|---:|---:|---:|---:|
| `0.35` | 90,0 % (90/100) | 91,11 % | 8 / 11 | 3 / 11 | 87,0 % | **0** |
| `0.40` | 80,0 % (80/100) | 91,25 % | 7 / 11 | 4 / 11 | 88,0 % | **0** |
| `0.45` | 60,0 % (60/100) | 98,33 % | 1 / 11 | 10 / 11 | 88,0 % | **0** |
| **`0.50` (optimal IA)** | **50,0 % (50/100)** | **100,00 %** | **0 / 11** | **11 / 11 (100 %)** | **91,0 %** | **0** |
| `0.55` | 38,0 % (38/100) | 100,00 % | 0 / 11 | 11 / 11 (100 %) | 94,0 % | **0** |
| `0.65` | 12,0 % (12/100) | 100,00 % | 0 / 11 | 11 / 11 (100 %) | 98,0 % | **0** |
| **`0.75` (défaut prudent)** | **2,0 % (2/100)** | **100,00 %** | **0 / 11** | **11 / 11 (100 %)** | **100,0 %** | **0** |

**Justification mathématique du seuil** :
- La confiance maximale observée parmi les 11 erreurs du modèle sur `test.csv` est **`0.459`** (`T036`).
- Dès **$\tau = 0{,}50$**, la précision sur les prédictions au-dessus du seuil atteint **100,0 % (50/50)** avec **50 % de couverture IA** et **0 erreur non filtrée**.
- Le seuil par défaut configuré dans l'application (`0.75`) correspond à un **mode de prudence maximale** (marge de sécurité de $+0{,}25$ au-dessus du point d'erreur nulle `0.50`), modifiable par le gestionnaire dans les paramètres selon qu'il privilégie la revue systématique (`0.75`) ou le filtrage automatique des excuses simples (`0.50`).

### 5.3 Résultat sur la sécurité métier (0 faux négatif critique)

- Sur les **82 messages sensibles** du jeu de test (`24 LITIGE + 26 DEMANDE_ECHEANCIER + 32 ANNONCE_PAIEMENT`), **0 message** n'est prédit en `RETARD_SIMPLE` (dernière colonne de la matrice de confusion : `0, 0, 0`).
- **100 % des cas sensibles (82 / 82)** et **100 % des erreurs de classification (11 / 11)** sont routés vers un gestionnaire humain avant toute relance.

---

## 6. Analyse des erreurs observées (11 cas sur 100)

- **6 messages `ANNONCE_PAIEMENT` classés en `LITIGE`** (ex. `T055`, `T060`, `T061`, `T065`, `T072`, `T080`) : ces messages utilisent un vocabulaire inhabituel ou formel (*« je passe au cabinet demain avant midi avec le chèque »*, *« mon pharmacien doit encore me verser sa part »*). Le poids `class_weight="balanced"` combiné à la diversité lexicale de la classe `LITIGE` (100 exemples variés à l'entraînement) attire les phrases hors vocabulaire vers `LITIGE` avec une faible confiance ($0{,}34$ à $0{,}45$). C'est un comportement conservateur souhaitable en recouvrement : dans le doute, le dossier est bloqué pour vérification humaine.
- **1 message `LITIGE` classé en `DEMANDE_ECHEANCIER`** (`T010`, confiance `0.348`) : *« Les 15 sacs de ciment livrés sur le chantier sont en partie durcis. Je paierai après inspection par un expert »*. La structure *« Je paierai après… »* ressemble à une promesse de report, mais le faible score de confiance ($34{,}8\% < 75\%$) et la classe prédite maintiennent le dossier en revue humaine.

---

## 7. Limites connues et conditions d'expérimentation réelle

- **Corpus synthétique de taille modeste (350 messages au total)** : bien que contextualisé au Bénin, il ne remplace pas un corpus de messages WhatsApp/SMS réels annotés par plusieurs gestionnaires (calcul d'un accord inter-annotateurs Kappa de Cohen requis avant mise en production).
- **Modèle sac-de-mots (`TF-IDF 1-2 grammes`)** : sensible aux formulations inédites sans mots-clés partagés avec l'entraînement et limité sur les langues locales (fon, yoruba, mina) non représentées dans `train.csv`.
- **Calibration des probabilités** : sur 4 classes avec régression logistique régularisée, la probabilité maximale moyenne d'une bonne prédiction est de `0.52`. Le seuil `0.75` agit donc comme un filtre très prudent qui privilégie la validation humaine sur l'automatisation aveugle.

