"""
Configuration globale et parametres metier reproductibles - BWAI 2026 (Cas 1 : Recouvrement).
Charge automatiquement les variables depuis .env (si present) avec des valeurs de demonstration
non sensibles par defaut pour garantir l'execution immediate des tests hors-ligne.
"""
from datetime import date
import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

BASE_DIR = Path(__file__).resolve().parent
if load_dotenv is not None:
    load_dotenv(BASE_DIR / ".env")

DATABASE_PATH = Path(os.getenv("BWAI_DATABASE_PATH", str(BASE_DIR / "database" / "recouvrement.db")))
SCHEMA_PATH = BASE_DIR / "database" / "schema.sql"
DATA_DIR = BASE_DIR / "data"

# Date de reference fixe par defaut pour garantir des calculs 100 % reproductibles (Guide Phase 1 & 3)
DEFAULT_REFERENCE_DATE = date.fromisoformat(os.getenv("BWAI_REFERENCE_DATE", "2026-03-15"))

# Seuils configurables (Guide Phase 1, Section 5 & Module H)
DEFAULT_HIGH_AMOUNT_THRESHOLD = float(os.getenv("BWAI_HIGH_AMOUNT_THRESHOLD", "500000.0"))
DEFAULT_AI_CONFIDENCE_THRESHOLD = float(os.getenv("BWAI_AI_CONFIDENCE_THRESHOLD", "0.75"))
DEFAULT_MAX_DELAY_DAYS_NORM = float(os.getenv("BWAI_MAX_DELAY_DAYS_NORM", "60.0"))

# Poids de la formule heuristique explicable (Guide Section 5)
WEIGHT_DELAY = 0.40
WEIGHT_BALANCE = 0.35
WEIGHT_HISTORY = 0.25

# Parametres de sequence graduee et limites de frequence (Guide Module F & I)
DEFAULT_CHANNEL = os.getenv("BWAI_DEFAULT_CHANNEL", "WHATSAPP")
MIN_DAYS_BETWEEN_REMINDERS = int(os.getenv("BWAI_MIN_DAYS_BETWEEN_REMINDERS", "7"))
MAX_REMINDERS_PER_INVOICE = int(os.getenv("BWAI_MAX_REMINDERS_PER_INVOICE", "3"))
PRE_DUE_REMINDER_WINDOW_DAYS = int(os.getenv("BWAI_PRE_DUE_REMINDER_WINDOW_DAYS", "3"))
SECOND_REMINDER_THRESHOLD_DAYS = int(os.getenv("BWAI_SECOND_REMINDER_THRESHOLD_DAYS", "15"))

# Authentification stricte : tout appel sans jeton sur une route protegee renvoie 401 (desactivable pour le debogage uniquement)
REQUIRE_AUTH = os.getenv("BWAI_REQUIRE_AUTH", "1").strip() != "0"

# Jetons des comptes de test (lus depuis .env avec repli sur les jetons de demonstration documentes)
TOKEN_GESTIONNAIRE = os.getenv("BWAI_TOKEN_GESTIONNAIRE", "tok-gestionnaire-koffi")
TOKEN_VALIDATEUR_1 = os.getenv("BWAI_TOKEN_VALIDATEUR_1", "tok-validateur-amina")
TOKEN_VALIDATEUR_2 = os.getenv("BWAI_TOKEN_VALIDATEUR_2", "tok-validateur-boris")
TOKEN_ADMIN = os.getenv("BWAI_TOKEN_ADMIN", "tok-admin-direction")
