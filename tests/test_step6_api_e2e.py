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
    admin_headers = {"X-API-Token": "tok-admin-direction"}
    gest_headers = {"X-API-Token": "tok-gestionnaire-koffi"}

    res_health = client.get("/api/health")
    assert res_health.status_code == 200
    assert res_health.json()["framework"] == "FastAPI"

    res_reset = client.post("/api/system/reset-demo", headers=admin_headers)
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
    res_pay1 = client.post("/api/payments/simulate", headers=gest_headers, json=pay_payload)
    assert res_pay1.status_code == 200
    assert res_pay1.json()["duplicate"] is False
    assert res_pay1.json()["solde_restant"] == 0.0

    res_pay2 = client.post("/api/payments/simulate", headers=gest_headers, json=pay_payload)
    assert res_pay2.status_code == 200
    assert res_pay2.json()["duplicate"] is True


def test_excel_file_upload_endpoint_e2e(client: TestClient):
    """Test 20 : Upload HTTP reel du fichier Excel multi-onglets data/demo_portfolio.xlsx via POST /api/import/upload."""
    excel_path = config.DATA_DIR / "demo_portfolio.xlsx"
    assert excel_path.exists(), "Le fichier data/demo_portfolio.xlsx doit exister"

    with excel_path.open("rb") as f:
        res = client.post(
            "/api/import/upload",
            headers={"X-API-Token": "tok-gestionnaire-koffi"},
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


def test_complete_business_journey_benchmark_settings_exceptions_and_error_resolution(client: TestClient):
    """
    Verifie les fonctionnalites completant 'Utilite et parcours metier' (15/15) :
    1. Comparaison chiffree a la methode de reference Excel (GET /api/benchmark) distinguant regles metier et IA.
    2. Parametrage metier dynamique via POST /api/settings avec recalcul immediat du portefeuille.
    3. Correction et reintegration (ou exclusion) d'une ligne d'import invalide (Guide Section 3, Etape 2).
    4. Gestion interactive des exceptions sur un dossier facture : ouverture/resolution de litige, emission d'avoir,
       et activation d'un echeancier avec suspension automatique des relances.
    """
    admin_headers = {"X-API-Token": "tok-admin-direction"}
    val_headers = {"X-API-Token": "tok-validateur-amina"}
    gest_headers = {"X-API-Token": "tok-gestionnaire-koffi"}

    # Upload du fichier Excel de demo (contient 7 factures valides + 1 ligne invalide FAC-2026-ERR)
    excel_path = config.DATA_DIR / "demo_portfolio.xlsx"
    with excel_path.open("rb") as f:
        res_up = client.post(
            "/api/import/upload",
            headers=gest_headers,
            files={"file": ("demo_portfolio.xlsx", f, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert res_up.status_code == 200
    assert res_up.json()["total_rejected"] == 1

    # 1. Benchmark / comparaison a la methode de reference (distinction Regles vs IA)
    bench = client.get("/api/benchmark").json()
    assert bench["nb_factures_evaluees"] == 7
    assert bench["comparaison_temps"]["gain_temps_pct"] > 99.0
    assert "resultats_regles_deterministes" in bench
    assert "resultats_ia_ciblee" in bench
    assert bench["resultats_regles_deterministes"]["relances_injustifiees_evitees_total"] >= 4
    assert bench["resultats_ia_ciblee"]["faux_negatifs_critiques_test_holdout"] == 0

    # 2. Correction et reintegration de la ligne d'import rejetee (FAC-2026-ERR avec montant -15000 -> 150000)
    errors = client.get("/api/import/errors").json()["errors"]
    assert len(errors) == 1
    err_id = errors[0]["error_id"]

    res_resolve_err = client.post(
        f"/api/import/errors/{err_id}/resolve",
        headers=gest_headers,
        json={
            "donnees_corrigees": {
                "invoice_id": "FAC-2026-ERR",
                "client_id": "CLI-01",
                "date_emission": "2026-01-15",
                "date_echeance": "2026-02-15",
                "montant_initial": 150000,
                "devise": "FCFA",
            },
            "motif": "Correction du montant negatif apres verification piece comptable",
        },
    )
    assert res_resolve_err.status_code == 200
    assert res_resolve_err.json()["status"] == "RESOLVED_AND_IMPORTED"
    assert len(client.get("/api/import/errors").json()["errors"]) == 0
    assert client.get("/api/dashboard").json()["total_factures"] == 8

    # 3. Modification des parametres metier en direct (seuil gros montant abaisse a 300 000 FCFA, seuil IA a 0.50)
    res_set = client.post(
        "/api/settings",
        headers=val_headers,
        json={
            "reference_date": "2026-03-18",
            "high_amount_threshold": 300000.0,
            "ai_confidence_threshold": 0.50,
            "default_channel": "WHATSAPP",
        },
    )
    assert res_set.status_code == 200
    assert res_set.json()["recalculated_count"] == 8
    assert client.get("/api/dashboard").json()["reference_date"] == "2026-03-18"

    # 4. Gestion interactive des exceptions sur FAC-2026-002 (ouverture litige -> resolution -> avoir -> echeancier)
    client.post("/api/reminders/prepare/FAC-2026-003", headers=gest_headers)
    res_disp = client.post(
        "/api/disputes",
        headers=gest_headers,
        json={"invoice_id": "FAC-2026-003", "motif": "Ecart sur deux cartons a la livraison"},
    )
    assert res_disp.status_code == 200
    assert res_disp.json()["statut_calcule"] == "CONTESTEE"
    disp_id = res_disp.json()["dispute_id"]

    res_res_disp = client.post(
        f"/api/disputes/{disp_id}/resolve",
        headers=val_headers,
        json={"motif_resolution": "Accord sur emission d'un avoir de 30 000 FCFA"},
    )
    assert res_res_disp.status_code == 200
    assert res_res_disp.json()["statut_calcule"] == "PARTIELLEMENT_PAYEE"

    res_cn = client.post(
        "/api/credit-notes",
        headers=val_headers,
        json={"invoice_id": "FAC-2026-003", "montant": 30000, "motif": "Avoir suite a accord commercial"},
    )
    assert res_cn.status_code == 200
    assert res_cn.json()["solde_restant"] == 120000.0

    res_plan = client.post(
        "/api/installment-plans",
        headers=val_headers,
        json={"invoice_id": "FAC-2026-003", "date_prochaine_echeance": "2026-04-10", "montant_prevu": 60000},
    )
    assert res_plan.status_code == 200
    assert res_plan.json()["statut_calcule"] == "COUVERTE_PAR_ECHEANCIER"


def test_validated_small_features_template_exports_whatsapp_webhook_and_auto_settings(client: TestClient):
    """
    Verifie les petites fonctionnalites metier validees :
    1. Gabarit Excel telechargeable (GET /api/import/template.xlsx) et re-importable sans erreur.
    2. Exports CSV et JSON du portefeuille (avec contacts masques) et export CSV du journal d'audit.
    3. Reception simulee de message WhatsApp entrant (POST /api/webhooks/whatsapp-incoming et
       POST /api/invoices/{id}/incoming-message) basculant automatiquement une relance approuvee en revue humaine.
    4. Parametres auto_prepare_reminders et enable_pre_due_reminders + messages adaptes par canal (SMS, EMAIL, WHATSAPP).
    """
    admin_headers = {"X-API-Token": "tok-admin-direction"}
    val_headers = {"X-API-Token": "tok-validateur-amina"}
    gest_headers = {"X-API-Token": "tok-gestionnaire-koffi"}

    # 1. Telechargement du gabarit Excel (.xlsx) et import direct
    tpl_res = client.get("/api/import/template.xlsx")
    assert tpl_res.status_code == 200
    assert "spreadsheetml" in tpl_res.headers.get("content-type", "")
    assert len(tpl_res.content) > 1000

    up_tpl = client.post(
        "/api/import/upload",
        headers=gest_headers,
        files={"file": ("gabarit.xlsx", tpl_res.content, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert up_tpl.status_code == 200
    assert up_tpl.json()["total_rejected"] == 0
    assert up_tpl.json()["accepted_counts"]["invoices"] == 2

    # Chargement du portefeuille de demo complet
    client.post("/api/system/reset-demo", headers=admin_headers)

    # 2. Verification des exports CSV et JSON (minimisation des donnees : contact_masque present, contact_simule absent)
    csv_port = client.get("/api/export/portfolio.csv")
    assert csv_port.status_code == 200
    assert "FAC-2026-002" in csv_port.text
    assert "+22997****22" in csv_port.text
    assert "+22997001122" not in csv_port.text

    json_port = client.get("/api/export/portfolio.json")
    assert json_port.status_code == 200
    j_data = json_port.json()
    assert j_data["export_type"] == "BWAI_2026_PORTFOLIO_EXPORT"
    assert all("contact_simule" not in inv and "contact_masque" in inv for inv in j_data["invoices"])

    csv_audit = client.get("/api/export/audit.csv")
    assert csv_audit.status_code == 200
    assert "entry_hash" in csv_audit.text

    # 3. Reception simulee d'un message WhatsApp entrant sur FAC-2026-003 (dont la relance etait APPROUVEE)
    rems_before = {r["invoice_id"]: r for r in client.get("/api/reminders").json()["reminders"]}
    assert rems_before["FAC-2026-003"]["statut_action"] == "APPROUVEE"

    # Webhook entrant par numero/contact expediteur (+22996112233 = CLI-02 -> FAC-2026-003)
    wh_res = client.post(
        "/api/webhooks/whatsapp-incoming",
        json={
            "expediteur": "+22996112233",
            "canal": "WHATSAPP",
            "message_client": "Bonjour les cartons livres sont endommages nous contestons le solde de cette facture",
        },
    )
    assert wh_res.status_code == 200
    wh_data = wh_res.json()
    assert wh_data["simulated"] is True
    assert wh_data["invoice_id"] == "FAC-2026-003"
    assert wh_data["ai_analysis"]["categorie"] == "LITIGE"
    assert wh_data["routed_to_human_review"] is True
    assert wh_data["suspended_existing_reminders"] == 1

    rems_after = {r["invoice_id"]: r for r in client.get("/api/reminders").json()["reminders"]}
    assert rems_after["FAC-2026-003"]["statut_action"] == "EN_ATTENTE_VALIDATION"

    # Route directe par facture : POST /api/invoices/{invoice_id}/incoming-message
    inc_res = client.post(
        "/api/invoices/FAC-2026-002/incoming-message",
        headers=gest_headers,
        json={
            "canal": "SMS",
            "message_client": "J'ai deja effectue le virement bancaire ce matin merci de verifier",
        },
    )
    assert inc_res.status_code == 200
    assert inc_res.json()["ai_analysis"]["categorie"] == "ANNONCE_PAIEMENT"
    assert inc_res.json()["routed_to_human_review"] is True

    # 4. Activation de auto_prepare_reminders et enable_pre_due_reminders (FAC-2026-001 arrive a echeance le 2026-03-25 -> a J-2 le 2026-03-23)
    set_res = client.post(
        "/api/settings",
        headers=val_headers,
        json={
            "reference_date": "2026-03-23",
            "high_amount_threshold": 500000.0,
            "ai_confidence_threshold": 0.75,
            "default_channel": "WHATSAPP",
            "auto_prepare_reminders": True,
            "enable_pre_due_reminders": True,
        },
    )
    assert set_res.status_code == 200
    # FAC-2026-001 (client EMAIL, echeance 2026-03-25, ref 2026-03-23 = J-2) a recu automatiquement un RAPPEL_PRE_ECHEANCE adapte au canal EMAIL
    rems_all = {r["invoice_id"]: r for r in client.get("/api/reminders").json()["reminders"]}
    assert "FAC-2026-001" in rems_all
    assert rems_all["FAC-2026-001"]["niveau_relance"] == "RAPPEL_PRE_ECHEANCE"
    assert "[EMAIL DEMO" in rems_all["FAC-2026-001"]["contenu_message"]
    assert "[SMS DEMO]" in rems_all["FAC-2026-003"]["contenu_message"]


