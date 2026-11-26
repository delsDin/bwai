"""
Couche d'acces aux donnees SQLite (Repositories) et jeu de donnees fictif de demonstration.
Toutes les requetes sont parametrees pour garantir la securite et l'integrite.
"""
from datetime import date
import json
from pathlib import Path
from typing import Any, Dict, List
import uuid

import config
from database.db import db_atomic, db_session, init_db
from services.audit_logger import compute_audit_hash, log_event
from services.ai_classifier import classifier_singleton
from services.calculator import recalculate_all_invoices, recalculate_invoice
from services.importer import import_portfolio_dict
from services.reminder_engine import prepare_all_reminders
from services.security import mask_contact

DEMO_PORTFOLIO_DATA: Dict[str, List[Dict[str, Any]]] = {
    "clients": [
        {
            "client_id": "CLI-01",
            "nom_affiche": "Societe Cotonou Distribution SARL",
            "contact_simule": "+22997001122",
            "canal_prefere": "WHATSAPP",
            "consentement_contact": 1,
            "nb_factures_historiques": 4,
            "nb_retards_historiques": 3,
        },
        {
            "client_id": "CLI-02",
            "nom_affiche": "Quincaillerie Etoile Dantokpa",
            "contact_simule": "+22996112233",
            "canal_prefere": "SMS",
            "consentement_contact": 1,
            "nb_factures_historiques": 4,
            "nb_retards_historiques": 2,
        },
        {
            "client_id": "CLI-03",
            "nom_affiche": "Pharmacie du Littoral",
            "contact_simule": "compta@pharmalittoral.bj",
            "canal_prefere": "EMAIL",
            "consentement_contact": 1,
            "nb_factures_historiques": 3,
            "nb_retards_historiques": 0,
        },
        {
            "client_id": "CLI-04",
            "nom_affiche": "Boulangerie Moderne Parakou",
            "contact_simule": "+22995445566",
            "canal_prefere": "WHATSAPP",
            "consentement_contact": 1,
            "nb_factures_historiques": 5,
            "nb_retards_historiques": 2,
        },
        {
            "client_id": "CLI-05",
            "nom_affiche": "AgroBenin Export SA",
            "contact_simule": "+22994889900",
            "canal_prefere": "WHATSAPP",
            "consentement_contact": 1,
            "nb_factures_historiques": 6,
            "nb_retards_historiques": 4,
        },
    ],
    "invoices": [
        # T1 : Non echue
        {
            "invoice_id": "FAC-2026-001",
            "client_id": "CLI-03",
            "date_emission": "2026-03-01",
            "date_echeance": "2026-03-25",
            "montant_initial": 120000,
            "dernier_message_client": None,
        },
        # T2 : Echue sans paiement (45 jours de retard)
        {
            "invoice_id": "FAC-2026-002",
            "client_id": "CLI-01",
            "date_emission": "2026-01-01",
            "date_echeance": "2026-01-29",
            "montant_initial": 200000,
            "dernier_message_client": "Desole pour cet oubli nous faisons le necessaire rapidement",
        },
        # T3 : Paiement partiel + avoir (30 jours de retard)
        {
            "invoice_id": "FAC-2026-003",
            "client_id": "CLI-02",
            "date_emission": "2026-01-15",
            "date_echeance": "2026-02-13",
            "montant_initial": 250000,
            "dernier_message_client": None,
        },
        # T4 : Soldee
        {
            "invoice_id": "FAC-2026-004",
            "client_id": "CLI-03",
            "date_emission": "2026-01-10",
            "date_echeance": "2026-02-10",
            "montant_initial": 95000,
            "dernier_message_client": None,
        },
        # T5 : Contestee
        {
            "invoice_id": "FAC-2026-005",
            "client_id": "CLI-04",
            "date_emission": "2026-01-20",
            "date_echeance": "2026-02-18",
            "montant_initial": 310000,
            "dernier_message_client": "La marchandise livree est endommagee je conteste cette facture",
        },
        # T6 : Couverte par un echeancier actif
        {
            "invoice_id": "FAC-2026-006",
            "client_id": "CLI-04",
            "date_emission": "2026-01-15",
            "date_echeance": "2026-02-15",
            "montant_initial": 400000,
            "dernier_message_client": "Nous demandons un etalement du reglement sur deux echeances",
        },
        # T7 : Gros montant superieur au seuil (>= 500 000 FCFA)
        {
            "invoice_id": "FAC-2026-007",
            "client_id": "CLI-05",
            "date_emission": "2026-01-10",
            "date_echeance": "2026-02-05",
            "montant_initial": 780000,
            "dernier_message_client": "Bonjour pouvez-vous nous rappeler demain matin",
        },
    ],
    "payments": [
        {
            "payment_id": "PAY-2026-01",
            "invoice_id": "FAC-2026-003",
            "date_paiement": "2026-02-20",
            "montant": 80000,
            "reference_fictive": "MOMO-DEMO-003A",
        },
        {
            "payment_id": "PAY-2026-02",
            "invoice_id": "FAC-2026-004",
            "date_paiement": "2026-02-09",
            "montant": 95000,
            "reference_fictive": "MOMO-DEMO-004A",
        },
    ],
    "credit_notes": [
        {
            "credit_note_id": "AV-2026-01",
            "invoice_id": "FAC-2026-003",
            "date_avoir": "2026-02-22",
            "montant": 20000,
            "motif": "Remise commerciale exceptionnelle",
        }
    ],
    "installment_plans": [
        {
            "plan_id": "PLAN-2026-01",
            "invoice_id": "FAC-2026-006",
            "date_prochaine_echeance": "2026-03-28",
            "montant_prevu": 200000,
            "statut": "ACTIF",
        }
    ],
    "disputes": [
        {
            "dispute_id": "LIT-2026-01",
            "invoice_id": "FAC-2026-005",
            "motif": "Cartons endommages a la livraison",
            "date_ouverture": "2026-02-20",
            "statut": "OUVERT",
        }
    ],
}


