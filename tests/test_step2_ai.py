"""
Tests Etape 2 - Verification de la brique IA locale (classification NLP et seuil de confiance 0.75).
"""
from services.ai_classifier import classifier_singleton


def test_ai_classify_empty_message():
    res = classifier_singleton.classify(None)
    assert res["categorie"] == "AUCUN_MESSAGE"
    assert res["requiert_revue_humaine"] is False


def test_ai_classify_clear_dispute():
    res = classifier_singleton.classify(
        "La marchandise livree est endommagee nous contestons cette facture et demandons un avoir"
    )
    assert res["categorie"] == "LITIGE"
    assert res["requiert_revue_humaine"] is True
    assert res["motif_revue"] is not None


def test_ai_classify_installment_request():
    res = classifier_singleton.classify(
        "Nous avons un souci de tresorerie serait-il possible de mettre en place un echeancier en trois fois"
    )
    assert res["categorie"] == "DEMANDE_ECHEANCIER"
    assert res["requiert_revue_humaine"] is True


def test_ai_classify_payment_claim():
    res = classifier_singleton.classify(
        "Facture deja payee hier par Mobile Money reference MOMO8493 merci de verifier"
    )
    assert res["categorie"] == "ANNONCE_PAIEMENT"
    assert res["requiert_revue_humaine"] is True


def test_ai_classify_uncertain_message_triggers_human_review():
    """Tout message dont la confiance est inferieure au seuil (0.75) doit partir en revue humaine."""
    res = classifier_singleton.classify("Bonjour pouvez-vous nous rappeler demain matin", confidence_threshold=0.75)
    assert res["confiance"] < 0.75
    assert res["requiert_revue_humaine"] is True
    assert "incertain" in (res["motif_revue"] or "").lower()

