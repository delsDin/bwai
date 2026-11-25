"""
Configuration globale et parametres metier reproductibles - BWAI 2026 (Cas 1 : Recouvrement).
Aucun secret ni donnee personnelle reelle ne doit figurer dans ce fichier.
"""
from pathlib import Path
from datetime import date

BASE_DIR = Path(__file__).resolve().parent
DATABASE_PATH = BASE_DIR / "database" / "recouvrement.db"
SCHEMA_PATH = BASE_DIR / "database" / "schema.sql"
DATA_DIR = BASE_DIR / "data"

# Date de reference fixe par defaut pour garantir des calculs 100 % reproductibles (Guide Phase 1 & 3)
DEFAULT_REFERENCE_DATE = date(2026, 3, 15)

# Seuils configurables (Guide Phase 1, Section 5 & Module H)
DEFAULT_HIGH_AMOUNT_THRESHOLD = 500_000.0  # Seuil gros montant en FCFA declenchant la revue humaine obligatoire (Test T7)
DEFAULT_AI_CONFIDENCE_THRESHOLD = 0.75     # Seuil de confiance IA en dessous duquel la revue humaine est obligatoire
DEFAULT_MAX_DELAY_DAYS_NORM = 60.0         # Plafond de normalisation des jours de retard : min(retard / 60, 1)

# Poids de la formule heuristique explicable (Guide Section 5)
WEIGHT_DELAY = 0.40
WEIGHT_BALANCE = 0.35
WEIGHT_HISTORY = 0.25

# Parametres de sequence graduee et limites de frequence (Guide Module F & I)
DEFAULT_CHANNEL = "WHATSAPP"
MIN_DAYS_BETWEEN_REMINDERS = 7
MAX_REMINDERS_PER_INVOICE = 3
PRE_DUE_REMINDER_WINDOW_DAYS = 3
SECOND_REMINDER_THRESHOLD_DAYS = 15
