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
  label: string;
  node: string;
  status: 'active' | 'completed';
}