PORTFOLIO_TABLES = (
    "reminders",
    "disputes",
    "installment_plans",
    "credit_notes",
    "payments",
    "invoices",
    "clients",
    "import_errors",
    "audit_logs",
)


def _get_cache_path(db_path: Path | None = None) -> Path:
    target_db = db_path or config.DATABASE_PATH
    return target_db.parent / "app_state_cache.json"


def save_app_state_cache(db_path: Path | None = None) -> Dict[str, Any]:
    """
    Sauvegarde dans un fichier de cache JSON (et retourne) l'etat complet de l'application
    apres une importation ou une action utilisateur. Permet de recharger automatiquement
    les donnees si l'utilisateur quitte l'application sans avoir efface les donnees.
    """
    init_db(db_path)
    snapshot: Dict[str, Any] = {"has_active_data": False, "tables": {}}
    with db_session(db_path) as conn:
        for table in (
            "clients",
            "invoices",
            "payments",
            "credit_notes",
            "installment_plans",
            "disputes",
            "reminders",
            "import_errors",
            "audit_logs",
            "settings",
        ):
            rows = conn.execute(f"SELECT * FROM {table}").fetchall()
            snapshot["tables"][table] = [dict(r) for r in rows]

    has_data = (
        len(snapshot["tables"].get("invoices", [])) > 0
        or len(snapshot["tables"].get("clients", [])) > 0
        or len(snapshot["tables"].get("import_errors", [])) > 0
    )
    snapshot["has_active_data"] = has_data

    cache_file = _get_cache_path(db_path)
    if has_data:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    elif cache_file.exists():
        cache_file.unlink()

    return snapshot


def restore_from_app_state_cache(
    snapshot: Dict[str, Any] | None = None,
    db_path: Path | None = None,
) -> bool:
    """
    Restaure l'etat de l'application depuis le cache (fichier local ou payload JSON du navigateur)
    si la base est vide et que l'utilisateur n'avait pas efface ses donnees.
    """
    init_db(db_path)
    data = snapshot
    if data is None:
        cache_file = _get_cache_path(db_path)
        if not cache_file.exists():
            return False
        try:
            data = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            return False

    if not data or not data.get("has_active_data"):
        return False

    tables = data.get("tables", {})
    # Verification cryptographique obligatoire de la chaine SHA-256 des audit_logs avant toute restauration
    audit_rows = sorted(tables.get("audit_logs", []), key=lambda r: int(r.get("event_id", 0)))
    expected_prev = "GENESIS"
    for row in audit_rows:
        actual_prev = str(row.get("prev_hash", ""))
        expected_entry = compute_audit_hash(
            prev_hash=expected_prev,
            timestamp=str(row.get("timestamp", "")),
            acteur=str(row.get("acteur", "")),
            action=str(row.get("action", "")),
            objet_type=str(row.get("objet_type", "")),
            objet_id=str(row.get("objet_id", "")),
            ancienne_valeur=row.get("ancienne_valeur"),
            nouvelle_valeur=row.get("nouvelle_valeur"),
            motif=row.get("motif"),
        )
        if actual_prev != expected_prev or str(row.get("entry_hash", "")) != expected_entry:
            raise ValueError(
                f"Restauration refusee : chaine d'audit SHA-256 invalide ou falsifiee a l'evenement {row.get('event_id')}."
            )
        expected_prev = expected_entry

    with db_session(db_path) as conn:
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'allow_audit_reset'")
        for table in PORTFOLIO_TABLES:
            conn.execute(f"DELETE FROM {table}")

        for table in (
            "clients",
            "invoices",
            "payments",
            "credit_notes",
            "installment_plans",
            "disputes",
            "reminders",
            "import_errors",
            "audit_logs",
        ):
            rows = tables.get(table, [])
            for row in rows:
                cols = list(row.keys())
                placeholders = ", ".join("?" for _ in cols)
                col_names = ", ".join(cols)
                conn.execute(
                    f"INSERT OR REPLACE INTO {table} ({col_names}) VALUES ({placeholders})",
                    tuple(row[c] for c in cols),
                )
        conn.execute("UPDATE settings SET value = '0' WHERE key = 'allow_audit_reset'")

    save_app_state_cache(db_path=db_path)
    return True


