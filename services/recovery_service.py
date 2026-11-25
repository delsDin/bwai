"""
Module K - Service de reprise controlee apres interruption hors connexion sans doublon (Test T10).
"""
from pathlib import Path
from typing import Dict, Any

from database.db import db_session
from services.audit_logger import log_event
from services.calculator import recalculate_all_invoices
from services.reminder_engine import prepare_all_reminders


def resume_after_interruption(db_path: Path | None = None) -> Dict[str, Any]:
    """
    Relit l'etat persistant SQLite local apres une coupure ou un redemarrage :
    1. Recalcule de maniere deterministe tous les soldes et statuts.
    2. Reprend la preparation des relances uniquement pour les factures non encore traitees,
       sans jamais recreer les relances deja enregistrees (grace a la cle UNIQUE(invoice_id, niveau_relance)).
    3. Retourne un bilan exact de la file de reprise.
    """
    recalculate_all_invoices(db_path=db_path)

    with db_session(db_path) as conn:
        before_count = int(conn.execute("SELECT COUNT(*) AS cnt FROM reminders").fetchone()["cnt"])

    prep_results = prepare_all_reminders(db_path=db_path)
    newly_created = sum(1 for r in prep_results if r.get("status") == "CREATED")
    duplicates_prevented = sum(1 for r in prep_results if r.get("duplicate_prevented") is True)

    with db_session(db_path) as conn:
        after_count = int(conn.execute("SELECT COUNT(*) AS cnt FROM reminders").fetchone()["cnt"])
        pending_review = int(
            conn.execute(
                "SELECT COUNT(*) AS cnt FROM reminders WHERE statut_action = 'EN_ATTENTE_VALIDATION'"
            ).fetchone()["cnt"]
        )
        approved_ready = int(
            conn.execute("SELECT COUNT(*) AS cnt FROM reminders WHERE statut_action = 'APPROUVEE'").fetchone()["cnt"]
        )
        already_simulated = int(
            conn.execute("SELECT COUNT(*) AS cnt FROM reminders WHERE statut_action = 'SIMULEE'").fetchone()["cnt"]
        )

    log_event(
        acteur="SYSTEME",
        action="REPRISE_APRES_INTERRUPTION",
        objet_type="WORKFLOW",
        objet_id="RECOVERY-LOCAL",
        ancienne_valeur=f"Relances_avant={before_count}",
        nouvelle_valeur=f"Relances_apres={after_count}, Nouvelles={newly_created}, Doublons_evites={duplicates_prevented}",
        motif="Reprise controlee hors connexion sans doublon",
        db_path=db_path,
    )

    return {
        "reminders_before": before_count,
        "reminders_after": after_count,
        "newly_created": newly_created,
        "duplicates_prevented": duplicates_prevented,
        "pending_human_review": pending_review,
        "approved_ready_to_send": approved_ready,
        "already_simulated": already_simulated,
    }
