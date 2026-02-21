# Atlas Country Pages Exploration

## URL Structure

- **Base URL**: `https://atlas.hks.harvard.edu/countries/{id}`
- **Country ID scheme**: ISO 3166-1 numeric codes (e.g., 840=USA, 404=Kenya, 724=Spain, 392=Japan, 792=Turkiye)
- **Total countries**: 145 studied
- **Country selector**: Dropdown on every page shows country name + ISO alpha-3 code (e.g., "Afghanistan (AFG)"). The dropdown is searchable.
- **Navigating to `/countries`** (no ID): Redirects to an arbitrary country page (e.g., `/countries/792`)

### Known Subpage URL Patterns

| # | Subpage Slug | Full URL Pattern | Section Group |
|---|---|---|---|
| 1 | (none) | `/countries/{id}` | Introduction |
| 2 | `export-basket` | `/countries/{id}/export-basket` | Economic Structure |
| 3 | `export-complexity` | `/countries/{id}/export-complexity` | Economic Structure |
| 4 | `growth-dynamics` | `/countries/{id}/growth-dynamics` | Market Dynamics |
| 5 | `market-share` | `/countries/{id}/market-share` | Market Dynamics |
| 6 | `new-products` | `/countries/{id}/new-products` | Market Dynamics |
| 7 | `product-space` | `/countries/{id}/product-space` | Strategy Space (explanatory) |
| 8 | `paths` | `/countries/{id}/paths` | Strategy Space (country-specific) |
| 9 | `strategic-approach` | `/countries/{id}/strategic-approach` | Strategy Space |
| 10 | `growth-opportunities` | `/countries/{id}/growth-opportunities` | Growth Opportunities |
| 11 | `product-table` | `/countries/{id}/product-table` | Growth Opportunities |
| 12 | `summary` | `/countries/{id}/summary` | Summary |

---

## Main Country Page (`/countries/{id}`)

### Section: Country Introduction / Hero

- **Type**: Stat cards + sparkline charts + globe + text summary
- **Visible data**:
  - **Country name** and income classification (e.g., "high-income", "lower-middle-income", "upper-middle-income")
  - **GDP Per Capita** (current year, e.g., 2024): Dollar amount, PPP amount, rank out of 145, sparkline chart (2012-present), min/max values on sparkline
  - **Population**: Mentioned in text (e.g., "340 million inhabitants")
  - **GDP per capita growth**: 5-year average (e.g., "averaged 1.8% over the past five years"), compared to regional averages
  - **ECI Ranking**: Rank out of 145, sparkline chart (2012-present), direction of change (e.g., "worsening 7 positions")
  - **Complexity trend description**: Whether complexity improved or worsened, and the driver
  - **Growth Projection to 2034**: Percentage, rank out of 145
  - **Complexity-income relationship**: Whether the country is "more complex than expected", "less complex than expected", or "as complex as expected" for its income level
  - **Globe visualization**: Highlights country location
- **Interactions**: Country selector dropdown (searchable, shows all 145 countries)
- **Navigation**: "Jump To Specific Section" with 4 icons linking to Economic Structure, Market Dynamics, Strategy Space, Growth Opportunities
- **Data sources**: "UN COMTRADE (HS 1992) and the IMF's WEO data" (stated in text)

---

## Subpage: Export Basket (`/countries/{id}/export-basket`)

### Top Bar Stats
- **Total Exports**: USD dollar amount (e.g., "USD $3.19T")
- **Exporter Rank**: Rank out of 145 (e.g., "2nd of 145")
- **Current Account**: USD dollar amount (e.g., "USD -$1.19T")

### Section: Export Basket in {year}
- **Type**: Treemap visualization
- **Visible data**:
  - Products shown as colored rectangles sized by export share
  - Each product labeled with name and percentage share (e.g., "Business 12.64%")
  - Sector color coding: Services (pink/red), Textiles (green), Agriculture (yellow), Stone (tan), Minerals (brown), Metals (dark red), Chemicals (magenta), Vehicles (purple), Machinery (blue), Electronics (cyan), Other (dark)
- **Tooltip on hover**: Product name, HS92 code (e.g., "ict HS92", "8703 HS92"), Gross Export value (e.g., "$403B"), Share percentage
- **Dropdowns**:
  - **Trade Flow**: Gross, Net