def clear_all_portfolio_data(db_path: Path | None = None) -> Dict[str, Any]:
    """
    Vide toutes les donnees (factures, clients, paiements, relances, erreurs, journal)
    et supprime le cache pour reprendre a zero.
    """
    init_db(db_path)
    with db_session(db_path) as conn:
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'allow_audit_reset'")
        conn.execute("UPDATE settings SET value = '0' WHERE key = 'emergency_stop'")
        for table in PORTFOLIO_TABLES:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("UPDATE settings SET value = '0' WHERE key = 'allow_audit_reset'")

    cache_file = _get_cache_path(db_path)
    if cache_file.exists():
        cache_file.unlink()

    return {
        "status": "CLEARED",
        "total_factures": 0,
        "cache_cleared": True,
        "message": "Toutes les donnees et le cache ont ete effaces. Importez un fichier pour commencer.",
    }


def reset_and_load_demo_portfolio(db_path: Path | None = None) -> Dict[str, Any]:
    """Reinitialise toutes les tables SQLite, charge le portefeuille fictif de demonstration et met a jour le cache."""
    clear_all_portfolio_data(db_path)
    import_res = import_portfolio_dict(DEMO_PORTFOLIO_DATA, db_path=db_path)
    reminders_res = prepare_all_reminders(db_path=db_path)
    save_app_state_cache(db_path=db_path)
    return {
        "import_summary": import_res,
        "reminders_prepared": len([r for r in reminders_res if r.get("status") == "CREATED"]),
    }


def get_settings(db_path: Path | None = None) -> dict[str, str]:
    with db_session(db_path) as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {row["key"]: row["value"] for row in rows}


def update_setting(key: str, value: str, db_path: Path | None = None) -> None:
    with db_session(db_path) as conn:
        conn.execute("UPDATE settings SET value = ? WHERE key = ?", (value, key))


