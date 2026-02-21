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
