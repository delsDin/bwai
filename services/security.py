"""
Module Securite & Maitrise des Actions :
1. Authentification legere par jetons avec 3 roles (GESTIONNAIRE, VALIDATEUR, ADMIN)
2. Separation des taches et principe des 4 yeux (auteur != validateur)
3. Masquage des coordonnees personnelles (telephone, email) pour la minimisation des donnees
"""
from typing import Dict, Any, Optional, Tuple
from fastapi import Header, HTTPException

from database.db import db_session

# Comptes de test documentes (sans secret reel) pour la demonstration et les tests automatises
DEMO_TOKENS: Dict[str, Dict[str, str]] = {
    "tok-gestionnaire-koffi": {
        "username": "agent.koffi",
        "role": "GESTIONNAIRE",
        "display_name": "Koffi (Gestionnaire Recouvrement)",
    },
    "tok-validateur-amina": {
        "username": "val.amina",
        "role": "VALIDATEUR",
        "display_name": "Amina (Superviseure / Validatrice)",
    },
    "tok-validateur-boris": {
        "username": "val.boris",
        "role": "VALIDATEUR",
        "display_name": "Boris (Directeur / Second Validateur)",
    },
    "tok-admin-direction": {
        "username": "admin.direction",
        "role": "ADMIN",
        "display_name": "Direction Generale (Administrateur)",
    },
}

DEFAULT_ANONYMOUS_ACTOR: Dict[str, str] = {
    "username": "GESTIONNAIRE",
    "role": "ADMIN",
    "display_name": "Session locale de demonstration",
}


def mask_contact(contact: str | None) -> str:
    """
    Masque partiellement un numero de telephone ou une adresse courriel pour proteger
    les donnees dans les vues, exports et journaux :
    - +22997001122 -> +22997****22
    - compta@pharmalittoral.bj -> co***@pharmalittoral.bj
    """
    if not contact or not contact.strip():
        return "***"
    raw = contact.strip()
    if "@" in raw:
        local, domain = raw.split("@", 1)
        prefix = local[:2] if len(local) >= 2 else local[:1]
        return f"{prefix}***@{domain}"
    if len(raw) > 6:
        return f"{raw[:6]}****{raw[-2:]}"
    return "****"


def resolve_actor_from_token(
    x_api_token: Optional[str] = None,
    authorization: Optional[str] = None,
) -> Dict[str, str]:
    """
    Resout l'identite et le role de l'acteur a partir de X-API-Token ou Authorization: Bearer.
    - Si un jeton est fourni mais invalide -> HTTP 401.
    - Si aucun jeton n'est fourni :
      - Si le parametre 'require_auth' vaut '1' en base -> HTTP 401.
      - Sinon -> retourne l'acteur de session locale (retrocompatibilite IHM Web / tests E2E).
    """
    token = None
    if x_api_token and x_api_token.strip():
        token = x_api_token.strip()
    elif authorization and authorization.strip().lower().startswith("bearer "):
        token = authorization.strip()[7:].strip()

    if token is not None:
        actor = DEMO_TOKENS.get(token)
        if not actor:
            raise HTTPException(
                status_code=401,
                detail="Jeton d'authentification invalide ou expire.",
            )
        return actor

    with db_session() as conn:
        row = conn.execute("SELECT value FROM settings WHERE key = 'require_auth'").fetchone()
        require_auth = (row["value"] == "1") if row else False

    if require_auth:
        raise HTTPException(
            status_code=401,
            detail="Authentification requise (en-tete X-API-Token ou Authorization: Bearer manquant).",
        )

    return DEFAULT_ANONYMOUS_ACTOR


def require_roles(*allowed_roles: str):
    """
    Dependance FastAPI verifiant que le role de l'acteur figure parmi allowed_roles.
    Retourne le dictionnaire de l'acteur (username, role, display_name).
    """
    allowed_set = set(allowed_roles)

    def dependency(
        x_api_token: Optional[str] = Header(default=None, alias="X-API-Token"),
        authorization: Optional[str] = Header(default=None, alias="Authorization"),
    ) -> Dict[str, str]:
        actor = resolve_actor_from_token(x_api_token=x_api_token, authorization=authorization)
        if actor["role"] not in allowed_set:
            raise HTTPException(
                status_code=403,
                detail=(
                    f"Acces refuse : le role [{actor['role']}] de '{actor['username']}' "
                    f"n'est pas autorise pour cette action (roles requis : {', '.join(allowed_roles)})."
                ),
            )
        return actor

    return dependency

