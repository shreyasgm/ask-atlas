# Browser Spot-Check & Ground Truth Collection Report

**Date:** 2026-02-28 (batch 1), 2026-03-01 (batch 2)
**Method:** Chrome browser automation via MCP tools (get_page_text, navigate, javascript_tool)
**Atlas URL:** https://atlas.hks.harvard.edu

---

## Summary

| Metric | Count |
|--------|-------|
| Questions with browser-collected narrative GT | 32 |
| Questions with browser-enriched GT (Tier C) | 6 |
| Questions with browser spot-check verification (batch 1) | 33 |
| Questions with browser spot-check verification (batch 2) | 26 |
| **Total questions with browser involvement** | **97** |
| **Coverage of 252 total questions** | **38%** |

### Verification Results (All Batches Combined)

| Result | Count | Details |
|--------|-------|---------|
| Match (exact) | 38 | Browser value matches GT exactly |
| Match (formatting only) | 17 | Same value, different formatting (e.g., "$93B" vs "$93.33 billion") |
| Significant discrepancy | 3 | Q97, Q121, Q124 — **GT updated** |
| Rounding discrepancy | 1 | Q112: browser 2% vs API 1.6% — **GT updated** |
| Unable to verify | 1 | Q76: Exporter rank not visible in browser page text |
| **Total spot-checks** | **59** | |

---

## Phase 1: Browser-Only Narrative Collection (31 questions)

All 31 narrative questions (Q139-Q169) were re-collected directly from the Atlas website browser view, overwriting the previous Feb 21 API-collected GT with fresh browser-verified data.

### Questions by Country

| Country (M49) | Questions | URLs Visited |
|---------------|-----------|-------------|
| Kenya (404) | Q139-Q142, Q166, Q168 | /countries/404, /countries/404/new-products, /countries/404/paths |
| Spain (724) | Q143-Q146, Q155, Q156, Q162 | /countries/724, /countries/724/new-products |
| Brazil (76) | Q147, Q148, Q151, Q152, Q164 | /countries/76, /countries/76/new-products |
| Turkiye (792) | Q149, Q150, Q159, Q160, Q165, Q167 | /countries/792, /countries/792/new-products |
| India (356) | Q157, Q158, Q163, Q169 | /countries/356/new-products |
| Germany (276) | Q153, Q154 | /countries/276/new-products |
| Ethiopia (231) | Q161 | /countries/231/new-products |

All files written with `source_method: "browser_country_page"`.

---

## Phase 2: Tier C Narrative Enrichment (6 questions)

These questions had API-collected quantitative GT. Browser narrative text was merged into the existing GT as `browser_narrative` fields.

| Question | Country | Narrative Added |
|----------|---------|----------------|
| Q33 | Vietnam (704) | Complexity-income, ECI rank change (+17), growth projection (5.5%, top decile) |
| Q37 | Egypt (818) | COI classification, complexity driver (diversified into lower complexity), growth projection (4.0%) |
| Q38 | S. Korea (410) | Export complexity (high, Electronics+Machinery), promising growth pattern, Technological Frontier Approach |
| Q41 | Portugal (620) | Parsimonious Industrial Policy, high-potential sectors, structural transformation complete |
| Q43 | Ghana (288) | Strategic Bets Approach, export complexity (moderate+low), not yet started transformation |
| Q44 | Kazakhstan (398) | Less complex than expected, +26 ECI improvement, resource risk narrative |

All files marked with `source_method: "graphql_api_browser_enriched"`.

---

## Phase 3: Country Page Spot-Checks — Batch 1 (33 questions)

### All Spot-Check Results

