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


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Initialisation de la base SQLite locale au demarrage (Module K)
    init_db()
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

# Montage des fichiers statiques
(config.BASE_DIR / "static" / "css").mkdir(parents=True, exist_ok=True)
(config.BASE_DIR / "static" / "js").mkdir(parents=True, exist_ok=True)
(config.BASE_DIR / "templates").mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(config.BASE_DIR / "static")), name="static")

# Enregistrement des routes Web (Jinja2) et API REST (/api/...)
app.include_router(web_router)
app.include_router(api_router, prefix="/api")
