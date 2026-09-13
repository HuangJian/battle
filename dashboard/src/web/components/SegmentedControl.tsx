/** SegmentedControl.tsx — 分段切换（节点卡 控制|统计、指标行过滤等）。 */

export interface SegmentOption<T extends string> {
  value: T
  label: string
}

export interface SegmentedControlProps<T extends string> {
  value: T
  options: Array<SegmentOption<T>>
  onChange: (value: T) => void
  ariaLabel?: string
}

export function SegmentedControl<T extends string>({
  value,
  options,
  onChange,
  ariaLabel,
}: SegmentedControlProps<T>) {
  return (
    <div className="tc-segmented" role="group" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          type="button"
          className={`tc-segmented__btn${value === o.value ? ' tc-segmented__btn--on' : ''}`}
          aria-pressed={value === o.value}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}
