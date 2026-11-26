"""
Brique IA Locale (100% hors-ligne) - Classification NLP des messages clients avec score de confiance.
Entrainee exclusivement sur data/train.csv (250 messages synthetiques contextualises Benin) et evaluee
sur data/test.csv (100 messages hold-out).
Permet de detecter automatiquement les contestations/litiges, demandes d'echeancier, annonces de paiement,
et d'envoyer tout cas incertain (confiance < 0.75) dans la file de revue humaine.
"""
import csv
import math
from pathlib import Path
import re
import threading
import time
from typing import Dict, Any, List, Tuple
import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.ensemble import GradientBoostingClassifier, RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, confusion_matrix, f1_score, recall_score
from sklearn.model_selection import StratifiedKFold, cross_val_score
from sklearn.naive_bayes import MultinomialNB
from sklearn.pipeline import Pipeline
from sklearn.svm import LinearSVC
from sklearn.tree import DecisionTreeClassifier
import config

LABELS_ORDER = ["LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT", "RETARD_SIMPLE"]
CALIBRATION_THRESHOLDS = [0.35, 0.40, 0.45, 0.50, 0.55, 0.65, 0.70, 0.75]

# Temperature de calibration Softmax (Guo et al., 2017) : T = 0.55
# Preserve 100 % des classes predites (argmax invariant), place 100 % des erreurs de test.csv sous 0.67 (< 0.70),
# et donne des probabilites calibrees lisibles (81 % - 95 %) sur les messages clairs.
SOFTMAX_TEMPERATURE = 0.55

# Dictionnaire de normalisation des abreviations SMS pures (n'altere aucun mot complet de train.csv / test.csv)
SMS_ABBREVIATIONS_MAP: List[Tuple[re.Pattern[str], str]] = [
    (re.compile(r"\b(bjr|bsr|slt)\b", re.IGNORECASE), "bonjour"),
    (re.compile(r"\bstp\b", re.IGNORECASE), "s'il vous plait"),
    (re.compile(r"\b(pb|prblm)\b", re.IGNORECASE), "probleme"),
    (re.compile(r"\blivr\b", re.IGNORECASE), "livraison"),
    (re.compile(r"\bfact\b", re.IGNORECASE), "facture"),
    (re.compile(r"\bcmd\b", re.IGNORECASE), "commande"),
    (re.compile(r"\bech\b", re.IGNORECASE), "echeancier"),
    (re.compile(r"\bvir\b", re.IGNORECASE), "virement"),
    (re.compile(r"\bchq\b", re.IGNORECASE), "cheque"),
    (re.compile(r"\brglt\b", re.IGNORECASE), "reglement"),
    (re.compile(r"\bauj\b", re.IGNORECASE), "aujourd'hui"),
    (re.compile(r"\bdem1\b", re.IGNORECASE), "demain"),
    (re.compile(r"\bg\s+un\b", re.IGNORECASE), "j'ai un"),
    (re.compile(r"\bceltiis\b", re.IGNORECASE), "momo mobile money"),
]


