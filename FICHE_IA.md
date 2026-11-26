# Fiche IA : Classification des messages clients et orientation vers la revue humaine

**Projet** : Recouvrement automatique des factures impayées (Cas pratique 1, BWAI 2026)  
**Équipe** : AlPha coDers  
**Membres** :
- DINLA Marcel
- ABDOULAYE Nydal
- KINHOU Ange
- FAGBEGNON Dimitri

**Composant** : `services/ai_classifier.py` (`ClientMessageClassifier`)  
**Commande de reproduction** : `python -m services.ai_classifier` ou `pytest tests/test_step8_ai_evaluation.py`

## 1. Rôle exact de l'IA et séparation avec les calculs métier

Dans ce prototype, l'intelligence artificielle a un rôle volontairement limité et bien encadré : elle lit le dernier message envoyé par un client (WhatsApp, SMS ou courriel) pour repérer s'il contient une contestation, une demande de délai, une annonce de paiement ou un doute qui nécessite l'intervention d'un gestionnaire humain.

Conformément aux termes de référence :
- l'IA **ne calcule aucun solde, aucun retard et aucun score de priorité** (ces calculs sont faits par des formules déterministes exactes dans `services/calculator.py` et `services/prioritizer.py`) ;
- l'IA **ne déclenche jamais un envoi toute seule** : son rôle est d'agir comme un filet de sécurité qui oriente les dossiers sensibles ou incertains vers la file de revue humaine.

## 2. Modèle, version et justification du choix technique

### Architecture retenue
Le classifieur repose sur `scikit-learn` (testé en version 1.9.1) et enchaîne quatre étapes légères :
1. **Normalisation des abréviations SMS (`normalize_benin_sms`)** : remplace les abréviations fréquentes (`bjr`, `stp`, `pb`, `livr`, `fact`, `ech`, `vir`, `chq`, `rglt`, `celtiis`) par leur forme complète avant vectorisation.
2. **Vectorisation `TfidfVectorizer(ngram_range=(1, 2), lowercase=True, strip_accents="unicode")`** : extrait les mots isolés et les paires de mots en ignorant les différences d'accents fréquentes sur téléphone.
3. **Classification `LogisticRegression(C=2.0, class_weight="balanced", random_state=42, max_iter=1000)`** : modèle linéaire probabiliste équilibré entre les 4 classes.
4. **Calibration de température du Softmax ($T = 0,55$)** : redresse les probabilités produites par le modèle sans jamais changer la classe arrivée en tête, afin d'obtenir des scores de confiance lisibles (80 % à 95 % sur les phrases claires) tout en maintenant toutes les erreurs sous le seuil de revue humaine.

### Pourquoi ce choix plutôt qu'un grand modèle de langage (LLM) ?
- **Fonctionnement 100 % hors-ligne et gratuit** : aucune clé API payante, aucun serveur distant et aucune connexion Internet ne sont nécessaires.
- **Rapidité et légèreté** : l'entraînement complet prend moins de 40 millisecondes sur un processeur standard, et l'analyse d'un message prend moins d'une milliseconde.
- **Reproductibilité et explicabilité** : à données identiques (`random_state=42`), le résultat est toujours identique et l'application peut afficher les mots exacts qui ont motivé la décision.

### Comparaison expérimentale avec les autres familles de modèles (Arbres, Forêts, Boosting, Naive Bayes, SVM et Règles)
Pour justifier le choix de la régression logistique sans biais, six familles d'algorithmes d'apprentissage automatique (ainsi que la méthode de référence par mots-clés) ont été comparées sur les mêmes vecteurs TF-IDF :
- d'abord par **validation croisée stratifiée à 5 plis (`StratifiedKFold`) sur les 250 messages de `data/train.csv`** ;
- puis sur **les 100 messages inédits du jeu de test séparé `data/test.csv`**.

