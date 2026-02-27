# Trade Data Methodology

**Purpose:** Explains how the Harvard Growth Lab constructs the Atlas of Economic Complexity trade dataset — reconciling exporter/importer reporting discrepancies and harmonizing product classifications across HS vintages.

**When to load this document:** Load when the user explicitly asks why Atlas trade values differ from raw UN Comtrade
or WTO data, how mirror statistics work, how CIF/FOB adjustments are applied, how
product codes are harmonized across HS revisions, or which external data sources
feed the Atlas pipeline. Do NOT load for questions about which classification to use
(see `classification_systems.md`) or for CIF/FOB value differences in a specific
query (see `inflation_and_valuation.md`).

---

## Data Sources

| Source | Role in Atlas |
|---|---|
| UN Comtrade | Primary — raw goods trade (imports + exports reported separately by each country) |
| IMF Direction of Trade Statistics (DOTS) | Services trade (Travel, Transport, ICT, Business, Financial, etc.) |
| IMF World Economic Outlook (WEO) | GDP, population, current account balances |
| Federal Reserve Economic Data (FRED) | Producer Price Index for Industrial Commodities — used for constant-dollar deflation |
| CEPII GeoDist | Bilateral distances and contiguity indicators — used in CIF/FOB regression |
| WCO correlation tables | Mapping of product codes across HS classification vintages |

Atlas trade values are denominated in USD. Constant-dollar values use the FRED Producer Price Index for Industrial Commodities with the base year set to the most recent Atlas data year. For historical trend analysis (growth rates, long-run comparisons), constant-dollar values are recommended.

---

## Why Atlas Values Differ from Raw Comtrade Downloads

Raw Comtrade data has two structural problems that the Atlas corrects:

1. **Reporting discrepancies** — the same trade flow is reported twice (once by the exporter, once by the importer) and the numbers often diverge significantly.
2. **Classification mismatches** — countries report using different HS vintage years, and Comtrade's 1:1 code mapping drops ~10% of six-digit products when translating across vintages.

The Atlas applies a five-step mirroring procedure (described below) and a weighted concordance method (the Lukaszuk-Torun method) to produce reconciled, harmonized estimates. The result is a single imputed trade value per country-pair-product-year that differs from both the raw exporter-reported and importer-reported figures.

**Scale of impact in 2024:** The methodology recovered $861 billion in trade that would otherwise be missing, and recovered approximately 8% of product codes lost under Comtrade's 1:1 mapping approach.

---

## The Reporting Problem: Scale and Causes

For 2010 global trade data (a representative year):

| Category | Share of country pairs |
|---|---|
| Both reported, discrepancy < 25% | 22% |
| Both reported, discrepancy > 25% | 23% |
| Importer reported only | 11% |
| Exporter reported only | 10% |
| Neither reported | 34% |

**Sources of legitimate discrepancy:**
- **CIF vs. FOB valuation** — Exporters report FOB (Free On Board): value of goods at the port of departure. Importers report CIF (Cost, Insurance, Freight): value including transport and insurance. CIF values are systematically higher than FOB values for the same flow.
- **Temporal mismatches** — Shipments crossing year-end boundaries may be recorded in different calendar years by exporter and importer.
- **Re-exports** — Goods imported and then re-exported without substantial transformation inflate the apparent export complexity of trade hubs (Singapore, Hong Kong, Netherlands).
- **Areas Not Specified (ANS)** — Some countries report a significant share of trade against an unidentified partner code. The Atlas subtracts ANS from a country's reported total when the ANS ratio exceeds 25% of total trade, to avoid double-counting.

---

## Mirroring Pipeline: Five Steps

The mirroring pipeline converts raw Comtrade bilateral data into a single reconciled estimate.

```
Converted Bilateral Comtrade Data
        ↓
Step 1: Preprocessing & Trade Aggregation
        ↓
Step 2: CIF-to-FOB Adjustment
        ↓
Step 3: Compute Country Reliability Scores
        ↓
Step 4: Country-Pair Total Trade Reconciliation
        ↓
Step 5: Product-Level Trade Reconciliation
        ↓
Mirrored Bilateral Trade Data
```

### Step 1: Preprocessing and Trade Aggregation

Raw Comtrade data is filtered by trade flow type and classification level, country codes are standardized, and world-level trade totals are integrated. The ANS filter is applied here (subtract ANS from totals when ANS share > 25%). At this stage the data is kept at the aggregate bilateral (country-pair) level — not product level — to avoid concordance issues when computing reliability scores.

### Step 2: CIF-to-FOB Adjustment

Only a handful of countries submit both FOB and CIF import values to Comtrade. For the rest, the Atlas estimates the CIF/FOB ratio using a regression:

```
ln(CIF/FOB)_{e,i} = α + τ₁ × ln(dist)_{e,i} + τ₂ × contiguity_{e,i} + λ_e + λ_i + ε_{e,i}
```

Where `dist` is bilateral distance, `contiguity` is a border-sharing indicator, and `λ_e`, `λ_i` are exporter and importer fixed effects. Estimated ratios are constrained to be non-negative and capped at 20% (the estimated maximum plausible CIF/FOB uplift for the most distant country pairs). The cap applies to fewer than 1% of observations. The adjusted importer-reported values are then directly comparable to FOB exporter-reported values.