- **Text data**:
  - Total export value and year
  - Export growth rate (5-year annual average)
  - Non-oil export growth rate
  - Total imports
  - Trade balance (deficit or surplus)
  - **Top 3 export destination / import origin countries**: Country name + percentage share. Toggle dropdown switches between "export destination" and "import origin"

---

## Subpage: Export Complexity (`/countries/{id}/export-complexity`)

### Top Bar Stats
- **ECI Ranking**: Rank out of 145 (e.g., "20th of 145")
- **Rank Change**: Direction + number of positions over 10 years (e.g., "↓7 positions over 10 years")

### Section: Export Complexity in {year}
- **Type**: Treemap visualization (same layout as export basket but colored by complexity)
- **Visible data**:
  - Products shown with their **Product Complexity Index (PCI)** values overlaid (e.g., "Business -0.48", "Cars 0.893", "Electronic integrated circuits 1.47")
  - Color scale: Low Complexity (teal/blue) → High Complexity (coral/brown)
- **Tooltip on hover**: Product name, HS92 code, Gross Export value, PCI value
- **Dropdowns**:
  - **Trade Flow**: Gross, Net
  - **Colored by**: Complexity, Entry Year
- **Text**: Description of complexity concept, identifies which sectors contain the largest exports by complexity level

---

## Subpage: Export Growth Dynamics (`/countries/{id}/growth-dynamics`)

### Section: Export Growth Dynamics
- **Type**: Bubble/scatter chart
- **Axes**:
  - X-axis: Product Complexity (Less Complex ← → More Complex)
  - Y-axis: Annual Export Growth (CAGR) over selected period (e.g., 2019-2024)
- **Bubble properties**: Sized by trade volume, colored by sector
- **Reference line**: Dashed vertical line at country's ECI value (e.g., "ECI (2024): 0.90")
- **Visible data**:
  - Each bubble represents a product group
  - Named labels on largest bubbles (e.g., "Mineral fuels, oils and waxes", "Pharmaceutical products")
- **Tooltip on hover**: Product name, HS92 code (e.g., "27 HS92"), Gross Country Export value, Export Growth percentage
- **Dropdowns**:
  - **Year Range**: 3 Years, 5 Years, 10 Years
  - **Sizing Products by**: Country Trade, World Trade, None
- **Legend**: Same sector color coding as other pages
- **Text**: Description of export growth pattern, which complexity level and sectors drive growth

---

## Subpage: Growth in Global Market Share (`/countries/{id}/market-share`)

### Top Bar Stats
- **Largest Market Share**: Sector name (e.g., "Minerals, fuels, ores and salts")
- **Share of Global Trade**: Percentage (e.g., "10.87%")

### Section: Growth in Global Market Share
- **Type**: Multi-line time series chart
- **Axes**:
  - X-axis: Years (1996–2024)
  - Y-axis: Share of World Market by Sector (0%–20%)
- **Lines**: One per sector, color-coded
- **Toggleable sector filters**: Each sector has an "X" button to remove it from the chart (Textiles, Agriculture, Stone, Minerals, Metals, Chemicals, Vehicles, Machinery, Electronics, Services)
- **Tooltip on hover** (crosshair): Shows year and all sector market share percentages at that point in time
  - Example: "Year: 2024 — Minerals: 11.68%, Chemicals: 11.09%, Machinery: 10.7%, Agriculture: 8.65%, Stone: 7.94%, Vehicles: 7.93%, Services: 7.68%, Electronics: 6.81%, Metals: 6.14%, Textiles: 3.58%"
- **Text**: Description of structural transformation status, which sectors drive export growth, whether growth is from market share gains or global sector growth

---

## Subpage: Diversification into New Products (`/countries/{id}/new-products`)

### Top Bar Stats
- **Economic Diversification Grade**: Letter grade (e.g., "C")
- **Diversity Rank**: Rank out of 145 (e.g., "14th of 145")
- **Rank Change**: Direction + number of positions over 15 years (e.g., "↓8 over 15 years")

### Section: Diversification into New Products
- **Type**: Treemap + comparison table
- **Treemap**: "New Products Exported, {start_year} - {end_year}" showing new products the country has added, sized by their share of new exports
  - Example for USA: Petroleum oils, crude (63.66%), Petroleum gases (33.83%)
