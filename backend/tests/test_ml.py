"""The from-scratch machine-learning models in ``app.ml`` and the e-mail classifier built on them."""

from __future__ import annotations

import math
import random

import pytest

from app.ml import KMeans, LogisticRegression, NaiveBayes, TfidfVectorizer, cosine
from app.ml.logistic import sigmoid
from app.services import email_intent
from app.services.email_parser import EmailMessage, heuristic_parse

DOCS = [
    "python backend intern fastapi postgresql rest api",
    "backend engineer python django postgresql",
    "machine learning intern pytorch deep learning python",
    "deep learning research pytorch computer vision",
    "frontend intern react typescript css",
    "react developer typescript next.js tailwind",
]


# --------------------------------------------------------------------------- TF-IDF
def test_tfidf_vectors_are_normalised_and_rare_terms_weigh_more():
    vectorizer = TfidfVectorizer()
    vectors = vectorizer.fit_transform(DOCS)
    assert all(math.isclose(sum(w * w for w in v.values()), 1.0) for v in vectors)
    assert vectorizer.idf["django"] > vectorizer.idf["python"]  # in 1 document against 3
    assert math.isclose(cosine(vectors[0], vectors[0]), 1.0)
    assert cosine(vectors[0], vectors[1]) > cosine(vectors[0], vectors[4])  # backend ~ backend, not frontend
    assert cosine(vectors[4], vectors[3]) == 0.0  # nothing in common
    assert vectorizer.transform("words never seen before") == {}
    assert TfidfVectorizer(max_features=3).fit(DOCS).idf.keys() <= {"python", "intern", "backend", "postgresql", "pytorch",
                                                                   "deep", "learning", "react", "typescript"}


# --------------------------------------------------------------------------- Naive Bayes
def test_naive_bayes_learns_from_counts():
    model = NaiveBayes().fit([
        ("we are pleased to offer you the position", "offer"), ("offer letter attached congratulations", "offer"),
        ("unfortunately we will not move forward", "rejection"), ("we regret to inform you", "rejection"),
        ("please schedule an interview call", "interview"), ("interview scheduled for monday", "interview"),
    ])
    assert model.classes == ["interview", "offer", "rejection"]
    label, probability = model.predict("congratulations, here is your offer letter")
    assert label == "offer" and probability > 0.8
    assert model.predict("we regret that we cannot move forward")[0] == "rejection"
    probabilities = model.predict_proba("interview on monday")
    assert math.isclose(sum(probabilities.values()), 1.0) and max(probabilities, key=probabilities.get) == "interview"
    assert "offer" in " ".join(model.informative_features("offer", 5))
    with pytest.raises(ValueError):
        NaiveBayes().predict("anything")


def test_email_classifier_generalises_leave_one_out():
    """Train on all examples but one, predict that one, for every example. Measures the model, not its memory."""
    examples = [(text, label) for label, texts in email_intent.TRAINING.items() for text in texts]
    correct = confident = confident_correct = 0
    for index, (text, label) in enumerate(examples):
        model = NaiveBayes().fit(examples[:index] + examples[index + 1:])
        predicted, probability = model.predict(text)
        correct += predicted == label
        if probability >= email_intent.STATUS_CONFIDENCE:
            confident += 1
            confident_correct += predicted == label
    assert correct / len(examples) >= 0.85
    assert confident_correct / confident >= 0.97  # the predictions allowed to change a status
    described = email_intent.describe()
    assert described["examples"] == len(examples) and len(described["classes"]) == 8


def _mail(subject: str, body: str) -> EmailMessage:
    return EmailMessage(sender_email="hr@acme.com", sender_name="Riya Shah", subject=subject, body=body)


