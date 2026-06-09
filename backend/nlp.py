"""IndoBERT-powered NLP for Indonesian political text analysis.

Uses:
- Ensemble sentiment: keyword lexicon (primary) + RoBERTa model (confidence booster)
- IndoBERT NER for entity extraction (cahya/bert-base-indonesian-NER)
- Keyword-based category classification (well-defined domain categories)

Models are lazy-loaded on first use to avoid startup delays.
Results are cached with LRU to avoid re-inference on repeated text.
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from typing import Any

logger = logging.getLogger(__name__)

# ── Model singletons (lazy-loaded) ────────────────────────────────

_ner_model = None
_sentiment_model = None
_ner_load_failed = False
_sentiment_load_failed = False


def _load_ner():
    """Load IndoBERT NER model on first use."""
    global _ner_model, _ner_load_failed
    if _ner_model is not None or _ner_load_failed:
        return
    try:
        from transformers import pipeline

        logger.info("Loading IndoBERT NER model...")
        _ner_model = pipeline(
            "ner",
            model="cahya/bert-base-indonesian-NER",
            tokenizer="cahya/bert-base-indonesian-NER",
            aggregation_strategy="simple",
        )
        logger.info("IndoBERT NER model loaded!")
    except Exception as e:
        logger.warning(f"Failed to load NER model; using regex fallback: {e}")
        _ner_model = None
        _ner_load_failed = True


def _load_sentiment():
    """Load RoBERTa sentiment model on first use."""
    global _sentiment_model, _sentiment_load_failed
    if _sentiment_model is not None or _sentiment_load_failed:
        return
    try:
        from transformers import pipeline

        logger.info("Loading RoBERTa sentiment model...")
        _sentiment_model = pipeline(
            "sentiment-analysis",
            model="w11wo/indonesian-roberta-base-sentiment-classifier",
            tokenizer="w11wo/indonesian-roberta-base-sentiment-classifier",
            top_k=None,  # return all class scores
        )
        logger.info("RoBERTa sentiment model loaded!")
    except Exception as e:
        logger.warning(f"Failed to load sentiment model; using keyword fallback: {e}")
        _sentiment_model = None
        _sentiment_load_failed = True


# ── Sentiment Analysis ────────────────────────────────────────────

# Comprehensive Indonesian financial/political sentiment lexicon
_POSITIVE_WORDS = [
    # Financial positive
    "naik", "positif", "untung", "laba", "cuan", "melonjak", "meroket",
    "bullish", "rally", "profit", "growth", "rebound",
    # Economic positive
    "investasi", "pertumbuhan", "pemulihan", "optimis", "prospek",
    "peningkatan", "perkembangan", "kemajuan", "keberhasilan", "kinerja",
    "dividen", "akuisisi", "ekspansi", "inovasi", "efisiensi",
    # Political positive
    "dorong", "dukung", "stabil", "penguatan", "berhasil", "disetujui",
    "pengesahan", "perjanjian", "kerjasama", "damai", "reformasi",
    # Market positive
    "demand", "buying", "accumulation", "breakout", "support",
]
_NEGATIVE_WORDS = [
    # Financial negative
    "turun", "anjlok", "merosot", "rugi", "kerugian", "defisit",
    "bearish", "crash", "sell-off", "capital outflow",
    # Economic negative
    "risiko", "krisis", "resesi", "inflasi", "gejolak", "utang",
    "default", "gagal bayar", "likuidasi",
    # Political negative
    "korupsi", "skandal", "konflik", "sanksi", "denda", "pelanggaran",
    "penyelundupan", "gugatan", "batal", "ditolak", "dibekukan",
    # Market negative
    "melemah", "tekan", "jatuh", "masalah", "larang", "polemik",
    "negative", "negatif", "uncertainty",
]

# Negation words that flip sentiment
_NEGATION_WORDS = ["tidak", "bukan", "belum", "tanpa", "tak", "jangan", "bukanlah"]

# Multi-word phrases
_POSITIVE_PHRASES = [
    "menunjukkan peningkatan", "kinerja positif", "prospek cerah",
    "tumbuh signifikan", "berpotensi naik",
]
_NEGATIVE_PHRASES = [
    "menurun tajam", "berpotensi turun", "risiko tinggi",
    "ancaman serius", "dampak negatif",
]


def _keyword_sentiment(text: str) -> tuple[str, float, float]:
    """Enhanced keyword-based sentiment with negation handling."""
    text_lower = text.lower()
    words = text_lower.split()

    pos_count = 0
    neg_count = 0

    for i, word in enumerate(words):
        # Check for negation in preceding 2 words
        is_negated = False
        for j in range(max(0, i - 2), i):
            if words[j] in _NEGATION_WORDS:
                is_negated = True
                break

        if word in _POSITIVE_WORDS:
            if is_negated:
                neg_count += 1
            else:
                pos_count += 1
        elif word in _NEGATIVE_WORDS:
            if is_negated:
                pos_count += 1
            else:
                neg_count += 1

    # Also check multi-word phrases
    for phrase in _POSITIVE_PHRASES:
        if phrase in text_lower:
            pos_count += 1

    for phrase in _NEGATIVE_PHRASES:
        if phrase in text_lower:
            neg_count += 1

    total = pos_count + neg_count
    if total == 0:
        return "neutral", 0.0, 0.35

    score = max(-1.0, min(1.0, (pos_count - neg_count) / total))
    confidence = min(1.0, 0.35 + 0.10 * total)

    if score > 0.12:
        return "positive", round(score, 3), round(confidence, 3)
    if score < -0.12:
        return "negative", round(score, 3), round(confidence, 3)
    return "neutral", round(score, 3), round(confidence, 3)


def _model_sentiment(text: str) -> tuple[str, float, float] | None:
    """Run the RoBERTa sentiment model. Returns (label, score, confidence) or None if unavailable."""
    _load_sentiment()
    if _sentiment_model is None:
        return None

    try:
        results = _sentiment_model(text[:512])
        # results is [[{'label': 'positive', 'score': 0.9}, {'label': 'negative', 'score': 0.05}, ...]]
        if not results or not results[0]:
            return None

        scores = {r["label"].lower(): r["score"] for r in results[0]}
        pos = scores.get("positive", 0.0)
        neg = scores.get("negative", 0.0)
        neu = scores.get("neutral", 0.0)

        # Convert to -1..+1 score
        score = pos - neg
        # Confidence = how dominant the top class is
        max_score = max(pos, neg, neu)
        confidence = max_score

        # Label from highest score
        if pos > neg and pos > neu:
            label = "positive"
        elif neg > pos and neg > neu:
            label = "negative"
        else:
            label = "neutral"

        return label, round(score, 3), round(confidence, 3)

    except Exception as e:
        logger.warning(f"Sentiment model inference failed: {e}")
        return None


def _ensemble_sentiment(text: str) -> tuple[str, float, float]:
    """Ensemble sentiment: keywords are PRIMARY, model only adjusts confidence.

    Strategy:
    - Keywords determine the label and score (domain-tuned for Indonesian finance)
    - Model is used ONLY to adjust confidence:
      * Agreement → boost confidence (+0.12)
      * Disagreement → slight confidence penalty (-0.05)
      * Model unavailable → use keywords as-is
    - This prevents the model (trained on general text) from overriding
      domain-specific keyword signals like "turun" (bearish) or "dorong" (bullish)
    """
    kw_label, kw_score, kw_conf = _keyword_sentiment(text)
    ml_result = _model_sentiment(text)

    # Model not available — use keywords only
    if ml_result is None:
        return kw_label, kw_score, kw_conf

    ml_label, ml_score, ml_conf = ml_result

    # Both agree → boost confidence
    if kw_label == ml_label:
        boosted_conf = min(1.0, max(kw_conf, ml_conf) + 0.12)
        return kw_label, kw_score, round(boosted_conf, 3)

    # Model says neutral, keywords have a clear signal → keep keywords, small penalty
    if ml_label == "neutral" and kw_label != "neutral":
        return kw_label, kw_score, round(max(0.2, kw_conf - 0.05), 3)

    # Keywords say neutral, model has a signal → keep keywords (domain priority)
    if kw_label == "neutral" and ml_label != "neutral":
        return kw_label, kw_score, round(max(0.2, kw_conf - 0.03), 3)

    # Full disagreement (positive vs negative) → keep keywords, penalty
    return kw_label, kw_score, round(max(0.2, kw_conf - 0.08), 3)


@lru_cache(maxsize=512)
def analyze_sentiment_ml(text: str) -> tuple[str, float, float]:
    """Sentiment analysis. Ensemble of keyword lexicon + RoBERTa model.

    Keywords are the primary signal (domain-tuned for Indonesian finance).
    The RoBERTa model adjusts confidence: agreement boosts, disagreement penalizes.

    Returns (label, score, confidence).
    label: 'positive', 'negative', 'neutral'
    score: -1.0 to 1.0
    confidence: 0.0 to 1.0
    """
    return _ensemble_sentiment(text)


# ── Named Entity Recognition ──────────────────────────────────────

# Known Indonesian political/business entities for alias matching
_KNOWN_ENTITIES = {
    # Government officials
    "Jokowi": "Joko Widodo", "Prabowo": "Prabowo Subianto",
    "Sri Mulyani": "Sri Mulyani Indrawati", "Luhut": "Luhut Binsar Pandjaitan",
    "Erick Thohir": "Erick Thohir", "Bahlil": "Bahlil Lahadalia",
    "Airlangga": "Airlangga Hartarto", "Zulhas": "Zulkifli Hasan",
    # Institutions
    "BI": "Bank Indonesia", "OJK": "Otoritas Jasa Keuangan",
    "KPK": "Komisi Pemberantasan Korupsi", "KPU": "Komisi Pemilihan Umum",
    "MK": "Mahkamah Konstitusi", "DPR": "Dewan Perwakilan Rakyat",
    "BUMN": "Badan Usaha Milik Negara", "BPS": "Badan Pusat Statistik",
    "Kemenkeu": "Kementerian Keuangan", "Kemenko": "Kementerian Koordinator",
    # Companies (common abbreviations)
    "BCA": "Bank Central Asia", "BRI": "Bank Rakyat Indonesia",
    "Mandiri": "Bank Mandiri", "Telkom": "Telkom Indonesia",
    "Pertamina": "Pertamina", "PLN": "PLN",
}

# Words to exclude from entity extraction
_EXCLUDE_WORDS = {
    "yang", "dengan", "untuk", "dari", "dalam", "pada", "adalah", "akan",
    "telah", "dapat", "juga", "sudah", "masih", "belum", "tidak", "bukan",
    "para", "seluruh", "semua", "serta", "namun", "tetapi", "karena",
    "oleh", "kepada", "antara", "setelah", "sebelum", "saat", "ketika",
    "hal", "ini", "itu", "tersebut", "seperti", "menjadi", "oleh",
    "The", "And", "For", "With", "From", "This", "That",
}


def _regex_entities(text: str) -> list[str]:
    """Extract entities using regex patterns (fallback)."""
    entities = []
    seen = set()

    # Known entity aliases
    for alias, full_name in _KNOWN_ENTITIES.items():
        if alias.lower() in text.lower() and full_name.lower() not in seen:
            entities.append(full_name)
            seen.add(full_name.lower())

    # Capitalized multi-word names (Indonesian proper nouns)
    for match in re.findall(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,}){0,3}\b", text):
        if match not in _EXCLUDE_WORDS and len(match) > 3 and match.lower() not in seen:
            entities.append(match)
            seen.add(match.lower())

    return entities[:12]


@lru_cache(maxsize=512)
def extract_entities_ml(text: str) -> list[str]:
    """Extract named entities using IndoBERT NER (with regex fallback)."""
    _load_ner()
    if _ner_model is None:
        return _regex_entities(text)

    try:
        ner_results = _ner_model(text[:512])
        entities = []
        seen = set()

        for ent in ner_results:
            word = ent["word"].replace("##", "").strip()
            if word and len(word) > 2 and word.lower() not in seen and word not in _EXCLUDE_WORDS:
                seen.add(word.lower())
                entities.append(word)

        # Merge with known entity aliases
        for alias, full_name in _KNOWN_ENTITIES.items():
            if alias.lower() in text.lower() and full_name.lower() not in seen:
                entities.append(full_name)
                seen.add(full_name.lower())

        return entities[:12]

    except Exception as e:
        logger.warning(f"NER inference failed, using regex fallback: {e}")
        return _regex_entities(text)


# ── Category Classification ───────────────────────────────────────

CATEGORY_KEYWORDS = {
    "REGULATION_NEW": ["regulasi", "peraturan", "permen", "perpres", "perppu", "uu ", "undang-undang", "keputusan", "surat edaran"],
    "CABINET_RESHUFFLE": ["reshuffle", "kabinet", "menteri", "menteri baru", "pelantikan", "pengganti"],
    "CORRUPTION_CASE": ["korupsi", "kpk", "gratifikasi", "suap", "kolusi", "penggelapan", "pencucian uang"],
    "TAX_POLICY": ["pajak", "tarif", "ppn", "pph", "tax amnesty", "pengampunan pajak"],
    "TRADE_POLICY": ["impor", "ekspor", "tarif", "larangan ekspor", "larangan impor", "kuota", "bea masuk"],
    "MONETARY_POLICY": ["suku bunga", "bi rate", "inflasi", "moneter", "likuiditas", "gwm", "obligasi"],
    "INFRASTRUCTURE": ["infrastruktur", "jalan tol", "pelabuhan", "bandara", "kereta", "proyek", "ibukota baru"],
    "ENERGY_POLICY": ["energi", "bbm", "listrik", "minyak", "gas", "tambang", "batu bara", "hilirisasi"],
    "INVESTMENT": ["investasi", "fdi", "penanaman modal", "izin usaha", "ease of doing business"],
    "DEFENSE": ["pertahanan", "militer", "alutsista", "anggaran pertahanan", "tni"],
    "HEALTH": ["kesehatan", "rs", "vaksin", "bpjs", "farmasi", "pandemi"],
    "EDUCATION": ["pendidikan", "sekolah", "universitas", "kurikulum", "beasiswa"],
}


def classify_categories_ml(text: str) -> list[str]:
    """Classify text into political event categories using keyword matching."""
    text_lower = text.lower()
    hits = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            hits.append(category)
    return hits[:4]


# ── Composite Analysis ────────────────────────────────────────────

def analyze_article_nlp(title: str, text: str = "") -> dict[str, Any]:
    """Full NLP analysis of an article."""
    full_text = f"{title} {text}".strip()

    sentiment_label, sentiment_score, sentiment_confidence = analyze_sentiment_ml(full_text[:512])
    entities = extract_entities_ml(full_text[:512])
    categories = classify_categories_ml(full_text)

    return {
        "sentiment_label": sentiment_label,
        "sentiment_score": sentiment_score,
        "sentiment_confidence": sentiment_confidence,
        "entities": entities,
        "categories": categories,
        "nlp_method": f"ensemble(keyword+roberta)+{'indobert-ner' if _ner_model is not None else 'regex'}",
    }


def get_nlp_status() -> dict[str, Any]:
    """Return current NLP model status for dashboard display."""
    ner_loaded = _ner_model is not None
    sentiment_loaded = _sentiment_model is not None
    return {
        "sentiment_engine": "ensemble_keyword_roberta" if sentiment_loaded else "expanded_keyword_v2",
        "sentiment_model": "w11wo/indonesian-roberta-base-sentiment-classifier" if sentiment_loaded else None,
        "sentiment_model_loaded": sentiment_loaded,
        "ner_model": "cahya/bert-base-indonesian-NER" if ner_loaded else "regex_fallback",
        "ner_loaded": ner_loaded,
        "features": [
            "expanded_lexicon",
            "negation_handling",
            "multi_word_phrases",
            "roberta_sentiment" if sentiment_loaded else "keyword_only_sentiment",
            "indobert_ner" if ner_loaded else "regex_entities",
        ],
        "cache_size": analyze_sentiment_ml.cache_info().currsize if hasattr(analyze_sentiment_ml, 'cache_info') else 0,
        "entity_cache_size": extract_entities_ml.cache_info().currsize if hasattr(extract_entities_ml, 'cache_info') else 0,
    }