def get_dashboard_kpis(db_path: Path | None = None) -> Dict[str, Any]:
    with db_session(db_path) as conn:
        inv_stats = conn.execute(
            """
            SELECT
                COUNT(*) AS total_factures,
                COALESCE(SUM(montant_initial), 0.0) AS total_facture,
                COALESCE(SUM(solde_restant), 0.0) AS encours_total,
                COALESCE(SUM(CASE WHEN jours_retard > 0 AND solde_restant > 0 THEN solde_restant ELSE 0.0 END), 0.0) AS total_en_retard,
                SUM(CASE WHEN statut_calcule IN ('ECHUE', 'PARTIELLEMENT_PAYEE') AND jours_retard > 0 THEN 1 ELSE 0 END) AS nb_factures_echues,
                SUM(CASE WHEN statut_calcule IN ('CONTESTEE', 'COUVERTE_PAR_ECHEANCIER') THEN 1 ELSE 0 END) AS nb_dossiers_suspendus,
                SUM(CASE WHEN statut_calcule = 'SOLDEE' THEN 1 ELSE 0 END) AS nb_factures_soldees
            FROM invoices
            """
        ).fetchone()

        pay_stats = conn.execute(
            """
            SELECT
                COUNT(*) AS nb_paiements,
                COALESCE(SUM(montant), 0.0) AS total_paiements,
                COALESCE(SUM(CASE WHEN source = 'SIMULATION' THEN montant ELSE 0.0 END), 0.0) AS total_paiements_simules
            FROM payments
            WHERE statut = 'VALIDE'
            """
        ).fetchone()

        rem_stats = conn.execute(
            """
            SELECT
                SUM(CASE WHEN statut_action = 'EN_ATTENTE_VALIDATION' THEN 1 ELSE 0 END) AS nb_en_revue_humaine,
                SUM(CASE WHEN statut_action = 'APPROUVEE' THEN 1 ELSE 0 END) AS nb_pretes_envoi,
                SUM(CASE WHEN statut_action = 'SIMULEE' THEN 1 ELSE 0 END) AS nb_relances_simulees,
                SUM(CASE WHEN statut_action = 'ANNULEE' THEN 1 ELSE 0 END) AS nb_relances_annulees
            FROM reminders
            """
        ).fetchone()

        settings = get_settings(db_path)

        return {
            "reference_date": settings.get("reference_date"),
            "high_amount_threshold": float(settings.get("high_amount_threshold", 500000)),
            "ai_confidence_threshold": float(settings.get("ai_confidence_threshold", 0.75)),
            "emergency_stop": settings.get("emergency_stop", "0") == "1",
            "total_factures": int(inv_stats["total_factures"] or 0),
            "total_facture": float(inv_stats["total_facture"] or 0.0),
            "encours_total": float(inv_stats["encours_total"] or 0.0),
            "total_en_retard": float(inv_stats["total_en_retard"] or 0.0),
            "nb_factures_echues": int(inv_stats["nb_factures_echues"] or 0),
            "nb_dossiers_suspendus": int(inv_stats["nb_dossiers_suspendus"] or 0),
            "nb_factures_soldees": int(inv_stats["nb_factures_soldees"] or 0),
            "total_paiements": float(pay_stats["total_paiements"] or 0.0),
            "total_paiements_simules": float(pay_stats["total_paiements_simules"] or 0.0),
            "nb_en_revue_humaine": int(rem_stats["nb_en_revue_humaine"] or 0),
            "nb_pretes_envoi": int(rem_stats["nb_pretes_envoi"] or 0),
            "nb_relances_simulees": int(rem_stats["nb_relances_simulees"] or 0),
            "nb_relances_annulees": int(rem_stats["nb_relances_annulees"] or 0),
        }


def list_invoices(statut_filter: str | None = None, db_path: Path | None = None) -> list[dict[str, Any]]:
    with db_session(db_path) as conn:
        query = """
            SELECT i.*, c.nom_affiche, c.contact_simule, c.canal_prefere,
                   c.consentement_contact, c.nb_factures_historiques, c.nb_retards_historiques
            FROM invoices i
            JOIN clients c ON i.client_id = c.client_id
        """
        params: tuple[Any, ...] = ()
        if statut_filter:
            query += " WHERE i.statut_calcule = ?"
            params = (statut_filter,)
        query += " ORDER BY i.eligible_relance DESC, i.score_priorite DESC, i.jours_retard DESC"
        rows = conn.execute(query, params).fetchall()
        results = []
        for r in rows:
            item = dict(r)
            item["contact_masque"] = mask_contact(item.get("contact_simule"))
            results.append(item)
        return results


def get_invoice_detail(invoice_id: str, db_path: Path | None = None) -> Dict[str, Any] | None:
    with db_session(db_path) as conn:
        inv = conn.execute(
            """
            SELECT i.*, c.nom_affiche, c.contact_simule, c.canal_prefere,
                   c.consentement_contact, c.nb_factures_historiques, c.nb_retards_historiques
            FROM invoices i
            JOIN clients c ON i.client_id = c.client_id
            WHERE i.invoice_id = ?
            """,
            (invoice_id,),
        ).fetchone()
        if not inv:
            return None

        payments = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM payments WHERE invoice_id = ? ORDER BY date_paiement DESC", (invoice_id,)
            ).fetchall()
        ]
        credit_notes = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM credit_notes WHERE invoice_id = ? ORDER BY date_avoir DESC", (invoice_id,)
            ).fetchall()
        ]
        plans = [
            dict(r)
            for r in conn.execute("SELECT * FROM installment_plans WHERE invoice_id = ?", (invoice_id,)).fetchall()
        ]
        disputes = [
            dict(r) for r in conn.execute("SELECT * FROM disputes WHERE invoice_id = ?", (invoice_id,)).fetchall()
        ]
        reminders = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM reminders WHERE invoice_id = ? ORDER BY date_creation DESC", (invoice_id,)
            ).fetchall()
        ]

        inv_dict = dict(inv)
        inv_dict["contact_masque"] = mask_contact(inv_dict.get("contact_simule"))
        inv_dict["ai_details"] = classifier_singleton.classify(inv_dict.get("dernier_message_client"))

        return {
            "invoice": inv_dict,
            "payments": payments,
            "credit_notes": credit_notes,
            "installment_plans": plans,
            "disputes": disputes,
            "reminders": reminders,
        }


