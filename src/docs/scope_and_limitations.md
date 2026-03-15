---
title: Scope and Limitations of the Atlas
purpose: >
  Defines what the Atlas of Economic Complexity can and cannot answer, helping
  the agent respond honestly to questions that fall outside the system's data
  or methodology. Critical for avoiding unsupported embellishment.
keywords:
  - scope
  - limitations
  - cannot answer
  - out of scope
  - not available
  - external data
  - research question
  - immigration
  - FDI
  - employment
  - institutions
  - gravity model
  - diaspora
  - currency
  - re-exports
  - unit value
  - quality ladder
  - GDP decomposition
  - R&D
  - what the Atlas measures
  - what the Atlas does not measure
  - honest response
  - embellishment
when_to_load: >
  Load when a user asks about topics that may fall outside the Atlas's data
  coverage — such as FDI, employment, immigration, institutional quality, R&D,
  GDP sectoral decomposition, gravity models, currency effects, diaspora
  effects, unit values, or quality analysis. Also load when the question
  references a research paper finding that may require data or analysis beyond
  trade flows and complexity metrics.
when_not_to_load: >
  Do not load for straightforward questions about trade data, complexity
  metrics, product space, or growth projections that are clearly within the
  Atlas's scope.
related_docs:
  - metrics_glossary.md
  - interpreting_trade_patterns.md
---

## What the Atlas Measures: Trade Flows, RCA, ECI, PCI, Product Space, and Strategic Metrics

The Atlas of Economic Complexity is built on **international goods and services trade data**. From this foundation it computes:

- **Trade flows**: Bilateral exports and imports by product, country, and year (goods from UN Comtrade; services from IMF/WTO)
- **Revealed Comparative Advantage (RCA)**: Which products a country specializes in relative to the world average
- **Economic Complexity Index (ECI)**: A country's overall productive sophistication, derived from the diversity and ubiquity of its export basket
- **Product Complexity Index (PCI)**: How sophisticated a product is, based on which countries export it
- **Product space and relatedness**: Which products share underlying capabilities (proximity, distance, density)
- **Strategic metrics**: COI, COG, diversification grades, strategic approaches, growth projections
- **Composition analysis**: Export/import shares by product, sector, partner, and over time

These metrics capture the **productive knowledge embedded in an economy** as revealed by its trade patterns.

### Trade Data Quality

The Atlas relies on UN Comtrade bilateral trade data, which the Growth Lab processes through a reliability-weighted mirroring methodology (Bustos et al., Growth Lab Working Paper 251, 2025). Key facts about the underlying data quality:

- **Approximately two-thirds** of jointly reported bilateral trade flows show discrepancies exceeding 25% between exporter-reported and importer-reported values.
- Sources of discrepancy include CIF vs. FOB valuation, re-exports and transit trade, misattribution of partner country, classification concordance differences, and deliberate under/over-invoicing.
- The Growth Lab assigns **country reliability scores** based on historical consistency patterns and uses these to optimally combine importer- and exporter-reported values for each flow.
- Product classification changes over time (SITC revisions, HS versions) are handled via the Lukaszuk-Torun concordance method, which preserves many-to-many relationships rather than forcing artificial 1-to-1 mappings.

These corrections improve data quality but do not eliminate all issues. Complexity metrics computed from trade data inherit whatever noise remains.

## What the Atlas Does NOT Measure: FDI, Employment, Immigration, R&D, Institutions, GDP Decomposition

The following topics require data sources or analytical methods the Atlas does not have:

### Data Not in the System

