"""
Routes API REST FastAPI (/api/...) documentees automatiquement dans Swagger (/docs).
Couvre tous les modules A a L du Guide BWAI 2026.
"""
from pathlib import Path
import tempfile
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response

from models.repositories import (
    clear_all_portfolio_data,
    create_credit_note,
    create_dispute,
    create_installment_plan,
    dismiss_import_error,
    export_audit_logs_csv,
    export_portfolio_csv,
    export_portfolio_data,
    generate_import_template_xlsx_bytes,
    get_dashboard_kpis,
    get_invoice_detail,
    get_settings,
    list_audit_logs,
    list_import_errors,
    list_invoices,
    list_reminders,
    list_users,
    override_invoice_ai_classification,
    receive_simulated_incoming_message,
    reset_and_load_demo_portfolio,
    resolve_dispute,
    resolve_import_error,
    restore_from_app_state_cache,
    save_app_state_cache,
    update_business_settings,
    update_setting,
)
from models.schemas import (
    AIClassificationRequest,
    AIOverrideRequest,
    CreditNoteCreateRequest,
    DisputeCreateRequest,
    DisputeResolveRequest,
    EmergencyStopRequest,
    HumanReviewDecisionRequest,
    ImportErrorResolveRequest,
    InstallmentPlanCreateRequest,
    InvoiceIncomingMessageRequest,
    SettingsUpdateRequest,
    SimulatedIncomingMessageRequest,
    SimulatedPaymentRequest,
)
from services.ai_classifier import classifier_singleton
from services.audit_logger import log_event, verify_audit_chain
from services.benchmark_service import run_live_concurrency_proof, run_reference_benchmark
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
def api_recalculate_all(
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    results = recalculate_all_invoices()
    save_app_state_cache()
    return {"status": "ok", "actor": actor["username"], "recalculated_count": len(results), "invoices": results}


@router.post("/import/upload")
async def api_import_file(
    file: UploadFile = File(...),
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    suffix = Path(file.filename or "portfolio.xlsx").suffix
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        content = await file.read()
        tmp.write(content)
        tmp_path = Path(tmp.name)
    try:
        report = import_portfolio_file(tmp_path, acteur=actor["username"])
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


@router.delete("/import/errors/{error_id}")
def api_dismiss_import_error(
    error_id: int,
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    try:
        return dismiss_import_error(error_id=error_id, acteur=actor["username"])
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/import/errors/{error_id}/resolve")
def api_resolve_import_error(
    error_id: int,
    payload: ImportErrorResolveRequest,
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    try:
        return resolve_import_error(
            error_id=error_id,
            donnees_corrigees=payload.donnees_corrigees,
            motif=payload.motif,
            acteur=actor["username"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
    settings = get_settings()
    ai_thresh = float(settings.get("ai_confidence_threshold", 0.75))
    return classifier_singleton.classify(payload.message_client, confidence_threshold=ai_thresh)


@router.get("/ai/evaluation")
def api_get_ai_holdout_evaluation():
    settings = get_settings()
    ai_thresh = float(settings.get("ai_confidence_threshold", 0.75))
    return classifier_singleton.evaluate_holdout(confidence_threshold=ai_thresh)


@router.post("/invoices/{invoice_id}/ai-override")
def api_override_invoice_ai_classification(
    invoice_id: str,
    payload: AIOverrideRequest,
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    try:
        return override_invoice_ai_classification(
            invoice_id=invoice_id,
            categorie_humaine=payload.categorie_humaine,
            motif=payload.motif,
            acteur=actor["username"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


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
    actor: Dict[str, str] = Depends(require_roles("ADMIN")),
):
    return clear_all_portfolio_data()


@router.get("/system/cache")
def api_get_app_state_cache(
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    return save_app_state_cache()


@router.post("/system/cache/restore")
def api_restore_app_state_cache(
    payload: Dict[str, Any] = Body(...),
    actor: Dict[str, str] = Depends(require_roles("ADMIN")),
):
    try:
        restored = restore_from_app_state_cache(snapshot=payload)
        return {"status": "RESTORED" if restored else "EMPTY", "restored": restored, "actor": actor["username"]}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


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
def api_deterministic_replay(
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    return deterministic_replay()


@router.get("/benchmark")
def api_get_reference_benchmark():
    return run_reference_benchmark()


@router.post("/system/concurrency-proof")
def api_run_concurrency_proof(
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    return run_live_concurrency_proof(num_threads=20)


@router.get("/settings")
def api_get_settings():
    return get_settings()


@router.post("/settings")
def api_update_settings(
    payload: SettingsUpdateRequest,
    actor: Dict[str, str] = Depends(require_roles("VALIDATEUR", "ADMIN")),
):
    return update_business_settings(
        reference_date=payload.reference_date.isoformat(),
        high_amount_threshold=payload.high_amount_threshold,
        ai_confidence_threshold=payload.ai_confidence_threshold,
        default_channel=payload.default_channel.value,
        auto_prepare_reminders=payload.auto_prepare_reminders,
        enable_pre_due_reminders=payload.enable_pre_due_reminders,
        acteur=actor["username"],
    )


@router.get("/import/template.xlsx")
def api_download_import_template_xlsx():
    xlsx_bytes = generate_import_template_xlsx_bytes()
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="gabarit_portefeuille_bwai2026.xlsx"'},
    )


@router.get("/export/portfolio.csv")
def api_export_portfolio_csv():
    csv_text = export_portfolio_csv()
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="portefeuille_priorise_bwai2026.csv"'},
    )


@router.get("/export/portfolio.json")
def api_export_portfolio_json():
    return export_portfolio_data()


@router.get("/export/audit.csv")
def api_export_audit_csv():
    csv_text = export_audit_logs_csv()
    return Response(
        content=csv_text.encode("utf-8-sig"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="journal_audit_sha256_bwai2026.csv"'},
    )


@router.post("/webhooks/whatsapp-incoming")
def api_simulated_whatsapp_incoming_webhook(
    payload: SimulatedIncomingMessageRequest,
):
    """
    Route de reception simulee d'un message WhatsApp/SMS entrant (100 % hors-ligne, sans connexion externe).
    Rattache le message a la facture ou au client expediteur, lance la classification IA explicable
    et bascule la relance en file de revue humaine si un litige ou une incertitude est detecte.
    """
    try:
        return receive_simulated_incoming_message(
            message_client=payload.message_client,
            canal=payload.canal.value,
            invoice_id=payload.invoice_id,
            expediteur=payload.expediteur,
            acteur="WEBHOOK_WHATSAPP_SIMULE",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/invoices/{invoice_id}/incoming-message")
def api_simulated_invoice_incoming_message(
    invoice_id: str,
    payload: InvoiceIncomingMessageRequest,
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    """Simule la reception d'une reponse WhatsApp/SMS/Email d'un client directement sur une facture."""
    try:
        return receive_simulated_incoming_message(
            message_client=payload.message_client,
            canal=payload.canal.value,
            invoice_id=invoice_id,
            acteur=actor["username"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/disputes")
def api_create_dispute(
    payload: DisputeCreateRequest,
    actor: Dict[str, str] = Depends(require_roles("GESTIONNAIRE", "VALIDATEUR", "ADMIN")),
):
    try:
        return create_dispute(
            invoice_id=payload.invoice_id,
            motif=payload.motif,
            date_ouverture=payload.date_ouverture,
            acteur=actor["username"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/disputes/{dispute_id}/resolve")
def api_resolve_dispute(
    dispute_id: str,
    payload: Optional[DisputeResolveRequest] = None,
    actor: Dict[str, str] = Depends(require_roles("VALIDATEUR", "ADMIN")),
):
    try:
        motif = payload.motif_resolution if payload else "Litige verifie et resolu apres echange client"
        return resolve_dispute(
            dispute_id=dispute_id,
            motif_resolution=motif,
            acteur=actor["username"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/credit-notes")
def api_create_credit_note(
    payload: CreditNoteCreateRequest,
    actor: Dict[str, str] = Depends(require_roles("VALIDATEUR", "ADMIN")),
):
    try:
        return create_credit_note(
            invoice_id=payload.invoice_id,
            montant=payload.montant,
            motif=payload.motif,
            date_avoir=payload.date_avoir,
            acteur=actor["username"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/installment-plans")
def api_create_installment_plan(
    payload: InstallmentPlanCreateRequest,
    actor: Dict[str, str] = Depends(require_roles("VALIDATEUR", "ADMIN")),
):
    try:
        return create_installment_plan(
            invoice_id=payload.invoice_id,
            date_prochaine_echeance=payload.date_prochaine_echeance,
            montant_prevu=payload.montant_prevu,
            acteur=actor["username"],
        )
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


