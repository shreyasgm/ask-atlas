import type { PipelineType } from '@/types/chat';

const GRAPHQL_NODES = new Set([
  'build_and_execute_graphql',
  'classify_query',
  'extract_entities',
  'extract_graphql_question',
  'format_graphql_results',
  'resolve_ids',
]);

const DOCS_NODES = new Set([
  'extract_docs_question',
  'format_docs_results',
  'select_and_synthesize',
]);

export function classifyPipelineNode(node: string): PipelineType {
  if (GRAPHQL_NODES.has(node)) {
    return 'graphql';
  }
  if (DOCS_NODES.has(node)) {
    return 'docs';
  }
  return 'sql';
}
