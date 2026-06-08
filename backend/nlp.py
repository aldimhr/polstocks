"""IndoBERT-powered NLP for Indonesian political text analysis.

Uses:
- Expanded keyword-based sentiment analysis (better than ML for domain-specific text)
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


def _load_ner():
    """Load IndoBERT NER model on first use."""
    global _ner_model
    if _ner_model is not None:
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
        logger.warning(f"Failed to load NER model: {e}")
        _ner_model = None


# ── Sentiment Analysis (expanded keyword-based) ───────────────────

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
    for phrase in ["menunjukkan peningkatan", "kinerja positif", "prospek cerah",
                    "tumbuh signifikan", "berpotensi naik"]:
        if phrase in text_lower:
            pos_count += 1

    for phrase in ["menurun tajam", "berpotensi turun", "risiko tinggi",
                    "ancaman serius", "dampak negatif"]:
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


@lru_cache(maxsize=512)
def analyze_sentiment_ml(text: str) -> tuple[str, float, float]:
    """Sentiment analysis. Uses expanded keyword lexicon with negation handling.

    Returns (label, score, confidence).
    label: 'positive', 'negative', 'neutral'
    score: -1.0 to 1.0
    confidence: 0.0 to 1.0
    """
    return _keyword_sentiment(text)


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
        "nlp_method": "indobert-ner" if _ner_model is not None else "keyword+regex",
    }


def get_nlp_status() -> dict[str, Any]:
    """Return current NLP model status for dashboard display."""
    ner_loaded = _ner_model is not None
    return {
        "sentiment_engine": "expanded_keyword_v2",
        "ner_model": "cahya/bert-base-indonesian-NER" if ner_loaded else "regex_fallback",
        "ner_loaded": ner_loaded,
        "features": [
            "expanded_lexicon",
            "negation_handling",
            "multi_word_phrases",
            "indobert_ner" if ner_loaded else "regex_entities",
        ],
        "cache_size": analyze_sentiment_ml.cache_info().currsize if hasattr(analyze_sentiment_ml, 'cache_info') else 0,
        "entity_cache_size": extract_entities_ml.cache_info().currsize if hasattr(extract_entities_ml, 'cache_info') else 0,
    }