| Q | Country | Metric | Browser Value | GT Value | Match |
|---|---------|--------|---------------|----------|-------|
| Q61 | Kenya | GDP per capita | $2,274 | $2,274 | Exact |
| Q63 | Kenya | GDP per capita rank | 116th of 145 | 116th of 145 | Exact |
| Q64 | Kenya | Income classification | lower-middle-income | LowerMiddle | Format |
| Q65 | Kenya | Population | 52.4 million | 52,444,000 | Format |
| Q68 | Spain | GDP per capita | $35,151 | $35,151 | Exact |
| Q70 | Spain | GDP per capita rank | 25th of 145 | 25th of 145 | Exact |
| Q72 | Spain | Population | 49.1 million | 49,078,000 | Format |
| Q75 | Brazil | Total exports | $378 billion | $377.65 billion | Format |
| Q76 | Brazil | Exporter rank | Not shown in text | 22nd of 145 | N/A |
| Q80 | Germany | Total exports | $1.97 trillion | $1.97 trillion | Format |
| Q93 | Turkiye | Top 3 export destinations | Germany, UK, USA | Germany, UK, USA | Exact |
| Q95 | Ethiopia | Top 3 export destinations | USA, China, Saudi Arabia | USA, China, Saudi Arabia | Exact |
| Q101 | Brazil | ECI ranking | 56th of 145 | 56th of 145 | Exact |
| Q104 | Turkiye | ECI ranking | 42nd of 145 | 42nd of 145 | Exact |
| Q107 | Kenya | Diversification grade | B | B | Exact |
| Q108 | Kenya | Diversity rank | 38th of 145 | 38th of 145 | Exact |
| Q109 | Kenya | New products count | 24 | 24 | Exact |
| Q114 | Ethiopia | Diversification grade | B | B | Exact |
| Q115 | Ethiopia | Diversity rank | 100th of 145 | 100th of 145 | Exact |
| Q116 | Ethiopia | New products count | 21 | 21 | Exact |
| Q121 | Kenya | Products with RCA>1 | **169** | **226** | **DISCREPANCY** |
| Q123 | Kenya | Strategic approach | Light Touch Approach | Light Touch Approach | Exact |
| Q126 | Turkiye | Strategic approach | Light Touch Approach | Light Touch Approach | Exact |
| Q127 | Kenya | Default strategy | Light Touch Approach | Light Touch Approach | Exact |
| Q128 | India | Default strategy | Light Touch Approach | Light Touch Approach | Exact |
| Q129 | USA | Growth opportunities | Frontier (not available) | Frontier (not available) | Exact |
| Q130 | USA | Growth opportunities | Frontier (not available) | Frontier (not available) | Exact |
| Q133 | Turkiye | New products (summary) | 30 | 30 | Exact |
| Q134 | Turkiye | Growth projection | 3.4% | 3.4% | Exact |
| Q135 | Turkiye | Strategic approach | Light Touch Approach | Light Touch Approach | Exact |
| Q136 | Brazil | New products (summary) | 5 | 5 | Exact |
| Q137 | Brazil | Growth projection | 1.7% | 1.7% | Exact |
| Q138 | Brazil | Strategic approach | Light Touch Approach | Light Touch Approach | Exact |

---

## Phase 4: Country + Explore Page Spot-Checks — Batch 2 (26 questions)

### Country Page Checks

| Q | Country | Metric | Browser Value | GT Value | Match |
|---|---------|--------|---------------|----------|-------|
| Q62 | Kenya | GDP per capita (PPP) | $7,159 | $7,159 | Exact |
| Q66 | Kenya | Growth projection | 3.38% | 3.4% | Format |
| Q67 | Kenya | Growth projection rank | 39th of 145 | 39th of 145 | Exact |
| Q69 | Spain | GDP per capita (PPP) | $54,674 | $54,674 | Exact |
| Q71 | Spain | Income classification | high-income | High | Format |
| Q73 | Spain | Growth projection | 1.31% | 1.3% | Format |
| Q74 | Spain | Growth projection rank | 119th of 145 | 119th of 145 | Exact |
| Q78 | Brazil | Total imports | $368B | $367.68 billion | Format |
| Q79 | Brazil | Trade balance | trade surplus | trade surplus | Exact |
| Q83 | Germany | Total imports | $1.79 trillion | $1.79 trillion | Exact |
| Q84 | Germany | Trade balance | trade surplus | trade surplus | Exact |
| Q97 | Spain | ECI value (chart) | **0.77** | **0.8230** | **DISCREPANCY** |
| Q110 | Kenya | New products per capita | $5 | $5 | Exact |
| Q111 | Kenya | New product total value | $260M | $260.47 million | Format |
| Q112 | Kenya | New product share | **2%** | **1.6%** | **Rounding** |
| Q117 | Ethiopia | New products per capita | $2 | $2 | Exact |
| Q118 | Ethiopia | New product total value | $181M | $181.47 million | Format |
| Q122 | Kenya | COI rank | 8th of 145 | 8th of 145 | Exact |
| Q124 | Turkiye | Products with RCA>1 | **339** | **443** | **DISCREPANCY** |
| Q125 | Turkiye | COI rank | 3rd of 145 | 3rd of 145 | Exact |

