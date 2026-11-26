# Sécurité et Maîtrise des Actions — Documentation Technique (Partie C — 15 points)

**Projet** : Recouvrement automatique des factures impayées (Cas pratique 1 — BWAI 2026)  
**Composants** : `services/security.py`, `services/audit_logger.py`, `services/reminder_engine.py`, `database/schema.sql`, `routes/api_routes.py`  
**Tests automatisés** : `pytest tests/test_step9_security.py`

---

## 1. Objectif et couverture du barème (15 / 15 points)

Le critère **« Sécurité et maîtrise des actions » (15 points)** de la partie 8 du TDR évalue :
1. **La protection des données et des accès** (authentification, contrôle d'accès par rôle, masquage des coordonnées, absence de secrets en dur, requêtes SQL paramétrées).
2. **Les contrôles propres au recouvrement et les validations humaines** (séparation des tâches / principe des quatre yeux, journal d'audit infalsifiable, interrupteur d'arrêt d'urgence).

L'ensemble des 5 recommandations de la **Partie C** de `ANALYSE_BACKEND_ET_NOTATION.md` est implémenté et couvert par des tests automatisés dédiés.

---

## 2. Authentification légère, contrôle d'accès par rôles (RBAC) et traçabilité de l'acteur (Point C.1)

### 2.1 Rôles et jetons de démonstration (`services/security.py`)

L'API supporte l'authentification par en-tête HTTP `X-API-Token` (ou `Authorization: Bearer <jeton>`) avec **3 rôles métier distincts** :

| Jeton de test | Identifiant (`acteur`) | Rôle | Droits accordés | Actions interdites (`403 Forbidden`) |
|---|---|---|---|---|
| `tok-gestionnaire-koffi` | `agent.koffi` | `GESTIONNAIRE` | Préparer les relances, simuler un paiement, déclencher l'envoi d'une relance déjà approuvée | Approuver/rejeter une relance en revue humaine (`/api/review`), réinitialiser la démo, activer l'arrêt d'urgence |
| `tok-validateur-amina` | `val.amina` | `VALIDATEUR` | Approuver, modifier, suspendre ou rejeter les dossiers sensibles en revue humaine (`/api/review`), lancer la reprise | Réinitialiser la base (`/api/system/reset-demo`), basculer l'arrêt d'urgence (`/api/system/emergency-stop`) |
| `tok-validateur-boris` | `val.boris` | `VALIDATEUR` | Second superviseur permettant la validation croisée (principe des 4 yeux) | Actions réservées à `ADMIN` |
| `tok-admin-direction` | `admin.direction` | `ADMIN` | Administration complète, réinitialisation du portefeuille, activation/levée de l'arrêt d'urgence | Auto-validation d'une relance qu'il a lui-même créée (4 yeux) |

- Tout jeton inconnu ou erroné est immédiatement rejeté avec **`HTTP 401 Unauthorized`**.
- Tout rôle insuffisant pour la route appelée est rejeté avec **`HTTP 403 Forbidden`**.
- **Traçabilité nominative** : l'identifiant réel de l'opérateur (`agent.koffi`, `val.amina`, `val.boris`, `admin.direction`) est enregistré dans la colonne `acteur` de `audit_logs` ainsi que dans les colonnes `cree_par` et `valide_par` de la table `reminders`.

---

## 3. Principe des quatre yeux — Séparation des tâches (Point C.2)

Afin d'empêcher qu'un même opérateur ne prépare une relance sur un dossier sensible (gros montant $\ge 500\,000\text{ FCFA}$, litige, cas incertain IA) et ne la valide lui-même :
- La table `reminders` trace explicitement `cree_par` (auteur de la préparation) et `valide_par` (auteur de la décision de revue humaine).
- Dans `apply_human_review()` (`services/reminder_engine.py`), toute tentative d'`APPROUVER` ou de `MODIFIER` une relance lorsque `acteur == cree_par` (pour un opérateur humain identifié) lève une `PermissionError` traduite en **`HTTP 403 Forbidden`** (*« Violation du principe des 4 yeux : l'acteur a préparé cette relance et ne peut pas la valider lui-même »*).
- Un second superviseur (ex. `val.boris` après préparation par `val.amina`) doit intervenir pour débloquer l'envoi.

---

## 4. Journal d'audit infalsifiable : Hachage chaîné SHA-256 et Triggers SQLite (Point C.3)

### 4.1 Chaînage cryptographique SHA-256 (`services/audit_logger.py`)

Chaque événement inséré dans `audit_logs` embarque deux empreintes :
- `prev_hash` : le hachage `entry_hash` de l'événement précédent (`"GENESIS"` pour le premier événement).
- `entry_hash` : l'empreinte $\text{SHA-256}$ calculée sur la concaténation :
  $$\text{SHA-256}(\texttt{prev\_hash} \parallel \texttt{timestamp} \parallel \texttt{acteur} \parallel \texttt{action} \parallel \texttt{objet\_type} \parallel \texttt{objet\_id} \parallel \texttt{ancienne\_valeur} \parallel \texttt{nouvelle\_valeur} \parallel \texttt{motif})$$

### 4.2 Verrouillage physique en base (`database/schema.sql`)

Deux triggers SQLite protègent `audit_logs` contre toute altération a posteriori :
- `trg_audit_logs_no_update` (`BEFORE UPDATE ON audit_logs`) : bloque toute modification d'une ligne existante (`RAISE(ABORT, 'Journal d audit immuable : modification interdite')`).
- `trg_audit_logs_no_delete` (`BEFORE DELETE ON audit_logs`) : bloque toute suppression d'événement en exploitation.

### 4.3 Endpoint de vérification d'intégrité (`GET /api/audit-logs/verify`)

L'endpoint `GET /api/audit-logs/verify` relit l'intégralité du journal depuis l'événement `GENESIS`, recalcule chaque empreinte SHA-256 et retourne :
```json
{
  "valid": true,
  "checked_events": 14,
  "head_hash": "a94f..."
}
```

---

## 5. Minimisation des données et masquage des contacts (Point C.4)

Pour éviter l'exposition inutile des coordonnées clients sur les écrans partagés et dans les flux API :
- La fonction `mask_contact()` (`services/security.py`) masque automatiquement les numéros de téléphone et adresses courriel :
  - `+22997001122` $\rightarrow$ `+22997****22`
  - `compta@pharmalittoral.bj` $\rightarrow$ `co***@pharmalittoral.bj`
- Les réponses de `/api/invoices` et `/api/invoices/{invoice_id}` exposent systématiquement l'attribut `contact_masque`, et aucune coordonnée brute n'est écrite dans `audit_logs`.

---

## 6. Interrupteur d'arrêt d'urgence — Mode « Tout est bloqué » (Point C.5)

En cas d'anomalie sur un import ou de contrôle comptable exceptionnel, un administrateur peut suspendre instantanément toute simulation d'envoi sans perdre la file de travail :
- **Activation / Désactivation** : `POST /api/system/emergency-stop` (réservé au rôle `ADMIN`) avec `{"active": true, "motif": "..."}`.
- **Effet immédiat dans `verify_and_simulate_send()`** : tant que `emergency_stop == "1"`, toute tentative d'envoi retourne `status: "BLOCKED_EMERGENCY_STOP"`, conserve la relance à son statut `APPROUVEE` et enregistre un événement `BLOCAGE_ARRET_URGENCE` dans le journal d'audit.
- Dès que l'administrateur désactive l'arrêt d'urgence (`{"active": false}`), les envois simulés reprennent normalement.