**Important:** The typical CIF/FOB margin is approximately 5% for average trading partners. The cap of 20% applies only to extreme-distance outliers.

### Step 3: Compute Country Reliability Scores

The bilateral discrepancy between exporter $e$ and importer $i$ for a given flow is:

```
D_{j,k} = |V^E_{j,k} - V^I_{j,k}| / (V^E_{j,k} + V^I_{j,k})   ∈ [0, 1]
```

Where `D = 0` when both sides report identically and `D = 1` when only one side reports.

Reliability scores are estimated via a network-aware OLS regression that decomposes each discrepancy into contributions from the exporter and importer separately:

```
D_{j,k} = α_j + α_k + ε_{jk}
```

The matrix form `α̂ = (B'B)⁻¹B'D` exploits the full bilateral trade network. `B` is an incidence matrix (rows = trade flows, columns = countries) and `B'B` is closely related to the network's adjacency matrix. This ensures that a country's reliability score reflects its own reporting accuracy — not discrepancies driven by unreliable partners.

Country reporting accuracy is reported as `1 - α`. Countries with scores above the 10th percentile threshold are classified as reliable reporters. Countries below the threshold have their reports disregarded in the weighted average — the pipeline relies solely on the more reliable partner's report.

### Step 4: Country-Pair Total Trade Reconciliation

The reconciled trade value for each bilateral flow uses a reliability-weighted average:

```
V^F_{e,i} = (1 - w_{e,i}) × V^E_{e,i} + w_{e,i} × V^I_{e,i}
```

Where `w_{e,i}` is the importer's reliability score converted to a complementary probability via a softmax transformation. A higher exporter reliability relative to importer reliability shifts weight toward the exporter-reported value, and vice versa.

**Coverage constraint:** Mirroring applies only when at least one partner has reported. Country pairs where neither partner has reported are left as missing — the Atlas does not use gravity-model imputation to fill these gaps.

### Step 5: Product-Level Trade Reconciliation

Country-pair reliability weights are applied to disaggregated 6-digit product data. The product-level estimates are proportionally reweighted to match reconciled country-pair totals. Large unexplained discrepancies that exceed either 20% deviation or $25M absolute difference (for flows above $100M) are assigned to a separate commodity code `XXXX` to preserve accounting consistency. As a result, aggregate totals and product-level sums always match.

---

## The Product Concordance Problem

### Why Classifications Diverge

The World Customs Organization (WCO) updates the Harmonized System (HS) approximately every five years. Each new vintage splits, merges, or renames product codes. In any given year, trading partners may report using different vintages. In 2007, for example, countries reported using five different classification vintages simultaneously (SITC Rev 3, HS1992, HS1996, HS2002, and HS2007).

**Comtrade's default approach** enforces 1:1 code mappings when converting between vintages. This means:
- When one HS2007 code maps to multiple HS1992 codes, only one target code receives the full trade value; the others are dropped entirely.
- Cumulative product loss from chaining conversions (HS2022 → HS2017 → HS2012 → HS2007 → HS2002 → HS1996 → HS1992) results in only ~4,500 six-digit codes in Comtrade's HS1992 harmonized data — roughly 500 fewer than the ~5,000 products defined in HS1992.

**Concrete example — HS2007 code 854231 (Electronic Integrated Circuits):**

| Step | Source | Comtrade (1:1) | Atlas LT Method |
|---|---|---|---|
| HS2007 → HS1992 | 854231 | 100% → 854219 | Distributed across 4 codes |
| 854211 (Monolithic digital ICs) | — | 0% | **68%** |
| 854219 (Monolithic non-digital ICs) | — | 100% | 23% |
| 854220 (Hybrid ICs) | — | 0% | 9% |
| 854800 (Electrical parts) | — | 0% | 0% |

Comtrade entirely omits the digital IC code (854211) — the dominant category in practice. The Atlas's method assigns 68% of trade value there.

### The Lukaszuk-Torun (LT) Concordance Method

The LT method exploits the empirical regularity that product-level trade flows are highly persistent from year to year. Countries that switch classification vintages in a given year provide a natural bridge: by comparing their reported trade patterns in the year before the switch (old vintage) and the year of the switch (new vintage), the method infers how to allocate trade values across product codes.

**Three-step algorithm:**

**Step 1: Group products into networks.** The WCO correlation tables define which product codes are interconnected across vintages (1:1, 1:n, m:1, and m:n relationships). Products are grouped into closed clusters so that the full value of every code in a group is preserved through the concordance.

**Step 2: Build trade matrices.** Using countries that were timely adopters of each new vintage, construct matrices of product-level import shares scaled within each group:

```
v_{i,k} = V_{i,k} / Σ_{î,k̂} V_{î,k̂}
```

This scaling controls for group-specific trade trends and is computed for adjacent vintage transition years only.

**Step 3: Compute conversion weights via constrained least squares.** Minimize squared deviations between observed import shares in the new vintage and predicted shares from the old vintage:

