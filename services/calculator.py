"""
Modules C & D - Calcul deterministe des soldes, des jours de retard et qualification des 6 statuts metier.
Aucun modele generatif n'est utilise ici (Phase 3 & 4 du Guide).
"""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import Tuple, Dict, Any

import config
from database.db import db_session
from services.prioritizer import compute_priority_score
from services.ai_classifier import classifier_singleton

CENT_QUANTUM = Decimal("0.01")
ZERO_DEC = Decimal("0.00")


def to_money_decimal(amount: float | int | str | Decimal) -> Decimal:
    """
    Convertit un montant en Decimal base-10 exact arrondi au centime selon la regle comptable ROUND_HALF_UP.
    Elimine toute derive binaire IEEE-754 (ex: 0.1 + 0.2 == 0.30, 100000.10 - 33333.03 - 66667.07 == 0.00).
    """
    if isinstance(amount, Decimal):
        raw = amount
    elif isinstance(amount, float):
        raw = Decimal(f"{amount:.6f}")
    else:
        raw = Decimal(str(amount))
    return raw.quantize(CENT_QUANTUM, rounding=ROUND_HALF_UP)


def compute_overpayment(
    montant_initial: float | Decimal,
    total_paye: float | Decimal,
    total_avoirs: float | Decimal,
) -> float:
    """
    Partie A.3 - Calcule en arithmetique Decimal exacte le montant de surpaiement (trop-percu)
    si total_paye + total_avoirs > montant_initial.
    Garantit l'identite comptable : solde_restant + total_paye + total_avoirs - montant_excedent == montant_initial.
    """
    m_init = to_money_decimal(montant_initial)
    m_paye = to_money_decimal(total_paye)
    m_avoir = to_money_decimal(total_avoirs)
    excedent = max(ZERO_DEC, (m_paye + m_avoir - m_init).quantize(CENT_QUANTUM, rounding=ROUND_HALF_UP))
    return float(excedent)


def compute_balance_and_delay(
    montant_initial: float | Decimal,
    total_paye: float | Decimal,
    total_avoirs: float | Decimal,
    date_echeance: date,
    reference_date: date,
) -> Tuple[float, int]:
    """
    Solde restant = max(0, montant initial - paiements imputes - avoirs imputes) en Decimal base-10 exact.
    Jours de retard = max(0, date_reference - date_echeance) sauf si la facture est soldee.
    """
    m_init = to_money_decimal(montant_initial)
    m_paye = to_money_decimal(total_paye)
    m_avoir = to_money_decimal(total_avoirs)
    solde_dec = max(ZERO_DEC, (m_init - m_paye - m_avoir).quantize(CENT_QUANTUM, rounding=ROUND_HALF_UP))
    if solde_dec <= ZERO_DEC:
        return 0.0, 0
    delta_days = (reference_date - date_echeance).days
    jours_retard = max(0, delta_days)
    return float(solde_dec), jours_retard


def determine_maturity_status(
    solde_restant: float | Decimal,
    date_echeance: date,
    reference_date: date,
) -> str:
    """
    Determine la dimension orthogonale d'echeance (SOLDEE, ECHUE, NON_ECHUE),
    separant explicitement l'etat d'echeance de l'etat de paiement (Guide Phase 4, Note de conception).
    """
    if to_money_decimal(solde_restant) <= ZERO_DEC:
        return "SOLDEE"
    return "ECHUE" if reference_date > date_echeance else "NON_ECHUE"


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


def _recalculate_invoice_on_conn(
    conn: sqlite3.Connection,
    invoice_id: str,
    reference_date: date | None = None,
) -> Dict[str, Any]:
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

    pay_rows = conn.execute(
        "SELECT montant FROM payments WHERE invoice_id = ? AND statut = 'VALIDE'",
        (invoice_id,),
    ).fetchall()
    total_paye_dec = sum((to_money_decimal(r["montant"]) for r in pay_rows), ZERO_DEC)
    total_paye = float(total_paye_dec)

    avoir_rows = conn.execute(
        "SELECT montant FROM credit_notes WHERE invoice_id = ?",
        (invoice_id,),
    ).fetchall()
    total_avoirs_dec = sum((to_money_decimal(r["montant"]) for r in avoir_rows), ZERO_DEC)
    total_avoirs = float(total_avoirs_dec)

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

    # Analyse IA du message client si present (preserve la reprise humaine si ai_confiance == 1.0 sur un message)
    if (
        inv["dernier_message_client"]
        and inv["ai_categorie"] in ("LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT", "RETARD_SIMPLE")
        and float(inv["ai_confiance"] or 0.0) >= 1.0
    ):
        ai_categorie = str(inv["ai_categorie"])
        ai_confiance = 1.0
    else:
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
    montant_excedent = compute_overpayment(
        montant_initial=float(inv["montant_initial"]),
        total_paye=total_paye,
        total_avoirs=total_avoirs,
    )
    surpaiement_detecte = montant_excedent > 0.0

    statut_calcule, statut_paiement = determine_invoice_status(
        solde_restant=solde_restant,
        total_paye_ou_avoir=float(total_paye_dec + total_avoirs_dec),
        date_echeance=date.fromisoformat(inv["date_echeance"]),
        reference_date=ref_date,
        has_open_dispute=has_open_dispute,
        has_active_installment_plan=has_active_plan,
    )
    statut_echeance = determine_maturity_status(
        solde_restant=solde_restant,
        date_echeance=date.fromisoformat(inv["date_echeance"]),
        reference_date=ref_date,
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
        "montant_initial": float(to_money_decimal(inv["montant_initial"])),
        "total_paye": total_paye,
        "total_avoirs": total_avoirs,
        "solde_restant": solde_restant,
        "surpaiement_detecte": surpaiement_detecte,
        "montant_excedent": montant_excedent,
        "jours_retard": jours_retard,
        "statut_calcule": statut_calcule,
        "statut_paiement": statut_paiement,
        "statut_echeance": statut_echeance,
        "score_priorite": score_priorite,
        "explication_score": explication_score,
        "eligible_relance": eligible_relance,
        "ai_categorie": ai_categorie,
        "ai_confiance": ai_confiance,
    }