- **Dropdowns**:
  - **Colored by**: Sector (likely also other options)
- **Comparison mini-visual**: Shows 2024 Export Basket alongside "New Export Proportion (Added in 15 years)" as percentage (e.g., "6%")
- **Table: "New Export Products, {start_year} - {end_year}"**:
  - Columns: Country, New Products (count), USD Per Capita, USD (Total Value)
  - Compares the selected country with 3 peer countries
  - Example: China 17/$32/$44.7B, Germany 9/$57/$4.78B, Canada 8/$59/$2.41B, USA 6/$536/$182B
- **Text**: Number of new products added, per-capita income contribution, assessment of diversification impact

---

## Subpage: What is the Product Space? (`/countries/{id}/product-space`)

### Section: What is the Product Space?
- **Type**: Network graph visualization (explanatory/generic, not country-specific)
- **Visible data**: Product Space network with all products as colored nodes grouped by sector (Chemicals, Machinery, Minerals, Stone, Agriculture, Electronics, Vehicles, Metals, Textiles)
- **Text**: Explanation of the Product Space concept and how countries diversify into related products
- **No country-specific data** — this is a reference/educational page

---

## Subpage: Country's Product Space (`/countries/{id}/paths`)

### Top Bar Stats
- **Export Products**: Count with RCA>1 (e.g., "283 (RCA>1)")
- **Complexity Outlook Index**: Rank out of 145 (e.g., "50th of 145")

### Section: {Country}'s Product Space
- **Type**: Network graph visualization (country-specific)
- **Visible data**:
  - **Colored nodes**: Products the country exports (with RCA > 1)
  - **Gray nodes**: Products the country does not export
  - Node size reflects world trade volume
- **Dropdowns**:
  - **Sizing of Dots**: World Trade (likely other options)
- **Interactions**: +ZOOM, -ZOOM, RESET ZOOM controls
- **Tooltip on hover** (nodes): Likely shows product name, export status, RCA value

---

## Subpage: Recommended Strategic Approach (`/countries/{id}/strategic-approach`)

### Section: Recommended Strategic Approach
- **Type**: Scatter plot of all countries
- **Axes**:
  - X-axis: "Is the {country} complex enough for its income to grow?" (Low relative complexity ← → High relative complexity)
  - Y-axis: "Is the {country} well-connected to many new opportunities (COI)?" (Not well connected ← → Well connected)
- **Four quadrants** (labeled with strategic approach names):
  - Top-left: **Parsimonious Industrial Policy Approach**
  - Top-right: **Light Touch Approach**
  - Bottom-left: **Strategic Bets Approach**
  - Bottom-right: **Technological Frontier Approach**
- **Country highlight**: Selected country shown with label, positioned in its quadrant
- **Visible data**:
  - Which quadrant/approach is recommended
  - Country position relative to all others
- **Text**: Description of the recommended approach and what it means

---

## Subpage: Potential Growth Opportunities (`/countries/{id}/growth-opportunities`)

### Section: Potential Growth Opportunities
- **Type**: Scatter plot of products (NOT available for highest-complexity countries)
- **For complex countries (e.g., USA)**: Shows "Visualization not available for highest complexity countries" with a "Continue to Country Summary" button
- **For other countries (e.g., Kenya)**:
  - **Axes**: X-axis = Distance (to existing capabilities), Y-axis = Complexity / Opportunity Gain
  - **Reference line**: Average Complexity (SITC {year}) value
  - **Bubbles**: Colored by sector, sized by global trade
  - **Controls**:
    - **(1) Your Strategic Approach**: Shows the recommended approach (e.g., "Light Touch Approach")
    - **(2) Product Selection Criteria** (radio buttons): Low-hanging Fruit, Balanced Portfolio, Long Jumps
    - **Weight visualization** (pie chart): Shows relative weights of Opportunity Gain, Distance, Complexity (e.g., 20%/60%/20%)
  - **Text**: Explanation of Distance, Complexity, and Opportunity Gain concepts

---

## Subpage: New Product Opportunities (`/countries/{id}/product-table`)

