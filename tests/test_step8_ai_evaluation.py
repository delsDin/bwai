"""
Tests Etape 8 - Evaluation quantitative complete du modele IA sur data/test.csv (100 messages hold-out)
et verification de l'absence de fuite de donnees entre data/train.csv (250 messages) et data/test.csv.
"""
import config
from services.ai_classifier import classifier_singleton, load_labeled_dataset


def test_train_and_test_csv_no_data_leakage():
    """Verifie que data/train.csv (250) et data/test.csv (100) existent et n'ont aucun message en commun."""
    train_texts, train_labels, _ = load_labeled_dataset(config.DATA_DIR / "train.csv")
    test_texts, test_labels, _ = load_labeled_dataset(config.DATA_DIR / "test.csv")

    assert len(train_texts) == 250
    assert len(test_texts) == 100
    assert set(train_labels) == {"LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT", "RETARD_SIMPLE"}
    assert set(test_labels) == {"LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT", "RETARD_SIMPLE"}

    overlap = {t.lower() for t in train_texts} & {t.lower() for t in test_texts}
    assert len(overlap) == 0, f"Fuite de donnees detectee entre train.csv et test.csv : {overlap}"


def test_full_holdout_evaluation_on_test_csv():
    """
    Evalue le classifieur entraine sur data/train.csv face aux 100 messages inedits de data/test.csv :
    - Accuracy >= 85% (89.0% observe)
    - F1 Macro >= 0.85 (0.90 observe)
    - Gain massif par rapport a la baseline par mots-cles (89% vs 36%)
    - Rappel LITIGE >= 90% (95.8% observe : 23/24)
    - 100% des erreurs de classification et 100% des cas sensibles (LITIGE, DEMANDE_ECHEANCIER, ANNONCE_PAIEMENT)
      sont interceptes par la file de revue humaine (0 envoi automatique abusif).
    """
    report = classifier_singleton.evaluate_holdout()

    assert report["train_size"] == 250
    assert report["test_size"] == 100
    assert report["accuracy"] >= 0.85
    assert report["macro_f1"] >= 0.85
    assert report["gain_vs_baseline"] >= 0.40
    assert report["per_class_metrics"]["LITIGE"]["recall"] >= 0.90

    # Garanties de securite metier : aucune erreur ni aucun cas sensible n'echappe a la revue humaine
    assert report["errors_caught_by_human_review"] == report["total_errors"]
    assert report["critical_false_negatives_auto_sent"] == 0
    assert len(report["threshold_calibration_curve"]) >= 6


def test_threshold_calibration_curve_justifies_zero_error_above_050():
    """
    Point B.3 - Verifie la courbe Couverture / Erreur sur data/test.csv :
    - Au seuil 0.35 : couverture IA = 90%, mais il reste des erreurs non filtrees par la confiance seule.
    - A partir du seuil 0.50 (et a fortiori 0.75) : la precision IA au-dessus du seuil atteint 100% (0 erreur non filtree).
    - Sur tous les seuils testes, la regle metier globale garantit 0 faux negatif critique.
    """
    curve = classifier_singleton.calibrate_thresholds()
    by_thresh = {pt["threshold"]: pt for pt in curve}

    assert by_thresh[0.35]["ai_coverage"] >= 0.85
    assert by_thresh[0.35]["ai_errors_above_threshold"] > 0

    # A partir de 0.50, 100% des erreurs IA ont une confiance < seuil (precision au-dessus du seuil = 100%)
    assert by_thresh[0.50]["ai_Coverage" if "ai_Coverage" in by_thresh[0.50] else "ai_coverage"] == 0.50
    assert by_thresh[0.50]["ai_precision_above_threshold"] == 1.0
    assert by_thresh[0.50]["ai_errors_above_threshold"] == 0

    assert by_thresh[0.75]["ai_precision_above_threshold"] == 1.0
    assert by_thresh[0.75]["ai_errors_above_threshold"] == 0
    assert all(pt["critical_false_negatives"] == 0 for pt in curve)


def test_critical_safeguard_negation_and_amount_on_retard_simple():
    """
    Point B.4 - Tout message contenant une negation ('je ne conteste pas...') ou un montant chiffre
    passe obligatoirement en revue humaine, meme si la classe predite est RETARD_SIMPLE et le seuil bas.
    """
    # Message de retard simple contenant une negation ("ne ... pas")
    res_neg = classifier_singleton.classify(
        "Desole pour le retard, je ne refuse pas de payer, je regle rapidement",
        confidence_threshold=0.20,
    )
    assert res_neg["requiert_revue_humaine"] is True

    # Message de retard simple contenant un montant chiffre ("25000 fcfa")
    res_amt = classifier_singleton.classify(
        "Desole pour cet oubli sur les 25000 fcfa, je m'en occupe rapidement",
        confidence_threshold=0.20,
    )
    assert res_amt["requiert_revue_humaine"] is True
    assert res_amt["garde_fou_critique"] is True or res_amt["categorie"] != "RETARD_SIMPLE"