def recalculate_invoice(
    invoice_id: str,
    reference_date: date | None = None,
    db_path: Path | None = None,
    conn: sqlite3.Connection | None = None,
) -> Dict[str, Any]:
    """
    Recalcule en base de donnees le solde, les jours de retard, le statut, l'analyse IA et le score
    de priorite pour une facture donnee.
    """
    if conn is not None:
        return _recalculate_invoice_on_conn(conn, invoice_id, reference_date=reference_date)
    with db_session(db_path) as session_conn:
        return _recalculate_invoice_on_conn(session_conn, invoice_id, reference_date=reference_date)


def recalculate_all_invoices(
    reference_date: date | None = None,
    db_path: Path | None = None,
) -> list[Dict[str, Any]]:
    with db_session(db_path) as conn:
        rows = conn.execute("SELECT invoice_id FROM invoices ORDER BY invoice_id ASC").fetchall()
        invoice_ids = [r["invoice_id"] for r in rows]
    return [recalculate_invoice(inv_id, reference_date=reference_date, db_path=db_path) for inv_id in invoice_ids]


def compute_portfolio_state_hash(db_path: Path | None = None) -> Dict[str, Any]:
    """
    Partie A.5 - Calcule une empreinte SHA-256 canonique de l'etat complet du portefeuille
    (soldes, retards, statuts, scores de priorite, qualification IA).
    Permet de prouver mathematiquement que deux rejeux deterministes donnent exactement le meme etat.
    """
    with db_session(db_path) as conn:
        row_ref = conn.execute("SELECT value FROM settings WHERE key = 'reference_date'").fetchone()
        ref_date = row_ref["value"] if row_ref else config.DEFAULT_REFERENCE_DATE.isoformat()
        rows = conn.execute(
            """
            SELECT invoice_id, client_id, montant_initial, total_paye, total_avoirs,
                   solde_restant, jours_retard, statut_calcule, statut_paiement,
                   score_priorite, eligible_relance, ai_categorie, ai_confiance
            FROM invoices
            ORDER BY invoice_id ASC
            """
        ).fetchall()

    canonical_list = [
        {
            "invoice_id": r["invoice_id"],
            "client_id": r["client_id"],
            "montant_initial": round(float(r["montant_initial"]), 2),
            "total_paye": round(float(r["total_paye"]), 2),
            "total_avoirs": round(float(r["total_avoirs"]), 2),
            "solde_restant": round(float(r["solde_restant"]), 2),
            "jours_retard": int(r["jours_retard"]),
            "statut_calcule": r["statut_calcule"],
            "statut_paiement": r["statut_paiement"],
            "score_priorite": round(float(r["score_priorite"]), 3),
            "eligible_relance": int(r["eligible_relance"]),
            "ai_categorie": r["ai_categorie"],
            "ai_confiance": round(float(r["ai_confiance"] or 0.0), 3),
        }
        for r in rows
    ]
    payload = json.dumps(
        {"reference_date": ref_date, "invoices": canonical_list},
        sort_keys=True,
        ensure_ascii=False,
    )
    state_hash = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return {
        "reference_date": ref_date,
        "invoice_count": len(canonical_list),
        "state_hash": state_hash,
        "invoices": canonical_list,
    }


def deterministic_replay(
    reference_date: date | None = None,
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """
    Partie A.5 - Rejoue l'integralite des calculs du portefeuille a la date de reference
    et retourne l'empreinte SHA-256 deterministe de l'etat final.
    """
    recalculate_all_invoices(reference_date=reference_date, db_path=db_path)
    return compute_portfolio_state_hash(db_path=db_path)

