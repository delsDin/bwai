"""
Tests Etape 7 - Verification de la concurrence multi-threads (traitements concurrents du TDR) :
1. 20 threads tentent d'envoyer simultanement la meme relance -> 1 seul envoi simule, 19 doublons bloques.
2. 20 threads tentent d'enregistrer simultanement le meme paiement simule -> 1 seul enregistrement, 19 doublons ignores.
3. 20 threads tentent de preparer simultanement la meme relance -> 1 seule creation, 19 doublons evites.
4. Course concurrente entre un reglement complet et l'envoi d'une relance -> etat final coherent sans double action.
"""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import threading
import pytest

from database.db import db_session, init_db
from services.ai_classifier import ClientMessageClassifier
from services.importer import import_portfolio_dict
from services.payment_service import record_simulated_payment
from services.reminder_engine import prepare_reminder_for_invoice, verify_and_simulate_send


@pytest.fixture
def concurrent_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_concurrency.db"
    init_db(db_file)
    import_portfolio_dict(
        {
            "clients": [
                {
                    "client_id": "CLI-CONC",
                    "nom_affiche": "Societe Concurrence Cotonou",
                    "contact_simule": "+22997000001",
                    "canal_prefere": "WHATSAPP",
                    "consentement_contact": 1,
                    "nb_factures_historiques": 5,
                    "nb_retards_historiques": 1,
                }
            ],
            "invoices": [
                {
                    "invoice_id": "FAC-CONC-1",
                    "client_id": "CLI-CONC",
                    "date_emission": "2026-01-10",
                    "date_echeance": "2026-02-10",
                    "montant_initial": 200000,
                },
                {
                    "invoice_id": "FAC-CONC-2",
                    "client_id": "CLI-CONC",
                    "date_emission": "2026-01-15",
                    "date_echeance": "2026-02-15",
                    "montant_initial": 180000,
                },
                {
                    "invoice_id": "FAC-CONC-3",
                    "client_id": "CLI-CONC",
                    "date_emission": "2026-01-20",
                    "date_echeance": "2026-02-20",
                    "montant_initial": 150000,
                },
            ],
        },
        db_path=db_file,
    )
    return db_file


def test_concurrent_prepare_reminder_20_threads(concurrent_db: Path):
    """20 threads preparent la meme relance en meme temps : 1 CREATED, 19 ALREADY_EXISTS."""
    num_threads = 20
    barrier = threading.Barrier(num_threads)

    def worker():
        barrier.wait()
        return prepare_reminder_for_invoice("FAC-CONC-1", db_path=concurrent_db)

    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        results = list(pool.map(lambda _: worker(), range(num_threads)))

    created = [r for r in results if r["status"] == "CREATED"]
    duplicates = [r for r in results if r["status"] == "ALREADY_EXISTS" and r["duplicate_prevented"] is True]

    assert len(created) == 1
    assert len(duplicates) == num_threads - 1

    with db_session(concurrent_db) as conn:
        cnt = conn.execute("SELECT COUNT(*) AS c FROM reminders WHERE invoice_id = 'FAC-CONC-1'").fetchone()["c"]
        assert cnt == 1


def test_concurrent_send_reminder_20_threads(concurrent_db: Path):
    """20 threads envoient la meme relance en meme temps : 1 SENT_SIMULATED, 19 ALREADY_SENT."""
    prep = prepare_reminder_for_invoice("FAC-CONC-1", db_path=concurrent_db)
    reminder_id = prep["reminder_id"]
    num_threads = 20
    barrier = threading.Barrier(num_threads)

    def worker():
        barrier.wait()
        return verify_and_simulate_send(reminder_id, db_path=concurrent_db)

    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        results = list(pool.map(lambda _: worker(), range(num_threads)))

    sent = [r for r in results if r["status"] == "SENT_SIMULATED"]
    blocked = [r for r in results if r["status"] == "ALREADY_SENT" and r["duplicate_prevented"] is True]

    assert len(sent) == 1
    assert len(blocked) == num_threads - 1

    with db_session(concurrent_db) as conn:
        audit_cnt = conn.execute(
            "SELECT COUNT(*) AS c FROM audit_logs WHERE action = 'ENVOI_SIMULE_EFFECTUE' AND objet_id = ?",
            (reminder_id,),
        ).fetchone()["c"]
        assert audit_cnt == 1


