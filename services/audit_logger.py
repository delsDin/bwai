"""
Module L - Journalisation horodatee immuable (Tracabilite complete).
"""
from datetime import datetime, timezone
from pathlib import Path
from database.db import db_session


def log_event(
    acteur: str,
    action: str,
    objet_type: str,
    objet_id: str,
    ancienne_valeur: str | None = None,
    nouvelle_valeur: str | None = None,
    motif: str | None = None,
    db_path: Path | None = None,
) -> int:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with db_session(db_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO audit_logs (timestamp, acteur, action, objet_type, objet_id, ancienne_valeur, nouvelle_valeur, motif)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (now_iso, acteur, action, objet_type, objet_id, ancienne_valeur, nouvelle_valeur, motif),
        )
        return int(cursor.lastrowid)
