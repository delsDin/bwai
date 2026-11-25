"""
Modeles Pydantic v2 - Validation stricte des donnees d'import et des payloads API (Module B).
"""
from datetime import date
from enum import Enum
from typing import Optional
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
