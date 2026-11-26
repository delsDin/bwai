"""
Brique IA Locale (100% hors-ligne) - Classification NLP des messages clients avec score de confiance.
Entrainee exclusivement sur data/train.csv (250 messages synthetiques contextualises Benin) et evaluee
sur data/test.csv (100 messages hold-out).
Permet de detecter automatiquement les contestations/litiges, demandes d'echeancier, annonces de paiement,
et d'envoyer tout cas incertain (confiance < 0.75) dans la file de revue humaine.
"""
import csv
from pathlib import Path
import re
import threading
from typing import Dict, Any, List, Tuple
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix
from sklearn.pipeline import Pipeline
import config

LABELS_ORDER = ["LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT", "RETARD_SIMPLE"]
CALIBRATION_THRESHOLDS = [0.35, 0.40, 0.45, 0.50, 0.55, 0.65, 0.75]

# Garde-fous lexicaux critiques (Point B.4) : toute negation ou montant chiffre force la revue humaine
NEGATION_PATTERN = re.compile(
    r"\b(ne\s+\w+\s+pas|n'\w+\s+pas|jamais|aucun|aucune|pas\s+d'accord|pas\s+recu|pas\s+reçu|pas\s+livre|pas\s+livré|conteste\w*|refus\w*)\b",
    re.IGNORECASE,
)
PROMISED_AMOUNT_PATTERN = re.compile(
    r"\b(\d{3,}|\d+\s*(?:fcfa|cfa|francs?|%|tranches?|fois|mois))\b",
    re.IGNORECASE,
)


def detect_critical_patterns(message: str) -> List[str]:
    """
    Detecte la presence d'une negation ou d'un montant/echeance chiffre dans un message client.
    Sert de filet de securite pour empecher qu'un message contenant une negation ('je ne conteste pas...')
    ou un engagement chiffre ne passe sans revue humaine s'il est classe en RETARD_SIMPLE.
    """
    triggers: List[str] = []
    if NEGATION_PATTERN.search(message):
        triggers.append("negation detectee")
    if PROMISED_AMOUNT_PATTERN.search(message):
        triggers.append("montant ou echeance chiffree")
    return triggers


def load_labeled_dataset(csv_path: Path) -> Tuple[List[str], List[str], List[Dict[str, str]]]:
    """Charge un fichier CSV (id, message, classe, ambigu) et valide son schema."""
    if not csv_path.exists():
        raise FileNotFoundError(f"Fichier de donnees IA introuvable : {csv_path}")

    texts: List[str] = []
    labels: List[str] = []
    rows: List[Dict[str, str]] = []
    with csv_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            msg = (row.get("message") or "").strip()
            label = (row.get("classe") or "").strip()
            if msg and label:
                texts.append(msg)
                labels.append(label)
                rows.append(row)

    if not texts:
        raise ValueError(f"Aucune donnee exploitable dans {csv_path}")
    return texts, labels, rows


