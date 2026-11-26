"""
Couche d'acces aux donnees SQLite (Repositories) et jeu de donnees fictif de demonstration.
Toutes les requetes sont parametrees pour garantir la securite et l'integrite.
"""
from pathlib import Path
from typing import Any, Dict, List

from database.db import db_session, init_db
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


def reset_and_load_demo_portfolio(db_path: Path | None = None) -> Dict[str, Any]:
    """Reinitialise toutes les tables SQLite et charge le portefeuille fictif de demonstration."""
    init_db(db_path)
    with db_session(db_path) as conn:
        conn.execute("UPDATE settings SET value = '1' WHERE key = 'allow_audit_reset'")
        conn.execute("UPDATE settings SET value = '0' WHERE key = 'emergency_stop'")
        for table in (
            "reminders",
            "disputes",
            "installment_plans",
            "credit_notes",
            "payments",
            "invoices",
            "clients",
            "import_errors",
            "audit_logs",
        ):
            conn.execute(f"DELETE FROM {table}")
        conn.execute("UPDATE settings SET value = '0' WHERE key = 'allow_audit_reset'")

    import_res = import_portfolio_dict(DEMO_PORTFOLIO_DATA, db_path=db_path)
    reminders_res = prepare_all_reminders(db_path=db_path)
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