| Topic | Why It's Out of Scope |
|-------|----------------------|
| **Foreign Direct Investment (FDI)** | FDI data comes from balance-of-payments statistics (UNCTAD, central banks), not trade flows. The Atlas cannot tell you FDI inflows/outflows or their sectoral composition. |
| **Employment and labor markets** | Job counts, wages, unemployment, and labor force participation come from labor surveys and national statistics offices. Trade data does not reveal employment. |
| **Immigration and diaspora** | Migration data comes from census records, visa statistics, and UN population estimates. The Atlas has no migration or diaspora information. |
| **Institutional quality** | Governance indicators (rule of law, corruption, regulatory quality) come from World Bank WGI, Freedom House, or similar. The Atlas measures productive capabilities as revealed by trade, not institutional inputs. |
| **R&D and innovation inputs** | R&D expenditure, patent counts, and innovation surveys come from OECD, WIPO, and national agencies. The Atlas captures the output side (what countries export) not the input side (what they invest in R&D). |
| **GDP sectoral decomposition** | GDP broken down by agriculture/industry/services share comes from national accounts. The Atlas has total GDP and GDP per capita but not sectoral GDP. |
| **Education and human capital stocks** | Years of schooling, enrollment rates, and skills data come from UNESCO and household surveys. |
| **Infrastructure** | Transport networks, electricity access, internet penetration come from World Bank and national statistics. |
| **Tariffs and trade policy** | Applied and bound tariff rates come from WTO/UNCTAD TRAINS. The Atlas shows trade outcomes, not the policy instruments that shape them. |

### Analyses That Require Methods Beyond Trade Data