def test_concurrent_simulated_payment_20_threads(concurrent_db: Path):
    """20 threads soumettent le meme paiement en meme temps : 1 RECORDED, 19 DUPLICATE_IGNORED."""
    num_threads = 20
    barrier = threading.Barrier(num_threads)

    def worker():
        barrier.wait()
        return record_simulated_payment(
            invoice_id="FAC-CONC-2",
            montant=80000,
            reference_fictive="MOMO-CONC-20X",
            db_path=concurrent_db,
        )

    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        results = list(pool.map(lambda _: worker(), range(num_threads)))

    recorded = [r for r in results if r["status"] == "RECORDED" and r["duplicate"] is False]
    ignored = [r for r in results if r["status"] == "DUPLICATE_IGNORED" and r["duplicate"] is True]

    assert len(recorded) == 1
    assert len(ignored) == num_threads - 1

    # Le solde de 180 000 ne doit avoir ete debite qu'une seule fois de 80 000 -> 100 000 FCFA
    with db_session(concurrent_db) as conn:
        inv = conn.execute("SELECT solde_restant, total_paye FROM invoices WHERE invoice_id = 'FAC-CONC-2'").fetchone()
        assert float(inv["total_paye"]) == 80000.0
        assert float(inv["solde_restant"]) == 100000.0


def test_concurrent_distinct_payments_accumulate_exact_balance(concurrent_db: Path):
    """5 paiements partiels distincts de 30 000 FCFA envoyes en parallele soldent exactement une facture de 150 000 FCFA."""
    prep = prepare_reminder_for_invoice("FAC-CONC-3", db_path=concurrent_db)
    reminder_id = prep["reminder_id"]
    num_threads = 5
    barrier = threading.Barrier(num_threads)

    def worker(idx: int):
        barrier.wait()
        return record_simulated_payment(
            invoice_id="FAC-CONC-3",
            montant=30000,
            reference_fictive=f"MOMO-PART-{idx}",
            db_path=concurrent_db,
        )

    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        results = list(pool.map(worker, range(num_threads)))

    assert all(r["status"] == "RECORDED" for r in results)

    with db_session(concurrent_db) as conn:
        inv = conn.execute(
            "SELECT solde_restant, total_paye, statut_calcule FROM invoices WHERE invoice_id = 'FAC-CONC-3'"
        ).fetchone()
        assert float(inv["total_paye"]) == 150000.0
        assert float(inv["solde_restant"]) == 0.0
        assert inv["statut_calcule"] == "SOLDEE"

    # La relance doit etre annulee car la facture est maintenant soldee
    send_res = verify_and_simulate_send(reminder_id, db_path=concurrent_db)
    assert send_res["status"] == "CANCELLED_PRE_SEND"
    assert send_res["statut_action"] == "ANNULEE"


def test_concurrent_ai_classifier_cold_start_20_threads():
    """20 threads appellent classify() simultanement sur une instance non entrainee sans condition de course scikit-learn."""
    fresh_clf = ClientMessageClassifier()
    num_threads = 20
    barrier = threading.Barrier(num_threads)

    def worker():
        barrier.wait()
        return fresh_clf.classify("La marchandise livree est cassee je conteste cette facture")

    with ThreadPoolExecutor(max_workers=num_threads) as pool:
        results = list(pool.map(lambda _: worker(), range(num_threads)))

    assert all(r["categorie"] == "LITIGE" for r in results)
    assert fresh_clf.train_size == 250


