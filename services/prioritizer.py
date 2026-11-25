"""
Module E - Priorisation explicable des creances (Section 5 du Guide).
Score = 0,40 * score_retard + 0,35 * score_solde + 0,25 * score_historique
"""
from typing import Tuple
import config


def compute_priority_score(
    jours_retard: int,
    solde_restant: float,
    nb_retards_historiques: int,
    nb_factures_historiques: int,
    statut_calcule: str,
    high_amount_threshold: float = config.DEFAULT_HIGH_AMOUNT_THRESHOLD,
) -> Tuple[float, str, bool, bool]:
    """
    Retourne (score_total, explication_score, eligible_relance, historique_inconnu).
    Les factures SOLDEE, CONTESTEE ou COUVERTE_PAR_ECHEANCIER ne sont pas classees comme relances actives.
    """
    if statut_calcule in ("SOLDEE", "CONTESTEE", "COUVERTE_PAR_ECHEANCIER") or solde_restant <= 0:
        return 0.0, f"Non eligible au classement de relance (statut : {statut_calcule})", False, False

    score_retard = min(max(jours_retard, 0) / config.DEFAULT_MAX_DELAY_DAYS_NORM, 1.0)
    score_solde = min(max(solde_restant, 0.0) / max(high_amount_threshold, 1.0), 1.0)

    historique_inconnu = nb_factures_historiques <= 0
    if historique_inconnu:
        score_historique = 0.50
        hist_label = "inconnu (0.50 par defaut -> revue conseillee)"
    else:
        score_historique = min(max(nb_retards_historiques / nb_factures_historiques, 0.0), 1.0)
        hist_label = f"{nb_retards_historiques}/{nb_factures_historiques} ({score_historique:.2f})"

    score_total = round(
        config.WEIGHT_DELAY * score_retard
        + config.WEIGHT_BALANCE * score_solde
        + config.WEIGHT_HISTORY * score_historique,
        3,
    )

    explication = (
        f"Score={score_total:.3f} | Retard: {jours_retard}j ({score_retard:.2f}*40%) | "
        f"Solde: {solde_restant:,.0f} FCFA ({score_solde:.2f}*35%) | "
        f"Historique: {hist_label} (*25%)"
    )
    return score_total, explication, True, historique_inconnu