| Analysis Type | What the Atlas CAN Show | What It CANNOT Do |
|--------------|------------------------|-------------------|
| **Diaspora effects on complexity** | Country complexity trends over time; whether a country diversified into new products | Isolate the causal effect of immigrant diversity on export diversification — this requires panel econometric analysis linking migration data to trade outcomes. Note: Growth Lab research (Bahar, Rapoport & Turati, 2020) has established that 1 SD higher birthplace diversity is associated with 0.1-0.18 SD higher ECI, operating through the extensive margin (new products). This finding uses external migration data not available in the Atlas. |
| **Neighbor spillover effects** | Each country's individual complexity and trade patterns | Measure whether geographic neighbors' complexity spills over — this requires spatial econometric models with controls for confounders |
| **Currency devaluation impacts** | Export value trends before/after a devaluation event | Distinguish currency effects from other macroeconomic changes — this requires controlling for simultaneous policy changes, global demand shocks, and terms-of-trade movements |
| **Re-export decomposition** | Total trade values (which may include re-exports for hub economies like Singapore, UAE, Hong Kong — e.g., re-exports account for 40% of UAE's goods exports) | Separate domestic production from re-exported goods — this requires customs-level data on origin/destination that the Atlas does not store. The Atlas uses gross exports, not value-added exports. |
| **Unit value / quality analysis** | Total export value and quantity (where available), allowing rough unit value computation | Rigorous quality ladder analysis — this requires detailed product-level unit values with quality adjustments. Growth Lab research (Schetter, 2020) shows international specialization follows an "upper-triangular" pattern where rich countries produce all quality levels while poor countries produce only low-quality varieties, but the Atlas treats products as homogeneous and does not distinguish quality tiers within a product code. |
| **Gravity model predictions** | Bilateral trade flows between country pairs | Estimate gravity model coefficients or predict trade volumes from distance, GDP, language, colonial ties — this requires running the econometric model with additional covariates not in the Atlas |
| **Causal identification** | Correlations between complexity and growth outcomes | Identify causal mechanisms (e.g., "did complexity cause growth or did growth enable complexity?") — this requires instrumental variables, natural experiments, or structural models |

## How to Handle Research Paper Questions: Causal Claims, External Data, and Partial Answers

Many questions are inspired by academic research papers that use the Atlas's complexity metrics alongside external datasets and econometric methods. The right approach:

### Pattern: "Did X cause Y in countries Z?"

**Example:** "Between 1990 and 2000, did immigrant diversity help countries export more unique products?"

**Good response strategy:**
1. Provide what the Atlas CAN show: complexity trends, diversification patterns, new product counts for the countries in question during the relevant period
2. Explicitly acknowledge what the Atlas CANNOT do: "The Atlas can show that Country X diversified into N new products during this period and its ECI rose from A to B. However, isolating whether immigrant diversity drove this diversification requires linking migration data to trade outcomes with econometric controls — analysis that goes beyond the Atlas's trade data."
3. Do NOT fabricate a causal narrative connecting Atlas metrics to the research hypothesis

### Pattern: "What does the research say about X?"

**Example:** "What is the relationship between economic complexity and income inequality?"

**Good response strategy:**
1. Explain relevant Atlas concepts (ECI, the complexity-income relationship, growth projections)
2. If the Atlas has relevant data, provide it (e.g., ECI and GDP per capita trends)
3. Be clear that the Atlas does not contain inequality data (Gini coefficients, income distribution) and that answering the full question requires external datasets

### Pattern: "Why did country X's economy do Y?"

**Example:** "Why did Jordan's ECI stagnate despite export growth?"

**Good response strategy:**
1. Use Atlas data to document the pattern (ECI trend, export composition, product space position)
2. Provide interpretive guidance from what the metrics reveal (e.g., export growth driven by low-complexity products doesn't increase ECI)
3. Note factors that could contribute but are outside Atlas scope (e.g., regional instability, specific policy changes, FDI patterns)

## General Principles: Show Don't Speculate, Name the Gap, Partial Answers Are Valuable

- **Show, don't speculate.** Present the data the Atlas has. Let the user draw conclusions about factors outside the system.
- **Name the gap.** When a question requires data or methods the Atlas doesn't have, say so explicitly. "The Atlas does not contain immigration data" is more helpful than a vague hedging phrase.
- **Partial answers are valuable.** A question may be 60% answerable with Atlas data. Provide that 60% clearly and flag the 40% that requires external sources.
- **Don't invent mechanisms.** If the Atlas shows a correlation (e.g., ECI and growth move together), describe the pattern. Don't fabricate a causal story about WHY they moved together unless the mechanism is well-established in the complexity economics literature (e.g., the complexity-income convergence relationship documented in the growth projections methodology).

## Methodological Limitations: ECI Gaps (Services, Quality, Re-Exports, RCA Sensitivity) and Research Frontier

Beyond data gaps, there are inherent limitations in the complexity methodology itself:

### What ECI Does Not Capture

- **Services complexity:** ECI is computed from goods trade data. Services exports (which the Atlas does visualize) are not integrated into ECI, PCI, or the product space. This systematically underestimates the sophistication of service-oriented economies.
- **Within-product quality differences:** The framework treats each product code as homogeneous. A country exporting high-quality machinery and one exporting low-quality machinery both receive the same PCI credit. Quality differentiation research (Schetter, 2020) shows this matters — rich countries make higher-quality versions of the same products.
- **Domestic production vs. exports:** The Atlas measures what countries *export*, not what they *produce*. Countries may produce complex goods for domestic consumption without exporting them, or may export goods they do not produce (re-exports). As the Atlas itself notes: "Countries may be able to make things that they do not export, though the fact that they do not export them suggests that they may not be very good at them."
- **RCA threshold sensitivity:** The binary RCA ≥ 1 threshold (used in the pre-2026 methodology) means small changes around the threshold can flip a product's presence status, affecting all downstream metrics. The 2026 continuous M formula (M = RCA / (1 + RCA)) mitigates this by treating presence as a continuous variable.

### Where the Research Frontier Goes Beyond the Atlas

Recent Growth Lab and broader academic research has extended the complexity framework in ways the Atlas tool has not yet incorporated:

- **Genotypic product space** (Schetter et al., 2024): Constructs product proximity from observed capability requirements (occupational inputs) rather than co-export correlations, enabling identification of specific missing capabilities.
- **Greenplexity Index** (Growth Lab, 2024-2025): Dedicated complexity analysis of 10 green energy value chains, ranking 145 countries by competitive presence in the energy transition.
- **Implied comparative advantage** (Hausmann, Stock & Yildirim, *Research Policy* 2022): Predicts future product entry from deviations between observed and "implied" CA — highly predictive over decade-long horizons.
- **Innovation complexity** (Hausmann et al., 2024): Extends the complexity toolkit to scientific publications and patents, finding that trade-based complexity is the strongest GDP correlate but publications and patents capture distinct aspects of capability.
- **Distance-to-frontier ranking** (Schetter, 2022): Provides a micro-founded structural ranking of countries by distance to the technological frontier, offering a formal theoretical justification for the ECI eigenvector method.

These extensions are documented here so the agent can point users toward relevant research when questions go beyond what the current Atlas implementation can answer.
