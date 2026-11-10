"""Machine learning written from scratch (no NumPy, no scikit-learn).

=================  ==============================================  ================================
Module             Model                                           Used for
=================  ==============================================  ================================
``tfidf``          TF-IDF vectors + cosine similarity              "jobs like this one", clustering
``naive_bayes``    Multinomial Naive Bayes (Laplace smoothing)     classifying recruiter e-mails
``logistic``       Logistic regression (gradient descent, L2)      chance that an application gets
                                                                   a reply
``kmeans``         k-means++ clustering on sparse vectors          grouping your jobs into themes
=================  ==============================================  ================================

These are the app's *classical* ML models: small, fast, explainable, and trained on the user's own
data inside the request. The large language model (Claude / Ollama, ``services/llm.py``) handles
the open-ended language work; these models back it up when no LLM is configured and answer the
questions where a number with a reason beats a paragraph.
"""

from app.ml.kmeans import KMeans
from app.ml.logistic import LogisticRegression
from app.ml.naive_bayes import NaiveBayes
from app.ml.tfidf import TfidfVectorizer, cosine

__all__ = ["KMeans", "LogisticRegression", "NaiveBayes", "TfidfVectorizer", "cosine"]
