"""
Routes Web HTML/Jinja2 FastAPI (Tableau de bord et ecrans recommandes par le Guide BWAI 2026).
"""
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from database.db import init_db
from models.repositories import (
    get_dashboard_kpis,
    get_invoice_detail,
    get_settings,
    list_audit_logs,
    list_import_errors,
    list_invoices,
    list_reminders,
    list_users,
    restore_from_app_state_cache,
)
from services.benchmark_service import run_reference_benchmark

router = APIRouter(tags=["Interface Web"])
templates = Jinja2Templates(directory=str(config.BASE_DIR / "frontend" / "templates"))


def _get_operator_tokens() -> list[dict[str, str]]:
    return [
        {"token": config.TOKEN_ADMIN, "label": "Direction (ADMIN)"},
        {"token": config.TOKEN_VALIDATEUR_1, "label": "Amina (VALIDATEUR 1)"},
        {"token": config.TOKEN_VALIDATEUR_2, "label": "Boris (VALIDATEUR 2 — 4 yeux)"},
        {"token": config.TOKEN_GESTIONNAIRE, "label": "Koffi (GESTIONNAIRE)"},
    ]


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request, statut: Optional[str] = None):
    init_db()
    kpis = get_dashboard_kpis()
    # Si la base est vide mais qu'un cache d'import non efface existe sur disque, on le recharge automatiquement.
    # Sinon, on n'affiche aucune donnee par defaut : l'utilisateur doit importer un fichier pour commencer.
    if kpis["total_factures"] == 0:
        try:
            if restore_from_app_state_cache():
                kpis = get_dashboard_kpis()
        except ValueError:
            pass

    invoices = list_invoices(statut_filter=statut)
    review_queue = list_reminders(statut_filter="EN_ATTENTE_VALIDATION")
    reminders = list_reminders()
    import_errors = list_import_errors()
    audit_logs = list_audit_logs(limit=40)
    users = list_users()
    settings = get_settings()
    benchmark = run_reference_benchmark()

    return templates.TemplateResponse(
        request=request,
        name="dashboard.html",
        context={
            "kpis": kpis,
            "invoices": invoices,
            "review_queue": review_queue,
            "reminders": reminders,
            "import_errors": import_errors,
            "audit_logs": audit_logs,
            "users": users,
            "settings": settings,
            "benchmark": benchmark,
            "operator_tokens": _get_operator_tokens(),
            "current_filter": statut,
        },
    )


@router.get("/invoices/{invoice_id}", response_class=HTMLResponse)
def invoice_detail_page(request: Request, invoice_id: str):
    detail = get_invoice_detail(invoice_id)
    if not detail:
        return RedirectResponse(url="/")
    return templates.TemplateResponse(
        request=request,
        name="invoice_detail.html",
        context={
            "detail": detail,
            "operator_tokens": _get_operator_tokens(),
        },
    )
