import type { ClassificationSchema, SystemMode, TradeMode, TradeOverrides } from '@/types/chat';
import { cn } from '@/lib/utils';

interface TradeTogglesBarProps {
  onModeChange: (v: TradeMode | null) => void;
  onSchemaChange: (v: ClassificationSchema | null) => void;
  onSystemModeChange?: (v: SystemMode | null) => void;
  overrides: TradeOverrides;
}

interface ToggleOption<T extends string> {
  label: string;
  value: T | null;
}

const MODE_OPTIONS: Array<ToggleOption<TradeMode>> = [
  { label: 'Auto', value: null },
  { label: 'Goods', value: 'goods' },
  { label: 'Services', value: 'services' },
];

const SCHEMA_OPTIONS: Array<ToggleOption<ClassificationSchema>> = [
  { label: 'Auto', value: null },
  { label: 'HS92', value: 'hs92' },
  { label: 'HS12', value: 'hs12' },
  { label: 'SITC', value: 'sitc' },
];

const SYSTEM_MODE_OPTIONS: Array<ToggleOption<SystemMode>> = [
  { label: 'Auto', value: null },
  { label: 'GQL+SQL', value: 'graphql_sql' },
  { label: 'SQL Only', value: 'sql_only' },
];

function ToggleGroup<T extends string>({
  onChange,
  options,
  value,
  variant,
}: {
  onChange: (v: T | null) => void;
  options: Array<ToggleOption<T>>;
  value: T | null;
  variant: 'filled' | 'outlined';
}) {
  return (
    <div className="flex items-center gap-0.5">
      {options.map((opt) => {
        const isActive = value === opt.value;
        return (
          <button
            aria-pressed={isActive}
            className={cn(
              'cursor-pointer rounded px-2.5 py-1 text-xs whitespace-nowrap transition-colors focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none',
              isActive
                ? variant === 'filled'
                  ? 'bg-primary font-semibold text-primary-foreground'
                  : 'border border-primary bg-card font-medium text-primary'
                : 'text-muted-foreground hover:text-foreground',
            )}
            key={opt.label}
            onClick={() => onChange(opt.value)}
            type="button"
          >
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

export default function TradeTogglesBar({
  onModeChange,
  onSchemaChange,
  onSystemModeChange,
  overrides,
}: TradeTogglesBarProps) {
  const showSchema = overrides.mode !== 'services';

  return (
    <div
      aria-label="Trade query constraints"
      className="flex min-h-10 flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-border bg-secondary px-5 py-1.5"
      role="toolbar"
    >
      <span className="hidden text-[9px] font-semibold tracking-widest text-muted-foreground uppercase select-none sm:inline">
        MODE
      </span>

      <ToggleGroup
        onChange={onModeChange}
        options={MODE_OPTIONS}
        value={overrides.mode}
        variant="filled"
      />

      {showSchema && (
        <>
          {/* Force schema group to next row on mobile */}
          <div className="h-0 basis-full sm:hidden" />

          <div
            className="hidden h-4 w-px shrink-0 bg-border sm:block"
            data-testid="toggle-divider"
          />

          <ToggleGroup
            onChange={onSchemaChange}
            options={SCHEMA_OPTIONS}
            value={overrides.schema}
            variant="outlined"
          />
        </>
      )}

      {import.meta.env.DEV && onSystemModeChange && (
        <>
          <div
            className="hidden h-4 w-px shrink-0 bg-border sm:block"
            data-testid="system-mode-divider"
          />

          <span className="hidden text-[9px] font-semibold tracking-widest text-amber-600 uppercase select-none sm:inline">
            PIPELINE
          </span>

          <ToggleGroup
            onChange={onSystemModeChange}
            options={SYSTEM_MODE_OPTIONS}
            value={overrides.systemMode}
            variant="outlined"
          />
        </>
      )}
    </div>
  );
}
