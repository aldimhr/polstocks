# Signal Robustness — Implementation Plan

**Goal:** Make PolStock harder to fool with weak/vague political noise — only link stocks when the policy connection is real, specific, and evidenced.

**Architecture:** The system already has strong foundations (relevance gate, channel matching, evidence tiers, stage detection). This plan tightens the gaps: stricter relevance filtering, vague-sector rejection, confidence penalties for weak signals, and explicit transmission path logging.

---

## What's Already Solid ✅

| Component | Status | Notes |
|-----------|--------|-------|
| Political relevance gate | ✅ | keyword scoring → political/maybe/not_political |
| Policy channel matching | ✅ | company knowledge base + keyword confidence |
| Relationship type gate | ✅ | requires direct alias OR matched channels |
| Evidence quality scoring | ✅ | source rank + freshness + themes + direct alias |
| Min thresholds | ✅ | MIN_RELATIONSHIP_SCORE=3.0, MIN_EVIDENCE_QUALITY=2.0 |
| Event stage detection | ✅ | proposal→approved→effective→revoked |
| Reversal detection | ✅ | negation/reversal term matching |
| Direction analysis | ✅ | supportive/restrictive/relief scoring |
| Thread grouping | ✅ | proposal→approved→reversed threads |

---

## Gap Analysis — 6 Tasks

### Task 1: Reject "maybe" articles at the relationship gate

**Problem:** Articles labeled `"maybe"` (score 0.3–0.59) still flow into `build_stock_relationships`. A vague "pemerintah dorong ekonomi" article can match tickers through broad theme/sector overlap even though it's not a real policy signal.

**Fix:** In `build_stock_relationships`, skip articles with `relevance_label == "maybe"` unless they have a direct alias hit. "maybe" + no direct mention = too weak.

**Files:** `backend/main.py` (~L2945), `tests/test_app.py`

**Test:**
```python
def test_maybe_relevance_articles_rejected_without_direct_mention():
    vague = {**VAGUE_ARTICLE, "headline": "Pemerintah dorong ekonomi nasional", "summary": "Pemerintah mendorong pertumbuhan ekonomi tanpa sektor spesifik."}
    result = appmod.analyze_article(vague, ["ANTM.JK"], window="7d")
    # Should have no relationships — "maybe" + no direct alias
    assert result.get("stock_relationships") == []
```

**Implementation:** Add guard in `build_stock_relationships` before the relationship construction:
```python
if article.get("relevance_label") == "maybe" and not direct_alias_hit:
    continue
```

---

### Task 2: Reject vague sector-only spillovers

**Problem:** An article about "infrastruktur" can match BBCA (banking) through sector overlap alone, without any company-specific policy channel. The `relationship_type_for_link` gate requires `direct_alias_hit OR matched_channels`, but `matched_channels` can include very broad theme matches.

**Fix:** For `relationship_type == "indirect"`, require at least one matched channel with `channel_confidence >= 0.3`. Low-confidence channel matches are essentially sector spillovers.

**Files:** `backend/main.py` (`relationship_type_for_link` ~L2560), `tests/test_app.py`

**Test:**
```python
def test_indirect_relationship_requires_minimum_channel_confidence():
    # Article with only broad sector match, no company-specific channel
    broad = {**FAKE_ARTICLE, "summary": "Investasi dan infrastruktur nasional terus bertumbuh tanpa target spesifik."}
    result = appmod.analyze_article(broad, ["BBCA.JK"], window="7d")
    # If no channel has confidence >= 0.3, should not create relationship
    for rel in result.get("stock_relationships", []):
        if rel["relationship_type"] == "indirect":
            assert rel["channel_confidence"] >= 0.3
```

**Implementation:** In `relationship_type_for_link`, add minimum channel confidence check:
```python
def relationship_type_for_link(direct_alias_hit, matched_channels):
    if direct_alias_hit:
        return "direct"
    if matched_channels and max(ch.get("channel_confidence", 0) for ch in matched_channels) >= 0.3:
        return "indirect"
    return None
```

---

### Task 3: Penalize "maybe" relevance in confidence calculation

**Problem:** Even when "maybe" articles pass the gate (via direct alias), their confidence should be lower than "political" articles. Currently `confidence` in `build_stock_relationships` doesn't factor in `relevance_label`.

**Fix:** Apply a relevance multiplier: political=1.0, maybe=0.75. Already have `relevance_score` on the article.

**Files:** `backend/main.py` (~L2987, confidence calculation), `tests/test_app.py`

