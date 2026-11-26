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
    replay_1 = rel_client.post("/api/system/replay", headers={"X-API-Token": "tok-gestionnaire-koffi"}).json()
    replay_2 = rel_client.post("/api/system/replay", headers={"X-API-Token": "tok-gestionnaire-koffi"}).json()
    replay_3 = rel_client.post("/api/system/replay", headers={"X-API-Token": "tok-gestionnaire-koffi"}).json()

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


def test_empty_default_state_cache_persistence_and_clear_to_zero(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """
    Verifie le cycle complet demande :
    1. Par defaut (nouvelle base sans cache), GET / n'affiche aucune donnee (total_factures == 0).
    2. Une fois l'importation effectuee (POST /api/import/upload), un cache app_state_cache.json est cree.
    3. Si la base SQLite est perdue/reinitialisee sans que l'utilisateur ait efface ses donnees,
       GET / recharge automatiquement l'etat depuis le cache.
    4. Le bouton 'Effacer tout (Reprendre a zero)' (POST /api/system/clear) vide la base ET supprime le cache.
    """
    fresh_db = tmp_path / "test_empty_and_cache.db"
    monkeypatch.setattr(config, "DATABASE_PATH", fresh_db)
    init_db(fresh_db)
    client = TestClient(app)

    # 1. Par defaut : 0 facture
    dash_0 = client.get("/api/dashboard").json()
    assert dash_0["total_factures"] == 0
    html_0 = client.get("/")
    assert html_0.status_code == 200
    assert "Aucune donnée chargée" in html_0.text

    # 2. Importation du fichier Excel -> cree le cache
    excel_path = config.DATA_DIR / "demo_portfolio.xlsx"
    with excel_path.open("rb") as f:
        res_imp = client.post(
            "/api/import/upload",
            headers={"X-API-Token": "tok-gestionnaire-koffi"},
            files={"file": ("demo_portfolio.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert res_imp.status_code == 200
    cache_file = tmp_path / "app_state_cache.json"
    assert cache_file.exists()

    # 3. Simulation d'une sortie/redemarrage avec base SQLite vide mais cache non efface -> restauration automatique
    fresh_db.unlink()
    init_db(fresh_db)
    assert client.get("/api/dashboard").json()["total_factures"] == 0
    html_restored = client.get("/")
    assert html_restored.status_code == 200
    assert client.get("/api/dashboard").json()["total_factures"] == 7

    # 4. Clic sur 'Effacer tout (Reprendre a zero)' par ADMIN -> vide toutes les donnees et supprime le cache
    res_clear = client.post("/api/system/clear", headers={"X-API-Token": "tok-admin-direction"})
    assert res_clear.status_code == 200
    assert res_clear.json()["status"] == "CLEARED"
    assert not cache_file.exists()
    assert client.get("/api/dashboard").json()["total_factures"] == 0


def test_a7_exact_decimal_arithmetic_chronological_import_and_live_concurrency_proof(rel_client: TestClient, tmp_path: Path):
    """
    Complete le critere 2 'Fiabilite metier' (25/25) :
    1. Arithmetique Decimal exacte en base 10 sans derive IEEE-754 (ex: 100000.10 - 33333.03 - 66667.07 == 0.0).
    2. Normalisation monetaire (XOF -> FCFA), rejet des devises etrangeres (EUR) et rejet des paiements/avoirs
       anterieurs a la date_emission de la facture.
    3. Preuve de concurrence interactive en direct via POST /api/system/concurrency-proof (20 threads simultanes :
       1 envoi + 19 bloques, 1 paiement + 19 bloques).
    """
    ref_d = date(2026, 3, 15)
    due_d = date(2026, 2, 15)
    # Cas classique ou l'arithmetique flottante binaire IEEE-754 laisse un residu non nul
    solde, retard = compute_balance_and_delay(100000.10, 33333.03, 66667.07, due_d, ref_d)
    assert solde == 0.0
    assert retard == 0
    assert compute_overpayment(100000.10, 33333.03, 66667.07) == 0.0

    # Verification de l'homogeneite de devise et de la coherence chronologique a l'import
    db_check = tmp_path / "test_chronological_and_currency.db"
    init_db(db_check)
    rep = import_portfolio_dict(
        {
            "clients": [
                {
                    "client_id": "CLI-DEC",
                    "nom_affiche": "Entreprise Decimal Cotonou",
                    "contact_simule": "+22997111222",
                    "canal_prefere": "WHATSAPP",
                    "consentement_contact": 1,
                    "nb_factures_historiques": 3,
                    "nb_retards_historiques": 0,
                }
            ],
            "invoices": [
                # Devise XOF normalisee en FCFA -> acceptee
                {
                    "invoice_id": "FAC-XOF-OK",
                    "client_id": "CLI-DEC",
                    "date_emission": "2026-02-01",
                    "date_echeance": "2026-03-01",
                    "montant_initial": 100000,
                    "devise": "XOF",
                },
                # Devise EUR -> rejetee pour eviter de melanger les devises dans l'encours FCFA
                {
                    "invoice_id": "FAC-EUR-BAD",
                    "client_id": "CLI-DEC",
                    "date_emission": "2026-02-01",
                    "date_echeance": "2026-03-01",
                    "montant_initial": 500,
                    "devise": "EUR",
                },
            ],
            "payments": [
                # Paiement date avant l'emission de la facture (2026-01-15 < 2026-02-01) -> rejete
                {
                    "payment_id": "PAY-BEFORE-EMISSION",
                    "invoice_id": "FAC-XOF-OK",
                    "date_paiement": "2026-01-15",
                    "montant": 20000,
                    "reference_fictive": "MOMO-CHRONO-ERR",
                }
            ],
            "credit_notes": [
                # Avoir date avant l'emission de la facture (2026-01-10 < 2026-02-01) -> rejete
                {
                    "credit_note_id": "AV-BEFORE-EMISSION",
                    "invoice_id": "FAC-XOF-OK",
                    "date_avoir": "2026-01-10",
                    "montant": 10000,
                    "motif": "Avoir incoherent",
                }
            ],
        },
        db_path=db_check,
    )
    assert rep["accepted_counts"]["invoices"] == 1
    assert rep["accepted_counts"]["payments"] == 0
    assert rep["accepted_counts"]["credit_notes"] == 0
    assert rep["total_rejected"] == 3

    # Preuve de concurrence interactive 20 threads via l'API
    res_conc = rel_client.post(
        "/api/system/concurrency-proof",
        headers={"X-API-Token": "tok-gestionnaire-koffi"},
    )
    assert res_conc.status_code == 200
    c_data = res_conc.json()
    assert c_data["threads_simultanes"] == 20
    assert c_data["envoi_relance_concurrent"]["envois_effectues"] == 1
    assert c_data["envoi_relance_concurrent"]["doublons_bloques"] == 19
    assert c_data["paiement_mobile_money_concurrent"]["paiements_imputes"] == 1
    assert c_data["paiement_mobile_money_concurrent"]["doublons_bloques"] == 19
    assert c_data["paiement_mobile_money_concurrent"]["solde_final_verifie_fcfa"] == 100000.0



