import type { Components } from 'react-markdown';
import { Bot } from 'lucide-react';
import { memo } from 'react';
import Markdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { ChatMessage } from '@/types/chat';
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
}

export default memo(function AssistantMessage({ message }: AssistantMessageProps) {
  return (
    <div className="flex flex-col gap-2">
      {message.content && (
        <>
          <div className="flex items-center gap-2">
            <Bot className="h-4 w-4 text-blue-500" />
            <span className="text-xs font-semibold text-blue-500">Ask-Atlas Assistant</span>
          </div>
          <div className="flex flex-col gap-1">
            <Markdown components={MARKDOWN_COMPONENTS} remarkPlugins={[remarkGfm]}>
              {message.content}
            </Markdown>
          </div>
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

      {message.queryResults.length > 0 && (
        <p className="font-mono text-xs text-muted-foreground">
          Source: Atlas of Economic Complexity
        </p>
      )}
    </div>
  );
});
