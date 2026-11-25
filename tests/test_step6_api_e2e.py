"""
Tests Etape 6 - Tests d'integration de bout en bout via FastAPI TestClient
et les 6 tests complementaires (Tests 20 a 25) pour une couverture maximale du bareme BWAI 2026.
"""
from datetime import date
from pathlib import Path
import pytest
from fastapi.testclient import TestClient

import config
from app import app
from database.db import init_db, db_session
from services.ai_classifier import classifier_singleton
from services.calculator import recalculate_invoice
from services.importer import import_portfolio_dict
from services.reminder_engine import (
    apply_human_review,
    prepare_reminder_for_invoice,
    verify_and_simulate_send,
)


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    test_db = tmp_path / "test_api_e2e.db"
    monkeypatch.setattr(config, "DATABASE_PATH", test_db)
    init_db(test_db)
    return TestClient(app)


def test_api_health_and_reset_demo_and_web_dashboard(client: TestClient):
    """Test 19 : Parcours E2E API REST et rendu HTML du tableau de bord."""
    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    assert res_health.json()["framework"] == "FastAPI"

    res_reset = client.post("/api/system/reset-demo")
    assert res_reset.status_code == 200
    data_reset = res_reset.json()
    assert data_reset["import_summary"]["accepted_counts"]["invoices"] == 7

    res_dash = client.get("/api/dashboard")
    assert res_dash.status_code == 200
    kpis = res_dash.json()
    assert kpis["total_factures"] == 7
    assert kpis["nb_en_revue_humaine"] >= 2

    res_html = client.get("/")
    assert res_html.status_code == 200
    assert "FAC-2026-002" in res_html.text

    pay_payload = {
        "invoice_id": "FAC-2026-002",
        "montant": 200000,
        "reference_fictive": "MOMO-E2E-002",
    }
    res_pay1 = client.post("/api/payments/simulate", json=pay_payload)
    assert res_pay1.status_code == 200
    assert res_pay1.json()["duplicate"] is False
    assert res_pay1.json()["solde_restant"] == 0.0

    res_pay2 = client.post("/api/payments/simulate", json=pay_payload)
    assert res_pay2.status_code == 200
    assert res_pay2.json()["duplicate"] is True


