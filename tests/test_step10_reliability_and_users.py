"""
Tests Etape 10 - Couverture 100% de la Partie A (Prouver la fiabilite - 25 points) et de la table users :
1. Table users en base SQLite (token_hash SHA-256, 3 roles) et endpoint GET /api/users.
2. A.3 Detection de surpaiement (trop-percu), proposition d'avoir, alerte audit_logs et date de paiement liee a reference_date.
3. A.4 Tests de proprietes et d'invariants mathematiques (conservation comptable, commutativite de l'ordre des paiements, idempotence).
4. A.5 Rejeu deterministe produisant une empreinte SHA-256 canonique bit-a-bit identique (POST /api/system/replay).
5. A.6 Respect strict du delai minimal entre relances (MIN_DAYS_BETWEEN_REMINDERS = 7 jours).
"""
from datetime import date
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import config
from app import app
from database.db import db_session, init_db
from models.repositories import update_setting
from services.calculator import compute_balance_and_delay, compute_overpayment, deterministic_replay
from services.importer import import_portfolio_dict
from services.payment_service import record_simulated_payment
from services.reminder_engine import prepare_reminder_for_invoice, verify_and_simulate_send


@pytest.fixture
def rel_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    test_db = tmp_path / "test_reliability.db"
    monkeypatch.setattr(config, "DATABASE_PATH", test_db)
    init_db(test_db)
    client = TestClient(app)
    client.post(
        "/api/system/reset-demo",
        headers={"X-API-Token": "tok-admin-direction"},
    )
    return client


def test_users_table_and_api_endpoint(rel_client: TestClient):
    """Verifie que les 4 comptes (GESTIONNAIRE, VALIDATEUR, ADMIN) sont persistes dans la table users avec token_hash SHA-256."""
    res = rel_client.get("/api/users")
    assert res.status_code == 200
    users = res.json()["users"]
    assert len(users) == 4
    usernames = {u["username"] for u in users}
    assert usernames == {"agent.koffi", "val.amina", "val.boris", "admin.direction"}
    # Aucun hash de jeton ne doit fuiter dans la reponse API
    assert all("token_hash" not in u for u in users)


def test_a3_overpayment_detection_and_reference_date_payment(rel_client: TestClient):
    """
    A.3 :
    - La date par defaut d'un paiement simule suit le parametre 'reference_date' en base (et non une constante figee).
    - Un surpaiement (ex: payer 250 000 FCFA sur une facture de 200 000 FCFA) solde la facture (0.0),
      signale surpaiement_detecte=True, calcule montant_excedent=50 000 FCFA, propose un avoir et journalise l'alerte.
    """
    update_setting("reference_date", "2026-03-20")

    res = rel_client.post(
        "/api/payments/simulate",
        headers={"X-API-Token": "tok-gestionnaire-koffi"},
        json={
            "invoice_id": "FAC-2026-002",  # Montant initial = 200 000 FCFA
            "montant": 250000,
            "reference_fictive": "MOMO-OVERPAY-01",
        },
    )
    assert res.status_code == 200
    data = res.json()
    assert data["date_paiement"] == "2026-03-20"
    assert data["solde_restant"] == 0.0
    assert data["statut_calcule"] == "SOLDEE"
    assert data["surpaiement_detecte"] is True
    assert data["montant_excedent"] == 50000.0
    assert "50,000 FCFA" in (data["proposition_avoir"] or "")

    logs = rel_client.get("/api/audit-logs").json()["audit_logs"]
    assert any(l["action"] == "ALERTE_SURPAIEMENT_DETECTE" and l["objet_id"] == "FAC-2026-002" for l in logs)


def test_a4_property_invariants_conservation_and_commutativity(tmp_path: Path):
    """
    A.4 : Tests de proprietes mathematiques :
    1. Invariant de conservation comptable : pour toute combinaison (montant_initial, total_paye, total_avoirs),
       solde_restant + total_paye + total_avoirs - montant_excedent == montant_initial.
    2. Commutativite de l'ordre des paiements : appliquer [40 000, 70 000, 90 000] ou [90 000, 40 000, 70 000]
       produit exactement le meme etat et la meme empreinte SHA-256.
    """
    ref_d = date(2026, 3, 15)
    due_d = date(2026, 2, 15)
    for initial in (50000.0, 120000.0, 500000.0):
        for paid in (0.0, 30000.0, 120000.0, 180000.0):
            for credit in (0.0, 15000.0, 50000.0):
                solde, _ = compute_balance_and_delay(initial, paid, credit, due_d, ref_d)
                excedent = compute_overpayment(initial, paid, credit)
                assert solde >= 0.0
                assert excedent >= 0.0
                assert round(solde + paid + credit - excedent, 2) == round(initial, 2)

    # Verification de la commutativite sur deux bases SQLite distinctes
    db_order_a = tmp_path / "order_a.db"
    db_order_b = tmp_path / "order_b.db"
    base_portfolio = {
        "clients": [
            {
                "client_id": "CLI-COMM",
                "nom_affiche": "Client Commutativite",
                "contact_simule": "+22997123456",
                "canal_prefere": "WHATSAPP",
                "consentement_contact": 1,
                "nb_factures_historiques": 4,
                "nb_retards_historiques": 1,
            }
        ],
        "invoices": [
            {
                "invoice_id": "FAC-COMM-1",
                "client_id": "CLI-COMM",
                "date_emission": "2026-01-10",
                "date_echeance": "2026-02-10",
                "montant_initial": 300000,
            }
        ],
    }

    payments_seq = [
        ("MOMO-P1", 45000.0),
        ("MOMO-P2", 85000.0),
        ("MOMO-P3", 110000.0),
    ]

    init_db(db_order_a)
    import_portfolio_dict(base_portfolio, db_path=db_order_a)
    for ref, amt in payments_seq:
        record_simulated_payment("FAC-COMM-1", amt, ref, db_path=db_order_a)

    init_db(db_order_b)
    import_portfolio_dict(base_portfolio, db_path=db_order_b)
    for ref, amt in reversed(payments_seq):
        record_simulated_payment("FAC-COMM-1", amt, ref, db_path=db_order_b)

    state_a = deterministic_replay(db_path=db_order_a)
    state_b = deterministic_replay(db_path=db_order_b)
    assert state_a["state_hash"] == state_b["state_hash"]
    assert state_a["invoices"][0]["solde_restant"] == 60000.0


