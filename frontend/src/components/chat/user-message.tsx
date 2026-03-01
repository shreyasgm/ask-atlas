import { memo } from 'react';

interface UserMessageProps {
  content: string;
}

export default memo(function UserMessage({ content }: UserMessageProps) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[70%] rounded-xl bg-muted px-3.5 py-2.5">
        <p className="text-sm break-words">{content}</p>
      </div>
    </div>
  );
});
