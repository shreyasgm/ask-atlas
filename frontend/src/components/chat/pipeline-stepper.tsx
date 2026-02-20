import { Check } from 'lucide-react';
import type { PipelineStep } from '@/types/chat';
import { cn } from '@/lib/utils';

interface PipelineStepperProps {
  steps: Array<PipelineStep>;
}

export default function PipelineStepper({ steps }: PipelineStepperProps) {
  if (steps.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-0">
      {steps.map((step, i) => (
        <div className="flex items-start gap-2" key={step.node}>
          <div className="flex flex-col items-center">
            {step.status === 'completed' ? (
              <div className="flex h-5 w-5 items-center justify-center rounded-full bg-green-500">
                <Check className="h-3 w-3 text-white" />
              </div>
            ) : (
              <div className="h-5 w-5 animate-pulse rounded-full bg-primary" />
            )}
            {i < steps.length - 1 && <div className="h-4 w-px bg-border" />}
          </div>
          <span
            className={cn(
              'text-xs',
              step.status === 'completed' ? 'text-muted-foreground' : 'text-foreground',
            )}
          >
            {step.label}
            {step.status === 'active' && '...'}
          </span>
        </div>
      ))}
    </div>
  );
}