**Test:**
```python
def test_maybe_relevance_penalizes_confidence_vs_political():
    political = appmod.analyze_article(DIRECT_MENTION_ARTICLE, ["ANTM.JK"], window="7d")
    maybe = {**DIRECT_MENTION_ARTICLE, "source": "Blog", "source_type": "other", "source_weight": 0.3}
    maybe_result = appmod.analyze_article(maybe, ["ANTM.JK"], window="7d")
    if political.get("stock_relationships") and maybe_result.get("stock_relationships"):
        p_conf = political["stock_relationships"][0]["confidence"]
        m_conf = maybe_result["stock_relationships"][0]["confidence"]
        assert p_conf > m_conf
```

**Implementation:** In `build_stock_relationships`, after computing `confidence`, apply relevance penalty:
```python
relevance_label = str(article.get("relevance_label", "") or "")
if relevance_label == "maybe":
    confidence *= 0.75
```

---

### Task 4: Log transmission path rationale on each relationship

**Problem:** When a ticker is linked, the user can't see *why* — what specific transmission path connects the policy to the stock. The `rationale` field exists but is generic ("ANTM is mentioned directly"). For indirect links, it should explain the channel.

**Fix:** Enrich the `rationale` field to include the matched channel name and key evidence.

**Files:** `backend/main.py` (~L3001-3014), `tests/test_app.py`

**Test:**
```python
def test_relationship_rationale_includes_transmission_path():
    result = appmod.analyze_article(DIRECT_MENTION_ARTICLE, ["ANTM.JK"], window="7d")
    link = next(r for r in result["stock_relationships"] if r["ticker"] == "ANTM.JK")
    assert len(link["rationale"]) > 20  # Not just a generic stub
    assert "hilirisasi" in link["rationale"].lower() or "downstream" in link["rationale"].lower() or link["relationship_type"] == "direct"
```

**Implementation:** Enhance the rationale construction (currently ~L3001-3004):
```python
if direct_alias_hit:
    channel_hint = f" via {policy_channel}" if policy_channel else ""
    rationale = f"{company_name_for_ticker(ticker)} mentioned directly{channel_hint}"
else:
    channel_names = [ch["channel"] for ch in matched_channels[:2]]
    rationale = f"Linked through policy channel: {', '.join(channel_names)}"
```

---

### Task 5: Add confidence_label to formatted_events

**Problem:** The dashboard shows confidence on relationships but not the aggregate label on events. Users can't quickly scan which events are high-confidence vs predicted-only.

**Fix:** Add `confidence_label` to `formatted_events` based on the average relationship confidence.

**Files:** `backend/main.py` (~L3912), `tests/test_app.py`

**Test:**
```python
def test_formatted_events_include_confidence_label():
    payload = appmod.build_refresh_payload(["ANTM"], force=True, window="7d",
        news_fetcher=fake_news_fetcher, stock_fetcher=fake_stock_fetcher, market_fetcher=fake_market_fetcher)
    event = payload["events"][0]
    assert "confidence_label" in event
    assert event["confidence_label"] in {"high_confidence", "confirmed", "low_confidence", "predicted_only", "insufficient_data"}
```

**Implementation:** In `formatted_events` construction, add:
```python
"confidence_label": event.get("confidence_label",
    relationship_confidence_label(event.get("confidence", 0.0))),
```

---

### Task 6: Test that broad non-political articles produce zero relationships

**Problem:** No regression test verifies that purely non-political articles (sports, lifestyle) get completely rejected.

**Test:**
```python
def test_non_political_article_produces_no_relationships():
    result = appmod.analyze_article(NON_POLITICAL_ARTICLE, ["ANTM.JK", "BBCA.JK"], window="7d")
    assert result.get("stock_relationships") == []
    assert result.get("relevance_label") == "not_political"
```

---

## Execution Order

| Order | Task | Impact | Effort |
|-------|------|--------|--------|
| 1 | Task 1: Reject "maybe" without direct mention | High | Trivial |
| 2 | Task 2: Min channel confidence for indirect | High | Easy |
| 3 | Task 3: Penalize "maybe" in confidence | Medium | Easy |
| 4 | Task 6: Non-political regression test | Medium | Trivial |
| 5 | Task 4: Transmission path rationale | Medium | Easy |
| 6 | Task 5: Confidence label on events | Low | Trivial |

## Verification

After all tasks:
```bash
cd /opt/hermes/politics_stock_mapper && python -m pytest tests/test_app.py -v
```
Expected: 56 existing + ~6 new = ~62 tests, all passing.
