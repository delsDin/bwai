"""
Tests Etape 1 - Verification de la base SQLite et des calculs deterministes (Cas T1 a T6 du Guide).
"""
from datetime import date
from pathlib import Path
import pytest

from database.db import init_db, db_session
from services.calculator import (
    compute_balance_and_delay,
    determine_invoice_status,
    recalculate_invoice,
)
from services.prioritizer import compute_priority_score

REF_DATE = date(2026, 3, 15)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_recouvrement.db"
    init_db(db_file)
    with db_session(db_file) as conn:
        conn.execute(
            """
            INSERT INTO clients (client_id, nom_affiche, contact_simule, canal_prefere, consentement_contact, nb_factures_historiques, nb_retards_historiques)
            VALUES ('CLI-01', 'Societe Kerekou SARL', '+22901020304', 'WHATSAPP', 1, 4, 2)
            """
        )
    return db_file


def test_t1_facture_non_echue_solde_positif(temp_db: Path):
    """T1 : Facture non echue, solde positif -> Statut NON_ECHUE ; 0 jour de retard."""
    with db_session(temp_db) as conn:
        conn.execute(
            """
            INSERT INTO invoices (invoice_id, client_id, date_emission, date_echeance, montant_initial)
            VALUES ('FAC-T1', 'CLI-01', '2026-03-01', '2026-03-20', 150000)
            """
        )
    res = recalculate_invoice("FAC-T1", reference_date=REF_DATE, db_path=temp_db)
    assert res["solde_restant"] == 150000.0
    assert res["jours_retard"] == 0
    assert res["statut_calcule"] == "NON_ECHUE"


def test_t2_facture_echue_aucun_paiement(temp_db: Path):
    """T2 : Facture echue, aucun paiement -> Solde exact ; nombre de jours de retard exact."""
    with db_session(temp_db) as conn:
        conn.execute(
            """
            INSERT INTO invoices (invoice_id, client_id, date_emission, date_echeance, montant_initial)
            VALUES ('FAC-T2', 'CLI-01', '2026-01-15', '2026-02-13', 200000)
            """
        )
    res = recalculate_invoice("FAC-T2", reference_date=REF_DATE, db_path=temp_db)
    assert res["solde_restant"] == 200000.0
    assert res["jours_retard"] == 30
    assert res["statut_calcule"] == "ECHUE"
    assert res["eligible_relance"] is True


def test_t3_paiement_partiel_et_avoir(temp_db: Path):
    """T3 : Paiement partiel -> Solde restant correct ; statut PARTIELLEMENT_PAYEE."""
    with db_session(temp_db) as conn:
        conn.execute(
            """
            INSERT INTO invoices (invoice_id, client_id, date_emission, date_echeance, montant_initial)
            VALUES ('FAC-T3', 'CLI-01', '2026-02-01', '2026-03-05', 300000)
            """
        )
        conn.execute(
            """
            INSERT INTO payments (payment_id, invoice_id, date_paiement, montant, reference_fictive)
            VALUES ('PAY-T3', 'FAC-T3', '2026-03-02', 100000, 'MOMO-T3-01')
            """
        )
        conn.execute(
            """
            INSERT INTO credit_notes (credit_note_id, invoice_id, date_avoir, montant, motif)
            VALUES ('AV-T3', 'FAC-T3', '2026-03-03', 20000, 'Remise commerciale')
            """
        )
    res = recalculate_invoice("FAC-T3", reference_date=REF_DATE, db_path=temp_db)
    assert res["solde_restant"] == 180000.0
    assert res["jours_retard"] == 10
    assert res["statut_calcule"] == "PARTIELLEMENT_PAYEE"
    assert res["statut_paiement"] == "PARTIELLEMENT_PAYEE"


def test_t4_paiement_total(temp_db: Path):
    """T4 : Paiement total -> Statut SOLDEE ; 0 jour de retard ; non eligible a la relance."""
    with db_session(temp_db) as conn:
        conn.execute(
            """
            INSERT INTO invoices (invoice_id, client_id, date_emission, date_echeance, montant_initial)
            VALUES ('FAC-T4', 'CLI-01', '2026-01-10', '2026-02-10', 120000)
            """
        )
        conn.execute(
            """
            INSERT INTO payments (payment_id, invoice_id, date_paiement, montant, reference_fictive)
            VALUES ('PAY-T4', 'FAC-T4', '2026-02-15', 120000, 'MOMO-T4-01')
            """
        )
    res = recalculate_invoice("FAC-T4", reference_date=REF_DATE, db_path=temp_db)
    assert res["solde_restant"] == 0.0
    assert res["jours_retard"] == 0
    assert res["statut_calcule"] == "SOLDEE"
    assert res["eligible_relance"] is False


def test_t5_facture_contestee(temp_db: Path):
    """T5 : Facture contestee -> Statut CONTESTEE ; relance automatique suspendue."""
    with db_session(temp_db) as conn:
        conn.execute(
            """
            INSERT INTO invoices (invoice_id, client_id, date_emission, date_echeance, montant_initial)
            VALUES ('FAC-T5', 'CLI-01', '2026-01-10', '2026-02-10', 250000)
            """
        )
        conn.execute(
            """
            INSERT INTO disputes (dispute_id, invoice_id, motif, date_ouverture, statut)
            VALUES ('LIT-T5', 'FAC-T5', 'Marchandise livree endommagee', '2026-02-12', 'OUVERT')
            """
        )
    res = recalculate_invoice("FAC-T5", reference_date=REF_DATE, db_path=temp_db)
    assert res["statut_calcule"] == "CONTESTEE"
    assert res["eligible_relance"] is False


def test_t6_echeancier_actif(temp_db: Path):
    """T6 : Echeancier actif -> Statut COUVERTE_PAR_ECHEANCIER ; pas de relance inappropriee."""
    with db_session(temp_db) as conn:
        conn.execute(
            """
            INSERT INTO invoices (invoice_id, client_id, date_emission, date_echeance, montant_initial)
            VALUES ('FAC-T6', 'CLI-01', '2026-01-10', '2026-02-10', 400000)
            """
        )
        conn.execute(
            """
            INSERT INTO installment_plans (plan_id, invoice_id, date_prochaine_echeance, montant_prevu, statut)
            VALUES ('PLAN-T6', 'FAC-T6', '2026-03-25', 200000, 'ACTIF')
            """
        )
    res = recalculate_invoice("FAC-T6", reference_date=REF_DATE, db_path=temp_db)
    assert res["statut_calcule"] == "COUVERTE_PAR_ECHEANCIER"
    assert res["eligible_relance"] is False


def test_prioritizer_guide_formula():
    """Verifie la formule exacte de la Section 5 du Guide : 0.40 * retard + 0.35 * solde + 0.25 * historique."""
    # Client A du Guide : 30j de retard (0.50), solde 150 000 / 500 000 (0.30), historique 2/4 (0.50)
    # Score attendu = 0.40*0.50 + 0.35*0.30 + 0.25*0.50 = 0.20 + 0.105 + 0.125 = 0.430
    score, explication, eligible, incertain = compute_priority_score(
        jours_retard=30,
        solde_restant=150_000,
        nb_retards_historiques=2,
        nb_factures_historiques=4,
        statut_calcule="ECHUE",
        high_amount_threshold=500_000,
    )
    assert score == 0.43
    assert eligible is True
    assert incertain is False
    assert "0.430" in explication

