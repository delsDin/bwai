"""
Configuration centrale et parametres metier reproductibles - BWAI 2026 (Cas 1 : Recouvrement).
Conformement a la Section 6 du TDR ("sans secret ni donnee sensible"), tous les parametres de
demonstration sont centralises directement dans ce fichier Python (aucun fichier .env ni cle
d'API externe n'est requis, fonctionnement 100 % hors-ligne).
"""
from datetime import date
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# Chemins locaux de la base SQLite, du schema SQL et des jeux de donnees fictifs
DATABASE_PATH = Path(os.getenv("BWAI_DATABASE_PATH", str(BASE_DIR / "database" / "recouvrement.db")))
SCHEMA_PATH = BASE_DIR / "database" / "schema.sql"
DATA_DIR = BASE_DIR / "data"

# Date de reference fixe par defaut pour garantir des calculs 100 % reproductibles (Guide Phase 1 & 3)
DEFAULT_REFERENCE_DATE = date.fromisoformat(os.getenv("BWAI_REFERENCE_DATE", "2026-03-15"))

# Seuils metier configurables (modifiables egalement depuis l'interface Web via "Parametres metier")
DEFAULT_HIGH_AMOUNT_THRESHOLD = float(os.getenv("BWAI_HIGH_AMOUNT_THRESHOLD", "500000.0"))
DEFAULT_AI_CONFIDENCE_THRESHOLD = float(os.getenv("BWAI_AI_CONFIDENCE_THRESHOLD", "0.75"))
DEFAULT_MAX_DELAY_DAYS_NORM = float(os.getenv("BWAI_MAX_DELAY_DAYS_NORM", "60.0"))

# Poids de la formule heuristique explicable de priorisation (Guide Section 5)
# Score = 0,40 * score_retard + 0,35 * score_solde + 0,25 * score_historique
WEIGHT_DELAY = 0.40
WEIGHT_BALANCE = 0.35
WEIGHT_HISTORY = 0.25

# Parametres de la sequence graduee de relances et limites de frequence (Guide Modules F & I)
DEFAULT_CHANNEL = os.getenv("BWAI_DEFAULT_CHANNEL", "WHATSAPP")
MIN_DAYS_BETWEEN_REMINDERS = int(os.getenv("BWAI_MIN_DAYS_BETWEEN_REMINDERS", "7"))
MAX_REMINDERS_PER_INVOICE = int(os.getenv("BWAI_MAX_REMINDERS_PER_INVOICE", "3"))
PRE_DUE_REMINDER_WINDOW_DAYS = int(os.getenv("BWAI_PRE_DUE_REMINDER_WINDOW_DAYS", "3"))
SECOND_REMINDER_THRESHOLD_DAYS = int(os.getenv("BWAI_SECOND_REMINDER_THRESHOLD_DAYS", "15"))

# Authentification stricte active par defaut (1 = jeton d'operateur requis sur les routes protegees)
REQUIRE_AUTH = os.getenv("BWAI_REQUIRE_AUTH", "1").strip() != "0"

# Comptes de test RBAC de demonstration (aucun secret reel : les empreintes SHA-256 sont stockees dans SQLite)
TOKEN_GESTIONNAIRE = "tok-gestionnaire-koffi"
TOKEN_VALIDATEUR_1 = "tok-validateur-amina"
TOKEN_VALIDATEUR_2 = "tok-validateur-boris"
TOKEN_ADMIN = "tok-admin-direction"
