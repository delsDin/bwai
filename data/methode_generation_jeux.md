# Méthode de génération des jeux de données

**Équipe** : AlPha coDers (DINLA Marcel, ABDOULAYE Nydal, KINHOU Ange, FAGBEGNON Dimitri)

Ce document décrit comment ont été produits les deux fichiers CSV de classification de messages clients de recouvrement (contexte béninois) :

- `jeu_entrainement_recouvrement_benin.csv` : 250 messages, destinés à l'entraînement.
- `jeu_test_holdout_recouvrement_benin.csv` : 100 messages, destinés à l'évaluation finale.

## 1. Principe général

Les deux jeux sont **entièrement synthétiques**. Aucun message réel de client, de recouvreur ou d'entreprise n'a été utilisé. Chaque message a été rédigé à la main, en français, pour illustrer une classe donnée dans un contexte donné.

Il n'y a eu ni script de génération, ni modèle de langage appelé pour produire les messages, ni tirage aléatoire de phrases. La cohérence vient de la rédaction et de la relecture, pas d'un procédé automatique.

## 2. Format commun

Les deux fichiers partagent le même schéma :

| Colonne | Contenu |
|---|---|
| `id` | Identifiant séquentiel. `001` à `250` pour l'entraînement, `T001` à `T100` pour le test. |
| `message` | Texte du message client, entre guillemets doubles pour échapper les virgules. |
| `classe` | Une des quatre étiquettes : `LITIGE`, `DEMANDE_ECHEANCIER`, `ANNONCE_PAIEMENT`, `RETARD_SIMPLE`. |
| `ambigu` | `oui` si le message peut raisonnablement appartenir à plusieurs classes, `non` sinon. |

Encodage : UTF-8. Séparateur : virgule.

## 3. Définition des classes utilisées

Les étiquettes suivent la définition suivante, appliquée de façon uniforme aux deux jeux :

- **LITIGE** : le client conteste la dette elle-même (montant, existence, qualité du produit ou du service, erreur de facturation) et le paiement est suspendu à la résolution de ce désaccord.
- **DEMANDE_ECHEANCIER** : le client reconnaît la dette, en tout ou partie, et demande un étalement, un report ou un plan de versements.
- **ANNONCE_PAIEMENT** : le client annonce un paiement effectué, en cours ou prévu à une date précise.
- **RETARD_SIMPLE** : le client reconnaît un retard, s'excuse, sans contestation ni demande de plan, et promet de régulariser.

Quand un message mélange deux intentions (par exemple un retard reconnu avec une contestation de pénalité), la règle retenue est : **la contestation prime sur le retard**, donc le message est étiqueté `LITIGE` et marqué `ambigu=oui`.

## 4. Jeu d'entraînement (250 messages)

### 4.1 Processus

1. **Définition des quatre classes** et de leurs frontières (section 3).
2. **Rédaction par blocs de classe** : 100 messages `LITIGE`, 50 `DEMANDE_ECHEANCIER`, 50 `ANNONCE_PAIEMENT`, 50 `RETARD_SIMPLE`.
3. **Variation intentionnelle** au sein de chaque bloc :
   - Registre : SMS abrégés (`bjr`, `svp`, `pardon`), messages standard, quelques phrases en créole béninois (ex. « Kon fè pou mwen peye an 3 fwa ? »).
   - Moyens de paiement : MoMo, Moov Money, Flooz, MTN, virement, espèces à un agent, chèque.
   - Fautes de frappe et d'accents, parfois volontaires (« d accord », « tous », « paie »).
   - Négations, pour tester la compréhension de la portée : « je ne conteste pas la somme mais… », « je ne nie pas la dette, je demande un audit ».
   - Cas limites : promesses vagues (« je paie dès que la boutique vend »), retard reconnu avec contestation, demande de plan avec contestation de pénalité.
