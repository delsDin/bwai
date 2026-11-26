"""
Tests Etape 9 - Verification de la Partie C : Securite et maitrise des actions (15 points du bareme) :
1. Authentification legere par jetons, 3 roles (GESTIONNAIRE, VALIDATEUR, ADMIN) et trace de l'acteur reel.
2. Principe des quatre yeux (celui qui prepare une relance sensible ne peut pas l'approuver lui-meme).
3. Journal d'audit infalsifiable : hachage chaine SHA-256 + triggers SQLite interdisant UPDATE et DELETE + /api/audit-logs/verify.
4. Masquage des contacts clients (telephone et email).
5. Mode d'arret d'urgence (Kill-Switch emergency_stop) suspendant tout envoi simule.
"""
from pathlib import Path
import sqlite3
import pytest
from fastapi.testclient import TestClient

import config
from app import app
from database.db import db_session, init_db
from services.audit_logger import verify_audit_chain
from services.security import mask_contact


@pytest.fixture
def sec_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    test_db = tmp_path / "test_security.db"
    monkeypatch.setattr(config, "DATABASE_PATH", test_db)
    init_db(test_db)
    client = TestClient(app)
    client.post(
        "/api/system/reset-demo",
        headers={"X-API-Token": "tok-admin-direction"},
    )
    return client


def test_c1_authentication_and_rbac_roles(sec_client: TestClient):
    """
    C.1 : Jeton invalide -> 401.
    Un GESTIONNAIRE ne peut ni approuver en revue humaine (/api/review), ni reinitialiser le systeme (/api/system/reset-demo) -> 403.
    Un VALIDATEUR peut approuver en revue humaine -> 200, et son nom d'utilisateur exact figure dans audit_logs.
    """
    # 1. Jeton inconnu -> 401
    res_bad = sec_client.post(
        "/api/system/reset-demo",
        headers={"X-API-Token": "jeton-pirate-invalide"},
    )
    assert res_bad.status_code == 401

    # 2. GESTIONNAIRE tente un reset-demo (reserve a ADMIN) -> 403
    res_gest_reset = sec_client.post(
        "/api/system/reset-demo",
        headers={"X-API-Token": "tok-gestionnaire-koffi"},
    )
    assert res_gest_reset.status_code == 403

    # 3. GESTIONNAIRE tente d'approuver une relance de gros montant (FAC-2026-007) -> 403
    rem_id = "REL-FAC-2026-007-RELANCE_2"
    res_gest_rev = sec_client.post(
        f"/api/review/{rem_id}",
        headers={"X-API-Token": "tok-gestionnaire-koffi"},
        json={"decision": "APPROUVER", "motif": "Tentative non autorisee"},
    )
    assert res_gest_rev.status_code == 403

    # 4. VALIDATEUR approuve la relance -> 200 et son identite ('val.amina') est tracee dans le journal
    res_val_rev = sec_client.post(
        f"/api/review/{rem_id}",
        headers={"X-API-Token": "tok-validateur-amina"},
        json={"decision": "APPROUVER", "motif": "Validation superviseure Amina"},
    )
    assert res_val_rev.status_code == 200
    assert res_val_rev.json()["valide_par"] == "val.amina"

    logs = sec_client.get("/api/audit-logs").json()["audit_logs"]
    assert any(l["acteur"] == "val.amina" and l["action"] == "REVUE_HUMAINE_APPROUVER" for l in logs)


def test_c2_four_eyes_principle_blocks_self_approval(sec_client: TestClient):
    """
    C.2 : Principe des quatre yeux : si 'val.amina' prepare elle-meme une relance sur un dossier sensible,
    elle ne peut pas l'approuver elle-meme (403 Forbidden), mais 'val.boris' (second validateur) peut l'approuver.
    """
    with db_session() as conn:
        conn.execute("DELETE FROM reminders WHERE invoice_id = 'FAC-2026-007'")

    # 1. val.amina prepare la relance sur la facture gros montant FAC-2026-007
    prep = sec_client.post(
        "/api/reminders/prepare/FAC-2026-007",
        headers={"X-API-Token": "tok-validateur-amina"},
    )
    assert prep.status_code == 200
    rem_id = prep.json()["reminder_id"]
    assert prep.json()["cree_par"] == "val.amina"

    # 2. val.amina tente d'approuver sa propre relance -> 403 (Violation des 4 yeux)
    self_approve = sec_client.post(
        f"/api/review/{rem_id}",
        headers={"X-API-Token": "tok-validateur-amina"},
        json={"decision": "APPROUVER", "motif": "Auto-validation interdite"},
    )
    assert self_approve.status_code == 403
    assert "4 yeux" in self_approve.json()["detail"]

    # 3. val.boris (autre validateur) approuve la relance -> 200 OK
    peer_approve = sec_client.post(
        f"/api/review/{rem_id}",
        headers={"X-API-Token": "tok-validateur-boris"},
        json={"decision": "APPROUVER", "motif": "Validation croisee conforme par Boris"},
    )
    assert peer_approve.status_code == 200
    assert peer_approve.json()["statut_action"] == "APPROUVEE"
    assert peer_approve.json()["valide_par"] == "val.boris"


