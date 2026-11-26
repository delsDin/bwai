"""
Point d'entree FastAPI - Prototype Recouvrement Automatique des Factures Impayees (BWAI 2026).
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

import config
from database.db import init_db
from routes.api_routes import router as api_router
from routes.web_routes import router as web_router
from services.ai_classifier import classifier_singleton


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialisation de la base SQLite locale et pre-entrainement thread-safe du modele IA au demarrage
    init_db()
    classifier_singleton.ensure_trained()
    yield


app = FastAPI(
    title="BWAI 2026 - Prototype Recouvrement Factures Impayees",
    description=(
        "Cas pratique 1 : Calcul deterministe des soldes et retards, priorisation explicable, "
        "detection IA des litiges/incertitudes, validation humaine, relances simulees et tracabilite complete."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Montage des fichiers statiques depuis frontend/static
FRONTEND_DIR = config.BASE_DIR / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"
TEMPLATES_DIR = FRONTEND_DIR / "templates"

STATIC_DIR.mkdir(parents=True, exist_ok=True)
TEMPLATES_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Enregistrement des routes Web (Jinja2) et API REST (/api/...)
app.include_router(web_router)
app.include_router(api_router, prefix="/api")