def test_excel_file_upload_endpoint_e2e(client: TestClient):
    """Test 20 : Upload HTTP reel du fichier Excel multi-onglets data/demo_portfolio.xlsx via POST /api/import/upload."""
    excel_path = config.DATA_DIR / "demo_portfolio.xlsx"
    assert excel_path.exists(), "Le fichier data/demo_portfolio.xlsx doit exister"

    with excel_path.open("rb") as f:
        res = client.post(
            "/api/import/upload",
            files={"file": ("demo_portfolio.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert res.status_code == 200
    report = res.json()
    assert report["accepted_counts"]["clients"] == 5
    assert report["accepted_counts"]["invoices"] == 7
    # Le fichier Excel de demo contient 1 ligne de facture invalide (montant negatif) pour demontrer T12 en direct
    assert report["total_rejected"] == 1


def test_expired_installment_plan_becomes_overdue(tmp_path: Path):
    """Test 21 : Un echeancier dont la prochaine echeance est depassee ne protege plus la facture (redevient ECHUE)."""
    db_file = tmp_path / "test_expired_plan.db"
    init_db(db_file)
    import_portfolio_dict(
        {
            "clients": [
                {
                    "client_id": "CLI-PLAN",
                    "nom_affiche": "Menuiserie Bohicon",
                    "contact_simule": "+22997223344",
                    "canal_prefere": "WHATSAPP",
                    "consentement_contact": 1,
                    "nb_factures_historiques": 4,
                    "nb_retards_historiques": 1,
                }
            ],
            "invoices": [
                {
                    "invoice_id": "FAC-PLAN-EXP",
                    "client_id": "CLI-PLAN",
                    "date_emission": "2026-01-05",
                    "date_echeance": "2026-02-05",
                    "montant_initial": 300000,
                }
            ],
            "installment_plans": [
                {
                    "plan_id": "PLAN-EXP-01",
                    "invoice_id": "FAC-PLAN-EXP",
                    "date_prochaine_echeance": "2026-03-01",  # Depassee par rapport a la date de reference 2026-03-15
                    "montant_prevu": 150000,
                    "statut": "ACTIF",
                }
            ],
        },
        db_path=db_file,
    )
    res = recalculate_invoice("FAC-PLAN-EXP", reference_date=date(2026, 3, 15), db_path=db_file)
    assert res["statut_calcule"] == "ECHUE"
    assert res["eligible_relance"] is True


def test_t5_disputed_invoice_goes_to_human_review_queue(tmp_path: Path):
    """Test 22 : Une facture contestee (T5) place automatiquement sa relance dans la file de revue humaine."""
    db_file = tmp_path / "test_t5_queue.db"
    init_db(db_file)
    import_portfolio_dict(
        {
            "clients": [
                {
                    "client_id": "CLI-LIT",
                    "nom_affiche": "Garage Central",
                    "contact_simule": "+22997556677",
                    "canal_prefere": "WHATSAPP",
                    "consentement_contact": 1,
                    "nb_factures_historiques": 3,
                    "nb_retards_historiques": 1,
                }
            ],
            "invoices": [
                {
                    "invoice_id": "FAC-LIT-01",
                    "client_id": "CLI-LIT",
                    "date_emission": "2026-01-10",
                    "date_echeance": "2026-02-10",
                    "montant_initial": 180000,
                }
            ],
            "disputes": [
                {
                    "dispute_id": "DISP-01",
                    "invoice_id": "FAC-LIT-01",
                    "motif": "Erreur sur la quantite livree",
                    "date_ouverture": "2026-02-12",
                    "statut": "OUVERT",
                }
            ],
        },
        db_path=db_file,
    )
    prep = prepare_reminder_for_invoice("FAC-LIT-01", db_path=db_file)
    assert prep["status"] == "CREATED"
    assert prep["requiert_revue_humaine"] is True
    assert prep["statut_action"] == "EN_ATTENTE_VALIDATION"
    assert "Litige" in (prep["motif_blocage"] or "")


def test_unknown_client_history_flagged_for_human_review(tmp_path: Path):
    """Test 23 : Un nouveau client avec historique inconnu (0 facture anterieure) affiche 'inconnu' et part en revue humaine."""
    db_file = tmp_path / "test_unknown_hist.db"
    init_db(db_file)
    import_portfolio_dict(
        {
            "clients": [
                {
                    "client_id": "CLI-NEW",
                    "nom_affiche": "Nouvelle Entreprise Porto-Novo",
                    "contact_simule": "+22997889911",
                    "canal_prefere": "WHATSAPP",
                    "consentement_contact": 1,
                    "nb_factures_historiques": 0,
                    "nb_retards_historiques": 0,
                }
            ],
            "invoices": [
                {
                    "invoice_id": "FAC-NEW-01",
                    "client_id": "CLI-NEW",
                    "date_emission": "2026-01-20",
                    "date_echeance": "2026-02-20",
                    "montant_initial": 140000,
                }
            ],
        },
        db_path=db_file,
    )
    calc = recalculate_invoice("FAC-NEW-01", db_path=db_file)
    assert "inconnu" in calc["explication_score"]

    prep = prepare_reminder_for_invoice("FAC-NEW-01", db_path=db_file)
    assert prep["requiert_revue_humaine"] is True
    assert prep["statut_action"] == "EN_ATTENTE_VALIDATION"
    assert "Historique client inconnu" in (prep["motif_blocage"] or "")


def test_human_review_modify_suspend_and_reject_actions(tmp_path: Path):
    """Test 24 : Verifie les decisions humaines MODIFIER (edition du message), SUSPENDRE et REJETER."""
    db_file = tmp_path / "test_review_actions.db"
    init_db(db_file)
    import_portfolio_dict(
        {
            "clients": [
                {
                    "client_id": "CLI-REV",
                    "nom_affiche": "Societe Lokossa",
                    "contact_simule": "+22997009988",
                    "canal_prefere": "WHATSAPP",
                    "consentement_contact": 1,
                    "nb_factures_historiques": 4,
                    "nb_retards_historiques": 2,
                }
            ],
            "invoices": [
                {
                    "invoice_id": "FAC-REV-1",
                    "client_id": "CLI-REV",
                    "date_emission": "2026-01-10",
                    "date_echeance": "2026-02-10",
                    "montant_initial": 600000,
                },
                {
                    "invoice_id": "FAC-REV-2",
                    "client_id": "CLI-REV",
                    "date_emission": "2026-01-12",
                    "date_echeance": "2026-02-12",
                    "montant_initial": 550000,
                },
            ],
        },
        db_path=db_file,
    )
    prep1 = prepare_reminder_for_invoice("FAC-REV-1", db_path=db_file)
    prep2 = prepare_reminder_for_invoice("FAC-REV-2", db_path=db_file)

    # 1. SUSPENDRE maintient la relance en attente
    res_susp = apply_human_review(prep1["reminder_id"], decision="SUSPENDRE", motif="Attente appel telephonique", db_path=db_file)
    assert res_susp["statut_action"] == "EN_ATTENTE_VALIDATION"

    # 2. MODIFIER met a jour le texte et approuve la relance
    custom_msg = "Bonjour Societe Lokossa, message personnalise valide par le directeur pour FAC-REV-1 (600 000 FCFA)."
    res_mod = apply_human_review(
        prep1["reminder_id"],
        decision="MODIFIER",
        motif="Ton adapte au client grand compte",
        nouveau_message=custom_msg,
        db_path=db_file,
    )
    assert res_mod["statut_action"] == "APPROUVEE"
    assert res_mod["contenu_message"] == custom_msg

    # 3. REJETER interdit definitivement l'envoi
    res_rej = apply_human_review(prep2["reminder_id"], decision="REJETER", motif="Accord commercial en cours", db_path=db_file)
    assert res_rej["statut_action"] == "REJETEE"
    send_rej = verify_and_simulate_send(prep2["reminder_id"], db_path=db_file)
    assert send_rej["status"] == "NOT_SENDABLE"


def test_ai_classifier_holdout_test_set_accuracy():
    """
    Test 25 : Evaluation quantitative du classifieur IA sur un jeu de test separe (12 phrases inedites)
    pour documenter la Fiche IA officielle avec une Accuracy >= 80 %.
    """
    holdout_test_set = [
        ("Les articles livres sont casses nous contestons le montant de cette facture", "LITIGE"),
        ("Le prix facture ne respecte pas notre contrat nous ouvrons un litige", "LITIGE"),
        ("Marchandise non conforme a la livraison nous demandons un avoir", "LITIGE"),
        ("Pouvons-nous mettre en place un echeancier pour payer en deux fois", "DEMANDE_ECHEANCIER"),
        ("Suite a un probleme de tresorerie nous souhaitons etaler le reglement", "DEMANDE_ECHEANCIER"),
        ("Je propose de payer un acompte vendredi et le solde le mois prochain", "DEMANDE_ECHEANCIER"),
        ("J'ai deja effectue le virement bancaire ce matin merci de verifier", "ANNONCE_PAIEMENT"),
        ("La facture a ete payee hier par MTN Mobile Money voici la reference", "ANNONCE_PAIEMENT"),
        ("Notre service comptable a deja envoye le paiement la semaine derniere", "ANNONCE_PAIEMENT"),
        ("Desole pour cet oubli nous reglons la facture rapidement", "RETARD_SIMPLE"),
        ("Merci pour votre rappel nous traitons ce paiement cette semaine", "RETARD_SIMPLE"),
        ("Excusez-nous pour ce retard de reglement nous faisons le necessaire", "RETARD_SIMPLE"),
    ]

    correct = 0
    for text, expected_label in holdout_test_set:
        pred = classifier_singleton.classify(text)
        if pred["categorie"] == expected_label:
            correct += 1

    accuracy = correct / len(holdout_test_set)
    assert accuracy >= 0.80, f"L'accuracy sur le jeu de test separe ({accuracy:.2%}) doit etre >= 80%"
