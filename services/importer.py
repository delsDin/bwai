"""
Modules A & B - Importation locale de portefeuille (CSV, Excel ou dictionnaire) et controle strict des donnees.
Signale les lignes invalides et les doublons dans import_errors sans les integrer silencieusement (Test T12).
"""
from datetime import date, datetime, timezone
import json
from pathlib import Path
import sqlite3
from typing import Dict, Any, List
import uuid

import pandas as pd
from pydantic import ValidationError

from database.db import db_session
from models.schemas import (
    ClientImportSchema,
    InvoiceImportSchema,
    PaymentImportSchema,
    CreditNoteImportSchema,
    InstallmentPlanImportSchema,
    DisputeImportSchema,
)
from services.audit_logger import log_event
from services.calculator import recalculate_all_invoices


def _record_import_error(
    conn: sqlite3.Connection,
    batch_id: str,
    ligne_numero: int,
    entite: str,
    raw_row: Dict[str, Any],
    motif: str,
) -> Dict[str, Any]:
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    raw_str = json.dumps(raw_row, default=str, ensure_ascii=False)
    conn.execute(
        """
        INSERT INTO import_errors (batch_id, ligne_numero, entite, donnees_brutes, motif_rejet, date_import)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (batch_id, ligne_numero, entite, raw_str, motif, now_iso),
    )
    return {
        "batch_id": batch_id,
        "ligne_numero": ligne_numero,
        "entite": entite,
        "motif_rejet": motif,
        "donnees_brutes": raw_row,
    }


def import_portfolio_dict(
    payload: Dict[str, List[Dict[str, Any]]],
    db_path: Path | None = None,
    acteur: str = "GESTIONNAIRE",
) -> Dict[str, Any]:
    """
    Importe un portefeuille complet (clients, invoices, payments, credit_notes, installment_plans, disputes).
    Valide chaque ligne via Pydantic et controle les doublons et l'integrite referentielle.
    """
    batch_id = f"IMP-{uuid.uuid4().hex[:8].upper()}"
    accepted_counts = {
        "clients": 0,
        "invoices": 0,
        "payments": 0,
        "credit_notes": 0,
        "installment_plans": 0,
        "disputes": 0,
    }
    rejected_rows: List[Dict[str, Any]] = []
    duplicate_count = 0

    with db_session(db_path) as conn:
        # 1. Clients
        for idx, row in enumerate(payload.get("clients", []), start=1):
            try:
                valid = ClientImportSchema(**row)
            except ValidationError as exc:
                err_msg = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
                rejected_rows.append(_record_import_error(conn, batch_id, idx, "Client", row, f"Champ invalide ({err_msg})"))
                continue

            existing = conn.execute("SELECT 1 FROM clients WHERE client_id = ?", (valid.client_id,)).fetchone()
            if existing:
                duplicate_count += 1
                rejected_rows.append(
                    _record_import_error(conn, batch_id, idx, "Client", row, f"Doublon client_id : {valid.client_id}")
                )
                continue

            conn.execute(
                """
                INSERT INTO clients (client_id, nom_affiche, contact_simule, canal_prefere, consentement_contact, nb_factures_historiques, nb_retards_historiques)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    valid.client_id,
                    valid.nom_affiche,
                    valid.contact_simule,
                    valid.canal_prefere.value,
                    valid.consentement_contact,
                    valid.nb_factures_historiques,
                    valid.nb_retards_historiques,
                ),
            )
            accepted_counts["clients"] += 1

        # 2. Factures
        for idx, row in enumerate(payload.get("invoices", []), start=1):
            try:
                valid = InvoiceImportSchema(**row)
            except ValidationError as exc:
                err_msg = "; ".join(f"{e['loc'][0] if e['loc'] else 'date'}: {e['msg']}" for e in exc.errors())
                rejected_rows.append(_record_import_error(conn, batch_id, idx, "Facture", row, f"Champ invalide ({err_msg})"))
                continue

            existing_inv = conn.execute("SELECT 1 FROM invoices WHERE invoice_id = ?", (valid.invoice_id,)).fetchone()
            if existing_inv:
                duplicate_count += 1
                rejected_rows.append(
                    _record_import_error(conn, batch_id, idx, "Facture", row, f"Doublon invoice_id : {valid.invoice_id}")
                )
                continue

            client_exists = conn.execute("SELECT 1 FROM clients WHERE client_id = ?", (valid.client_id,)).fetchone()
            if not client_exists:
                rejected_rows.append(
                    _record_import_error(conn, batch_id, idx, "Facture", row, f"Client inconnu : {valid.client_id}")
                )
                continue

            conn.execute(
                """
                INSERT INTO invoices (invoice_id, client_id, date_emission, date_echeance, montant_initial, devise, statut_source, dernier_message_client)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    valid.invoice_id,
                    valid.client_id,
                    valid.date_emission.isoformat(),
                    valid.date_echeance.isoformat(),
                    valid.montant_initial,
                    valid.devise,
                    valid.statut_source,
                    valid.dernier_message_client,
                ),
            )
            accepted_counts["invoices"] += 1

        # 3. Paiements
        for idx, row in enumerate(payload.get("payments", []), start=1):
            try:
                valid = PaymentImportSchema(**row)
            except ValidationError as exc:
                err_msg = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
                rejected_rows.append(_record_import_error(conn, batch_id, idx, "Paiement", row, f"Champ invalide ({err_msg})"))
                continue

            inv_exists = conn.execute(
                "SELECT date_emission FROM invoices WHERE invoice_id = ?", (valid.invoice_id,)
            ).fetchone()
            if not inv_exists:
                rejected_rows.append(
                    _record_import_error(conn, batch_id, idx, "Paiement", row, f"Facture inconnue : {valid.invoice_id}")
                )
                continue

            if valid.date_paiement < date.fromisoformat(str(inv_exists["date_emission"])):
                rejected_rows.append(
                    _record_import_error(
                        conn,
                        batch_id,
                        idx,
                        "Paiement",
                        row,
                        f"Incoherence chronologique : date_paiement ({valid.date_paiement}) anterieure a date_emission ({inv_exists['date_emission']})",
                    )
                )
                continue

            dup_pay = conn.execute(
                "SELECT 1 FROM payments WHERE payment_id = ? OR (invoice_id = ? AND reference_fictive = ?)",
                (valid.payment_id, valid.invoice_id, valid.reference_fictive),
            ).fetchone()
            if dup_pay:
                duplicate_count += 1
                rejected_rows.append(
                    _record_import_error(
                        conn,
                        batch_id,
                        idx,
                        "Paiement",
                        row,
                        f"Doublon de paiement ({valid.payment_id} / {valid.reference_fictive})",
                    )
                )
                continue

            conn.execute(
                """
                INSERT INTO payments (payment_id, invoice_id, date_paiement, montant, reference_fictive, statut, source)
                VALUES (?, ?, ?, ?, ?, 'VALIDE', 'IMPORT')
                """,
                (
                    valid.payment_id,
                    valid.invoice_id,
                    valid.date_paiement.isoformat(),
                    valid.montant,
                    valid.reference_fictive,
                ),
            )
            accepted_counts["payments"] += 1

        # 4. Avoirs
        for idx, row in enumerate(payload.get("credit_notes", []), start=1):
            try:
                valid = CreditNoteImportSchema(**row)
            except ValidationError as exc:
                err_msg = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
                rejected_rows.append(_record_import_error(conn, batch_id, idx, "Avoir", row, f"Champ invalide ({err_msg})"))
                continue

            inv_exists = conn.execute(
                "SELECT date_emission FROM invoices WHERE invoice_id = ?", (valid.invoice_id,)
            ).fetchone()
            if not inv_exists:
                rejected_rows.append(
                    _record_import_error(conn, batch_id, idx, "Avoir", row, f"Facture inconnue : {valid.invoice_id}")
                )
                continue

            if valid.date_avoir < date.fromisoformat(str(inv_exists["date_emission"])):
                rejected_rows.append(
                    _record_import_error(
                        conn,
                        batch_id,
                        idx,
                        "Avoir",
                        row,
                        f"Incoherence chronologique : date_avoir ({valid.date_avoir}) anterieure a date_emission ({inv_exists['date_emission']})",
                    )
                )
                continue

            dup_cn = conn.execute("SELECT 1 FROM credit_notes WHERE credit_note_id = ?", (valid.credit_note_id,)).fetchone()
            if dup_cn:
                duplicate_count += 1
                rejected_rows.append(
                    _record_import_error(conn, batch_id, idx, "Avoir", row, f"Doublon credit_note_id : {valid.credit_note_id}")
                )
                continue

            conn.execute(
                """
                INSERT INTO credit_notes (credit_note_id, invoice_id, date_avoir, montant, motif)
                VALUES (?, ?, ?, ?, ?)
                """,
                (valid.credit_note_id, valid.invoice_id, valid.date_avoir.isoformat(), valid.montant, valid.motif),
            )
            accepted_counts["credit_notes"] += 1

        # 5. Echeanciers
        for idx, row in enumerate(payload.get("installment_plans", []), start=1):
            try:
                valid = InstallmentPlanImportSchema(**row)
            except ValidationError as exc:
                err_msg = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
                rejected_rows.append(_record_import_error(conn, batch_id, idx, "Echeancier", row, f"Champ invalide ({err_msg})"))
                continue

            inv_exists = conn.execute("SELECT 1 FROM invoices WHERE invoice_id = ?", (valid.invoice_id,)).fetchone()
            if not inv_exists:
                rejected_rows.append(
                    _record_import_error(conn, batch_id, idx, "Echeancier", row, f"Facture inconnue : {valid.invoice_id}")
                )
                continue

            dup_plan = conn.execute("SELECT 1 FROM installment_plans WHERE plan_id = ?", (valid.plan_id,)).fetchone()
            if dup_plan:
                duplicate_count += 1
                rejected_rows.append(
                    _record_import_error(conn, batch_id, idx, "Echeancier", row, f"Doublon plan_id : {valid.plan_id}")
                )
                continue

            conn.execute(
                """
                INSERT INTO installment_plans (plan_id, invoice_id, date_prochaine_echeance, montant_prevu, statut)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    valid.plan_id,
                    valid.invoice_id,
                    valid.date_prochaine_echeance.isoformat(),
                    valid.montant_prevu,
                    valid.statut,
                ),
            )
            accepted_counts["installment_plans"] += 1

        # 6. Contestations
        for idx, row in enumerate(payload.get("disputes", []), start=1):
            try:
                valid = DisputeImportSchema(**row)
            except ValidationError as exc:
                err_msg = "; ".join(f"{e['loc'][0]}: {e['msg']}" for e in exc.errors())
                rejected_rows.append(_record_import_error(conn, batch_id, idx, "Contestation", row, f"Champ invalide ({err_msg})"))
                continue

            inv_exists = conn.execute("SELECT 1 FROM invoices WHERE invoice_id = ?", (valid.invoice_id,)).fetchone()
            if not inv_exists:
                rejected_rows.append(
                    _record_import_error(conn, batch_id, idx, "Contestation", row, f"Facture inconnue : {valid.invoice_id}")
                )
                continue

            dup_disp = conn.execute("SELECT 1 FROM disputes WHERE dispute_id = ?", (valid.dispute_id,)).fetchone()
            if dup_disp:
                duplicate_count += 1
                rejected_rows.append(
                    _record_import_error(conn, batch_id, idx, "Contestation", row, f"Doublon dispute_id : {valid.dispute_id}")
                )
                continue

            conn.execute(
                """
                INSERT INTO disputes (dispute_id, invoice_id, motif, date_ouverture, statut, detecte_par)
                VALUES (?, ?, ?, ?, ?, 'HUMAIN')
                """,
                (valid.dispute_id, valid.invoice_id, valid.motif, valid.date_ouverture.isoformat(), valid.statut),
            )
            accepted_counts["disputes"] += 1

    # Recalcul deterministe de tout le portefeuille importe
    recalculate_all_invoices(db_path=db_path)

    # Si l'option de preparation automatique des relances est activee en base, prepare les relances sans clic manuel
    auto_prepared_count = 0
    with db_session(db_path) as conn:
        row_auto = conn.execute("SELECT value FROM settings WHERE key = 'auto_prepare_reminders'").fetchone()
        auto_enabled = (row_auto["value"] == "1") if row_auto else False
    if auto_enabled:
        from services.reminder_engine import prepare_all_reminders
        prep_res = prepare_all_reminders(db_path=db_path, acteur=acteur)
        auto_prepared_count = sum(1 for r in prep_res if r.get("status") == "CREATED")

    total_accepted = sum(accepted_counts.values())
    total_rejected = len(rejected_rows)

    log_event(
        acteur=acteur,
        action="IMPORT_PORTEFEUILLE",
        objet_type="BATCH",
        objet_id=batch_id,
        ancienne_valeur=None,
        nouvelle_valeur=f"Acceptees={total_accepted}, Rejetees={total_rejected}, Doublons={duplicate_count}, RelancesAuto={auto_prepared_count}",
        motif="Importation et controle des donnees",
        db_path=db_path,
    )

    return {
        "batch_id": batch_id,
        "accepted_counts": accepted_counts,
        "total_accepted": total_accepted,
        "total_rejected": total_rejected,
        "duplicate_count": duplicate_count,
        "auto_prepared_reminders": auto_prepared_count,
        "errors": rejected_rows,
    }


