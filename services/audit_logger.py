"""
Module L - Journalisation horodatee immuable (Tracabilite complete).
"""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
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
    conn: sqlite3.Connection | None = None,
) -> int:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    sql = """
        INSERT INTO audit_logs (timestamp, acteur, action, objet_type, objet_id, ancienne_valeur, nouvelle_valeur, motif)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """
    params = (now_iso, acteur, action, objet_type, objet_id, ancienne_valeur, nouvelle_valeur, motif)
    if conn is not None:
        cursor = conn.execute(sql, params)
        return int(cursor.lastrowid)
    with db_session(db_path) as session_conn:
        cursor = session_conn.execute(sql, params)
        return int(cursor.lastrowid)