class ClientMessageClassifier:
    def __init__(self, train_csv_path: Path | None = None) -> None:
        self.train_csv_path = train_csv_path or (config.DATA_DIR / "train.csv")
        self.pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), lowercase=True, strip_accents="unicode")),
            ("clf", LogisticRegression(C=2.0, class_weight="balanced", random_state=42, max_iter=1000)),
        ])
        self._is_trained = False
        self._lock = threading.Lock()
        self.train_size = 0

    def ensure_trained(self) -> None:
        if not self._is_trained:
            with self._lock:
                if not self._is_trained:
                    texts, labels, _ = load_labeled_dataset(self.train_csv_path)
                    self.pipeline.fit(texts, labels)
                    self.train_size = len(texts)
                    self._is_trained = True

    def classify(
        self,
        message: str | None,
        confidence_threshold: float = config.DEFAULT_AI_CONFIDENCE_THRESHOLD,
    ) -> Dict[str, Any]:
        if not message or not message.strip():
            return {
                "categorie": "AUCUN_MESSAGE",
                "confiance": 1.0,
                "requiert_revue_humaine": False,
                "motif_revue": None,
                "garde_fou_critique": False,
            }

        self.ensure_trained()
        probs = self.pipeline.predict_proba([message])[0]
        classes = self.pipeline.classes_
        best_idx = int(probs.argmax())
        categorie = str(classes[best_idx])
        confiance = round(float(probs[best_idx]), 3)

        critical_triggers = detect_critical_patterns(message)
        garde_fou_critique = len(critical_triggers) > 0
        requiert_revue = False
        motif = None

        if confiance < confidence_threshold:
            requiert_revue = True
            motif = f"Cas incertain IA (confiance {confiance:.0%} < seuil {confidence_threshold:.0%})"
        elif categorie in ("LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT"):
            requiert_revue = True
            motif = f"Message client detecte par IA comme [{categorie}] (confiance {confiance:.0%})"
        elif critical_triggers:
            requiert_revue = True
            motif = (
                f"Garde-fou critique sur [{categorie}] ({', '.join(critical_triggers)}) "
                f"exigeant une verification humaine"
            )

        return {
            "categorie": categorie,
            "confiance": confiance,
            "requiert_revue_humaine": requiert_revue,
            "motif_revue": motif,
            "garde_fou_critique": garde_fou_critique,
        }

    def calibrate_thresholds(
        self,
        test_csv_path: Path | None = None,
        thresholds: List[float] | None = None,
    ) -> List[Dict[str, Any]]:
        """
        Point B.3 - Courbe de calibration Couverture / Erreur en fonction du seuil de confiance.
        Pour chaque seuil, calcule :
        - la couverture IA (% de messages avec confiance >= seuil)
        - la precision du modele sur les messages au-dessus du seuil
        - le nombre d'erreurs IA filtrees (confiance < seuil) vs non filtrees (confiance >= seuil)
        - le taux de routage en revue humaine metier (incluant classes sensibles + garde-fou lexical)
        - le nombre de faux negatifs critiques (0 attendu sur tous les seuils)
        """
        self.ensure_trained()
        target_test = test_csv_path or (config.DATA_DIR / "test.csv")
        texts, y_true, _ = load_labeled_dataset(target_test)
        grid = thresholds or CALIBRATION_THRESHOLDS

        raw_preds = [self.classify(m, confidence_threshold=0.0) for m in texts]
        total_errors = sum(1 for p, y in zip(raw_preds, y_true) if p["categorie"] != y)

        curve: List[Dict[str, Any]] = []
        for thresh in grid:
            above = [(p, y) for p, y in zip(raw_preds, y_true) if p["confiance"] >= thresh]
            below = [(p, y) for p, y in zip(raw_preds, y_true) if p["confiance"] < thresh]

            ai_coverage = round(len(above) / len(texts), 4)
            precision_above = (
                round(sum(1 for p, y in above if p["categorie"] == y) / len(above), 4)
                if above
                else 1.0
            )
            unfiltered_errors = sum(1 for p, y in above if p["categorie"] != y)
            intercepted_errors_by_conf = sum(1 for p, y in below if p["categorie"] != y)

            # Evaluation avec la regle metier complete (classes sensibles + garde-fou lexical + seuil)
            decisions = [self.classify(m, confidence_threshold=thresh) for m in texts]
            human_review_count = sum(1 for d in decisions if d["requiert_revue_humaine"])
            auto_handled_count = len(texts) - human_review_count
            critical_fn = sum(
                1
                for d, y in zip(decisions, y_true)
                if y in ("LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT") and not d["requiert_revue_humaine"]
            )
            business_unintercepted_errors = sum(
                1 for d, y in zip(decisions, y_true) if d["categorie"] != y and not d["requiert_revue_humaine"]
            )

            curve.append({
                "threshold": thresh,
                "ai_coverage": ai_coverage,
                "ai_precision_above_threshold": precision_above,
                "ai_errors_above_threshold": unfiltered_errors,
                "ai_errors_below_threshold": intercepted_errors_by_conf,
                "total_ai_errors": total_errors,
                "business_auto_rate": round(auto_handled_count / len(texts), 4),
                "business_human_review_rate": round(human_review_count / len(texts), 4),
                "business_unintercepted_errors": business_unintercepted_errors,
                "critical_false_negatives": critical_fn,
            })

        return curve

    def evaluate_holdout(
        self,
        test_csv_path: Path | None = None,
        confidence_threshold: float = config.DEFAULT_AI_CONFIDENCE_THRESHOLD,
    ) -> Dict[str, Any]:
        """
        Evalue quantitativement le modele sur data/test.csv (hold-out) :
        - Accuracy globale, rapport par classe (precision, recall, f1-score)
        - Matrice de confusion sur les 4 classes
        - Performance separee sur les messages standards vs ambigus
        - Analyse de securite metier : taux d'interception en revue humaine des erreurs et des cas sensibles
        - Comparaison a une baseline deterministe par mots-cles
        """
        self.ensure_trained()
        target_test = test_csv_path or (config.DATA_DIR / "test.csv")
        texts, y_true, rows = load_labeled_dataset(target_test)

        y_pred: List[str] = []
        confidences: List[float] = []
        review_flags: List[bool] = []
        errors: List[Dict[str, Any]] = []

        ambiguous_correct = 0
        ambiguous_total = 0
        standard_correct = 0
        standard_total = 0

        sensitive_total = 0
        sensitive_routed_to_review = 0

        for row, msg, true_label in zip(rows, texts, y_true):
            res = self.classify(msg, confidence_threshold=confidence_threshold)
            pred_label = str(res["categorie"])
            conf = float(res["confiance"])
            req_review = bool(res["requiert_revue_humaine"])

            y_pred.append(pred_label)
            confidences.append(conf)
            review_flags.append(req_review)

            is_correct = pred_label == true_label
            is_ambigu = (row.get("ambigu") or "").strip().lower() == "oui"

            if is_ambigu:
                ambiguous_total += 1
                if is_correct:
                    ambiguous_correct += 1
            else:
                standard_total += 1
                if is_correct:
                    standard_correct += 1

            if true_label in ("LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT"):
                sensitive_total += 1
                if req_review:
                    sensitive_routed_to_review += 1

            if not is_correct:
                errors.append({
                    "id": row.get("id"),
                    "message": msg,
                    "vrai_label": true_label,
                    "pred_label": pred_label,
                    "confiance": conf,
                    "ambigu": row.get("ambigu"),
                    "intercepte_revue_humaine": req_review,
                })

        acc = round(float(accuracy_score(y_true, y_pred)), 4)
        report_dict = classification_report(
            y_true, y_pred, labels=LABELS_ORDER, output_dict=True, zero_division=0
        )
        cm = confusion_matrix(y_true, y_pred, labels=LABELS_ORDER).tolist()

        # Comparaison avec une baseline simple par mots-cles
        baseline_preds = [_keyword_baseline_predict(m) for m in texts]
        baseline_acc = round(float(accuracy_score(y_true, baseline_preds)), 4)

        errors_caught = sum(1 for e in errors if e["intercepte_revue_humaine"])
        calibration_curve = self.calibrate_thresholds(test_csv_path=target_test)

        return {
            "train_csv": str(self.train_csv_path.name),
            "test_csv": str(target_test.name),
            "train_size": self.train_size,
            "test_size": len(texts),
            "accuracy": acc,
            "baseline_keyword_accuracy": baseline_acc,
            "gain_vs_baseline": round(acc - baseline_acc, 4),
            "macro_f1": round(float(report_dict["macro avg"]["f1-score"]), 4),
            "weighted_f1": round(float(report_dict["weighted avg"]["f1-score"]), 4),
            "per_class_metrics": {
                label: {
                    "precision": round(float(report_dict[label]["precision"]), 4),
                    "recall": round(float(report_dict[label]["recall"]), 4),
                    "f1_score": round(float(report_dict[label]["f1-score"]), 4),
                    "support": int(report_dict[label]["support"]),
                }
                for label in LABELS_ORDER
            },
            "labels_order": LABELS_ORDER,
            "confusion_matrix": cm,
            "standard_accuracy": round(standard_correct / max(standard_total, 1), 4),
            "ambiguous_accuracy": round(ambiguous_correct / max(ambiguous_total, 1), 4),
            "total_errors": len(errors),
            "errors_caught_by_human_review": errors_caught,
            "sensitive_cases_total": sensitive_total,
            "sensitive_cases_routed_to_review": sensitive_routed_to_review,
            "critical_false_negatives_auto_sent": sensitive_total - sensitive_routed_to_review,
            "threshold_calibration_curve": calibration_curve,
            "errors_detail": errors,
        }


def _keyword_baseline_predict(message: str) -> str:
    """Baseline deterministe par mots-cles utilisee comme point de reference dans la Fiche IA."""
    m = message.lower()
    if any(k in m for k in ("conteste", "litige", "cassé", "casse", "endommage", "erreur", "refuse", "jamais reçu", "avoir", "périmé")):
        return "LITIGE"
    if any(k in m for k in ("échéancier", "echeancier", "trois fois", "deux fois", "étaler", "etaler", "acompte", "moitié", "tranches")):
        return "DEMANDE_ECHEANCIER"
    if any(k in m for k in ("déjà payé", "deja paye", "virement", "momo", "envoyé", "envoye", "réglé", "regle", "reçu de paiement")):
        return "ANNONCE_PAIEMENT"
    return "RETARD_SIMPLE"


classifier_singleton = ClientMessageClassifier()


if __name__ == "__main__":
    import json
    metrics = classifier_singleton.evaluate_holdout()
    print(json.dumps(metrics, indent=2, ensure_ascii=False))

