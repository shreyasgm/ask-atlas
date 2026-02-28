# Deferred GraphQL Pipeline Discrepancies

This document catalogs discrepancies between the design document (`docs/backend_redesign_analysis.md`) and the implemented GraphQL pipeline code that were **intentionally deferred** during the post-implementation fix pass (Issues #81-84). Each item includes context, rationale for deferral, and guidance for future implementation.

---

## 1. `product_space` Doesn't Combine with `productProduct` Query (Issue #84, Low)

**What was identified:** The design implies `product_space` should combine `countryProductYear` with `productProduct` to get proximity/relatedness data. Current implementation just delegates to `_build_country_product_year`.

**Why deferred:** The `countryProductYear` query returns COG (complexity outlook gain), distance, and RCA — which are the primary data for the product space visualization. The `productProduct` query provides pairwise product proximity, which is used for drawing edges in the network graph. The treemap/scatter visualization doesn't need edge data; the full network visualization does, but that's the frontend's job (it fetches product proximity separately).

**What a fix would entail:**
1. Create a combined query that batches `countryProductYear` and `productProduct` into one GraphQL request
2. Or execute two sequential API calls and merge results
3. Update the response format to include both datasets

**Recommendation:** Defer until the frontend needs edge data from the pipeline. Currently the frontend fetches this itself.

---

## Resolved Items

The following items were resolved and removed from the active list:

| Item | Resolution | Commit |
|------|-----------|--------|
| Rejection Routing Is Check-and-Bail | `graph.py` now has `route_after_classify` conditional edge that skips directly to `format_graphql_results` on rejection. Bail checks remain as defensive guards. | Pre-existing in `graph.py` |
| RetryPolicy Not Configured | Added `RetryPolicy(max_attempts=3, backoff_factor=1.5)` to `classify_query`, `extract_entities`, and `resolve_ids` in `graph.py`. Removed try/except from `classify_query` and `extract_entities` so errors propagate and RetryPolicy can trigger. `build_and_execute_graphql` keeps internal error handling (GraphQL client already retries HTTP errors). | Current PR |