### Section: New Product Opportunities
- **Type**: Data table (NOT available for highest-complexity countries)
- **For complex countries (e.g., USA)**: Shows "Visualization not available for highest complexity countries"
- **For other countries (e.g., Kenya)**:
  - **Title**: "Top 50 Products Based on Strategy Approach"
  - **Table columns**:
    - Product Name (with HS92 code)
    - "Nearby" Distance (diamond rating scale, ~5 diamonds)
    - Opportunity Gain (diamond rating scale)
    - Product Complexity (diamond rating scale)
    - Global Size (USD)
    - Global Growth 5 YR (percentage with ↑/↓ indicator)
  - **Strategy label**: Shows which approach is applied (e.g., "Light Touch Approach / Balanced Portfolio")
  - **Interactive**: "Click on product names to explore in the Atlas"
  - **Text**: Lists high-potential sectors for diversification

---

## Subpage: Country Summary (`/countries/{id}/summary`)

### Section: {Country} in Summary
- **Type**: Summary stat cards
- **Visible data**:
  - **Economic Structure**: Complexity rank change (e.g., "↓7"), Number of new products added (e.g., "6 New Products were added in the last 15 years")
  - **Future Dynamics**: Growth projection (e.g., "1.9% — The USA is expected to grow 1.9% per year over the next 10 years")
  - **Path to Diversification**: Recommended strategic approach name + description (e.g., "Technological Frontier Approach — Having exploited virtually all, major existing products, growth can be pursued by promoting innovation...")
- **Bottom CTAs**:
  - Search a New Country
  - Analyze & Explore This Country Further
  - Explore This Country's Cities with Metroverse

---

## Cross-Country Consistency

- **Structure identical**: Yes. All 4 countries tested (USA, Spain, Kenya, Turkiye) have the same page layout, same 12 subpage URLs, same navigation icons, same top bar stats per section.
- **Data availability varies**:
  - **Growth Opportunities scatter** (`/growth-opportunities`): NOT available for highest-complexity countries (e.g., USA). Available for others (e.g., Kenya).
  - **New Product Opportunities table** (`/product-table`): NOT available for highest-complexity countries. Available for others.
  - All other pages are populated for all countries.
- **Text is dynamically generated** per country — wording changes based on data (e.g., "worsening" vs "improving", "high-income" vs "lower-middle-income", specific sector names).
- **Strategic approach varies** by country position on complexity/COI scatter: USA → Technological Frontier, Kenya → Light Touch Approach

---

## Interactive Elements Summary

| Element | Location | Options/Range |
|---|---|---|
| Country selector | All pages (top-left) | 145 countries, searchable dropdown |
| Trade Flow | export-basket, export-complexity | Gross, Net |
| Colored by | export-complexity | Complexity, Entry Year |
| Year Range | growth-dynamics | 3 Years, 5 Years, 10 Years |
| Sizing Products by | growth-dynamics | Country Trade, World Trade, None |
| Sizing of Dots | paths | World Trade (possibly others) |
| Sector toggles | market-share | 10 sectors, each removable via X |
| Export dest / Import origin toggle | export-basket | export destination, import origin |
| Product Selection Criteria | growth-opportunities | Low-hanging Fruit, Balanced Portfolio, Long Jumps |
| Colored by | new-products | Sector (possibly others) |
| Zoom controls | paths | +ZOOM, -ZOOM, RESET ZOOM |

---

## Extractable Data Points Catalog

### From Main Page (`/countries/{id}`)
1. **GDP per capita** (nominal, USD) — stat card — e.g., "$86,144"
2. **GDP per capita** (PPP, USD) — text — e.g., "$86,144 PPP"
3. **GDP per capita rank** — stat card — e.g., "5th of 145"
4. **Income classification** — text — e.g., "high-income"
5. **Population** — text — e.g., "340 million"
6. **GDP per capita growth** (5-year avg) — text — e.g., "1.8%"
7. **GDP per capita growth vs regional avg** — text — "above" or "below"
8. **ECI ranking** — stat card — e.g., "20th of 145"
9. **ECI rank change** (decade) — text — e.g., "worsening 7 positions"
10. **Complexity trend driver** — text — e.g., "lack of diversification of exports"
11. **Growth projection to 2034** — stat card — e.g., "1.94%"
12. **Growth projection rank** — stat card — e.g., "90th of 145"
13. **Complexity-income relationship** — text — e.g., "as complex as expected"
14. **Projected growth speed** — text — e.g., "slowly", "moderately"

