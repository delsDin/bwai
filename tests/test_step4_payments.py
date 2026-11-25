"""
Tests Etape 4 - Verification des paiements simules idempotents (Test T9).
"""
from pathlib import Path
import pytest

from database.db import init_db
from services.importer import import_portfolio_dict
from services.payment_service import record_simulated_payment


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_payments.db"
    init_db(db_file)
    import_portfolio_dict(
        {
            "clients": [
                {
                    "client_id": "CLI-T9",
                    "nom_affiche": "Quincaillerie Ganhi",
                    "contact_simule": "+22997000001",
                    "canal_prefere": "WHATSAPP",
                    "consentement_contact": 1,
                    "nb_factures_historiques": 5,
                    "nb_retards_historiques": 1,
                }
            ],
            "invoices": [
                {
                    "invoice_id": "FAC-T9",
                    "client_id": "CLI-T9",
                    "date_emission": "2026-01-15",
                    "date_echeance": "2026-02-15",
                    "montant_initial": 200000,
                }
            ],
        },
        db_path=db_file,
    )
    return db_file


def test_t9_paiement_enregistre_deux_fois_une_seule_comptabilisation(temp_db: Path):
    """T9 : Paiement enregistre deux fois -> Une seule comptabilisation."""
    first = record_simulated_payment(
        invoice_id="FAC-T9",
        montant=80000,
        reference_fictive="MOMO-SIM-999",
        db_path=temp_db,
    )
    assert first["duplicate"] is False
    assert first["solde_restant"] == 120000.0
    assert first["statut_calcule"] == "PARTIELLEMENT_PAYEE"

    # Second appel avec la meme reference fictive (double clic ou re-soumission)
    second = record_simulated_payment(
        invoice_id="FAC-T9",
        montant=80000,
        reference_fictive="MOMO-SIM-999",
        db_path=temp_db,
    )
    assert second["duplicate"] is True
    assert second["status"] == "DUPLICATE_IGNORED"
    assert second["solde_restant"] == 120000.0

