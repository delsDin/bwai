"""
Modeles Pydantic v2 - Validation stricte des donnees d'import et des payloads API (Module B).
"""
from datetime import date
from enum import Enum
from typing import Any, Dict, Optional
from pydantic import BaseModel, Field, model_validator


class ChannelEnum(str, Enum):
    WHATSAPP = "WHATSAPP"
    SMS = "SMS"
    EMAIL = "EMAIL"


class InvoiceStatusEnum(str, Enum):
    NON_ECHUE = "NON_ECHUE"
    ECHUE = "ECHUE"
    PARTIELLEMENT_PAYEE = "PARTIELLEMENT_PAYEE"
    SOLDEE = "SOLDEE"
    CONTESTEE = "CONTESTEE"
    COUVERTE_PAR_ECHEANCIER = "COUVERTE_PAR_ECHEANCIER"


class ReminderLevelEnum(str, Enum):
    RAPPEL_PRE_ECHEANCE = "RAPPEL_PRE_ECHEANCE"
    RELANCE_1 = "RELANCE_1"
    RELANCE_2 = "RELANCE_2"


class ReminderActionStatusEnum(str, Enum):
    BROUILLON = "BROUILLON"
    EN_ATTENTE_VALIDATION = "EN_ATTENTE_VALIDATION"
    APPROUVEE = "APPROUVEE"
    SIMULEE = "SIMULEE"
    ANNULEE = "ANNULEE"
    REJETEE = "REJETEE"


class ClientImportSchema(BaseModel):
    client_id: str = Field(..., min_length=1)
    nom_affiche: str = Field(..., min_length=1)
    contact_simule: str = Field(..., min_length=1)
    canal_prefere: ChannelEnum = ChannelEnum.WHATSAPP
    consentement_contact: int = Field(1, ge=0, le=1)
    nb_factures_historiques: int = Field(0, ge=0)
    nb_retards_historiques: int = Field(0, ge=0)

    @model_validator(mode="after")
    def check_history_consistency(self) -> "ClientImportSchema":
        if self.nb_retards_historiques > self.nb_factures_historiques:
            raise ValueError("Le nombre de retards historiques ne peut pas depasser le nombre de factures historiques.")
        return self


class InvoiceImportSchema(BaseModel):
    invoice_id: str = Field(..., min_length=1)
    client_id: str = Field(..., min_length=1)
    date_emission: date
    date_echeance: date
    montant_initial: float = Field(..., gt=0, description="Le montant initial doit etre strictement positif")
    devise: str = Field("FCFA", min_length=1)
    statut_source: Optional[str] = None
    dernier_message_client: Optional[str] = None

    @model_validator(mode="after")
    def check_dates(self) -> "InvoiceImportSchema":
        if self.date_echeance < self.date_emission:
            raise ValueError("La date d'echeance ne peut pas etre anterieure a la date d'emission.")
        norm_dev = self.devise.strip().upper()
        if norm_dev in ("FCFA", "XOF", "CFA"):
            self.devise = "FCFA"
        else:
            raise ValueError(
                f"Devise non supportee ({self.devise}) : toutes les factures du portefeuille doivent etre en FCFA (XOF)."
            )
        return self


class PaymentImportSchema(BaseModel):
    payment_id: str = Field(..., min_length=1)
    invoice_id: str = Field(..., min_length=1)
    date_paiement: date
    montant: float = Field(..., gt=0)
    reference_fictive: str = Field(..., min_length=1)


class CreditNoteImportSchema(BaseModel):
    credit_note_id: str = Field(..., min_length=1)
    invoice_id: str = Field(..., min_length=1)
    date_avoir: date
    montant: float = Field(..., gt=0)
    motif: str = Field(..., min_length=1)


class InstallmentPlanImportSchema(BaseModel):
    plan_id: str = Field(..., min_length=1)
    invoice_id: str = Field(..., min_length=1)
    date_prochaine_echeance: date
    montant_prevu: float = Field(..., gt=0)
    statut: str = Field("ACTIF", pattern="^(ACTIF|ROMPU|TERMINE)$")


