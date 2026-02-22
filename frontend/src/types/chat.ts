export interface QueryResult {
  columns: Array<string>;
  executionTimeMs: number;
  rowCount: number;
  rows: Array<Array<unknown>>;
  sql: string;
}

export interface ChatMessage {
  content: string;
  id: string;
  isStreaming: boolean;
  queryResults: Array<QueryResult>;
  role: 'assistant' | 'user';
}

export interface PipelineStep {
  completedAt?: number;
  detail?: Record<string, unknown>;
  label: string;
  node: string;
  startedAt: number;
  status: 'active' | 'completed';
}

export interface ResolvedProduct {
  codes: Array<string>;
  name: string;
  schema: string;
}

export interface EntitiesData {
  lookupCodes: string;
  products: Array<ResolvedProduct>;
  schemas: Array<string>;
}

export interface QueryAggregateStats {
  totalExecutionTimeMs: number;
  totalQueries: number;
  totalRows: number;
  totalTimeMs: number;
}

export type ClassificationSchema = 'hs12' | 'hs92' | 'sitc';
export type TradeDirection = 'exports' | 'imports';
export type TradeMode = 'goods' | 'services';

export interface TradeOverrides {
  direction: TradeDirection | null;
  mode: TradeMode | null;
  schema: ClassificationSchema | null;
}

export interface ChatApiResponse {
  answer: string;
  queries: Array<QueryResult & { schemaName: string | null; tables: Array<string> }> | null;
  resolvedProducts: {
    products: Array<ResolvedProduct>;
    schemas: Array<string>;
  } | null;
  schemasUsed: Array<string> | null;
  threadId: string;
  totalExecutionTimeMs: number | null;
  totalRows: number | null;
}

export interface ConversationSummary {
  createdAt: string;
  threadId: string;
  title: string | null;
  updatedAt: string;
}