def _sanitize_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    cleaned: List[Dict[str, Any]] = []
    for row in records:
        cleaned.append(
            {
                k: (None if (v is None or (isinstance(v, float) and pd.isna(v)) or pd.isna(v)) else v)
                for k, v in row.items()
            }
        )
    return cleaned


def import_portfolio_file(
    file_path: Path,
    db_path: Path | None = None,
    acteur: str = "GESTIONNAIRE",
) -> Dict[str, Any]:
    """
    Lit un fichier Excel (.xlsx avec feuilles Clients, Factures, Paiements, Avoirs, Echeanciers, Contestations)
    ou un fichier CSV de factures et appelle import_portfolio_dict.
    """
    suffix = file_path.suffix.lower()
    payload: Dict[str, List[Dict[str, Any]]] = {
        "clients": [],
        "invoices": [],
        "payments": [],
        "credit_notes": [],
        "installment_plans": [],
        "disputes": [],
    }

    sheet_mapping = {
        "clients": "clients",
        "factures": "invoices",
        "invoices": "invoices",
        "paiements": "payments",
        "payments": "payments",
        "avoirs": "credit_notes",
        "credit_notes": "credit_notes",
        "echeanciers": "installment_plans",
        "installment_plans": "installment_plans",
        "contestations": "disputes",
        "disputes": "disputes",
    }

    if suffix in (".xlsx", ".xls"):
        excel_data = pd.read_excel(file_path, sheet_name=None)
        for sheet_name, df in excel_data.items():
            key = sheet_mapping.get(sheet_name.strip().lower())
            if key:
                payload[key].extend(_sanitize_records(df.to_dict(orient="records")))
    elif suffix == ".csv":
        df = pd.read_csv(file_path)
        rows = _sanitize_records(df.to_dict(orient="records"))
        if rows:
            first = rows[0]
            if "montant_initial" in first:
                payload["invoices"] = rows
            elif "nom_affiche" in first:
                payload["clients"] = rows
            elif "payment_id" in first or "reference_fictive" in first:
                payload["payments"] = rows
            elif "credit_note_id" in first:
                payload["credit_notes"] = rows
            elif "plan_id" in first or "date_prochaine_echeance" in first:
                payload["installment_plans"] = rows
            elif "dispute_id" in first:
                payload["disputes"] = rows
    else:
        raise ValueError(f"Format de fichier non supporte : {suffix}")

    return import_portfolio_dict(payload, db_path=db_path, acteur=acteur)
