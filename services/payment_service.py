"""
Module J - Service de paiements simules idempotents (Test T9 & T11).
Garantit qu'un reglement fictif n'est jamais comptabilise deux fois et met a jour le solde,
le statut de la facture et les relances en attente.
"""
from datetime import date
from pathlib import Path
from typing import Dict, Any
import uuid

import config
from database.db import db_atomic
from services.audit_logger import log_event
from services.calculator import recalculate_invoice


def record_simulated_payment(
    invoice_id: str,
    montant: float,
    reference_fictive: str,
    date_paiement: date | None = None,
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """
    Enregistre un paiement simule de maniere idempotente et atomique (BEGIN IMMEDIATE).
    - Si (invoice_id, reference_fictive) existe deja : retourne duplicate=True sans modifier le solde (Test T9).
    - Si le montant est <= 0 : leve une ValueError.
    - Sinon : insere le paiement, recalcule le solde/statut, et annule les relances en attente si la facture est soldee (Test T11).
    """
    if montant <= 0:
        raise ValueError("Le montant du paiement simule doit etre strictement positif.")

    pay_date = (date_paiement or config.DEFAULT_REFERENCE_DATE).isoformat()

    with db_atomic(db_path) as conn:
        inv = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if not inv:
            raise ValueError(f"Facture introuvable : {invoice_id}")

        ancien_solde = float(inv["solde_restant"])
        ancien_statut = str(inv["statut_calcule"])

        payment_id = f"PAY-SIM-{uuid.uuid4().hex[:8].upper()}"
        cur_ins = conn.execute(
            """
            INSERT OR IGNORE INTO payments (payment_id, invoice_id, date_paiement, montant, reference_fictive, statut, source)
            VALUES (?, ?, ?, ?, ?, 'VALIDE', 'SIMULATION')
            """,
            (payment_id, invoice_id, pay_date, montant, reference_fictive),
        )

        # Si rowcount == 0, la cle UNIQUE(invoice_id, reference_fictive) a bloque le doublon de maniere atomique
        if cur_ins.rowcount == 0:
            existing_pay = conn.execute(
                "SELECT payment_id FROM payments WHERE invoice_id = ? AND reference_fictive = ?",
                (invoice_id, reference_fictive),
            ).fetchone()
            dup_pay_id = existing_pay["payment_id"] if existing_pay else payment_id

            log_event(
                acteur="SYSTEME",
                action="DOUBLON_PAIEMENT_BLOQUE",
                objet_type="FACTURE",
                objet_id=invoice_id,
                ancienne_valeur=f"Solde={ancien_solde}",
                nouvelle_valeur=f"Solde={ancien_solde} (inchange)",
                motif=f"Reference {reference_fictive} deja comptabilisee ({dup_pay_id})",
                conn=conn,
            )
            return {
                "status": "DUPLICATE_IGNORED",
                "duplicate": True,
                "payment_id": dup_pay_id,
                "invoice_id": invoice_id,
                "solde_restant": ancien_solde,
                "statut_calcule": ancien_statut,
                "message": f"Paiement deja comptabilise (reference {reference_fictive}). Aucune double comptabilisation.",
            }

        # Recalcul deterministe de la facture dans la meme transaction
        updated = recalculate_invoice(invoice_id, conn=conn)

        # Si la facture est maintenant soldee, on annule les relances non encore envoyees (Preparation Test T11)
        cancelled_reminders = 0
        if updated["solde_restant"] <= 0.0:
            cur = conn.execute(
                """
                UPDATE reminders
                SET statut_action = 'ANNULEE',
                    motif_blocage = 'Facture soldee suite au paiement simule ' || ?
                WHERE invoice_id = ?
                  AND statut_action IN ('BROUILLON', 'EN_ATTENTE_VALIDATION', 'APPROUVEE')
                """,
                (reference_fictive, invoice_id),
            )
            cancelled_reminders = int(cur.rowcount)

        log_event(
            acteur="GESTIONNAIRE",
            action="PAIEMENT_SIMULE_ENREGISTRE",
            objet_type="FACTURE",
            objet_id=invoice_id,
            ancienne_valeur=f"Solde={ancien_solde}, Statut={ancien_statut}",
            nouvelle_valeur=f"Solde={updated['solde_restant']}, Statut={updated['statut_calcule']}",
            motif=f"Paiement fictif {reference_fictive} de {montant:,.0f} FCFA (relances annulees: {cancelled_reminders})",
            conn=conn,
        )

        return {
            "status": "RECORDED",
            "duplicate": False,
            "payment_id": payment_id,
            "invoice_id": invoice_id,
            "montant": montant,
            "reference_fictive": reference_fictive,
            "solde_restant": updated["solde_restant"],
            "statut_calcule": updated["statut_calcule"],
            "cancelled_reminders": cancelled_reminders,
            "message": "Paiement simule enregistre avec succes.",
        }