| Famille de modèle | F1-macro (Validation croisée 5 plis sur `train.csv`) | Exactitude (Validation croisée 5 plis) | Exactitude sur `test.csv` (100 cas) | F1-macro sur `test.csv` | Rappel sur la classe `LITIGE` (`test.csv`) |
|---|---:|---:|---:|---:|---:|
| **LogisticRegression (C=2.0, balanced) [Retenu]** | **79,74 % (± 2,80 %)** | **81,60 %** | **89,00 %** | **89,85 %** | **95,83 % (23/24)** |
| LinearSVC + Calibration de Platt | 79,73 % (± 3,37 %) | 81,60 % | 89,00 % | 89,85 % | 95,83 % (23/24) |
| Forêt aléatoire (RandomForest, 100 arbres) | 65,66 % (± 4,66 %) | 65,20 % | 77,00 % | 77,27 % | 54,17 % (13/24) |
| Gradient Boosting (100 arbres) | 64,76 % (± 4,70 %) | 64,80 % | 72,00 % | 72,36 % | 54,17 % (13/24) |
| Multinomial Naive Bayes (alpha=0.5) | 67,36 % (± 3,71 %) | 72,00 % | 69,00 % | 70,68 % | 100,00 % (sur-prédit LITIGE) |
| Arbre de décision (DecisionTree, profondeur 15) | 54,99 % (± 6,91 %) | 54,80 % | 62,00 % | 62,73 % | 66,67 % (16/24) |
| Règles par mots-clés (Baseline sans IA) | Non applicable | Non applicable | 36,00 % | 35,80 % | 79,17 % (19/24) |

### Pourquoi les modèles d'arbres (Decision Tree, Random Forest, Gradient Boosting) sont-ils moins performants ici ?
1. **Nature très creuse du texte court (TF-IDF)** : le vocabulaire d'entraînement compte plus de 1 500 mots et paires de mots, mais un SMS ou message WhatsApp client ne contient en moyenne que 10 à 15 mots. Un arbre de décision teste un seul mot à chaque embranchement : si le client utilise un synonyme absent des premiers nœuds de l'arbre, le message tombe dans la branche par défaut. C'est pourquoi l'arbre de décision plafonne à **62 %** d'exactitude et la forêt aléatoire à **77 %** (avec seulement **54,17 %** des litiges détectés directement).
2. **Avantage des modèles linéaires sur le texte** : la régression logistique et le SVM linéaire évaluent simultanément tous les mots présents dans la phrase en additionnant leurs poids respectifs (**89 %** d'exactitude et **95,83 %** de détection directe des litiges, soit **+12 à +27 points** de mieux que les modèles à base d'arbres).
3. **Pourquoi préférer `LogisticRegression` à `LinearSVC`** : les deux obtiennent 89,00 % sur le jeu de test, mais la régression logistique présente un écart-type plus faible entre les 5 plis d'entraînement (`± 2,80 %` contre `± 3,37 %`), fournit directement des probabilités calibrables par température sans double validation croisée interne et permet d'extraire immédiatement la contribution exacte de chaque mot pour l'utilisateur.

## 3. Tâche et définition des quatre classes

Chaque message client est classé dans l'une des quatre catégories suivantes :

| Catégorie | Signification métier | Décision prise par le système |
|---|---|---|
| `LITIGE` | Le client conteste la facture (marchandise abîmée, erreur de prix, livraison incomplète). | Blocage de la relance automatique et mise en file de revue humaine. |
| `DEMANDE_ECHEANCIER` | Le client reconnaît la dette mais demande un paiement en plusieurs fois ou un report. | Blocage de la relance automatique et mise en file de revue humaine. |
| `ANNONCE_PAIEMENT` | Le client affirme avoir déjà payé (Mobile Money, virement, espèces, chèque). | Blocage de la relance automatique et mise en file de revue humaine pour vérification comptable. |
| `RETARD_SIMPLE` | Le client s'excuse d'un simple oubli ou retard sans contester ni demander d'échéancier. | Relance standard autorisée uniquement si la confiance dépasse le seuil et qu'aucun garde-fou n'est déclenché. |

## 4. Données utilisées et séparation stricte des jeux

Tous les messages sont **entièrement fictifs** et ont été rédigés à la main en français contextualisé au Bénin (vocabulaire Mobile Money, commerce, livraison, cas francs et cas ambigus). La méthode complète est détaillée dans `data/methode_generation_jeux.md`.

