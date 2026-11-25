"""
Brique IA Locale (100% hors-ligne) - Classification NLP des messages clients avec score de confiance.
Permet de detecter automatiquement les contestations/litiges, promesses d'echeancier, preuves de paiement,
et d'envoyer tout cas incertain (confiance < 0.75) dans la file de revue humaine.
"""
from typing import Dict, Any
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
import config

TRAINING_CORPUS = [
    # LITIGE
    ("La marchandise livree est endommagee je conteste cette facture", "LITIGE"),
    ("Il y a une erreur sur le montant facture nous refusons de payer ce prix", "LITIGE"),
    ("Les cartons recus etaient incomplets nous avons ouvert une reclamation", "LITIGE"),
    ("Nous contestons la facture car la prestation n'est pas terminee", "LITIGE"),
    ("Le tarif applique ne correspond pas au devis signe c'est un litige", "LITIGE"),
    ("Produits defectueux a la livraison nous demandons un avoir", "LITIGE"),
    # DEMANDE_ECHEANCIER
    ("Nous avons un souci de tresorerie pouvons-nous payer en trois fois", "DEMANDE_ECHEANCIER"),
    ("Je propose de regler la moitie vendredi et le solde le mois prochain", "DEMANDE_ECHEANCIER"),
    ("Serait-il possible de mettre en place un echeancier de paiement", "DEMANDE_ECHEANCIER"),
    ("Nous demandons un etalement du reglement sur deux echeances", "DEMANDE_ECHEANCIER"),
    ("Je peux verser un acompte de 100000 FCFA cette semaine et le reste le 30", "DEMANDE_ECHEANCIER"),
    # ANNONCE_PAIEMENT
    ("J'ai deja paye cette facture hier par Mobile Money reference MOMO8493", "ANNONCE_PAIEMENT"),
    ("Le virement bancaire a ete effectue ce matin merci de verifier votre compte", "ANNONCE_PAIEMENT"),
    ("Facture deja reglee via MTN MoMo voici le numero de transaction", "ANNONCE_PAIEMENT"),
    ("Notre comptable a envoye le reglement la semaine derniere", "ANNONCE_PAIEMENT"),
    # RETARD_SIMPLE
    ("Desole pour cet oubli nous faisons le necessaire rapidement", "RETARD_SIMPLE"),
    ("Bonjour merci pour le rappel nous traitons la facture cette semaine", "RETARD_SIMPLE"),
    ("Bien recu votre message de relance nous nous en occupons", "RETARD_SIMPLE"),
    ("Excusez-nous pour le retard de paiement", "RETARD_SIMPLE"),
]


class ClientMessageClassifier:
    def __init__(self) -> None:
        self.pipeline = Pipeline([
            ("tfidf", TfidfVectorizer(ngram_range=(1, 2), lowercase=True)),
            ("clf", LogisticRegression(C=2.0, class_weight="balanced", random_state=42)),
        ])
        self._is_trained = False

    def ensure_trained(self) -> None:
        if not self._is_trained:
            texts = [t for t, _ in TRAINING_CORPUS]
            labels = [l for _, l in TRAINING_CORPUS]
            self.pipeline.fit(texts, labels)
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
            }

        self.ensure_trained()
        probs = self.pipeline.predict_proba([message])[0]
        classes = self.pipeline.classes_
        best_idx = int(probs.argmax())
        categorie = str(classes[best_idx])
        confiance = round(float(probs[best_idx]), 3)

        requiert_revue = False
        motif = None
        if confiance < confidence_threshold:
            requiert_revue = True
            motif = f"Cas incertain IA (confiance {confiance:.0%} < seuil {confidence_threshold:.0%})"
        elif categorie in ("LITIGE", "DEMANDE_ECHEANCIER", "ANNONCE_PAIEMENT"):
            requiert_revue = True
            motif = f"Message client detecte par IA comme [{categorie}] (confiance {confiance:.0%})"

        return {
            "categorie": categorie,
            "confiance": confiance,
            "requiert_revue_humaine": requiert_revue,
            "motif_revue": motif,
        }


classifier_singleton = ClientMessageClassifier()
