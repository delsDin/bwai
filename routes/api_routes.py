"""
Routes API REST FastAPI (/api/...) documentees automatiquement dans Swagger (/docs).
Couvre tous les modules A a L du Guide BWAI 2026.
"""
from pathlib import Path
import tempfile
from typing import Optional

from fastapi import APIRouter, HTTPException, UploadFile, File

from models.repositories import (
    get_dashboard_kpis,
    get_invoice_detail,
    list_audit_logs,
    list_import_errors,
    list_invoices,
    list_reminders,
    reset_and_load_demo_portfolio,
)
from models.schemas import (
    AIClassificationRequest,
    HumanReviewDecisionRequest,
    SimulatedPaymentRequest,
)
from services.ai_classifier import classifier_singleton
from services.calculator import recalculate_all_invoices
from services.importer import import_portfolio_file
from services.payment_service import record_simulated_payment
from services.recovery_service import resume_after_interruption
from services.reminder_engine import (
    apply_human_review,
    prepare_all_reminders,
    prepare_reminder_for_invoice,
    verify_and_simulate_send,
)

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
def api_prepare_all_reminders():
    results = prepare_all_reminders()
    return {"status": "ok", "results": results}


@router.post("/reminders/prepare/{invoice_id}")
def api_prepare_single_reminder(invoice_id: str, canal: Optional[str] = None):
    try:
        return prepare_reminder_for_invoice(invoice_id, requested_channel=canal)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/review/{reminder_id}")
def api_apply_human_review(reminder_id: str, payload: HumanReviewDecisionRequest):
    try:
        return apply_human_review(
            reminder_id=reminder_id,
            decision=payload.decision,
            motif=payload.motif,
            nouveau_message=payload.nouveau_message,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/reminders/{reminder_id}/send")
def api_verify_and_send_reminder(reminder_id: str, canal: Optional[str] = None):
    try:
        return verify_and_simulate_send(reminder_id=reminder_id, requested_channel=canal)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/payments/simulate")
def api_simulate_payment(payload: SimulatedPaymentRequest):
    try:
        return record_simulated_payment(
            invoice_id=payload.invoice_id,
            montant=payload.montant,
            reference_fictive=payload.reference_fictive,
            date_paiement=payload.date_paiement,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/ai/classify")
def classify_client_message(payload: AIClassificationRequest):
    return classifier_singleton.classify(payload.message_client)


@router.post("/system/recover")
def api_recover_after_interruption():
    return resume_after_interruption()


@router.post("/system/reset-demo")
def api_reset_demo():
    return reset_and_load_demo_portfolio()


@router.get("/audit-logs")
def api_get_audit_logs(limit: int = 150):
    return {"audit_logs": list_audit_logs(limit=limit)}