def test_parser_uses_the_classifier_only_when_rules_find_nothing():
    # A rule matches: the classifier is not consulted.
    by_rule = heuristic_parse(_mail("Update", "Unfortunately we are not moving forward."), [], "Jane")
    assert by_rule["intent"] == "rejection" and by_rule["method"] == "heuristic"
    # No rule matches this wording; the classifier recognises an interview invitation.
    learned = heuristic_parse(_mail("Next steps", "We'd love to chat about the role. Are you free for a video call on Thursday?"),
                              [], "Jane")
    assert learned["intent"] == "interview_invite" and learned["method"] == "naive_bayes"
    assert learned["confidence"] <= 0.85
    # Unrelated mail stays generic.
    other = heuristic_parse(_mail("Lunch", "The canteen menu for this week is attached."), [], "Jane")
    assert other["intent"] == "generic" and other["status_update"] == "none"


def test_a_learned_rejection_never_closes_an_application(monkeypatch):
    monkeypatch.setattr(email_intent, "classify", lambda text: ("rejection", 0.99))
    parsed = heuristic_parse(_mail("Your application", "Sadly this time it is a no from our side."), [], "Jane")
    assert parsed["intent"] == "rejection" and parsed["method"] == "naive_bayes"
    assert parsed["status_update"] == "none"  # labelled and flagged for you, but the status is yours to change
    monkeypatch.setattr(email_intent, "classify", lambda text: ("acknowledgment", 0.8))
    assert heuristic_parse(_mail("Hello", "Got it, the team has your profile now."), [], "Jane")["status_update"] == "none"
    monkeypatch.setattr(email_intent, "classify", lambda text: ("acknowledgment", 0.95))
    assert heuristic_parse(_mail("Hello", "Got it, the team has your profile now."), [], "Jane")["status_update"] == "acknowledged"


# --------------------------------------------------------------------------- logistic regression
def test_logistic_regression_learns_a_separable_rule():
    rng = random.Random(3)
    rows, labels = [], []
    for _ in range(200):
        score, remote = rng.random(), float(rng.random() < 0.5)
        rows.append([score, remote, rng.random()])  # the third feature is noise
        labels.append(int(score + 0.2 * remote > 0.6))
    model = LogisticRegression().fit(rows, labels)
    assert model.accuracy(rows, labels) > 0.93
    assert model.loss_history[-1] < model.loss_history[0] / 2  # gradient descent actually descended
    assert model.weights[0] > model.weights[1] > abs(model.weights[2])  # score matters most, noise least
    assert model.predict_proba([0.95, 1.0, 0.5]) > 0.9 > 0.1 > model.predict_proba([0.05, 0.0, 0.5])
    explanation = model.explain([0.95, 1.0, 0.5], ["score", "remote", "noise"])
    assert explanation[0]["feature"] == "score" and explanation[0]["contribution"] > 0
    assert sigmoid(0) == 0.5 and sigmoid(-800) == 0.0 and sigmoid(800) == 1.0  # no overflow
    with pytest.raises(ValueError):
        LogisticRegression().fit([], [])


def test_logistic_regression_handles_a_constant_feature():
    model = LogisticRegression(epochs=50).fit([[1.0, 0.0], [1.0, 1.0], [1.0, 0.0], [1.0, 1.0]], [0, 1, 0, 1])
    assert model.predict([1.0, 1.0]) == 1 and model.predict([1.0, 0.0]) == 0


# --------------------------------------------------------------------------- k-means
def test_kmeans_groups_similar_documents():
    vectors = TfidfVectorizer().fit_transform(DOCS)
    model = KMeans(k=3).fit(vectors)
    assert model.labels[0] == model.labels[1] and model.labels[2] == model.labels[3] and model.labels[4] == model.labels[5]
    assert len(set(model.labels)) == 3 and model.cohesion(vectors) > 0.5
    assert "react" in model.top_terms(model.labels[4], 4) and "pytorch" in model.top_terms(model.labels[2], 4)
    assert KMeans(k=3).fit(vectors).labels == model.labels  # fixed seed: the same themes every time
    assert model.predict(TfidfVectorizer().fit(DOCS).transform("react typescript")) == model.labels[4]


def test_kmeans_edge_cases():
    assert KMeans(k=3).fit([]).labels == []
    assert KMeans(k=5).fit([{"a": 1.0}, {}, {"a": 1.0}]).labels == [0, -1, 0]  # k shrinks; empty documents stay out
    with pytest.raises(ValueError):
        KMeans(k=0)
