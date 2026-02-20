export interface ChatMessage {
  content: string;
  id: string;
  isStreaming: boolean;
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
