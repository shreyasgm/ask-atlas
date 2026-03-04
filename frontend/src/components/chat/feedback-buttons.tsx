import { Check, ThumbsDown, ThumbsUp } from 'lucide-react';
import { memo, useCallback, useEffect, useState } from 'react';
import type { FeedbackState } from '@/types/chat';

interface FeedbackButtonsProps {
  feedback?: FeedbackState;
  onSubmit: (rating: 'down' | 'up', comment?: string) => void;
  onUpdate: (id: number, rating: 'down' | 'up', comment?: string) => void;
}

export default memo(function FeedbackButtons({
  feedback,
  onSubmit,
  onUpdate,
}: FeedbackButtonsProps) {
  const [showCommentInput, setShowCommentInput] = useState(false);
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [comment, setComment] = useState('');

  useEffect(() => {
    if (!showConfirmation) {
      return;
    }
    const timer = setTimeout(() => setShowConfirmation(false), 2000);
    return () => clearTimeout(timer);
  }, [showConfirmation]);

  const handleThumbsUp = useCallback(() => {
    if (feedback) {
      if (feedback.rating === 'up') {
        return;
      }
      onUpdate(feedback.id, 'up');
    } else {
      onSubmit('up');
    }
    setShowCommentInput(false);
    setComment('');
  }, [feedback, onSubmit, onUpdate]);

  const handleThumbsDown = useCallback(() => {
    if (feedback?.rating === 'down') {
      // Already downvoted — toggle comment input to add/edit comment
      setShowCommentInput((prev) => !prev);
      setComment(feedback.comment ?? '');
      return;
    }
    // Submit downvote immediately (no comment yet)
    if (feedback) {
      onUpdate(feedback.id, 'down');
    } else {
      onSubmit('down');
    }
    // Show comment input for optional note
    setShowCommentInput(true);
    setComment('');
  }, [feedback, onSubmit, onUpdate]);

  const handleCommentSubmit = useCallback(() => {
    if (feedback && feedback.id > 0) {
      onUpdate(feedback.id, 'down', comment || undefined);
    } else {
      // Feedback not yet persisted (id === -1) or missing — use upsert
      onSubmit('down', comment || undefined);
    }
    setShowCommentInput(false);
    setComment('');
    setShowConfirmation(true);
  }, [comment, feedback, onSubmit, onUpdate]);

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleCommentSubmit();
      }
      if (e.key === 'Escape') {
        setShowCommentInput(false);
        setComment('');
      }
    },
    [handleCommentSubmit],
  );

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex items-center gap-1">
        <button
          aria-label="Thumbs up"
          className={`rounded p-1 transition-colors ${
            feedback?.rating === 'up'
              ? 'text-green-600'
              : 'text-muted-foreground/40 hover:text-muted-foreground'
          }`}
          onClick={handleThumbsUp}
          type="button"
        >
          <ThumbsUp
            className="h-3.5 w-3.5"
            fill={feedback?.rating === 'up' ? 'currentColor' : 'none'}
          />
        </button>
        <button
          aria-label="Thumbs down"
          className={`rounded p-1 transition-colors ${
            feedback?.rating === 'down'
              ? 'text-red-500'
              : 'text-muted-foreground/40 hover:text-muted-foreground'
          }`}
          onClick={handleThumbsDown}
          type="button"
        >
          <ThumbsDown
            className="h-3.5 w-3.5"
            fill={feedback?.rating === 'down' ? 'currentColor' : 'none'}
          />
        </button>
      </div>
      {showCommentInput && (
        <div className="flex items-center gap-2">
          <input
            autoFocus
            className="flex-1 rounded border border-border bg-background px-2 py-1 text-xs text-foreground placeholder:text-muted-foreground focus:ring-1 focus:ring-ring focus:outline-none"
            onChange={(e) => setComment(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="What was wrong or expected? (optional)"
            type="text"
            value={comment}
          />
          <button
            className="rounded bg-primary px-2 py-1 text-xs text-primary-foreground hover:bg-primary/90"
            onClick={handleCommentSubmit}
            type="button"
          >
            Send
          </button>
        </div>
      )}
      {showConfirmation && !showCommentInput && (
        <div className="flex items-center gap-1 text-xs text-green-600">
          <Check className="h-3 w-3" />
          <span>Feedback sent</span>
        </div>
      )}
    </div>
  );
});
