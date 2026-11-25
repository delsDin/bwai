"""
Tests Etape 5 - Verification des relances, revue humaine, garde-fou pre-envoi et reprise (Tests T5, T7, T8, T10, T11).
"""
from pathlib import Path
import pytest

from database.db import init_db
from services.importer import import_portfolio_dict
from services.payment_service import record_simulated_payment
from services.recovery_service import resume_after_interruption
from services.reminder_engine import (
    apply_human_review,
    prepare_reminder_for_invoice,
    verify_and_simulate_send,
)


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_reminders.db"
    init_db(db_file)
    import_portfolio_dict(
        {
            "clients": [
                {
                    "client_id": "CLI-STD",
                    "nom_affiche": "Boutique Akpakpa",
                    "contact_simule": "+22997111111",
                    "canal_prefere": "WHATSAPP",
                    "consentement_contact": 1,
                    "nb_factures_historiques": 4,
                    "nb_retards_historiques": 1,
                },
                {
                    "client_id": "CLI-NOCONSENT",
                    "nom_affiche": "Import Export Cotonou",
                    "contact_simule": "contact@iecotonou.bj",
                    "canal_prefere": "EMAIL",
                    "consentement_contact": 1,
                    "nb_factures_historiques": 3,
                    "nb_retards_historiques": 0,
                },
            ],
            "invoices": [
                # T7 : Gros montant >= 500 000 FCFA
                {
                    "invoice_id": "FAC-T7",
                    "client_id": "CLI-STD",
                    "date_emission": "2026-01-10",
                    "date_echeance": "2026-02-10",
                    "montant_initial": 650000,
                },
                # T8 : Preference de contact EMAIL mais tentative d'envoi sur SMS
                {
                    "invoice_id": "FAC-T8",
                    "client_id": "CLI-NOCONSENT",
                    "date_emission": "2026-01-10",
                    "date_echeance": "2026-02-10",
                    "montant_initial": 120000,
                },
                # T10 & T11 : Facture echue standard (solde 150 000 FCFA)
                {
                    "invoice_id": "FAC-T11",
                    "client_id": "CLI-STD",
                    "date_emission": "2026-01-20",
                    "date_echeance": "2026-02-20",
                    "montant_initial": 150000,
                },
            ],
        },
        db_path=db_file,
    )
    return db_file


def test_t7_montant_superieur_au_seuil_validation_humaine_obligatoire(temp_db: Path):
    """T7 : Montant superieur au seuil (650 000 >= 500 000) -> Validation humaine obligatoire."""
    prep = prepare_reminder_for_invoice("FAC-T7", db_path=temp_db)
    assert prep["status"] == "CREATED"
    assert prep["requiert_revue_humaine"] is True
    assert prep["statut_action"] == "EN_ATTENTE_VALIDATION"
    assert "Montant eleve" in (prep["motif_blocage"] or "")

    # Tant qu'un humain n'a pas approuve, la tentative d'envoi est bloquee
    send_attempt = verify_and_simulate_send(prep["reminder_id"], db_path=temp_db)
    assert send_attempt["status"] == "PENDING_HUMAN_REVIEW"
    assert send_attempt["statut_action"] == "EN_ATTENTE_VALIDATION"

    # Apres approbation humaine, l'envoi simule fonctionne
    apply_human_review(prep["reminder_id"], decision="APPROUVER", motif="Valide par la direction", db_path=temp_db)
    send_ok = verify_and_simulate_send(prep["reminder_id"], db_path=temp_db)
    assert send_ok["status"] == "SENT_SIMULATED"
    assert send_ok["statut_action"] == "SIMULEE"


def test_t8_preference_de_contact_incompatible_aucune_simulation_envoi(temp_db: Path):
    """T8 : Preference de contact incompatible (client EMAIL, tentative SMS) -> Aucune simulation d'envoi sur ce canal."""
    prep = prepare_reminder_for_invoice("FAC-T8", db_path=temp_db)
    assert prep["statut_action"] == "APPROUVEE"
    assert prep["canal"] == "EMAIL"

    # Tentative de forcer l'envoi sur SMS alors que la preference client est EMAIL
    send_res = verify_and_simulate_send(prep["reminder_id"], requested_channel="SMS", db_path=temp_db)
    assert send_res["status"] == "BLOCKED_CHANNEL_PREFERENCE"
    assert send_res["statut_action"] == "ANNULEE"


def test_t10_double_clic_et_reprise_apres_interruption_sans_doublon(temp_db: Path):
    """T10 : Double clic / reprise apres interruption -> Pas de double relance."""
    first_prep = prepare_reminder_for_invoice("FAC-T11", db_path=temp_db)
    assert first_prep["status"] == "CREATED"
    assert first_prep["duplicate_prevented"] is False

    # Double clic sur la preparation de relance
    second_prep = prepare_reminder_for_invoice("FAC-T11", db_path=temp_db)
    assert second_prep["status"] == "ALREADY_EXISTS"
    assert second_prep["duplicate_prevented"] is True

    # Simulation d'une reprise apres interruption sur tout le portefeuille
    recovery = resume_after_interruption(db_path=temp_db)
    assert recovery["duplicates_prevented"] >= 1

    # Envoi simule puis double clic sur le bouton d'envoi
    first_send = verify_and_simulate_send(first_prep["reminder_id"], db_path=temp_db)
    assert first_send["status"] == "SENT_SIMULATED"

    second_send = verify_and_simulate_send(first_prep["reminder_id"], db_path=temp_db)
    assert second_send["status"] == "ALREADY_SENT"
    assert second_send["duplicate_prevented"] is True


def test_t11_relance_preparee_puis_facture_reglee_annule_la_relance(temp_db: Path):
    """T11 : Relance preparee puis facture reglee -> Nouvelle verification annule la relance."""
    prep = prepare_reminder_for_invoice("FAC-T11", db_path=temp_db)
    reminder_id = prep["reminder_id"]
    assert prep["statut_action"] == "APPROUVEE"

    # Le client regle la totalite de la facture (150 000 FCFA) avant l'envoi de la relance
    pay = record_simulated_payment(
        invoice_id="FAC-T11",
        montant=150000,
        reference_fictive="MOMO-T11-FULL",
        db_path=temp_db,
    )
    assert pay["solde_restant"] == 0.0
    assert pay["statut_calcule"] == "SOLDEE"

    # Au moment de l'envoi simule, la reverification confirme l'annulation
    send_check = verify_and_simulate_send(reminder_id, db_path=temp_db)
    assert send_check["status"] == "CANCELLED_PRE_SEND"
    assert send_check["statut_action"] == "ANNULEE"

