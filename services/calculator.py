"""
Modules C & D - Calcul deterministe des soldes, des jours de retard et qualification des 6 statuts metier.
Aucun modele generatif n'est utilise ici (Phase 3 & 4 du Guide).
"""
from datetime import date
from pathlib import Path
from typing import Tuple, Dict, Any

import config
from database.db import db_session
from services.prioritizer import compute_priority_score
from services.ai_classifier import classifier_singleton


def compute_balance_and_delay(
    montant_initial: float,
    total_paye: float,
    total_avoirs: float,
    date_echeance: date,
    reference_date: date,
) -> Tuple[float, int]:
    """
    Solde restant = montant initial - paiements imputes - avoirs imputes.
    Jours de retard = max(0, date_reference - date_echeance) sauf si la facture est soldee.
    """
    solde_restant = round(max(0.0, float(montant_initial) - float(total_paye) - float(total_avoirs)), 2)
    if solde_restant <= 0.0:
        return 0.0, 0
    delta_days = (reference_date - date_echeance).days
    jours_retard = max(0, delta_days)
    return solde_restant, jours_retard


def determine_invoice_status(
    solde_restant: float,
    total_paye_ou_avoir: float,
    date_echeance: date,
    reference_date: date,
    has_open_dispute: bool,
    has_active_installment_plan: bool,
) -> Tuple[str, str]:
    """
    Retourne (statut_calcule, statut_paiement) en respectant les 6 statuts obligatoires du TDR :
    SOLDEE, CONTESTEE, COUVERTE_PAR_ECHEANCIER, ECHUE, PARTIELLEMENT_PAYEE, NON_ECHUE.
    """
    if solde_restant <= 0.0:
        return "SOLDEE", "SOLDEE"

    statut_paiement = "PARTIELLEMENT_PAYEE" if total_paye_ou_avoir > 0 else "IMPAYEE"

    if has_open_dispute:
        return "CONTESTEE", statut_paiement

    if has_active_installment_plan:
        return "COUVERTE_PAR_ECHEANCIER", statut_paiement

    if reference_date > date_echeance:
        if total_paye_ou_avoir > 0:
            return "PARTIELLEMENT_PAYEE", statut_paiement
        return "ECHUE", statut_paiement

    if total_paye_ou_avoir > 0:
        return "PARTIELLEMENT_PAYEE", statut_paiement

    return "NON_ECHUE", statut_paiement


def recalculate_invoice(
    invoice_id: str,
    reference_date: date | None = None,
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """
    Recalcule en base de donnees le solde, les jours de retard, le statut, l'analyse IA et le score
    de priorite pour une facture donnee.
    """
    with db_session(db_path) as conn:
        if reference_date is None:
            row_ref = conn.execute("SELECT value FROM settings WHERE key = 'reference_date'").fetchone()
            ref_date = date.fromisoformat(row_ref["value"]) if row_ref else config.DEFAULT_REFERENCE_DATE
        else:
            ref_date = reference_date

        row_thresh = conn.execute("SELECT value FROM settings WHERE key = 'high_amount_threshold'").fetchone()
        high_threshold = float(row_thresh["value"]) if row_thresh else config.DEFAULT_HIGH_AMOUNT_THRESHOLD

        inv = conn.execute(
            """
            SELECT i.*, c.nb_factures_historiques, c.nb_retards_historiques
            FROM invoices i
            JOIN clients c ON i.client_id = c.client_id
            WHERE i.invoice_id = ?
            """,
            (invoice_id,),
        ).fetchone()
        if not inv:
            raise ValueError(f"Facture introuvable : {invoice_id}")

        pay_row = conn.execute(
            "SELECT COALESCE(SUM(montant), 0.0) AS total_paye FROM payments WHERE invoice_id = ? AND statut = 'VALIDE'",
            (invoice_id,),
        ).fetchone()
        total_paye = float(pay_row["total_paye"])

        avoir_row = conn.execute(
            "SELECT COALESCE(SUM(montant), 0.0) AS total_avoirs FROM credit_notes WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchone()
        total_avoirs = float(avoir_row["total_avoirs"])

        dispute_row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM disputes WHERE invoice_id = ? AND statut = 'OUVERT'",
            (invoice_id,),
        ).fetchone()
        has_open_dispute = int(dispute_row["cnt"]) > 0

        plan_row = conn.execute(
            """
            SELECT COUNT(*) AS cnt FROM installment_plans
            WHERE invoice_id = ? AND statut = 'ACTIF' AND date_prochaine_echeance >= ?
            """,
            (invoice_id, ref_date.isoformat()),
        ).fetchone()
        has_active_plan = int(plan_row["cnt"]) > 0

        # Analyse IA du message client si present
        ai_res = classifier_singleton.classify(inv["dernier_message_client"])
        ai_categorie = ai_res["categorie"]
        ai_confiance = ai_res["confiance"]

        solde_restant, jours_retard = compute_balance_and_delay(
            montant_initial=float(inv["montant_initial"]),
            total_paye=total_paye,
            total_avoirs=total_avoirs,
            date_echeance=date.fromisoformat(inv["date_echeance"]),
            reference_date=ref_date,
        )

        statut_calcule, statut_paiement = determine_invoice_status(
            solde_restant=solde_restant,
            total_paye_ou_avoir=total_paye + total_avoirs,
            date_echeance=date.fromisoformat(inv["date_echeance"]),
            reference_date=ref_date,
            has_open_dispute=has_open_dispute,
            has_active_installment_plan=has_active_plan,
        )

        score_priorite, explication_score, eligible_relance, _ = compute_priority_score(
            jours_retard=jours_retard,
            solde_restant=solde_restant,
            nb_retards_historiques=int(inv["nb_retards_historiques"]),
            nb_factures_historiques=int(inv["nb_factures_historiques"]),
            statut_calcule=statut_calcule,
            high_amount_threshold=high_threshold,
        )

        conn.execute(
            """
            UPDATE invoices
            SET total_paye = ?,
                total_avoirs = ?,
                solde_restant = ?,
                jours_retard = ?,
                statut_calcule = ?,
                statut_paiement = ?,
                score_priorite = ?,
                explication_score = ?,
                eligible_relance = ?,
                ai_categorie = ?,
                ai_confiance = ?
            WHERE invoice_id = ?
            """,
            (
                total_paye,
                total_avoirs,
                solde_restant,
                jours_retard,
                statut_calcule,
                statut_paiement,
                score_priorite,
                explication_score,
                1 if eligible_relance else 0,
                ai_categorie,
                ai_confiance,
                invoice_id,
            ),
        )

        return {
            "invoice_id": invoice_id,
            "total_paye": total_paye,
            "total_avoirs": total_avoirs,
            "solde_restant": solde_restant,
            "jours_retard": jours_retard,
            "statut_calcule": statut_calcule,
            "statut_paiement": statut_paiement,
            "score_priorite": score_priorite,
            "explication_score": explication_score,
            "eligible_relance": eligible_relance,
            "ai_categorie": ai_categorie,
            "ai_confiance": ai_confiance,
        }


def recalculate_all_invoices(
    reference_date: date | None = None,
    db_path: Path | None = None,
) -> list[Dict[str, Any]]:
    with db_session(db_path) as conn:
        rows = conn.execute("SELECT invoice_id FROM invoices").fetchall()
        invoice_ids = [r["invoice_id"] for r in rows]
    return [recalculate_invoice(inv_id, reference_date=reference_date, db_path=db_path) for inv_id in invoice_ids]
