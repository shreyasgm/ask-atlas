import { describe, expect, it } from 'vitest';
import { getStepDetail } from './step-detail';

describe('getStepDetail', () => {
  it('returns null for undefined detail', () => {
    expect(getStepDetail('classify_query', undefined)).toBeNull();
  });

  it('returns null for unrecognized / skip nodes', () => {
    expect(getStepDetail('format_results', { query_index: 0 })).toBeNull();
    expect(getStepDetail('format_graphql_results', { atlas_links: [] })).toBeNull();
    expect(getStepDetail('format_docs_results', {})).toBeNull();
    expect(getStepDetail('unknown_node', { foo: 'bar' })).toBeNull();
  });

  // --- GraphQL pipeline ---
  it('extract_graphql_question — shows question', () => {
    expect(
      getStepDetail('extract_graphql_question', { question: "What are Brazil's top exports?" }),
    ).toBe("What are Brazil's top exports?");
  });

  it('extract_graphql_question — returns null for empty question', () => {
    expect(getStepDetail('extract_graphql_question', { question: '' })).toBeNull();
  });

  it('classify_query — shows query type', () => {
    expect(getStepDetail('classify_query', { query_type: 'country_profile_exports' })).toBe(
      '\u2192 country_profile_exports',
    );
  });

  it('extract_entities — formats flat entity object', () => {
    const result = getStepDetail('extract_entities', {
      entities: { country: 'Brazil', product: 'Coffee', year: 2022 },
    });
    expect(result).toBe('\u2192 Brazil, Coffee, 2022');
  });

  it('extract_entities — skips null/empty values', () => {
    const result = getStepDetail('extract_entities', {
      entities: { country: 'Brazil', product: null, year: '' },
    });
    expect(result).toBe('\u2192 Brazil');
  });

  it('extract_entities — returns null for empty entities', () => {
    expect(getStepDetail('extract_entities', { entities: {} })).toBeNull();
  });

  it('resolve_ids — formats resolved ID pairs', () => {
    const result = getStepDetail('resolve_ids', {
      resolved_ids: { Brazil: 'bra', Coffee: '0901' },
    });
    expect(result).toBe('\u2192 Brazil \u2192 bra, Coffee \u2192 0901');
  });

  it('resolve_ids — returns null for empty object', () => {
    expect(getStepDetail('resolve_ids', { resolved_ids: {} })).toBeNull();
  });

  it('build_and_execute_graphql — shows api_target and time', () => {
    const result = getStepDetail('build_and_execute_graphql', {
      api_target: 'explore',
      execution_time_ms: 1234,
    });
    expect(result).toBe('\u2192 explore, 1.2s');
  });

  // --- SQL pipeline ---
  it('extract_tool_question — shows question', () => {
    expect(getStepDetail('extract_tool_question', { question: 'Top exports of Brazil' })).toBe(
      'Top exports of Brazil',
    );
  });

  it('extract_products — shows product and country names', () => {
    const result = getStepDetail('extract_products', {
      countries: [{ iso3_code: 'bra', name: 'Brazil' }],
      products: [{ codes: ['0901'], name: 'Coffee', schema: 'hs92' }],
    });
    expect(result).toBe('\u2192 Coffee, Brazil');
  });

  it('extract_products — returns null when both arrays empty', () => {
    expect(getStepDetail('extract_products', { countries: [], products: [] })).toBeNull();
  });

  it('lookup_codes — shows codes string', () => {
    expect(getStepDetail('lookup_codes', { codes: 'HS92: 0901' })).toBe('\u2192 HS92: 0901');
  });

  it('sql_query_agent — shows row count and time', () => {
    const result = getStepDetail('sql_query_agent', {
      attempt_count: 1,
      execution_time_ms: 312,
      row_count: 42,
    });
    expect(result).toBe('\u2192 42 rows, 0.3s');
  });

  it('sql_query_agent — shows query count when > 1', () => {
    const result = getStepDetail('sql_query_agent', {
      attempt_count: 3,
      execution_time_ms: 1500,
      row_count: 15,
    });
    expect(result).toBe('\u2192 3 queries, 15 rows, 1.5s');
  });

  it('sql_query_agent — singular row', () => {
    expect(
      getStepDetail('sql_query_agent', { attempt_count: 1, execution_time_ms: 100, row_count: 1 }),
    ).toBe('\u2192 1 row, 0.1s');
  });

  it('sql_query_agent — shows error', () => {
    const result = getStepDetail('sql_query_agent', {
      attempt_count: 2,
      error: 'column "export_val" does not exist',
    });
    expect(result).toBe('\u2192 2 queries, Error: column "export_val" does not exist');
  });

  it('sql_query_agent — returns null for empty detail', () => {
    expect(getStepDetail('sql_query_agent', {})).toBeNull();
  });

  // --- Docs pipeline ---
  it('extract_docs_question — shows question', () => {
    expect(getStepDetail('extract_docs_question', { question: 'How is ECI calculated?' })).toBe(
      'How is ECI calculated?',
    );
  });

  it('retrieve_docs — shows chunk count', () => {
    const result = getStepDetail('retrieve_docs', { chunk_count: 5 });
    expect(result).toBe('→ 5 chunks');
  });

  it('retrieve_docs — singular chunk', () => {
    expect(getStepDetail('retrieve_docs', { chunk_count: 1 })).toBe('→ 1 chunk');
  });

  it('retrieve_docs — returns null for zero chunks', () => {
    expect(getStepDetail('retrieve_docs', { chunk_count: 0 })).toBeNull();
  });

  it('retrieve_docs_context — shows chunk count', () => {
    expect(getStepDetail('retrieve_docs_context', { chunk_count: 3 })).toBe('→ 3 chunks');
  });

  // --- Truncation (safety ceiling — CSS line-clamp handles visual clamping) ---
  it('truncates strings longer than 2000 chars', () => {
    const longQuestion = 'A'.repeat(2500);
    const result = getStepDetail('extract_graphql_question', { question: longQuestion });
    expect(result).toBe('A'.repeat(2000) + '\u2026');
  });

  it('does not truncate strings under 2000 chars', () => {
    const question = 'A'.repeat(500);
    const result = getStepDetail('extract_graphql_question', { question });
    expect(result).toBe(question);
  });

  // --- Edge cases ---
  it('handles missing fields gracefully', () => {
    expect(getStepDetail('classify_query', {})).toBeNull();
    expect(getStepDetail('extract_entities', {})).toBeNull();
    expect(getStepDetail('resolve_ids', {})).toBeNull();
    expect(getStepDetail('build_and_execute_graphql', {})).toBeNull();
    expect(getStepDetail('sql_query_agent', {})).toBeNull();
  });

  it('handles null field values gracefully', () => {
    expect(getStepDetail('classify_query', { query_type: null })).toBeNull();
    expect(getStepDetail('extract_entities', { entities: null })).toBeNull();
    expect(getStepDetail('resolve_ids', { resolved_ids: null })).toBeNull();
  });
});
