"""
Tests Etape 3 - Verification de l'importation locale et du rejet des lignes invalides ou doublons (Test T12).
"""
from pathlib import Path
import pytest

from database.db import init_db, db_session
from services.importer import import_portfolio_dict


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_import.db"
    init_db(db_file)
    return db_file


def test_t12_ligne_import_invalide_et_doublon_non_integres_silencieusement(temp_db: Path):
    """
    T12 : Ligne d'import avec champ invalide -> Erreur expliquee ; ligne exclue sans integration silencieuse.
    Controle aussi le rejet des doublons a l'import.
    """
    payload = {
        "clients": [
            {
                "client_id": "CLI-100",
                "nom_affiche": "Etablissement Soglo",
                "contact_simule": "+22990001122",
                "canal_prefere": "WHATSAPP",
                "consentement_contact": 1,
                "nb_factures_historiques": 3,
                "nb_retards_historiques": 1,
            },
            # Doublon client_id
            {
                "client_id": "CLI-100",
                "nom_affiche": "Etablissement Soglo Copie",
                "contact_simule": "+22990001122",
                "canal_prefere": "WHATSAPP",
                "consentement_contact": 1,
                "nb_factures_historiques": 3,
                "nb_retards_historiques": 1,
            },
        ],
        "invoices": [
            # Facture valide
            {
                "invoice_id": "FAC-100",
                "client_id": "CLI-100",
                "date_emission": "2026-01-10",
                "date_echeance": "2026-02-10",
                "montant_initial": 250000,
            },
            # Facture invalide 1 : montant negatif
            {
                "invoice_id": "FAC-ERR-1",
                "client_id": "CLI-100",
                "date_emission": "2026-01-10",
                "date_echeance": "2026-02-10",
                "montant_initial": -50000,
            },
            # Facture invalide 2 : date d'echeance anterieure a la date d'emission
            {
                "invoice_id": "FAC-ERR-2",
                "client_id": "CLI-100",
                "date_emission": "2026-03-10",
                "date_echeance": "2026-02-01",
                "montant_initial": 100000,
            },
            # Facture invalide 3 : client inexistant
            {
                "invoice_id": "FAC-ERR-3",
                "client_id": "CLI-INCONNU",
                "date_emission": "2026-01-10",
                "date_echeance": "2026-02-10",
                "montant_initial": 100000,
            },
        ],
    }

    report = import_portfolio_dict(payload, db_path=temp_db)
    assert report["accepted_counts"]["clients"] == 1
    assert report["accepted_counts"]["invoices"] == 1
    assert report["total_rejected"] == 4
    assert report["duplicate_count"] == 1

    # Verifier qu'aucune facture invalide n'a ete inseree dans la table invoices
    with db_session(temp_db) as conn:
        inv_rows = conn.execute("SELECT invoice_id FROM invoices").fetchall()
        assert [r["invoice_id"] for r in inv_rows] == ["FAC-100"]

        err_rows = conn.execute("SELECT entite, motif_rejet FROM import_errors ORDER BY error_id").fetchall()
        assert len(err_rows) == 4
        motifs = " | ".join(r["motif_rejet"] for r in err_rows)
        assert "Doublon client_id" in motifs
        assert "montant_initial" in motifs
        assert "echeance" in motifs
        assert "Client inconnu" in motifs

