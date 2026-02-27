export interface QueryResult {
  columns: Array<string>;
  executionTimeMs: number;
  rowCount: number;
  rows: Array<Array<unknown>>;
  sql: string;
}

export interface AtlasLink {
  label: string;
  link_type: 'country_page' | 'explore_page';
  resolution_notes: Array<string>;
  url: string;
}

export type PipelineType = 'docs' | 'graphql' | 'sql';
export type SystemMode = 'auto' | 'graphql_sql' | 'sql_only';

export interface GraphqlClassification {
  apiTarget: string;
  isRejected: boolean;
  queryType: string;
  rejectionReason: string;
}

export interface GraphqlSummary {
  apiTarget: string;
  classification: GraphqlClassification;
  entities: Record<string, unknown>;
  executionTimeMs: number;
  links: Array<AtlasLink>;
}

export interface ChatMessage {
  atlasLinks: Array<AtlasLink>;
  content: string;
  docsConsulted: Array<string>;
  graphqlSummaries: Array<GraphqlSummary>;
  id: string;
  isStreaming: boolean;
  pipelineSteps?: Array<PipelineStep>;
  queryResults: Array<QueryResult>;
  role: 'assistant' | 'user';
}

export interface PipelineStep {
  completedAt?: number;
  detail?: Record<string, unknown>;
  label: string;
  node: string;
  pipelineType: PipelineType;
  queryIndex?: number;
  startedAt: number;
  status: 'active' | 'completed';
}

export interface ResolvedProduct {
  codes: Array<string>;
  name: string;
  schema: string;
}

export interface CountryInfo {
  iso3Code: string;
  name: string;
}

export interface EntitiesData {
  countries: Array<CountryInfo>;
  docsConsulted: Array<string>;
  graphqlClassification: GraphqlClassification | null;
  graphqlEntities: Record<string, unknown> | null;
  lookupCodes: string;
  products: Array<ResolvedProduct>;
  resolutionNotes: Array<string>;
  schemas: Array<string>;
}

export interface QueryAggregateStats {
  totalExecutionTimeMs: number;
  totalGraphqlQueries: number;
  totalGraphqlTimeMs: number;
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
  systemMode: SystemMode | null;
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

export interface TurnSummaryGraphqlSummary {
  api_target: string;
  classification: {
    is_rejected: boolean;
    query_type: string;
    rejection_reason: string;
  };
  entities: Record<string, unknown>;
  execution_time_ms: number;
  links: Array<AtlasLink>;
}

export interface TurnSummary {
  atlas_links?: Array<AtlasLink>;
  docs_consulted?: Array<string>;
  entities: {
    countries?: Array<{ iso3_code: string; name: string }>;
    products: Array<ResolvedProduct>;
    schemas: Array<string>;
  } | null;
  graphql_summaries?: Array<TurnSummaryGraphqlSummary>;
  queries: Array<{
    columns: Array<string>;
    execution_time_ms: number;
    row_count: number;
    rows: Array<Array<unknown>>;
    schema_name: string | null;
    sql: string;
    tables: Array<string>;
  }>;
  total_execution_time_ms: number;
  total_graphql_time_ms?: number;
  total_rows: number;
}

export interface ConversationSummary {
  createdAt: string;
  threadId: string;
  title: string | null;
  updatedAt: string;
}
