"""
Routes API REST FastAPI (/api/...) documentees automatiquement dans Swagger (/docs).
Couvre tous les modules A a L du Guide BWAI 2026.
"""
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File

from models.repositories import (
    clear_all_portfolio_data,
    get_dashboard_kpis,
    get_invoice_detail,
    get_settings,
    list_audit_logs,
    list_import_errors,
    list_invoices,
    list_reminders,
    list_users,
    reset_and_load_demo_portfolio,
    restore_from_app_state_cache,
    save_app_state_cache,
    update_setting,
)
from models.schemas import (
    AIClassificationRequest,
    EmergencyStopRequest,
    HumanReviewDecisionRequest,
    SimulatedPaymentRequest,
)
from services.ai_classifier import classifier_singleton
from services.audit_logger import log_event, verify_audit_chain
from services.calculator import deterministic_replay, recalculate_all_invoices
from services.importer import import_portfolio_file
from services.payment_service import record_simulated_payment
from services.recovery_service import resume_after_interruption
from services.reminder_engine import (
    apply_human_review,
    prepare_all_reminders,
    prepare_reminder_for_invoice,
    verify_and_simulate_send,
)
from services.security import require_roles

router = APIRouter(tags=["API Recouvrement BWAI 2026"])


@router.get("/health")
def health_check():
    return {"status": "ok", "framework": "FastAPI", "offline_ready": True}


@router.get("/dashboard")
def api_get_dashboard():
    return get_dashboard_kpis()


@router.get("/invoices")
def api_list_invoices(statut: Optional[str] = None):
    return {"invoices": list_invoices(statut_filter=statut)}


@router.get("/invoices/{invoice_id}")
def api_get_invoice(invoice_id: str):
    detail = get_invoice_detail(invoice_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Facture introuvable : {invoice_id}")
    return detail


@router.post("/recalculate")
def api_recalculate_all():
    results = recalculate_all_invoices()
    return {"status": "ok", "recalculated_count": len(results), "invoices": results}


@router.post("/import/upload")
async def api_import_file(file: UploadFile = File(...)):
    suffix = Path(file.filename or "portfolio.xlsx").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        report = import_portfolio_file(tmp_path)
        save_app_state_cache()
        return report
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


@router.get("/import/errors")
def api_get_import_errors():
    return {"errors": list_import_errors()}


@router.get("/reminders")
def api_list_reminders(statut: Optional[str] = None):
    return {"reminders": list_reminders(statut_filter=statut)}


@router.post("/reminders/prepare-all")
def api_prepare_all_reminders(
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    results = prepare_all_reminders(acteur=actor["username"])
    save_app_state_cache()
    return {"status": "ok", "actor": actor["username"], "results": results}


@router.post("/reminders/prepare/{invoice_id}")
def api_prepare_single_reminder(
    invoice_id: str,
    canal: Optional[str] = None,
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    try:
        res = prepare_reminder_for_invoice(
            invoice_id,
            requested_channel=canal,
            acteur=actor["username"],
        )
        save_app_state_cache()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/review/{reminder_id}")
def api_apply_human_review(
    reminder_id: str,
    payload: HumanReviewDecisionRequest,
    actor: Dict[str, str] = Depends(require_roles("VALIDATEUR", "ADMIN")),
):
    try:
        res = apply_human_review(
            reminder_id=reminder_id,
            decision=payload.decision,
            motif=payload.motif,
            nouveau_message=payload.nouveau_message,
            acteur=actor["username"],
        )
        save_app_state_cache()
        return res
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reminders/{reminder_id}/send")
def api_verify_and_send_reminder(
    reminder_id: str,
    canal: Optional[str] = None,
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    try:
        res = verify_and_simulate_send(
            reminder_id=reminder_id,
            requested_channel=canal,
            acteur=actor["username"],
        )
        save_app_state_cache()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/payments/simulate")
def api_simulate_payment(
    payload: SimulatedPaymentRequest,
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    try:
        res = record_simulated_payment(
            invoice_id=payload.invoice_id,
            montant=payload.montant,
            reference_fictive=payload.reference_fictive,
            date_paiement=payload.date_paiement,
            acteur=actor["username"],
        )
        save_app_state_cache()
        return res
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/classify")
def classify_client_message(payload: AIClassificationRequest):
    return classifier_singleton.classify(payload.message_client)


@router.post("/system/recover")
def api_recover_after_interruption(
    actor: Dict[str, str] = Depends(require_roles("VALIDATEUR", "ADMIN")),
):
    return resume_after_interruption()


@router.post("/system/reset-demo")
def api_reset_demo(
    actor: Dict[str, str] = Depends(require_roles("ADMIN")),
):
    return reset_and_load_demo_portfolio()


@router.post("/system/clear")
def api_clear_all_data(
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    return clear_all_portfolio_data()


@router.get("/system/cache")
def api_get_app_state_cache():
    return save_app_state_cache()


@router.post("/system/cache/restore")
def api_restore_app_state_cache(payload: Dict[str, Any] = Body(...)):
    restored = restore_from_app_state_cache(snapshot=payload)
    return {"status": "RESTORED" if restored else "EMPTY", "restored": restored}


@router.post("/system/emergency-stop")
def api_set_emergency_stop(
    payload: EmergencyStopRequest,
    actor: Dict[str, str] = Depends(require_roles("ADMIN")),
):
    settings = get_settings()
    old_val = settings.get("emergency_stop", "0")
    new_val = "1" if payload.active else "0"
    update_setting("emergency_stop", new_val)
    log_event(
        acteur=actor["username"],
        action="BASCULE_ARRET_URGENCE",
        objet_type="SYSTEME",
        objet_id="EMERGENCY-STOP",
        ancienne_valeur=f"emergency_stop={old_val}",
        nouvelle_valeur=f"emergency_stop={new_val}",
        motif=payload.motif,
    )
    return {
        "status": "ok",
        "emergency_stop": payload.active,
        "actor": actor["username"],
        "motif": payload.motif,
    }


@router.get("/audit-logs")
def api_get_audit_logs(limit: int = 150):
    return {"audit_logs": list_audit_logs(limit=limit)}


@router.get("/audit-logs/verify")
def api_verify_audit_logs():
    return verify_audit_chain()


@router.get("/users")
def api_list_users():
    return {"users": list_users()}


@router.post("/system/replay")
def api_deterministic_replay():
    return deterministic_replay()