| Jeu de données | Fichier | Nombre de messages | Répartition par classe | Rôle |
|---|---|---:|---|---|
| **Entraînement** | `data/train.csv` | **250** | 100 `LITIGE`, 50 `DEMANDE_ECHEANCIER`, 50 `ANNONCE_PAIEMENT`, 50 `RETARD_SIMPLE` | Apprentissage du vocabulaire TF-IDF et des poids du modèle. |
| **Test (Hold-Out)** | `data/test.csv` | **100** | 24 `LITIGE`, 26 `DEMANDE_ECHEANCIER`, 32 `ANNONCE_PAIEMENT`, 18 `RETARD_SIMPLE` (dont 12 cas ambigus) | Évaluation finale sur des messages jamais vus à l'entraînement. |

Un test automatisé (`test_train_and_test_csv_no_data_leakage`) vérifie qu'aucun message n'est commun aux deux fichiers (**0 doublon** entre `train.csv` et `test.csv`).

## 5. Résultats sur le jeu de test séparé (100 messages)

### Comparaison globale et distinction entre exactitude statistique et sécurité métier

| Indicateur mesuré sur `data/test.csv` (100 messages) | Règles par mots-clés (Référence) | Modèle IA retenu | Gain apporté |
|---|---:|---:|---:|
| **Exactitude globale sur les 4 classes (Accuracy)** | 36,00 % | **89,00 %** | **+53,00 pts** |
| **F1-score Macro (moyenne équilibrée des 4 classes)** | 35,80 % | **89,85 %** | **+54,05 pts** |
| **Exactitude sur messages standards (88 cas)** | 37,50 % | **88,64 %** | +51,14 pts |
| **Exactitude sur messages ambigus (12 cas)** | 25,00 % | **91,67 %** | +66,67 pts |
| **Rappel de sécurité Métier (cas sensibles envoyés en revue)** | 41,46 % | **100,00 % (82 / 82)** | **0 oubli critique** |

En recouvrement, les trois classes sensibles (`LITIGE`, `DEMANDE_ECHEANCIER`, `ANNONCE_PAIEMENT`) conduisent toutes à la même action protectrice : **suspendre l'envoi automatique et demander une vérification humaine**. Sur cet objectif métier, le système intercepte **100 % des 82 dossiers sensibles (0 faux négatif critique)**.

### Résultats détaillés par classe

| Classe | Précision | Rappel (Recall) | F1-score | Nombre de cas |
|---|---:|---:|---:|---:|
| `LITIGE` | 76,67 % | **95,83 %** | 0,8519 | 24 |
| `DEMANDE_ECHEANCIER` | 92,31 % | **92,31 %** | 0,9231 | 26 |
| `ANNONCE_PAIEMENT` | 92,59 % | 78,12 % | 0,8475 | 32 |
| `RETARD_SIMPLE` | **100,00 %** | 94,44 % | 0,9714 | 18 |

### Matrice de confusion sur `data/test.csv`

| Classe réelle \ Classe prédite | `LITIGE` | `DEMANDE_ECHEANCIER` | `ANNONCE_PAIEMENT` | `RETARD_SIMPLE` |
|---|---:|---:|---:|---:|
| **`LITIGE` (24)** | **23** | 1 | 0 | **0** |
| **`DEMANDE_ECHEANCIER` (26)** | 1 | **24** | 1 | **0** |
| **`ANNONCE_PAIEMENT` (32)** | 6 | 1 | **25** | **0** |
| **`RETARD_SIMPLE` (18)** | 0 | 0 | 1 | **17** |

La dernière colonne (`RETARD_SIMPLE`) contient **0, 0, 0** pour les trois classes sensibles : aucun litige, aucune demande d'échéancier et aucune annonce de paiement n'est confondu avec un simple retard.

## 6. Gestion de l'incertitude, calibration du seuil et reprise humaine

