"""
Routes Web HTML/Jinja2 FastAPI (Tableau de bord et ecrans recommandes par le Guide BWAI 2026).
"""
from typing import Optional
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

import config
from models.repositories import (
    get_dashboard_kpis,
    get_invoice_detail,
    list_audit_logs,
    list_import_errors,
    list_invoices,
    list_reminders,
    reset_and_load_demo_portfolio,
)

router = APIRouter(tags=["Interface Web"])
templates = Jinja2Templates(directory=str(config.BASE_DIR / "templates"))


@router.get("/", response_class=HTMLResponse)
def dashboard_page(request: Request, statut: Optional[str] = None):
    kpis = get_dashboard_kpis()
    if kpis["total_factures"] == 0:
        reset_and_load_demo_portfolio()
        kpis = get_dashboard_kpis()

    invoices = list_invoices(statut_filter=statut)
    review_queue = list_reminders(statut_filter="EN_ATTENTE_VALIDATION")
    reminders = list_reminders()
    import_errors = list_import_errors()
    audit_logs = list_audit_logs(limit=40)

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
        context={"detail": detail},
    )