### From Export Basket (`/countries/{id}/export-basket`)
15. **Total exports** (USD) — top bar — e.g., "USD $3.19T"
16. **Exporter rank** — top bar — e.g., "2nd of 145"
17. **Current account** (USD) — top bar — e.g., "USD -$1.19T"
18. **Export growth rate** (5-year annual avg) — text — e.g., "0.4%"
19. **Non-oil export growth rate** — text — e.g., "declined by 0.1%"
20. **Total imports** (USD) — text — e.g., "USD $3.91 trillion"
21. **Trade balance** — text — "trade deficit" or "trade surplus"
22. **Top 3 export destination countries** — stat cards — country name + share %
23. **Top 3 import origin countries** — stat cards (toggle) — country name + share %
24. **Product-level export share** — treemap — product name + percentage
25. **Product-level export value** — tooltip — product name + USD value
26. **Product HS92 code** — tooltip — e.g., "8703 HS92"

### From Export Complexity (`/countries/{id}/export-complexity`)
27. **ECI ranking** (repeated) — top bar — rank out of 145
28. **ECI rank change** (10 years) — top bar — e.g., "↓7 positions over 10 years"
29. **Product Complexity Index (PCI)** per product — treemap overlay — e.g., "Cars 0.893"
30. **Largest goods export sectors by complexity level** — text — e.g., "Machinery and Chemicals"

### From Export Growth Dynamics (`/countries/{id}/growth-dynamics`)
31. **Product export growth** (CAGR) — tooltip — e.g., "5.68%"
32. **Country's ECI value** — reference line — e.g., "ECI (2024): 0.90"
33. **Growth pattern description** — text — e.g., "static", "promising"
34. **Sectors/products driving growth** — text — specific names
35. **Product gross country export** — tooltip — e.g., "$316B"

### From Growth in Global Market Share (`/countries/{id}/market-share`)
36. **Largest market share sector** — top bar — e.g., "Minerals, fuels, ores and salts"
37. **Share of global trade** (total) — top bar — e.g., "10.87%"
38. **Sector-level global market share** (per year) — tooltip — e.g., "Minerals: 11.68%" for 2024
39. **Market share trends** (1996–2024) — line chart — per-sector time series
40. **Structural transformation status** — text — e.g., "completed the process"
41. **Sectors driving export growth** — text — specific names
42. **Growth mechanism** — text — "expanding global market share" vs "concentrating in growing sector"

### From Diversification into New Products (`/countries/{id}/new-products`)
43. **Economic Diversification Grade** — top bar — letter grade (e.g., "C")
44. **Diversity Rank** — top bar — rank out of 145 (e.g., "14th of 145")
45. **Diversity rank change** (15 years) — top bar — e.g., "↓8 over 15 years"
46. **New products count** — text/treemap — e.g., "6 new products since 2009"
47. **New products income contribution** (per capita) — text — e.g., "$536"
48. **New products total value** — table — e.g., "$182B"
49. **New export proportion** — mini-visual — percentage of export basket (e.g., "6%")
50. **Peer country comparison** — table — new product counts and values for 3-4 countries

### From Country's Product Space (`/countries/{id}/paths`)
51. **Export products count** (RCA>1) — top bar — e.g., "283"
52. **Complexity Outlook Index rank** — top bar — e.g., "50th of 145"

### From Strategic Approach (`/countries/{id}/strategic-approach`)
53. **Recommended strategic approach** — quadrant label — one of: Parsimonious Industrial Policy, Light Touch, Strategic Bets, Technological Frontier
54. **Approach description** — text — what the approach means

### From Growth Opportunities (`/countries/{id}/growth-opportunities`) — non-frontier countries only
55. **Top product opportunities** — scatter plot — product name, distance, complexity, opportunity gain
56. **Strategy type** — radio buttons — Low-hanging Fruit, Balanced Portfolio, Long Jumps

### From Product Table (`/countries/{id}/product-table`) — non-frontier countries only
57. **Ranked product opportunities** — table — product name, HS92 code, distance, opportunity gain, complexity, global size (USD), global growth (5yr %)
58. **High-potential sectors** — text — sector names

### From Summary (`/countries/{id}/summary`)
59. **Complexity rank change** — stat card — direction + number
60. **New products count** (repeated) — stat card
61. **Growth projection** (repeated) — stat card
62. **Strategic approach** (repeated) — stat card + description
