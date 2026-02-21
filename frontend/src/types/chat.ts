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
  toolCalls: Array<{ content: string; name?: string }>;
  toolOutputs: Array<{ content: string; name?: string }>;
}

export interface PipelineStep {
  label: string;
  node: string;
  status: 'active' | 'completed';
}

export interface DoneStats {
  threadId: string;
  totalExecutionTimeMs: number;
  totalQueries: number;
  totalRows: number;
  totalTimeMs: number;
}
