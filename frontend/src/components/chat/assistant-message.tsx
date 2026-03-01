import type { Components } from 'react-markdown';
import { Bot, Loader } from 'lucide-react';
import { memo, useEffect, useState } from 'react';
import Markdown from 'react-markdown';
import rehypeKatex from 'rehype-katex';
import remarkGfm from 'remark-gfm';
import remarkMath from 'remark-math';
import type { ChatMessage } from '@/types/chat';
import AtlasLinks from './atlas-links';
import DocsBlock from './docs-block';
import GraphqlSummaryBlock from './graphql-summary-block';
import QueryResultTable from './query-result-table';
import SqlBlock from './sql-block';

const MARKDOWN_COMPONENTS: Components = {
  h3: (props) => <h3 className="text-sm font-bold" {...props} />,
  li: (props) => <li className="ml-4 text-sm" {...props} />,
  ol: (props) => <ol className="ml-4 list-decimal space-y-1 text-sm" {...props} />,
  p: (props) => <p className="text-sm" {...props} />,
  strong: (props) => <strong className="font-bold" {...props} />,
  table: (props) => (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-left text-sm" {...props} />
    </div>
  ),
  td: (props) => <td className="border-b px-3 py-1.5 text-sm" {...props} />,
  th: (props) => (
    <th
      className="border-b bg-muted px-3 py-2 text-left text-sm font-semibold text-muted-foreground"
      {...props}
    />
  ),
  ul: (props) => <ul className="ml-4 list-disc space-y-1 text-sm" {...props} />,
};

interface AssistantMessageProps {
  message: ChatMessage;
  pipelineStarted?: boolean;
}

export default memo(function AssistantMessage({ message, pipelineStarted }: AssistantMessageProps) {
  const isLoading = message.isStreaming && !message.content && !pipelineStarted;
  const [showColdStartHint, setShowColdStartHint] = useState(false);

  useEffect(() => {
    if (!isLoading) {
      return;
    }
    const timer = setTimeout(() => setShowColdStartHint(true), 4000);
    return () => {
      clearTimeout(timer);
      setShowColdStartHint(false);
    };
  }, [isLoading]);

  const hasDataSources =
    message.queryResults.length > 0 ||
    message.atlasLinks.length > 0 ||
    message.graphqlSummaries.length > 0 ||
    message.docsConsulted.length > 0;

  return (
    <div className="flex flex-col gap-2">
      {(message.content || isLoading) && (
        <>
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-blue-500" />
            <span className="text-xs font-semibold text-blue-500">Ask-Atlas Assistant</span>
          </div>
          {isLoading ? (
            <div className="flex items-center gap-2">
              <Loader className="h-3.5 w-3.5 animate-spin text-blue-500" />
              <span className="text-sm text-muted-foreground">
                {showColdStartHint
                  ? 'Starting up the backend — this can take up to 15 seconds on first use'
                  : 'Processing your question...'}
              </span>
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              <Markdown
                components={MARKDOWN_COMPONENTS}
                rehypePlugins={[rehypeKatex]}
                remarkPlugins={[remarkGfm, remarkMath]}
              >
                {message.content}
              </Markdown>
            </div>
          )}
        </>
      )}

      {message.queryResults.map((qr, i) => (
        <div className="flex flex-col gap-2" key={`qr-${i}`}>
          <SqlBlock sql={qr.sql}>
            {qr.rowCount > 0 && (
              <div className="mt-2 flex flex-col gap-1">
                <QueryResultTable columns={qr.columns} rows={qr.rows} />
                <p className="font-mono text-xs text-muted-foreground">
                  {qr.rowCount} rows in {qr.executionTimeMs}ms
                </p>
              </div>
            )}
          </SqlBlock>
        </div>
      ))}

      {message.graphqlSummaries.map((gs, i) => (
        <GraphqlSummaryBlock key={`gs-${i}`} summary={gs} />
      ))}

      {message.docsConsulted.length > 0 && <DocsBlock files={message.docsConsulted} />}

      {message.atlasLinks.length > 0 && <AtlasLinks links={message.atlasLinks} />}

      {hasDataSources && (
        <p className="font-mono text-xs text-muted-foreground">
          Source: Atlas of Economic Complexity
        </p>
      )}
    </div>
  );
});