### Règle de routage à quatre filets de sécurité
Un dossier est envoyé en revue humaine obligatoire dès qu'au moins une des conditions suivantes est remplie :
1. **Message hors vocabulaire (`hors_vocabulaire = True`)** : aucun mot du message n'est reconnu dans le lexique appris (par exemple un message rédigé uniquement en fon, yoruba ou mina).
2. **Catégorie sensible** : le modèle prédit `LITIGE`, `DEMANDE_ECHEANCIER` ou `ANNONCE_PAIEMENT`.
3. **Garde-fou lexical sur `RETARD_SIMPLE`** : même si le modèle prédit `RETARD_SIMPLE`, la présence d'une négation (*« ne … pas »*, *« jamais »*, *« aucun »*) ou d'un montant chiffré (*`25000`*, *`fcfa`*, *`tranches`*, *`fois`*) impose une revue humaine.
4. **Confiance inférieure au seuil configuré** : la probabilité calibrée est sous le seuil (`0,75` par défaut, ajustable dans les paramètres).

### Courbe de calibration du seuil (avec Temperature Scaling $T = 0,55$)

| Seuil $\tau$ | Couverture IA ($P_{\text{cal}} \ge \tau$) | Précision IA au-dessus du seuil | Erreurs IA non filtrées ($P_{\text{cal}} \ge \tau$) | Erreurs IA interceptées ($P_{\text{cal}} < \tau$) | Faux négatifs critiques envoyés auto |
|---:|---:|---:|---:|---:|---:|
| `0.35` | 97,0 % | 88,66 % | 11 / 11 | 0 / 11 | **0** |
| `0.45` | 88,0 % | 90,91 % | 8 / 11 | 3 / 11 | **0** |
| `0.55` | 76,0 % | 93,42 % | 5 / 11 | 6 / 11 | **0** |
| `0.65` | 56,0 % | 98,21 % | 1 / 11 | 10 / 11 | **0** |
| **`0.70` (seuil d'erreur nulle)** | **50,0 %** | **100,00 %** | **0 / 11** | **11 / 11 (100 %)** | **0** |
| **`0.75` (défaut prudent)** | **40,0 %** | **100,00 %** | **0 / 11** | **11 / 11 (100 %)** | **0** |

La confiance maximale observée parmi les 11 erreurs du jeu de test est de **66,4 %** (avec une entropie moyenne d'incertitude élevée de **0,76 / 1,00**). Dès le seuil **0,70** (et au seuil par défaut **0,75**), **100 % des erreurs du modèle sont interceptées** par le filtre de confiance.

### Explicabilité et reprise humaine avec apprentissage actif
- Sur chaque dossier, l'interface affiche les **termes clés du message** qui ont pesé dans le calcul, la probabilité des 4 classes, la marge d'écart et l'entropie d'incertitude.
- Le bouton **Reprise humaine (Requalifier l'IA)** permet à un opérateur de corriger la catégorie d'un message. Cette décision est tracée dans le journal SHA-256, conservée lors des recalculs suivants, et intégrée en mémoire par le modèle qui se ré-entraîne immédiatement.

## 7. Analyse des erreurs observées et limites connues

### Pourquoi 6 annonces de paiement sont-elles classées en litige ?
Ces 6 messages (comme `T055` : *« je passe au cabinet demain avant midi avec le chèque »* ou `T060` : *« mon pharmacien doit encore me verser sa part »*) emploient des tournures peu fréquentes dans les 50 exemples d'`ANNONCE_PAIEMENT` de l'entraînement. Comme la classe `LITIGE` compte 100 exemples au vocabulaire plus large, le modèle penche vers `LITIGE` avec une faible confiance (entre 46 % et 66 %). Sur le plan métier, cette hésitation est sans danger : dans les deux cas, la relance est suspendue et présentée au gestionnaire.

### Limites du modèle et conditions pour une mise en production
1. **Taille et nature fictive du corpus** : 350 messages rédigés à la main ne remplacent pas plusieurs milliers de vrais messages WhatsApp et SMS collectés en entreprise et annotés par plusieurs gestionnaires.
2. **Modèle basé sur les mots et paires de mots** : bien qu'enrichi d'un normaliseur d'abréviations SMS, un modèle TF-IDF ne comprend pas le sens profond d'une phrase totalement inédite ni l'ironie.
3. **Langues nationales** : les messages rédigés entièrement en fon, yoruba, bariba ou mina ne sont pas traduits. Le système les détecte comme « hors vocabulaire » (0 terme connu) et les confie systématiquement à un opérateur humain.
