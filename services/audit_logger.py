"""
Module L & Partie C.3 - Journalisation horodatee immuable a hachage chaine SHA-256.
Chaque evenement est chaine cryptographiquement au precedent (prev_hash -> entry_hash)
et protege par des triggers SQLite interdisant tout UPDATE ou DELETE.
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
from typing import Dict, Any

from database.db import db_atomic, db_session


def compute_audit_hash(
    prev_hash: str,
    timestamp: str,
    acteur: str,
    action: str,
    objet_type: str,
    objet_id: str,
    ancienne_valeur: str | None,
    nouvelle_valeur: str | None,
    motif: str | None,
) -> str:
    payload = "|".join([
        prev_hash,
        timestamp,
        acteur,
        action,
        objet_type,
        objet_id,
        ancienne_valeur or "",
        nouvelle_valeur or "",
        motif or "",
    ])
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _insert_chained_event(
    conn: sqlite3.Connection,
    now_iso: str,
    acteur: str,
    action: str,
    objet_type: str,
    objet_id: str,
    ancienne_valeur: str | None,
    nouvelle_valeur: str | None,
    motif: str | None,
) -> int:
    last_row = conn.execute(
        "SELECT entry_hash FROM audit_logs ORDER BY event_id DESC LIMIT 1"
    ).fetchone()
    prev_hash = str(last_row["entry_hash"]) if (last_row and last_row["entry_hash"]) else "GENESIS"
    entry_hash = compute_audit_hash(
        prev_hash=prev_hash,
        timestamp=now_iso,
        acteur=acteur,
        action=action,
        objet_type=objet_type,
        objet_id=objet_id,
        ancienne_valeur=ancienne_valeur,
        nouvelle_valeur=nouvelle_valeur,
        motif=motif,
    )
    cursor = conn.execute(
        """
        INSERT INTO audit_logs (
            timestamp, acteur, action, objet_type, objet_id,
            ancienne_valeur, nouvelle_valeur, motif, prev_hash, entry_hash
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_iso,
            acteur,
            action,
            objet_type,
            objet_id,
            ancienne_valeur,
            nouvelle_valeur,
            motif,
            prev_hash,
            entry_hash,
        ),
    )
    return int(cursor.lastrowid)


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
    if conn is not None:
        return _insert_chained_event(
            conn, now_iso, acteur, action, objet_type, objet_id, ancienne_valeur, nouvelle_valeur, motif
        )
    with db_atomic(db_path) as atomic_conn:
        return _insert_chained_event(
            atomic_conn, now_iso, acteur, action, objet_type, objet_id, ancienne_valeur, nouvelle_valeur, motif
        )


def verify_audit_chain(db_path: Path | None = None) -> Dict[str, Any]:
    """
    Verifie l'integrite cryptographique complete de la chaine de hachage SHA-256 du journal d'audit.
    Detecte toute alteration, insertion ou suppression d'evenement.
    """
    with db_session(db_path) as conn:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY event_id ASC").fetchall()

    expected_prev = "GENESIS"
    checked = 0
    for row in rows:
        checked += 1
        actual_prev = str(row["prev_hash"])
        expected_entry = compute_audit_hash(
            prev_hash=expected_prev,
            timestamp=str(row["timestamp"]),
            acteur=str(row["acteur"]),
            action=str(row["action"]),
            objet_type=str(row["objet_type"]),
            objet_id=str(row["objet_id"]),
            ancienne_valeur=row["ancienne_valeur"],
            nouvelle_valeur=row["nouvelle_valeur"],
            motif=row["motif"],
        )
        if actual_prev != expected_prev or str(row["entry_hash"]) != expected_entry:
            return {
                "valid": False,
                "checked_events": checked,
                "corrupted_event_id": row["event_id"],
                "reason": f"Rupture de chaine SHA-256 a l'evenement {row['event_id']}",
            }
        expected_prev = expected_entry

    return {
        "valid": True,
        "checked_events": checked,
        "head_hash": expected_prev,
    }