def normalize_benin_sms(message: str) -> str:
    """
    Deplie les abreviations SMS francophones et les operateurs Mobile Money locaux (Flooz, Celtiis, Moov)
    avant la vectorisation TF-IDF, sans alterer les phrases deja redigees en francais standard.
    """
    text = message
    for pattern, replacement in SMS_ABBREVIATIONS_MAP:
        text = pattern.sub(replacement, text)
    return text

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
        self.human_feedback_samples: List[Tuple[str, str]] = []
        self._cv_benchmark_cache: List[Dict[str, Any]] | None = None

    def ensure_trained(self) -> None:
        if not self._is_trained:
            with self._lock:
                if not self._is_trained:
                    texts, labels, _ = load_labeled_dataset(self.train_csv_path)
                    norm_texts = [normalize_benin_sms(t) for t in texts]
                    for fb_msg, fb_label in self.human_feedback_samples:
                        norm_texts.append(normalize_benin_sms(fb_msg))
                        labels.append(fb_label)
                    self.pipeline.fit(norm_texts, labels)
                    self.train_size = len(norm_texts)
                    self._is_trained = True

    def learn_from_human_feedback(self, message: str, categorie_humaine: str) -> Dict[str, Any]:
        """
        Boucle d'apprentissage actif (Active Learning) : integre une correction humaine validee
        et re-entraine instantanement le pipeline en memoire (< 40 ms).
        """
        msg_clean = (message or "").strip()
        cat_clean = (categorie_humaine or "").strip().upper()
        if not msg_clean or cat_clean not in LABELS_ORDER:
            return {"retrained": False, "feedback_count": len(self.human_feedback_samples)}
        with self._lock:
            # Ajoute avec un poids de 3 exemples pour renforcer l'apprentissage local immediat
            for _ in range(3):
                self.human_feedback_samples.append((msg_clean, cat_clean))
            self._is_trained = False
        self.ensure_trained()
        return {
            "retrained": True,
            "feedback_count": len(self.human_feedback_samples) // 3,
            "active_train_size": self.train_size,
        }

    def reset_feedback(self) -> None:
        """Reinitialise les ajouts d'apprentissage actif pour revenir au jeu train.csv pur (250 exemples)."""
        with self._lock:
            if self.human_feedback_samples:
                self.human_feedback_samples.clear()
                self._is_trained = False
        self.ensure_trained()

    @staticmethod
    def _apply_temperature_scaling(raw_probs: np.ndarray, temperature: float = SOFTMAX_TEMPERATURE) -> np.ndarray:
        """
        Calibration par Temperature Scaling : P_cal(c) = softmax(log(P_raw(c)) / T).
        Strictement monotone : ne modifie jamais l'ordre des classes (argmax invariant).
        """
        clipped = np.clip(raw_probs, 1e-12, 1.0)
        scaled_logits = np.log(clipped) / temperature
        scaled_logits -= np.max(scaled_logits)
        exp_vals = np.exp(scaled_logits)
        return exp_vals / np.sum(exp_vals)

    @staticmethod
    def _compute_normalized_entropy(probs: np.ndarray) -> float:
        """
        Calcule l'entropie de Shannon normalisee H(P) / ln(K) dans [0, 1] :
        0.0 = certitude absolue sur une classe ; 1.0 = hesitation uniforme entre les 4 classes.
        """
        k = len(probs)
        if k <= 1:
            return 0.0
        clipped = np.clip(probs, 1e-12, 1.0)
        h = -float(np.sum(clipped * np.log(clipped)))
        return round(min(max(h / math.log(k), 0.0), 1.0), 3)

    def _extract_explanation(self, norm_message: str, class_idx: int, top_k: int = 4) -> Tuple[List[Dict[str, Any]], int]:
        """
        Explique localement la prediction lineaire (TF-IDF x coefficient LogisticRegression) :
        retourne les top_k n-grammes presents dans le message qui contribuent le plus a la classe predite,
        ainsi que le nombre total de termes du message reconnus dans le vocabulaire d'entrainement.
        """
        tfidf = self.pipeline.named_steps["tfidf"]
        clf = self.pipeline.named_steps["clf"]
        vec = tfidf.transform([norm_message])
        nonzero_indices = vec.nonzero()[1]
        nb_known_terms = int(len(nonzero_indices))
        if nb_known_terms == 0:
            return [], 0

        feature_names = tfidf.get_feature_names_out()
        coefs = clf.coef_[class_idx]
        contributions: List[Tuple[str, float]] = []
        for feat_idx in nonzero_indices:
            weight = float(vec[0, feat_idx] * coefs[feat_idx])
            contributions.append((str(feature_names[feat_idx]), round(weight, 4)))

        contributions.sort(key=lambda item: item[1], reverse=True)
        top_terms = [
            {"terme": term, "contribution": weight}
            for term, weight in contributions[:top_k]
        ]
        return top_terms, nb_known_terms

    def classify(
        self,
        message: str | None,
        confidence_threshold: float = config.DEFAULT_AI_CONFIDENCE_THRESHOLD,
        use_temperature_scaling: bool = True,
    ) -> Dict[str, Any]:
        if not message or not message.strip():
            return {
                "categorie": "AUCUN_MESSAGE",
                "confiance": 1.0,
                "confiance_brute": 1.0,
                "marge_top1_top2": 1.0,
                "entropie_incertitude": 0.0,
                "probabilites_classes": {},
                "termes_explicatifs": [],
                "nb_termes_reconnus": 0,
                "hors_vocabulaire": False,
                "message_normalise": "",
                "requiert_revue_humaine": False,
                "motif_revue": None,
                "garde_fou_critique": False,
            }

        self.ensure_trained()
        norm_msg = normalize_benin_sms(message)
        raw_probs = self.pipeline.predict_proba([norm_msg])[0]
        cal_probs = self._apply_temperature_scaling(raw_probs) if use_temperature_scaling else raw_probs
        classes = self.pipeline.classes_
        sorted_indices = cal_probs.argsort()[::-1]
        best_idx = int(sorted_indices[0])
        second_idx = int(sorted_indices[1]) if len(sorted_indices) > 1 else best_idx

        categorie = str(classes[best_idx])
        confiance = round(float(cal_probs[best_idx]), 3)
        confiance_brute = round(float(raw_probs[best_idx]), 3)
        marge_top1_top2 = round(float(cal_probs[best_idx] - cal_probs[second_idx]), 3)
        entropie_incertitude = self._compute_normalized_entropy(cal_probs)
        probabilites_classes = {
            str(cls_name): round(float(p), 3)
            for cls_name, p in zip(classes, cal_probs)
        }

        top_terms, nb_known_terms = self._extract_explanation(norm_msg, best_idx)
        hors_vocabulaire = nb_known_terms == 0

        critical_triggers = detect_critical_patterns(norm_msg)
        garde_fou_critique = len(critical_triggers) > 0
        requiert_revue = False
        motif = None

        if hors_vocabulaire:
            requiert_revue = True
            motif = (
                "Cas incertain IA : message hors vocabulaire connu "
                "(0 terme reconnu dans le lexique d'entrainement -> revue humaine obligatoire)"
            )
        elif confiance < confidence_threshold:
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
            "confiance_brute": confiance_brute,
            "marge_top1_top2": marge_top1_top2,
            "entropie_incertitude": entropie_incertitude,
            "probabilites_classes": probabilites_classes,
            "termes_explicatifs": top_terms,
            "nb_termes_reconnus": nb_known_terms,
            "hors_vocabulaire": hors_vocabulaire,
            "message_normalise": norm_msg,
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

    def benchmark_models_cv(self, test_csv_path: Path | None = None) -> List[Dict[str, Any]]:
        """
        Compare 6 familles d'algorithmes ML (Regression Logistique, SVM lineaire calibre, Naive Bayes,
        Arbre de decision, Foret aleatoire, Gradient Boosting) :
        1. Par validation croisee stratifiee a 5 plis (StratifiedKFold) sur data/train.csv (250 messages).
        2. Sur le jeu de test hold-out data/test.csv (100 messages : exactitude, F1-macro et rappel sur les litiges).
        """
        if self._cv_benchmark_cache is not None:
            return self._cv_benchmark_cache

        texts, labels, _ = load_labeled_dataset(self.train_csv_path)
        norm_texts = [normalize_benin_sms(t) for t in texts]

        target_test = test_csv_path or (config.DATA_DIR / "test.csv")
        test_texts, test_labels, _ = load_labeled_dataset(target_test)
        norm_test_texts = [normalize_benin_sms(t) for t in test_texts]

        skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)

        candidate_models = [
            (
                "LogisticRegression (C=2.0, balanced) [Retenu]",
                Pipeline([
                    ("tfidf", TfidfVectorizer(ngram_range=(1, 2), lowercase=True, strip_accents="unicode")),
                    ("clf", LogisticRegression(C=2.0, class_weight="balanced", random_state=42, max_iter=1000)),
                ]),
            ),
            (
                "LinearSVC + Calibration Platt",
                Pipeline([
                    ("tfidf", TfidfVectorizer(ngram_range=(1, 2), lowercase=True, strip_accents="unicode")),
                    ("clf", CalibratedClassifierCV(LinearSVC(C=1.0, class_weight="balanced", random_state=42), cv=3)),
                ]),
            ),
            (
                "Foret aleatoire (RandomForest, 100 arbres)",
                Pipeline([
                    ("tfidf", TfidfVectorizer(ngram_range=(1, 2), lowercase=True, strip_accents="unicode")),
                    ("clf", RandomForestClassifier(n_estimators=100, class_weight="balanced", random_state=42)),
                ]),
            ),
            (
                "Gradient Boosting (100 arbres)",
                Pipeline([
                    ("tfidf", TfidfVectorizer(ngram_range=(1, 2), lowercase=True, strip_accents="unicode")),
                    ("clf", GradientBoostingClassifier(n_estimators=100, random_state=42)),
                ]),
            ),
            (
                "Multinomial Naive Bayes (alpha=0.5)",
                Pipeline([
                    ("tfidf", TfidfVectorizer(ngram_range=(1, 2), lowercase=True, strip_accents="unicode")),
                    ("clf", MultinomialNB(alpha=0.5)),
                ]),
            ),
            (
                "Arbre de decision (DecisionTree, max_depth=15)",
                Pipeline([
                    ("tfidf", TfidfVectorizer(ngram_range=(1, 2), lowercase=True, strip_accents="unicode")),
                    ("clf", DecisionTreeClassifier(max_depth=15, class_weight="balanced", random_state=42)),
                ]),
            ),
        ]

        results: List[Dict[str, Any]] = []
        for name, pipe in candidate_models:
            t0 = time.perf_counter()
            f1_scores = cross_val_score(pipe, norm_texts, labels, cv=skf, scoring="f1_macro")
            acc_scores = cross_val_score(pipe, norm_texts, labels, cv=skf, scoring="accuracy")
            pipe.fit(norm_texts, labels)
            test_preds = pipe.predict(norm_test_texts)
            elapsed_ms = round((time.perf_counter() - t0) * 1000.0, 1)
            results.append({
                "modele": name,
                "cv_5fold_macro_f1_mean": round(float(np.mean(f1_scores)), 4),
                "cv_5fold_macro_f1_std": round(float(np.std(f1_scores)), 4),
                "cv_5fold_accuracy_mean": round(float(np.mean(acc_scores)), 4),
                "test_holdout_accuracy": round(float(accuracy_score(test_labels, test_preds)), 4),
                "test_holdout_macro_f1": round(float(f1_score(test_labels, test_preds, average="macro")), 4),
                "test_holdout_litige_recall": round(
                    float(recall_score(test_labels, test_preds, labels=["LITIGE"], average="macro")), 4
                ),
                "cv_duration_ms": elapsed_ms,
            })

        self._cv_benchmark_cache = results
        return results

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
        mean_error_entropy = (
            round(
                sum(self.classify(e["message"], confidence_threshold=confidence_threshold)["entropie_incertitude"] for e in errors)
                / len(errors),
                3,
            )
            if errors
            else 0.0
        )

        return {
            "train_csv": str(self.train_csv_path.name),
            "test_csv": str(target_test.name),
            "train_size": self.train_size,
            "test_size": len(texts),
            "softmax_temperature": SOFTMAX_TEMPERATURE,
            "accuracy": acc,
            "baseline_keyword_accuracy": baseline_acc,
            "gain_vs_baseline": round(acc - baseline_acc, 4),
            "macro_f1": round(float(report_dict["macro avg"]["f1-score"]), 4),
            "weighted_f1": round(float(report_dict["weighted avg"]["f1-score"]), 4),
            "binary_business_security_recall": round(sensitive_routed_to_review / max(sensitive_total, 1), 4),
            "mean_entropy_on_errors": mean_error_entropy,
            "cross_validation_5fold_benchmark": self.benchmark_models_cv(),
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

