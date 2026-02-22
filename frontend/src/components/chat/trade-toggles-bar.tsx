import type { ClassificationSchema, TradeMode, TradeOverrides } from '@/types/chat';

interface TradeTogglesBarProps {
  onModeChange: (v: TradeMode | null) => void;
  onSchemaChange: (v: ClassificationSchema | null) => void;
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
        let className =
          'rounded px-2.5 py-1 text-xs transition-colors cursor-pointer whitespace-nowrap ';
        if (isActive) {
          className +=
            variant === 'filled'
              ? 'bg-primary text-primary-foreground font-semibold'
              : 'border border-primary text-primary font-medium bg-card';
        } else {
          className += 'text-muted-foreground hover:text-foreground';
        }
        return (
          <button
            aria-pressed={isActive}
            className={className}
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
  overrides,
}: TradeTogglesBarProps) {
  const showSchema = overrides.mode !== 'services';

  return (
    <div
      aria-label="Trade query constraints"
      className="flex h-10 items-center gap-3 overflow-x-auto border-b border-border bg-secondary px-5"
      role="toolbar"
    >
      <span className="text-[9px] font-semibold tracking-widest text-muted-foreground uppercase select-none">
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
          <div className="h-4 w-px shrink-0 bg-border" data-testid="toggle-divider" />

          <ToggleGroup
            onChange={onSchemaChange}
            options={SCHEMA_OPTIONS}
            value={overrides.schema}
            variant="outlined"
          />
        </>
      )}
    </div>
  );
}