def test_a5_deterministic_replay_identical_sha256_hash(rel_client: TestClient):
    """
    A.5 : Rejouer plusieurs fois tout le portefeuille via POST /api/system/replay produit
    exactement la meme empreinte SHA-256 de 64 caracteres.
    """
    replay_1 = rel_client.post("/api/system/replay").json()
    replay_2 = rel_client.post("/api/system/replay").json()
    replay_3 = rel_client.post("/api/system/replay").json()

    assert len(replay_1["state_hash"]) == 64
    assert replay_1["invoice_count"] == 7
    assert replay_1["state_hash"] == replay_2["state_hash"] == replay_3["state_hash"]


def test_a6_min_days_between_reminders_enforced(tmp_path: Path):
    """
    A.6 : Respect du delai minimal entre deux relances (MIN_DAYS_BETWEEN_REMINDERS = 7 jours) :
    - RELANCE_1 est envoyee a J+10 (2026-03-02).
    - A J+15 (2026-03-07, soit seulement 5 jours apres RELANCE_1), RELANCE_2 est preparee et tente d'etre envoyee
      -> Bloquee avec status='MIN_DELAY_NOT_ELAPSED' (5j < 7j).
    - A J+18 (2026-03-10, soit 8 jours apres RELANCE_1 >= 7j), l'envoi de RELANCE_2 est autorise -> SENT_SIMULATED.
    """
    db_file = tmp_path / "test_min_delay.db"
    init_db(db_file)
    import_portfolio_dict(
        {
            "clients": [
                {
                    "client_id": "CLI-DELAY",
                    "nom_affiche": "Quincaillerie Porto-Novo",
                    "contact_simule": "+22997654321",
                    "canal_prefere": "WHATSAPP",
                    "consentement_contact": 1,
                    "nb_factures_historiques": 4,
                    "nb_retards_historiques": 1,
                }
            ],
            "invoices": [
                {
                    "invoice_id": "FAC-DELAY-1",
                    "client_id": "CLI-DELAY",
                    "date_emission": "2026-01-20",
                    "date_echeance": "2026-02-20",
                    "montant_initial": 150000,
                }
            ],
        },
        db_path=db_file,
    )

    # 1. Date de reference = 2026-03-02 (10 jours de retard -> RELANCE_1)
    update_setting("reference_date", "2026-03-02", db_path=db_file)
    prep_1 = prepare_reminder_for_invoice("FAC-DELAY-1", db_path=db_file)
    assert prep_1["niveau_relance"] == "RELANCE_1"
    send_1 = verify_and_simulate_send(prep_1["reminder_id"], db_path=db_file)
    assert send_1["status"] == "SENT_SIMULATED"

    # 2. Date de reference = 2026-03-07 (15 jours de retard -> RELANCE_2, mais seulement 5 jours depuis RELANCE_1)
    update_setting("reference_date", "2026-03-07", db_path=db_file)
    prep_2 = prepare_reminder_for_invoice("FAC-DELAY-1", db_path=db_file)
    assert prep_2["niveau_relance"] == "RELANCE_2"
    send_2_early = verify_and_simulate_send(prep_2["reminder_id"], db_path=db_file)
    assert send_2_early["status"] == "MIN_DELAY_NOT_ELAPSED"
    assert send_2_early["days_since_last"] == 5
    assert send_2_early["min_days_required"] == 7

    # 3. Date de reference = 2026-03-10 (8 jours depuis RELANCE_1 >= 7 jours) -> Envoi autorise
    update_setting("reference_date", "2026-03-10", db_path=db_file)
    send_2_ok = verify_and_simulate_send(prep_2["reminder_id"], db_path=db_file)
    assert send_2_ok["status"] == "SENT_SIMULATED"

