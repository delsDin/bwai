"""
Modules F, G, H, I - Moteur de relances graduees, messages personnalises, file de revue humaine
et garde-fou de reverification juste avant l'envoi simule (Tests T5, T7, T8, T10, T11).
"""
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional

import config
from database.db import db_atomic, db_session
from services.ai_classifier import classifier_singleton
from services.audit_logger import log_event
from services.calculator import recalculate_invoice


def determine_reminder_level(jours_retard: int, date_echeance: date, reference_date: date) -> Optional[str]:
    """
    Determine le niveau de relance graduee (Module F) :
    - RAPPEL_PRE_ECHEANCE : si la facture n'est pas encore echue mais arrive a echeance dans <= 3 jours.
    - RELANCE_1 : retard de 1 a 14 jours.
    - RELANCE_2 : retard >= 15 jours.
    """
    if jours_retard <= 0:
        days_until_due = (date_echeance - reference_date).days
        if 0 <= days_until_due <= config.PRE_DUE_REMINDER_WINDOW_DAYS:
            return "RAPPEL_PRE_ECHEANCE"
        return None
    if jours_retard < config.SECOND_REMINDER_THRESHOLD_DAYS:
        return "RELANCE_1"
    return "RELANCE_2"


def build_personalized_message(
    nom_client: str,
    invoice_id: str,
    date_echeance: str,
    solde_restant: float,
    devise: str,
    niveau_relance: str,
) -> str:
    """
    Construit un message courtois personnalise (Module G) avec mention explicite du caractere fictif
    du lien Mobile Money et du message de demonstration.
    """
    ref_momo_fictive = f"MOMO-DEMO-{invoice_id}"
    solde_fmt = f"{solde_restant:,.0f}".replace(",", " ")

    if niveau_relance == "RAPPEL_PRE_ECHEANCE":
        intro = f"Bonjour {nom_client}, nous vous rappelons courtoisement que la facture {invoice_id} arrivera a echeance le {date_echeance}"
    elif niveau_relance == "RELANCE_1":
        intro = f"Bonjour {nom_client}, sauf erreur de notre part, la facture {invoice_id}, arrivee a echeance le {date_echeance},"
    else:
        intro = f"Bonjour {nom_client}, malgre notre precedent rappel, la facture {invoice_id} (echeance du {date_echeance})"

    return (
        f"{intro} presente un solde restant verifie de {solde_fmt} {devise}. "
        f"Reference Mobile Money fictive (SIMULATION UNIQUEMENT) : {ref_momo_fictive}. "
        f"Si votre reglement a deja ete effectue, merci d'ignorer ce message et de nous transmettre la reference. "
        f"Ceci est un message de demonstration."
    )