```
min_{β_{k,s}}  Σ_s Σ_i (v^{t₁}_{i,s} - Σ_k v^{t₀}_{i,k} × β_{k,s})²

subject to:  β_{k,s} ≥ 0  for all k, s
             Σ_s β_{k,s} = 1  for all k
```

`β_{k,s}` is the conversion weight from source product `k` to target product `s`. Weights sum to 1 across all target codes for each source code, preserving total trade value. Non-adjacent vintage conversions (e.g., HS2017 to HS2007) multiply weights across intermediate conversion steps.

---

## Classification Systems Available in the Atlas

| System | Coverage in Atlas | Granularity | Notes |
|---|---|---|---|
| **HS92** (Harmonized System 1992) | 1995–2024 | 1-, 2-, 4-, 6-digit | Default for Atlas Country Pages and most SQL queries |
| **HS12** (Harmonized System 2012) | 2012–2024 | 1-, 2-, 4-, 6-digit | Captures newer product categories |
| **HS22** (Harmonized System 2022) | 2022–2024 | 1-, 2-, 4-, 6-digit | Available via GraphQL Explore API only; not in SQL DB or Country Pages API |
| **SITC Rev. 2** | 1962–2024 | 1-, 2-, 4-digit | Longest historical series; ~700 products at 4-digit level; use for pre-1995 analysis |
| **Services** | 1980–2024 | ~12–15 categories | IMF DOTS source; separate from goods classifications |

**HS vintage chaining used in Atlas publication:** HS2022 → HS2017 → HS2012 → HS2007 → HS2002 → HS1996 → HS1992

---

## Data Quality Indicators

### `public.data_flags` Table

Per-country flags indicating quality and inclusion status.

| Column | Description |
|---|---|
| `in_rankings` | Whether the country is included in ECI complexity rankings |
| `is_trusted` | Whether the country's reporting is considered reliable for analysis |
| `former_country` | Whether the entity is a historical/dissolved country |

### Country Inclusion Criteria (Country Profiles and Rankings)

Countries must meet all of the following to appear in Country Profiles and Rankings (as opposed to raw Explore data which includes all Comtrade territories):

- Population of at least 1 million
- Average annual trade volume of at least $1 billion
- Verified GDP and export data availability
- Consistent and reliable trade reporting history

### ANS (Areas Not Specified) Filter

When a country reports more than 25% of its total trade against unidentified partner codes, ANS is subtracted from its reported totals before mirroring to prevent double-counting.

---

## Data Update Cycle

| Update type | Frequency | Timing |
|---|---|---|
| Annual release | ~95% of data | April–June each year |
| Interim releases | As available | Throughout the year |

Countries typically require 12–18 months to report to UN Comtrade. Most 2024 trade data appears in the Atlas between April and June 2026. Annual releases may incorporate small retroactive corrections to historical data as late or revised country reports are received.

---

## Validation: IMF Balance of Payments Comparison

The Atlas compares its recovered trade values against IMF Balance of Payments (BoP) data as an independent validation check. The two series are highly correlated (log-scale R² near 1.0 for both exports and imports). This includes trade values recovered entirely by mirroring — i.e., for countries that did not themselves report to Comtrade — which confirms that partner-based imputation accurately reconstructs actual trade flows.

---

## Known Limitations

| Limitation | Detail |
|---|---|
| Current USD default | Trade values are in nominal USD. Constant-dollar series available using FRED PPI deflator. |
| No gravity imputation | Country pairs where neither partner reports remain missing; the Atlas does not fill gaps with model-based predictions. |
| Non-reporting dyads | Intra-regional gaps (e.g., within Africa) persist because many country pairs have no reporter on either side. |
| Services data granularity | Services trade (~12–15 categories) is far less granular than goods trade (~5,000 six-digit HS codes). |
| Bilateral services limits | Bilateral services data is more limited than bilateral goods data. Services are excluded from Explore page "Locations" mode totals (only bilateral goods data is available there). |
| Re-export hubs | Singapore, Hong Kong, Netherlands, and similar re-export hubs may have inflated apparent export complexity if re-exports are not fully netted. |
| HS22 scope | HS22 data (2022–2024) is available only through the GraphQL Explore API, not in the SQL database or Country Pages API. |
| Historical data quality | Earlier years (pre-1995 in HS, pre-1980 in SITC) have thinner country coverage and greater reporting gaps. |

---

## Key References

- **Peer-reviewed paper:** Bustos, S., Jackson, E., Torun, D., et al. (2026). "Tackling Discrepancies in Trade Data: The Harvard Growth Lab International Trade Datasets." *Scientific Data* 13, 170. https://doi.org/10.1038/s41597-025-06488-2
- **Mirrored trade dataset:** https://doi.org/10.7910/DVN/5NGVOB
- **Conversion weights dataset:** https://doi.org/10.7910/DVN/6AADMR
- **Mirroring pipeline code:** https://github.com/harvard-growth-lab/comtrade-mirroring
- **Conversion weights code:** https://github.com/harvard-growth-lab/comtrade-conversion-weights
- **Contact for methodology questions:** growthlabtools@hks.harvard.edu