def test_c3_tamper_proof_chained_audit_log_and_triggers(sec_client: TestClient):
    """
    C.3 : Journal infalsifiable :
    - /api/audit-logs/verify confirme la validite de la chaine SHA-256.
    - Toute tentative d'UPDATE ou de DELETE SQL sur audit_logs est bloquee par les triggers SQLite.
    """
    verif = sec_client.get("/api/audit-logs/verify")
    assert verif.status_code == 200
    data = verif.json()
    assert data["valid"] is True
    assert data["checked_events"] >= 3
    assert len(data["head_hash"]) == 64

    # Tentative de falsification directe par UPDATE ou DELETE en base SQLite -> IntegrityError declenchee par le trigger
    with pytest.raises(sqlite3.IntegrityError, match="immuable"):
        with db_session() as conn:
            conn.execute("UPDATE audit_logs SET motif = 'Falsifie' WHERE event_id = 1")

    with pytest.raises(sqlite3.IntegrityError, match="immuable"):
        with db_session() as conn:
            conn.execute("DELETE FROM audit_logs WHERE event_id = 1")

    # La chaine reste 100% valide apres les tentatives bloquees
    assert verify_audit_chain()["valid"] is True


def test_c4_contact_masking_data_minimization(sec_client: TestClient):
    """
    C.4 : Masquage des coordonnees clients (telephone et email) dans les reponses API.
    """
    assert mask_contact("+22997001122") == "+22997****22"
    assert mask_contact("compta@pharmalittoral.bj") == "co***@pharmalittoral.bj"

    invoices = sec_client.get("/api/invoices").json()["invoices"]
    assert len(invoices) > 0
    for inv in invoices:
        assert "contact_masque" in inv
        assert "****" in inv["contact_masque"] or "***@" in inv["contact_masque"]


def test_c5_emergency_stop_kill_switch(sec_client: TestClient):
    """
    C.5 : Mode 'tout est bloque' (interrupteur d'arret d'urgence) :
    - Quand emergency_stop est active par ADMIN, tout envoi simule est bloque (BLOCKED_EMERGENCY_STOP).
    - Quand emergency_stop est desactive, l'envoi simule reprend normalement.
    """
    rem_id = "REL-FAC-2026-003-RELANCE_2"

    # Activation de l'arret d'urgence par ADMIN
    res_stop = sec_client.post(
        "/api/system/emergency-stop",
        headers={"X-API-Token": "tok-admin-direction"},
        json={"active": True, "motif": "Verification comptable en cours"},
    )
    assert res_stop.status_code == 200
    assert res_stop.json()["emergency_stop"] is True

    # Tentative d'envoi pendant l'arret d'urgence -> bloquee sans perdre la relance
    res_send_blocked = sec_client.post(
        f"/api/reminders/{rem_id}/send",
        headers={"X-API-Token": "tok-gestionnaire-koffi"},
    )
    assert res_send_blocked.status_code == 200
    assert res_send_blocked.json()["status"] == "BLOCKED_EMERGENCY_STOP"
    assert res_send_blocked.json()["statut_action"] == "APPROUVEE"

    # Levee de l'arret d'urgence par ADMIN
    res_resume = sec_client.post(
        "/api/system/emergency-stop",
        headers={"X-API-Token": "tok-admin-direction"},
        json={"active": False, "motif": "Fin de verification comptable"},
    )
    assert res_resume.status_code == 200
    assert res_resume.json()["emergency_stop"] is False

    # L'envoi simule fonctionne a nouveau
    res_send_ok = sec_client.post(
        f"/api/reminders/{rem_id}/send",
        headers={"X-API-Token": "tok-gestionnaire-koffi"},
    )
    assert res_send_ok.status_code == 200
    assert res_send_ok.json()["status"] == "SENT_SIMULATED"