def prepare_reminder_for_invoice(
    invoice_id: str,
    requested_channel: Optional[str] = None,
    enable_pre_due_reminder: bool = False,
    db_path: Path | None = None,
    acteur: str = "SYSTEME",
) -> Dict[str, Any]:
    """
    Prepare de maniere idempotente et atomique (BEGIN IMMEDIATE) une relance ou une tache de revue humaine pour une facture.
    Respecte :
    - T1 / T4 / T6 : Pas de relance de retard pour une facture NON_ECHUE, SOLDEE ou COUVERTE_PAR_ECHEANCIER.
    - T5 : Une facture CONTESTEE est placee dans la file de revue humaine (relance suspendue).
    - T7 : Un montant >= seuil (500 000 FCFA) exige une validation humaine obligatoire.
    - Cas incertains IA (confiance < 0.75) ou historique inconnu -> File de revue humaine.
    - T10 : Cle anti-doublon UNIQUE(invoice_id, niveau_relance).
    """
    with db_atomic(db_path) as conn:
        recalculate_invoice(invoice_id, conn=conn)

        row_ref = conn.execute("SELECT value FROM settings WHERE key = 'reference_date'").fetchone()
        ref_date = date.fromisoformat(row_ref["value"]) if row_ref else config.DEFAULT_REFERENCE_DATE

        row_thresh = conn.execute("SELECT value FROM settings WHERE key = 'high_amount_threshold'").fetchone()
        high_threshold = float(row_thresh["value"]) if row_thresh else config.DEFAULT_HIGH_AMOUNT_THRESHOLD

        row_ai = conn.execute("SELECT value FROM settings WHERE key = 'ai_confidence_threshold'").fetchone()
        ai_threshold = float(row_ai["value"]) if row_ai else config.DEFAULT_AI_CONFIDENCE_THRESHOLD

        inv = conn.execute(
            """
            SELECT i.*, c.nom_affiche, c.canal_prefere, c.consentement_contact,
                   c.nb_factures_historiques, c.nb_retards_historiques
            FROM invoices i
            JOIN clients c ON i.client_id = c.client_id
            WHERE i.invoice_id = ?
            """,
            (invoice_id,),
        ).fetchone()
        if not inv:
            raise ValueError(f"Facture introuvable : {invoice_id}")

        statut = str(inv["statut_calcule"])
        solde = float(inv["solde_restant"])
        jours_retard = int(inv["jours_retard"])
        date_ech = date.fromisoformat(inv["date_echeance"])

        if statut == "SOLDEE" or solde <= 0:
            return {"status": "SKIPPED", "reason": "Facture deja soldee, aucune relance."}

        if statut == "COUVERTE_PAR_ECHEANCIER":
            return {"status": "SKIPPED", "reason": "Facture couverte par un echeancier actif, pas de relance."}

        # Determination du niveau de relance
        if statut == "CONTESTEE":
            niveau = "RELANCE_1" if jours_retard < config.SECOND_REMINDER_THRESHOLD_DAYS else "RELANCE_2"
        else:
            niveau = determine_reminder_level(jours_retard, date_ech, ref_date)
            if niveau == "RAPPEL_PRE_ECHEANCE" and not enable_pre_due_reminder:
                return {"status": "SKIPPED", "reason": "Facture non echue (rappel pre-echeance desactive)."}
            if niveau is None:
                return {"status": "SKIPPED", "reason": "Facture non echue, pas de relance de retard."}

        canal = requested_channel or str(inv["canal_prefere"])
        reasons_for_review: List[str] = []

        # Verification 1 : Litige ouvert (Test T5)
        if statut == "CONTESTEE":
            reasons_for_review.append("Litige ou contestation ouverte sur la facture")

        # Verification 2 : Gros montant superieur ou egal au seuil (Test T7)
        if solde >= high_threshold:
            reasons_for_review.append(f"Montant eleve ({solde:,.0f} FCFA >= seuil {high_threshold:,.0f} FCFA)")

        # Verification 3 : Analyse IA du message client (litige, demande echeancier ou confiance < 0.75)
        ai_res = classifier_singleton.classify(inv["dernier_message_client"], confidence_threshold=ai_threshold)
        if ai_res["requiert_revue_humaine"] and ai_res["motif_revue"]:
            reasons_for_review.append(str(ai_res["motif_revue"]))

        # Verification 4 : Historique client inconnu
        if int(inv["nb_factures_historiques"]) <= 0:
            reasons_for_review.append("Historique client inconnu (0 facture anterieure)")

        # Verification 5 : Preference de contact ou consentement incompatible (Test T8)
        if int(inv["consentement_contact"]) == 0:
            reasons_for_review.append("Client sans consentement de contact actif")
        elif requested_channel and requested_channel != inv["canal_prefere"]:
            reasons_for_review.append(
                f"Canal demande ({requested_channel}) incompatible avec la preference client ({inv['canal_prefere']})"
            )

        requiert_revue = len(reasons_for_review) > 0
        statut_action = "EN_ATTENTE_VALIDATION" if requiert_revue else "APPROUVEE"
        motif_blocage = " ; ".join(reasons_for_review) if requiert_revue else None

        message = build_personalized_message(
            nom_client=str(inv["nom_affiche"]),
            invoice_id=invoice_id,
            date_echeance=str(inv["date_echeance"]),
            solde_restant=solde,
            devise=str(inv["devise"]),
            niveau_relance=niveau,
        )

        idempotency_key = f"IDEMP-{invoice_id}-{niveau}"
        reminder_id = f"REL-{invoice_id}-{niveau}"
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

        cur_ins = conn.execute(
            """
            INSERT OR IGNORE INTO reminders (
                reminder_id, invoice_id, canal, date_creation, niveau_relance,
                contenu_message, statut_action, requiert_revue_humaine, motif_blocage, cree_par, idempotency_key
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                reminder_id,
                invoice_id,
                canal,
                now_iso,
                niveau,
                message,
                statut_action,
                1 if requiert_revue else 0,
                motif_blocage,
                acteur,
                idempotency_key,
            ),
        )

        # Verification d'idempotence atomique (Test T10) : si rowcount == 0, la relance existait deja
        if cur_ins.rowcount == 0:
            existing = conn.execute(
                "SELECT * FROM reminders WHERE invoice_id = ? AND niveau_relance = ?",
                (invoice_id, niveau),
            ).fetchone()
            return {
                "status": "ALREADY_EXISTS",
                "duplicate_prevented": True,
                "reminder_id": existing["reminder_id"],
                "statut_action": existing["statut_action"],
                "niveau_relance": existing["niveau_relance"],
            }

        log_event(
            acteur=acteur,
            action="PREPARATION_RELANCE",
            objet_type="RELANCE",
            objet_id=reminder_id,
            ancienne_valeur=None,
            nouvelle_valeur=f"Statut={statut_action}, Niveau={niveau}, Canal={canal}",
            motif=motif_blocage or "Relance standard prete pour simulation d'envoi",
            conn=conn,
        )

        return {
            "status": "CREATED",
            "duplicate_prevented": False,
            "reminder_id": reminder_id,
            "invoice_id": invoice_id,
            "niveau_relance": niveau,
            "canal": canal,
            "statut_action": statut_action,
            "requiert_revue_humaine": requiert_revue,
            "motif_blocage": motif_blocage,
            "cree_par": acteur,
            "contenu_message": message,
        }


def prepare_all_reminders(db_path: Path | None = None, acteur: str = "SYSTEME") -> List[Dict[str, Any]]:
    with db_session(db_path) as conn:
        rows = conn.execute(
            "SELECT invoice_id FROM invoices ORDER BY score_priorite DESC, jours_retard DESC"
        ).fetchall()
        inv_ids = [r["invoice_id"] for r in rows]
    return [prepare_reminder_for_invoice(inv_id, db_path=db_path, acteur=acteur) for inv_id in inv_ids]


def apply_human_review(
    reminder_id: str,
    decision: str,
    motif: str,
    nouveau_message: Optional[str] = None,
    db_path: Path | None = None,
    acteur: str = "GESTIONNAIRE",
) -> Dict[str, Any]:
    """
    Module H & Partie C.2 - Permet a un humain d'APPROUVER, MODIFIER, SUSPENDRE ou REJETER une relance
    en file de revue en respectant le principe des quatre yeux (l'auteur nomme d'une relance ne peut pas
    l'approuver lui-meme).
    """
    decision_upper = decision.upper()
    if decision_upper not in ("APPROUVER", "MODIFIER", "SUSPENDRE", "REJETER"):
        raise ValueError(f"Decision invalide : {decision}")

    with db_atomic(db_path) as conn:
        rem = conn.execute("SELECT * FROM reminders WHERE reminder_id = ?", (reminder_id,)).fetchone()
        if not rem:
            raise ValueError(f"Relance introuvable : {reminder_id}")

        cree_par = str(rem["cree_par"] or "SYSTEME")
        if (
            decision_upper in ("APPROUVER", "MODIFIER")
            and cree_par not in ("SYSTEME", "GESTIONNAIRE")
            and acteur == cree_par
        ):
            raise PermissionError(
                f"Violation du principe des 4 yeux : l'acteur '{acteur}' a prepare cette relance "
                f"et ne peut pas la valider lui-meme."
            )

        ancien_statut = str(rem["statut_action"])
        message_final = nouveau_message.strip() if (nouveau_message and nouveau_message.strip()) else str(rem["contenu_message"])

        if decision_upper in ("APPROUVER", "MODIFIER"):
            nouveau_statut = "APPROUVEE"
        elif decision_upper == "SUSPENDRE":
            nouveau_statut = "EN_ATTENTE_VALIDATION"
        else:
            nouveau_statut = "REJETEE"

        conn.execute(
            """
            UPDATE reminders
            SET statut_action = ?,
                contenu_message = ?,
                motif_blocage = ?,
                valide_par = ?
            WHERE reminder_id = ?
            """,
            (nouveau_statut, message_final, motif, acteur, reminder_id),
        )

        log_event(
            acteur=acteur,
            action=f"REVUE_HUMAINE_{decision_upper}",
            objet_type="RELANCE",
            objet_id=reminder_id,
            ancienne_valeur=ancien_statut,
            nouvelle_valeur=nouveau_statut,
            motif=motif,
            conn=conn,
        )

        return {
            "reminder_id": reminder_id,
            "decision": decision_upper,
            "statut_action": nouveau_statut,
            "motif": motif,
            "valide_par": acteur,
            "contenu_message": message_final,
        }


def verify_and_simulate_send(
    reminder_id: str,
    requested_channel: Optional[str] = None,
    db_path: Path | None = None,
    acteur: str = "SYSTEME",
) -> Dict[str, Any]:
    """
    Module I & Partie C.5 - Garde-fou obligatoire juste avant l'envoi simule (Tests T8, T10, T11) execute
    dans une transaction atomique unique (BEGIN IMMEDIATE) :
    0. Si l'interrupteur d'arret d'urgence (emergency_stop = '1') est actif : bloque tout envoi.
    1. Si la relance est deja SIMULEE : bloque tout double envoi (Test T10).
    2. Recalcule le solde et le statut de la facture en temps reel dans la meme transaction.
    3. Si la facture a ete soldee, contestee ou couverte par un echeancier entre-temps :
       ANNULE automatiquement la relance (Test T11).
    4. Verifie la preference de contact et le consentement client : bloque si incompatible (Test T8).
    5. Verifie que la relance a bien ete APPROUVEE si elle exigeait une validation humaine (Test T7).
    6. Sinon : passe atomiquement la relance de APPROUVEE a SIMULEE (WHERE statut_action = 'APPROUVEE')
       et horodate l'envoi.
    """
    with db_atomic(db_path) as conn:
        rem = conn.execute("SELECT * FROM reminders WHERE reminder_id = ?", (reminder_id,)).fetchone()
        if not rem:
            raise ValueError(f"Relance introuvable : {reminder_id}")
        invoice_id = str(rem["invoice_id"])
        statut_actuel = str(rem["statut_action"])

        # Garde-fou 0 (Partie C.5) : Interrupteur d'arret d'urgence global (Kill-Switch)
        stop_row = conn.execute("SELECT value FROM settings WHERE key = 'emergency_stop'").fetchone()
        if stop_row and stop_row["value"] == "1":
            reason = "Envoi suspendu : mode d'arret d'urgence (Kill-Switch) actif sur le systeme."
            log_event(
                acteur=acteur,
                action="BLOCAGE_ARRET_URGENCE",
                objet_type="RELANCE",
                objet_id=reminder_id,
                ancienne_valeur=statut_actuel,
                nouvelle_valeur=statut_actuel,
                motif=reason,
                conn=conn,
            )
            return {
                "status": "BLOCKED_EMERGENCY_STOP",
                "reminder_id": reminder_id,
                "statut_action": statut_actuel,
                "reason": reason,
            }

        # Protection anti-double envoi (Test T10)
        if statut_actuel == "SIMULEE":
            return {
                "status": "ALREADY_SENT",
                "duplicate_prevented": True,
                "reminder_id": reminder_id,
                "statut_action": "SIMULEE",
                "message": "Cette relance a deja ete envoyee en simulation (double envoi bloque).",
            }

        # 1. Recalcul deterministe juste avant l'envoi dans la meme transaction verrouillee (Module I)
        fresh_inv = recalculate_invoice(invoice_id, conn=conn)

        inv_client = conn.execute(
            """
            SELECT i.*, c.canal_prefere, c.consentement_contact
            FROM invoices i
            JOIN clients c ON i.client_id = c.client_id
            WHERE i.invoice_id = ?
            """,
            (invoice_id,),
        ).fetchone()

        canal_effectif = requested_channel or str(rem["canal"])
        ancien_statut_rem = str(rem["statut_action"])

        # Garde-fou A : Facture soldee, contestee ou sous echeancier (Test T11)
        if fresh_inv["solde_restant"] <= 0.0 or fresh_inv["statut_calcule"] in (
            "SOLDEE",
            "CONTESTEE",
            "COUVERTE_PAR_ECHEANCIER",
        ):
            reason = f"Relance annulee avant envoi : statut facture devenu {fresh_inv['statut_calcule']} (solde={fresh_inv['solde_restant']})"
            conn.execute(
                "UPDATE reminders SET statut_action = 'ANNULEE', motif_blocage = ? WHERE reminder_id = ?",
                (reason, reminder_id),
            )
            log_event(
                acteur="SYSTEME",
                action="ANNULATION_PRE_ENVOI",
                objet_type="RELANCE",
                objet_id=reminder_id,
                ancienne_valeur=ancien_statut_rem,
                nouvelle_valeur="ANNULEE",
                motif=reason,
                conn=conn,
            )
            return {
                "status": "CANCELLED_PRE_SEND",
                "reminder_id": reminder_id,
                "statut_action": "ANNULEE",
                "reason": reason,
            }

        # Garde-fou B : Preference de contact et consentement (Test T8)
        if int(inv_client["consentement_contact"]) == 0 or canal_effectif != str(inv_client["canal_prefere"]):
            reason = (
                f"Envoi bloque : canal ({canal_effectif}) ou consentement ({inv_client['consentement_contact']}) "
                f"incompatible avec la preference client ({inv_client['canal_prefere']})"
            )
            conn.execute(
                "UPDATE reminders SET statut_action = 'ANNULEE', motif_blocage = ? WHERE reminder_id = ?",
                (reason, reminder_id),
            )
            log_event(
                acteur="SYSTEME",
                action="BLOCAGE_CANAL_INCOMPATIBLE",
                objet_type="RELANCE",
                objet_id=reminder_id,
                ancienne_valeur=ancien_statut_rem,
                nouvelle_valeur="ANNULEE",
                motif=reason,
                conn=conn,
            )
            return {
                "status": "BLOCKED_CHANNEL_PREFERENCE",
                "reminder_id": reminder_id,
                "statut_action": "ANNULEE",
                "reason": reason,
            }

        # Garde-fou C : Validation humaine obligatoire non encore accordee (Test T7)
        if ancien_statut_rem == "EN_ATTENTE_VALIDATION":
            return {
                "status": "PENDING_HUMAN_REVIEW",
                "reminder_id": reminder_id,
                "statut_action": "EN_ATTENTE_VALIDATION",
                "reason": "Validation humaine obligatoire avant toute simulation d'envoi.",
            }

        if ancien_statut_rem in ("ANNULEE", "REJETEE"):
            return {
                "status": "NOT_SENDABLE",
                "reminder_id": reminder_id,
                "statut_action": ancien_statut_rem,
                "reason": f"Relance au statut {ancien_statut_rem}, envoi impossible.",
            }

        # Garde-fou D : Limite maximale de relances deja simulees sur cette facture
        sent_rows = conn.execute(
            "SELECT date_envoi_simule FROM reminders WHERE invoice_id = ? AND statut_action = 'SIMULEE' ORDER BY date_envoi_simule DESC",
            (invoice_id,),
        ).fetchall()
        if len(sent_rows) >= config.MAX_REMINDERS_PER_INVOICE:
            reason = f"Plafond de {config.MAX_REMINDERS_PER_INVOICE} relances atteint pour la facture {invoice_id}"
            conn.execute(
                "UPDATE reminders SET statut_action = 'ANNULEE', motif_blocage = ? WHERE reminder_id = ?",
                (reason, reminder_id),
            )
            log_event(
                acteur="SYSTEME",
                action="BLOCAGE_PLAFOND_RELANCES",
                objet_type="RELANCE",
                objet_id=reminder_id,
                ancienne_valeur=ancien_statut_rem,
                nouvelle_valeur="ANNULEE",
                motif=reason,
                conn=conn,
            )
            return {
                "status": "MAX_LIMIT_REACHED",
                "reminder_id": reminder_id,
                "statut_action": "ANNULEE",
                "reason": reason,
            }

        # Garde-fou E (Partie A.6) : Respect du delai minimal (MIN_DAYS_BETWEEN_REMINDERS = 7 jours) entre deux relances
        row_ref = conn.execute("SELECT value FROM settings WHERE key = 'reference_date'").fetchone()
        ref_date = date.fromisoformat(row_ref["value"]) if row_ref else config.DEFAULT_REFERENCE_DATE
        if sent_rows and sent_rows[0]["date_envoi_simule"]:
            last_sent_str = str(sent_rows[0]["date_envoi_simule"])[:10]
            try:
                last_sent_date = date.fromisoformat(last_sent_str)
                days_since_last = (ref_date - last_sent_date).days
                if 0 <= days_since_last < config.MIN_DAYS_BETWEEN_REMINDERS:
                    reason = (
                        f"Relance suspendue : delai minimal de {config.MIN_DAYS_BETWEEN_REMINDERS} jours "
                        f"non ecoule depuis la derniere relance du {last_sent_str} ({days_since_last}j < {config.MIN_DAYS_BETWEEN_REMINDERS}j)"
                    )
                    conn.execute(
                        "UPDATE reminders SET motif_blocage = ? WHERE reminder_id = ?",
                        (reason, reminder_id),
                    )
                    log_event(
                        acteur="SYSTEME",
                        action="SUSPENSION_DELAI_MINIMAL_RELANCE",
                        objet_type="RELANCE",
                        objet_id=reminder_id,
                        ancienne_valeur=ancien_statut_rem,
                        nouvelle_valeur=ancien_statut_rem,
                        motif=reason,
                        conn=conn,
                    )
                    return {
                        "status": "MIN_DELAY_NOT_ELAPSED",
                        "reminder_id": reminder_id,
                        "statut_action": ancien_statut_rem,
                        "days_since_last": days_since_last,
                        "min_days_required": config.MIN_DAYS_BETWEEN_REMINDERS,
                        "reason": reason,
                    }
            except ValueError:
                pass

        # Tous les controles ont reussi : transition atomique conditionnelle vers SIMULEE
        now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        sent_stamp = f"{ref_date.isoformat()} {now_utc}"
        cur_send = conn.execute(
            """
            UPDATE reminders
            SET statut_action = 'SIMULEE',
                date_envoi_simule = ?,
                canal = ?
            WHERE reminder_id = ? AND statut_action = 'APPROUVEE'
            """,
            (sent_stamp, canal_effectif, reminder_id),
        )
        if cur_send.rowcount == 0:
            return {
                "status": "ALREADY_SENT",
                "duplicate_prevented": True,
                "reminder_id": reminder_id,
                "statut_action": "SIMULEE",
                "message": "Cette relance a deja ete envoyee en simulation (double envoi bloque).",
            }

        log_event(
            acteur=acteur,
            action="ENVOI_SIMULE_EFFECTUE",
            objet_type="RELANCE",
            objet_id=reminder_id,
            ancienne_valeur="APPROUVEE",
            nouvelle_valeur="SIMULEE",
            motif=f"Envoi simule sur {canal_effectif} apres reverification du solde ({fresh_inv['solde_restant']:,.0f} FCFA)",
            conn=conn,
        )
        return {
            "status": "SENT_SIMULATED",
            "duplicate_prevented": False,
            "reminder_id": reminder_id,
            "invoice_id": invoice_id,
            "canal": canal_effectif,
            "statut_action": "SIMULEE",
            "solde_verifie": fresh_inv["solde_restant"],
        }