class DisputeImportSchema(BaseModel):
    dispute_id: str = Field(..., min_length=1)
    invoice_id: str = Field(..., min_length=1)
    motif: str = Field(..., min_length=1)
    date_ouverture: date
    statut: str = Field("OUVERT", pattern="^(OUVERT|RESOLU)$")


class SimulatedPaymentRequest(BaseModel):
    invoice_id: str = Field(..., min_length=1)
    montant: float = Field(..., gt=0)
    reference_fictive: str = Field(..., min_length=1)
    date_paiement: Optional[date] = None


class HumanReviewDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(APPROUVER|MODIFIER|SUSPENDRE|REJETER)$")
    motif: str = Field(..., min_length=3)
    nouveau_message: Optional[str] = None


class AIClassificationRequest(BaseModel):
    message_client: str = Field(..., min_length=2)


class EmergencyStopRequest(BaseModel):
    active: bool = Field(..., description="True pour activer le mode d'arret d'urgence, False pour le desactiver")
    motif: str = Field("Decision de securite", min_length=3)


class SettingsUpdateRequest(BaseModel):
    reference_date: date
    high_amount_threshold: float = Field(..., gt=0, description="Seuil de gros montant en FCFA")
    ai_confidence_threshold: float = Field(..., ge=0.1, le=1.0, description="Seuil de confiance IA entre 0.10 et 1.00")
    default_channel: ChannelEnum = ChannelEnum.WHATSAPP
    auto_prepare_reminders: bool = Field(False, description="Preparer automatiquement les relances apres import ou modification")
    enable_pre_due_reminders: bool = Field(False, description="Activer les rappels courtois pre-echeance (J-3 a J-0)")


class DisputeCreateRequest(BaseModel):
    invoice_id: str = Field(..., min_length=1)
    motif: str = Field(..., min_length=3)
    date_ouverture: Optional[date] = None


class DisputeResolveRequest(BaseModel):
    motif_resolution: str = Field("Litige verifie et resolu apres echange client", min_length=3)


class CreditNoteCreateRequest(BaseModel):
    invoice_id: str = Field(..., min_length=1)
    montant: float = Field(..., gt=0)
    motif: str = Field(..., min_length=3)
    date_avoir: Optional[date] = None


class InstallmentPlanCreateRequest(BaseModel):
    invoice_id: str = Field(..., min_length=1)
    date_prochaine_echeance: date
    montant_prevu: float = Field(..., gt=0)


class ImportErrorResolveRequest(BaseModel):
    donnees_corrigees: Dict[str, Any]
    motif: str = Field("Correction manuelle et reintegration de la ligne", min_length=3)


class AIOverrideRequest(BaseModel):
    categorie_humaine: str = Field(
        ...,
        pattern="^(LITIGE|DEMANDE_ECHEANCIER|ANNONCE_PAIEMENT|RETARD_SIMPLE)$",
        description="Qualification retenue par l'operateur humain apres reprise sur l'IA",
    )
    motif: str = Field("Reprise humaine et requalification du message client", min_length=3)


class SimulatedIncomingMessageRequest(BaseModel):
    invoice_id: Optional[str] = Field(None, description="Identifiant de la facture concernee (optionnel si expediteur fourni)")
    expediteur: Optional[str] = Field(None, description="Numero WhatsApp/SMS ou identifiant du client expediteur")
    canal: ChannelEnum = ChannelEnum.WHATSAPP
    message_client: str = Field(..., min_length=2, description="Contenu du message WhatsApp/SMS entrant simule")


class InvoiceIncomingMessageRequest(BaseModel):
    canal: ChannelEnum = ChannelEnum.WHATSAPP
    message_client: str = Field(..., min_length=2, description="Contenu du message entrant simule sur cette facture")




