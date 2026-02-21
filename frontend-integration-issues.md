# Frontend Integration Issues — Post-Backend Fix Checklist

## Backend Issue (to fix separately)

Classification lookup tables (`classification.location_country`, `classification.product_hs92`, etc.) are never included in the DDL sent to the LLM or the SQL validation whitelist. The LLM generates SQL with classification JOINs (matching example queries), but `validate_sql_node` rejects them. The agent then says "classification tables not available" and fails to answer.

**Root cause:** `get_tables_in_schemas()` and `validate_sql_node()` only iterate over trade schemas (e.g., `["hs92"]`) from `classification_schemas`. The `"classification"` schema is never in that list because it's a lookup schema, not a trade data schema.

**Evidence:** Feb 20 eval run (`evaluation/runs/20260220T145807Z/`) — Q1/Q5/Q10/Q15 all fail with "classification tables not available." The 80% pass rate is misleading; the LLM judge scored "I can't answer" as plausible.

**Fix approach:** Selectively include only the classification tables that the query actually needs, not all 10. The `classification` schema has tables for every product classification (hs92, hs12, sitc, services) plus location tables — dumping all of them into the LLM context dilutes SQL generation quality. The fix should be selective based on what the query requires.

---

## End-to-End Browser Tests (after backend is fixed)

Once the backend correctly includes classification tables, run these through the frontend in a browser to verify the full pipeline works:

### Basic queries (should return data tables)

- [ ] "What were the top 5 exports of Brazil in 2020?" — expect a table with product names and export values
- [ ] "What is the total value of exports for Brazil in 2018?" — expect a single numeric answer
- [ ] "List the top 5 export sectors for Canada in 2021 by export value." — expect product/sector names, not just IDs

### Classification table JOIN verification

- [ ] Results should show **human-readable names** (country names, product names), not raw numeric IDs — this confirms classification JOINs are working
- [ ] Check that the SQL shown in pipeline state uses `classification.location_country` and/or `classification.product_hs92` JOINs

### Multi-schema queries

- [ ] A services query (e.g., "What are the top service exports of India?") — should use `services_unilateral` schema + `classification.product_services_unilateral`
- [ ] Verify no irrelevant classification tables appear in the generated SQL (e.g., `product_hs12` shouldn't appear in an hs92 query)

### Streaming and UI behavior

- [ ] SSE streaming works — intermediate pipeline states appear progressively
- [ ] Query results table renders with real data (columns + rows)
- [ ] No 504 timeout errors for typical queries (120s API timeout)
- [ ] Thread continuity works — follow-up questions in the same thread maintain context

### Error cases

- [ ] A nonsensical query (e.g., "asdfghjkl") — should fail gracefully, not crash
- [ ] A query about data not in the DB (e.g., 2025 trade data) — should explain the limitation