4. **Attribution de `ambigu`** : marqué `oui` pour les messages où une seconde lecture est plausible. 27 messages sur 250 sont marqués ambigus.
5. **Relecture globale** : vérification des doublons, de la cohérence des étiquettes et de la présence de chaque registre.

### 4.2 Contexte et montants

Les montants sont variés (de 500 à 60000 FCFA environ), les contextes incluent livraisons, réparations, écolage, ventes à crédit, tontines, petits commerces. Les messages reprennent le vocabulaire courant du recouvrement au Bénin.

## 5. Jeu de test hold-out (100 messages)

### 5.1 Objectif

Le jeu de test doit mesurer la capacité d'un modèle à généraliser à des formulations qu'il n'a pas vues. Il a été conçu pour s'écarter du jeu d'entraînement sur plusieurs axes.

### 5.2 Processus

1. **Rédaction après l'entraînement**, avec une consigne explicite de s'écarter du style du premier jeu.
2. **Différences volontaires** :
   - **Contextes** : tailleur, traiteur, pharmacie, SBEE (électricité), garage, dépanneur, cabinet de formation, coopérative agricole, frais de scolarité, location de véhicule, déplacement à Abomey.
   - **Montants** : plage distincte (4800, 9750, 16200, 23750, 41000, 58000, 72000…).
   - **Tournures** : phrases plus longues et plus formelles, vouvoiement, formulations indirectes, moins de SMS abrégés.
3. **Contrôle de chevauchement** : vérification manuelle des montants et des formulations répétées avec le jeu d'entraînement. Un montant commun (25000 FCFA) a été remplacé par 23750 FCFA pour éviter une fuite de valeur.
4. **Attribution de `ambigu`** : 12 messages sur 100 sont marqués ambigus.
5. **Ajustement** : un montant saisi avec espace (« 5 600 ») a été normalisé en « 5600 » pour homogénéité.

### 5.3 Répartition

| Classe | Messages |
|---|---|
| LITIGE | 24 |
| DEMANDE_ECHEANCIER | 26 |
| ANNONCE_PAIEMENT | 32 |
| RETARD_SIMPLE | 18 |

La répartition n'est pas équilibrée. Elle n'a pas été corrigée.

## 6. Limites connues

- **Données synthétiques** : le vocabulaire et les tournures reflètent la rédaction, pas la réalité des échanges WhatsApp ou SMS. Un modèle entraîné dessus peut mal se comporter sur des messages réels.
- **Étiquettes non validées par des experts** : elles représentent l'interprétation de l'auteur. Aucune double annotation avec accord inter-annotateurs n'a été réalisée.
- **Proximité de style entre les deux jeux** : les deux ont été écrits par le même processus. Le hold-out mesure la généralisation à des formulations nouvelles, pas à un autre auteur ou à un autre canal.
- **Déséquilibre de classes** : `RETARD_SIMPLE` est sous-représenté dans le test (18 messages) et `ANNONCE_PAIEMENT` est surreprésenté (32).
- **Taille modeste** : 250 et 100 messages suffisent pour un premier test, pas pour une évaluation statistiquement robuste sur des sous-classes fines.

## 7. Bonnes pratiques d'utilisation

- Ne pas consulter le jeu de test pendant le réglage des hyperparamètres. Utiliser un jeu de validation prélevé dans l'entraînement si nécessaire.
- Faire relire les cas `ambigu=oui` par une personne qui travaille dans le recouvrement, avant de mesurer une performance.
- Compléter progressivement avec des messages réels anonymisés, en respectant la confidentialité des clients.
- Documenter toute modification des étiquettes dans une version ultérieure du fichier.

## 8. Historique et corrections

- **Version initiale** : création du jeu d'entraînement (250 messages) puis du jeu de test (100 messages).
- **Correction** : le nombre de messages ambigus dans le jeu de test est de **12**, et non 17 comme indiqué précédemment dans la présentation du jeu.
- **Correction** : le jeu de test ne comporte aucun chevauchement de montants avec le jeu d'entraînement après le remplacement de 25000 par 23750 FCFA.