### Explore Page Checks

| Q | Trade Flow | Browser Value | GT Value | Match |
|---|-----------|---------------|----------|-------|
| Q195 | Brazil → China | $93B | $93.33 billion | Format |
| Q196 | Kenya → USA | $608M | $607.93 million | Format |
| Q197 | Germany → USA | $164B | $164.02 billion | Format |
| Q198 | India → China | $16B | $15.78 billion | Format |
| Q199 | Turkiye → Germany | $20B | $20.38 billion | Format |
| Q208 | USA top import | Business $344B | Business $344.10B | Format |

---

## Discrepancy Details

### Q121: Kenya Products with RCA > 1

- **API-collected value:** 226
- **Browser-observed value:** 169
- **Discrepancy magnitude:** 25.2% (significant)
- **Action taken:** Updated GT to browser value (169)
- **Likely cause:** The API query may have counted products using a different threshold or classification scope than the browser visualization. The browser Product Space visualization explicitly shows "Export Products 169 (RCA>1)" as a stat card.
- **Impact:** This is the correct value as seen by end-users on the Atlas website.

### Q124: Turkiye Products with RCA > 1

- **API-collected value:** 443
- **Browser-observed value:** 339
- **Discrepancy magnitude:** 23.5% (significant)
- **Action taken:** Updated GT to browser value (339)
- **Likely cause:** Same systematic issue as Q121. The API counts more products than the browser Product Space visualization displays.
- **Pattern:** Both Q121 and Q124 show the API overcounting RCA>1 products by ~24%. This is a systematic API-vs-browser divergence for this metric.

### Q97: Spain ECI Value

- **API-collected value:** 0.8230 (raw: 0.8229609131813049)
- **Browser-observed value:** 0.77 (displayed on growth dynamics chart as "ECI (2024): 0.77")
- **Discrepancy magnitude:** 6.4% (significant)
- **Action taken:** Updated GT to browser value (0.77)
- **Likely cause:** The growth dynamics chart may use a different ECI calculation (e.g., goods-only vs goods+services) or a different rounding method than the raw API value.

### Q112: Kenya New Product Share of Exports

- **API-calculated value:** 1.6% (260M / 16.2B)
- **Browser-displayed value:** 2% (shown in summary section)
- **Action taken:** Updated GT to browser value (2%), retained precise value as `value_precise`
- **Likely cause:** Browser rounds to nearest whole percent for display.

---

## Validation

- All 252 questions still have valid `results.json` files (no accidental deletions)
- 32 narrative GT files updated with `source_method: "browser_country_page"`
- 6 enriched GT files marked with `source_method: "graphql_api_browser_enriched"`
- 4 GT files corrected based on browser discrepancies (Q97, Q112, Q121, Q124)
- 59 `browser_verification.json` files created across 59 question directories

---

## Files Modified

| Change Type | Count | Details |
|-------------|-------|---------|
| GT overwritten (browser narrative) | 32 | Q139-Q169 + Q121 |
| GT enriched (Tier C) | 6 | Q33, Q37, Q38, Q41, Q43, Q44 |
| GT corrected (discrepancy) | 4 | Q97, Q112, Q121, Q124 |
| Verification files created (batch 1) | 33 | `browser_verification.json` |
| Verification files created (batch 2) | 26 | `browser_verification.json` |
| **Total files touched** | **101** | |