def list_reminders(statut_filter: str | None = None, db_path: Path | None = None) -> List[Dict[str, Any]]:
    with db_session(db_path) as conn:
        query = """
            SELECT r.*, i.client_id, i.montant_initial, i.solde_restant, i.jours_retard,
                   i.statut_calcule, i.score_priorite, c.nom_affiche, c.canal_prefere
            FROM reminders r
            JOIN invoices i ON r.invoice_id = i.invoice_id
            JOIN clients c ON i.client_id = c.client_id
        """
        params: tuple[Any, ...] = ()
        if statut_filter:
            query += " WHERE r.statut_action = ?"
            params = (statut_filter,)
        query += " ORDER BY i.score_priorite DESC, r.date_creation DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]


def list_import_errors(db_path: Path | None = None) -> List[Dict[str, Any]]:
    with db_session(db_path) as conn:
        rows = conn.execute("SELECT * FROM import_errors ORDER BY error_id DESC LIMIT 100").fetchall()
        return [dict(r) for r in rows]


def list_audit_logs(limit: int = 150, db_path: Path | None = None) -> List[Dict[str, Any]]:
    with db_session(db_path) as conn:
        rows = conn.execute("SELECT * FROM audit_logs ORDER BY event_id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]


def list_users(db_path: Path | None = None) -> List[Dict[str, Any]]:
    """Retourne la liste des utilisateurs et administrateurs declares en base (sans exposer token_hash)."""
    with db_session(db_path) as conn:
        rows = conn.execute(
            "SELECT user_id, username, display_name, role, statut FROM users ORDER BY user_id ASC"
        ).fetchall()
        return [dict(r) for r in rows]


def update_business_settings(
    reference_date: str,
    high_amount_threshold: float,
    ai_confidence_threshold: float,
    default_channel: str,
    acteur: str = "ADMIN",
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """Met a jour les parametres metier configurables, recalcule le portefeuille et trace la modification."""
    old_settings = get_settings(db_path)
    old_summary = (
        f"ref={old_settings.get('reference_date')}, seuil={old_settings.get('high_amount_threshold')}, "
        f"ia={old_settings.get('ai_confidence_threshold')}, canal={old_settings.get('default_channel')}"
    )
    new_summary = (
        f"ref={reference_date}, seuil={high_amount_threshold}, "
        f"ia={ai_confidence_threshold}, canal={default_channel}"
    )
    with db_atomic(db_path) as conn:
        for k, v in (
            ("reference_date", reference_date),
            ("high_amount_threshold", str(high_amount_threshold)),
            ("ai_confidence_threshold", str(ai_confidence_threshold)),
            ("default_channel", default_channel),
        ):
            conn.execute("UPDATE settings SET value = ? WHERE key = ?", (v, k))
        log_event(
            acteur=acteur,
            action="MODIFICATION_PARAMETRES_METIER",
            objet_type="SYSTEME",
            objet_id="SETTINGS",
            ancienne_valeur=old_summary,
            nouvelle_valeur=new_summary,
            motif="Mise a jour des seuils et parametres de recouvrement",
            conn=conn,
        )

    recalculated = recalculate_all_invoices(db_path=db_path)
    save_app_state_cache(db_path=db_path)
    return {
        "status": "UPDATED",
        "settings": get_settings(db_path),
        "recalculated_count": len(recalculated),
    }


def create_dispute(
    invoice_id: str,
    motif: str,
    date_ouverture: date | None = None,
    acteur: str = "GESTIONNAIRE",
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """Ouvre une contestation sur une facture, passe son statut a CONTESTEE et suspend ses relances en attente."""
    with db_atomic(db_path) as conn:
        inv = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if not inv:
            raise ValueError(f"Facture introuvable : {invoice_id}")
        old_statut = str(inv["statut_calcule"])
        row_ref = conn.execute("SELECT value FROM settings WHERE key = 'reference_date'").fetchone()
        open_date = (
            date_ouverture.isoformat()
            if date_ouverture
            else (str(row_ref["value"]) if row_ref else config.DEFAULT_REFERENCE_DATE.isoformat())
        )
        dispute_id = f"LIT-{uuid.uuid4().hex[:6].upper()}"
        conn.execute(
            """
            INSERT INTO disputes (dispute_id, invoice_id, motif, date_ouverture, statut, detecte_par)
            VALUES (?, ?, ?, ?, 'OUVERT', 'HUMAIN')
            """,
            (dispute_id, invoice_id, motif, open_date),
        )
        updated = recalculate_invoice(invoice_id, conn=conn)
        cur = conn.execute(
            """
            UPDATE reminders
            SET statut_action = 'EN_ATTENTE_VALIDATION',
                requiert_revue_humaine = 1,
                motif_blocage = 'Litige ouvert : ' || ?
            WHERE invoice_id = ?
              AND statut_action IN ('BROUILLON', 'APPROUVEE')
            """,
            (motif, invoice_id),
        )
        suspended_count = int(cur.rowcount)
        log_event(
            acteur=acteur,
            action="OUVERTURE_LITIGE",
            objet_type="FACTURE",
            objet_id=invoice_id,
            ancienne_valeur=f"Statut={old_statut}",
            nouvelle_valeur=f"Statut={updated['statut_calcule']} ({dispute_id})",
            motif=f"{motif} (relances suspendues: {suspended_count})",
            conn=conn,
        )
    save_app_state_cache(db_path=db_path)
    return {
        "status": "DISPUTE_OPENED",
        "dispute_id": dispute_id,
        "invoice_id": invoice_id,
        "statut_calcule": updated["statut_calcule"],
        "suspended_reminders": suspended_count,
    }


def resolve_dispute(
    dispute_id: str,
    motif_resolution: str,
    acteur: str = "VALIDATEUR",
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """Cloture un litige ouvert et recalcule immediatement le statut et la priorite de la facture."""
    with db_atomic(db_path) as conn:
        disp = conn.execute("SELECT * FROM disputes WHERE dispute_id = ?", (dispute_id,)).fetchone()
        if not disp:
            raise ValueError(f"Litige introuvable : {dispute_id}")
        invoice_id = str(disp["invoice_id"])
        conn.execute("UPDATE disputes SET statut = 'RESOLU' WHERE dispute_id = ?", (dispute_id,))
        updated = recalculate_invoice(invoice_id, conn=conn)
        log_event(
            acteur=acteur,
            action="RESOLUTION_LITIGE",
            objet_type="FACTURE",
            objet_id=invoice_id,
            ancienne_valeur="CONTESTEE (OUVERT)",
            nouvelle_valeur=f"Statut={updated['statut_calcule']} (LITIGE RESOLU)",
            motif=motif_resolution,
            conn=conn,
        )
    save_app_state_cache(db_path=db_path)
    return {
        "status": "DISPUTE_RESOLVED",
        "dispute_id": dispute_id,
        "invoice_id": invoice_id,
        "statut_calcule": updated["statut_calcule"],
        "score_priorite": updated["score_priorite"],
    }


def create_credit_note(
    invoice_id: str,
    montant: float,
    motif: str,
    date_avoir: date | None = None,
    acteur: str = "VALIDATEUR",
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """Emet un avoir commercial sur une facture, recalcule le solde et annule les relances si soldee."""
    with db_atomic(db_path) as conn:
        inv = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if not inv:
            raise ValueError(f"Facture introuvable : {invoice_id}")
        old_solde = float(inv["solde_restant"])
        row_ref = conn.execute("SELECT value FROM settings WHERE key = 'reference_date'").fetchone()
        cn_date = (
            date_avoir.isoformat()
            if date_avoir
            else (str(row_ref["value"]) if row_ref else config.DEFAULT_REFERENCE_DATE.isoformat())
        )
        credit_note_id = f"AV-{uuid.uuid4().hex[:6].upper()}"
        conn.execute(
            """
            INSERT INTO credit_notes (credit_note_id, invoice_id, date_avoir, montant, motif)
            VALUES (?, ?, ?, ?, ?)
            """,
            (credit_note_id, invoice_id, cn_date, montant, motif),
        )
        updated = recalculate_invoice(invoice_id, conn=conn)
        cancelled = 0
        if updated["solde_restant"] <= 0.0:
            cur = conn.execute(
                """
                UPDATE reminders
                SET statut_action = 'ANNULEE',
                    motif_blocage = 'Facture soldee par avoir ' || ?
                WHERE invoice_id = ?
                  AND statut_action IN ('BROUILLON', 'EN_ATTENTE_VALIDATION', 'APPROUVEE')
                """,
                (credit_note_id, invoice_id),
            )
            cancelled = int(cur.rowcount)
        log_event(
            acteur=acteur,
            action="EMISSION_AVOIR",
            objet_type="FACTURE",
            objet_id=invoice_id,
            ancienne_valeur=f"Solde={old_solde}",
            nouvelle_valeur=f"Solde={updated['solde_restant']}, Statut={updated['statut_calcule']}",
            motif=f"Avoir {credit_note_id} de {montant:,.0f} FCFA : {motif}",
            conn=conn,
        )
    save_app_state_cache(db_path=db_path)
    return {
        "status": "CREDIT_NOTE_CREATED",
        "credit_note_id": credit_note_id,
        "invoice_id": invoice_id,
        "solde_restant": updated["solde_restant"],
        "statut_calcule": updated["statut_calcule"],
        "cancelled_reminders": cancelled,
    }


def create_installment_plan(
    invoice_id: str,
    date_prochaine_echeance: date,
    montant_prevu: float,
    acteur: str = "VALIDATEUR",
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """Met en place un echeancier actif sur une facture, passe son statut a COUVERTE_PAR_ECHEANCIER et suspend les relances."""
    with db_atomic(db_path) as conn:
        inv = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if not inv:
            raise ValueError(f"Facture introuvable : {invoice_id}")
        old_statut = str(inv["statut_calcule"])
        plan_id = f"PLAN-{uuid.uuid4().hex[:6].upper()}"
        conn.execute(
            """
            INSERT INTO installment_plans (plan_id, invoice_id, date_prochaine_echeance, montant_prevu, statut)
            VALUES (?, ?, ?, ?, 'ACTIF')
            """,
            (plan_id, invoice_id, date_prochaine_echeance.isoformat(), montant_prevu),
        )
        updated = recalculate_invoice(invoice_id, conn=conn)
        cur = conn.execute(
            """
            UPDATE reminders
            SET statut_action = 'ANNULEE',
                motif_blocage = 'Couverte par echeancier actif ' || ?
            WHERE invoice_id = ?
              AND statut_action IN ('BROUILLON', 'EN_ATTENTE_VALIDATION', 'APPROUVEE')
            """,
            (plan_id, invoice_id),
        )
        cancelled = int(cur.rowcount)
        log_event(
            acteur=acteur,
            action="ACTIVATION_ECHEANCIER",
            objet_type="FACTURE",
            objet_id=invoice_id,
            ancienne_valeur=f"Statut={old_statut}",
            nouvelle_valeur=f"Statut={updated['statut_calcule']} (Prochaine echeance: {date_prochaine_echeance.isoformat()})",
            motif=f"Echeancier {plan_id} de {montant_prevu:,.0f} FCFA",
            conn=conn,
        )
    save_app_state_cache(db_path=db_path)
    return {
        "status": "INSTALLMENT_PLAN_CREATED",
        "plan_id": plan_id,
        "invoice_id": invoice_id,
        "statut_calcule": updated["statut_calcule"],
        "cancelled_reminders": cancelled,
    }


def dismiss_import_error(
    error_id: int,
    motif: str = "Ligne exclue manuellement apres controle",
    acteur: str = "GESTIONNAIRE",
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """Exclut / acquitte une ligne d'import invalide du journal des rejets et trace la decision."""
    with db_atomic(db_path) as conn:
        err = conn.execute("SELECT * FROM import_errors WHERE error_id = ?", (error_id,)).fetchone()
        if not err:
            raise ValueError(f"Erreur d'import introuvable : {error_id}")
        conn.execute("DELETE FROM import_errors WHERE error_id = ?", (error_id,))
        log_event(
            acteur=acteur,
            action="EXCLUSION_ERREUR_IMPORT",
            objet_type="IMPORT_ERROR",
            objet_id=f"{err['batch_id']}-L{err['ligne_numero']}",
            ancienne_valeur=str(err["motif_rejet"]),
            nouvelle_valeur="EXCLUE",
            motif=motif,
            conn=conn,
        )
    save_app_state_cache(db_path=db_path)
    return {"status": "DISMISSED", "error_id": error_id}


def resolve_import_error(
    error_id: int,
    donnees_corrigees: Dict[str, Any],
    motif: str = "Correction manuelle et reintegration",
    acteur: str = "GESTIONNAIRE",
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """Corrige et reintegre une ligne d'import rejetee (Guide Section 3, Etape 2 : corriger ou exclure)."""
    with db_session(db_path) as conn:
        err = conn.execute("SELECT * FROM import_errors WHERE error_id = ?", (error_id,)).fetchone()
        if not err:
            raise ValueError(f"Erreur d'import introuvable : {error_id}")
        entite = str(err["entite"])
        batch_id = str(err["batch_id"])
        ligne_num = int(err["ligne_numero"])

    entity_key_map = {
        "Client": "clients",
        "Facture": "invoices",
        "Paiement": "payments",
        "Avoir": "credit_notes",
        "Echeancier": "installment_plans",
        "Contestation": "disputes",
    }
    target_key = entity_key_map.get(entite, "invoices")
    report = import_portfolio_dict({target_key: [donnees_corrigees]}, db_path=db_path, acteur=acteur)
    if report["total_accepted"] == 0:
        # Supprime la nouvelle ligne d'erreur redondante creee par la tentative echouee
        with db_session(db_path) as conn:
            conn.execute("DELETE FROM import_errors WHERE batch_id = ?", (report["batch_id"],))
        err_detail = report["errors"][0]["motif_rejet"] if report["errors"] else "Donnees toujours invalides"
        raise ValueError(f"La correction soumise est invalide : {err_detail}")

    with db_atomic(db_path) as conn:
        conn.execute("DELETE FROM import_errors WHERE error_id = ?", (error_id,))
        log_event(
            acteur=acteur,
            action="CORRECTION_ERREUR_IMPORT",
            objet_type="IMPORT_ERROR",
            objet_id=f"{batch_id}-L{ligne_num}",
            ancienne_valeur=str(err["motif_rejet"]),
            nouvelle_valeur=f"CORRIGEE_ET_IMPORTEE ({entite})",
            motif=motif,
            conn=conn,
        )
    save_app_state_cache(db_path=db_path)
    return {
        "status": "RESOLVED_AND_IMPORTED",
        "error_id": error_id,
        "entite": entite,
        "import_report": report,
    }


def override_invoice_ai_classification(
    invoice_id: str,
    categorie_humaine: str,
    motif: str,
    acteur: str = "VALIDATEUR",
    db_path: Path | None = None,
) -> Dict[str, Any]:
    """
    Reprise humaine sur la qualification IA d'un message client (Critere 3 : Qualite de l'automatisation).
    Permet a un operateur de confirmer ou corriger la categorie predite par l'IA, met a jour la file de revue
    et trace la decision dans le journal SHA-256.
    """
    cat_upper = categorie_humaine.strip().upper()
    if cat_upper not in ("LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT", "RETARD_SIMPLE"):
        raise ValueError(f"Categorie humaine invalide : {categorie_humaine}")

    with db_atomic(db_path) as conn:
        inv = conn.execute("SELECT * FROM invoices WHERE invoice_id = ?", (invoice_id,)).fetchone()
        if not inv:
            raise ValueError(f"Facture introuvable : {invoice_id}")
        old_cat = str(inv["ai_categorie"] or "AUCUN_MESSAGE")
        old_conf = float(inv["ai_confiance"] or 0.0)

        conn.execute(
            """
            UPDATE invoices
            SET ai_categorie = ?,
                ai_confiance = 1.0
            WHERE invoice_id = ?
            """,
            (cat_upper, invoice_id),
        )

        if cat_upper in ("LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT"):
            conn.execute(
                """
                UPDATE reminders
                SET statut_action = 'EN_ATTENTE_VALIDATION',
                    requiert_revue_humaine = 1,
                    motif_blocage = ?
                WHERE invoice_id = ?
                  AND statut_action IN ('BROUILLON', 'APPROUVEE')
                """,
                (f"Reprise humaine IA [{cat_upper}] : {motif}", invoice_id),
            )

        log_event(
            acteur=acteur,
            action="REPRISE_HUMAINE_QUALIFICATION_IA",
            objet_type="FACTURE",
            objet_id=invoice_id,
            ancienne_valeur=f"IA={old_cat} ({old_conf:.0%})",
            nouvelle_valeur=f"HUMAIN={cat_upper} (100%)",
            motif=motif,
            conn=conn,
        )

    save_app_state_cache(db_path=db_path)
    return {
        "status": "AI_OVERRIDDEN_BY_HUMAN",
        "invoice_id": invoice_id,
        "ancienne_categorie_ia": old_cat,
        "ancienne_confiance_ia": old_conf,
        "nouvelle_categorie_humaine": cat_upper,
        "acteur": acteur,
        "motif": motif,
    }



