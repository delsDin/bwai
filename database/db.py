"""
Gestionnaire de connexion SQLite locale et d'initialisation du schéma.
Fonctionne 100 % hors connexion (Module K).
"""
import hashlib
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import config


def get_connection(db_path: Path | None = None, immediate: bool = False) -> sqlite3.Connection:
    target_path = db_path or config.DATABASE_PATH
    target_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(
        str(target_path),
        timeout=30.0,
        isolation_level=None if immediate else "DEFERRED",
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA busy_timeout = 30000;")
    return conn


@contextmanager
def db_session(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = get_connection(db_path, immediate=False)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def db_atomic(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    """
    Transaction SQLite atomique avec verrouillage d'ecriture immediat (BEGIN IMMEDIATE).
    Empeche toute condition de course (TOCTOU) entre threads ou processus concurrents.
    """
    conn = get_connection(db_path, immediate=True)
    try:
        conn.execute("BEGIN IMMEDIATE;")
        yield conn
        conn.execute("COMMIT;")
    except Exception:
        try:
            conn.execute("ROLLBACK;")
        except sqlite3.Error:
            pass
        raise
    finally:
        conn.close()


def init_db(db_path: Path | None = None) -> None:
    """Crée les tables SQLite, migre les colonnes manquantes si nécessaire et insère les paramètres par défaut."""
    schema_sql = config.SCHEMA_PATH.read_text(encoding="utf-8")
    with db_session(db_path) as conn:
        conn.executescript(schema_sql)
        # Auto-migration idempotente si une base SQLite preexistante est ouverte
        for alter_sql in (
            "ALTER TABLE reminders ADD COLUMN cree_par TEXT NOT NULL DEFAULT 'SYSTEME'",
            "ALTER TABLE reminders ADD COLUMN valide_par TEXT",
            "ALTER TABLE audit_logs ADD COLUMN prev_hash TEXT NOT NULL DEFAULT 'GENESIS'",
            "ALTER TABLE audit_logs ADD COLUMN entry_hash TEXT NOT NULL DEFAULT ''",
        ):
            try:
                conn.execute(alter_sql)
            except sqlite3.OperationalError:
                pass
        default_settings = [
            ("reference_date", config.DEFAULT_REFERENCE_DATE.isoformat(), "Date de référence pour les calculs reproductibles"),
            ("high_amount_threshold", str(config.DEFAULT_HIGH_AMOUNT_THRESHOLD), "Seuil gros montant (FCFA) pour revue humaine obligatoire"),
            ("ai_confidence_threshold", str(config.DEFAULT_AI_CONFIDENCE_THRESHOLD), "Seuil de confiance IA minimal avant revue humaine"),
            ("default_channel", config.DEFAULT_CHANNEL, "Canal simulé par défaut (WHATSAPP, SMS, EMAIL)"),
            ("auto_prepare_reminders", "0", "Préparation automatique des relances après import ou modification (0 ou 1)"),
            ("enable_pre_due_reminders", "0", "Activer les rappels courtois pré-échéance J-3 à J-0 (0 ou 1)"),
            ("emergency_stop", "0", "Interrupteur d'arrêt d'urgence (1 = bloque tout envoi simulé)"),
            ("require_auth", "1", "Mode authentification stricte sur toutes les routes (0 ou 1)"),
            ("allow_audit_reset", "0", "Autorisation interne temporaire de purge lors du reset de démo"),
        ]
        conn.executemany(
            "INSERT OR IGNORE INTO settings (key, value, description) VALUES (?, ?, ?)",
            default_settings,
        )
        # Le mode d'authentification est impose a chaque demarrage depuis la configuration (.env),
        # y compris sur une base SQLite creee avant l'activation de l'authentification stricte.
        conn.execute(
            "UPDATE settings SET value = ? WHERE key = 'require_auth'",
            ("1" if config.REQUIRE_AUTH else "0",),
        )
        default_users = [
            (
                "USR-01",
                "agent.koffi",
                "Koffi (Gestionnaire Recouvrement)",
                "GESTIONNAIRE",
                hashlib.sha256(config.TOKEN_GESTIONNAIRE.encode("utf-8")).hexdigest(),
                "ACTIF",
            ),
            (
                "USR-02",
                "val.amina",
                "Amina (Superviseure / Validatrice)",
                "VALIDATEUR",
                hashlib.sha256(config.TOKEN_VALIDATEUR_1.encode("utf-8")).hexdigest(),
                "ACTIF",
            ),
            (
                "USR-03",
                "val.boris",
                "Boris (Directeur / Second Validateur)",
                "VALIDATEUR",
                hashlib.sha256(config.TOKEN_VALIDATEUR_2.encode("utf-8")).hexdigest(),
                "ACTIF",
            ),
            (
                "USR-04",
                "admin.direction",
                "Direction Generale (Administrateur)",
                "ADMIN",
                hashlib.sha256(config.TOKEN_ADMIN.encode("utf-8")).hexdigest(),
                "ACTIF",
            ),
        ]
        conn.executemany(
            """
            INSERT OR IGNORE INTO users (user_id, username, display_name, role, token_hash, statut)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            default_users,
        )

