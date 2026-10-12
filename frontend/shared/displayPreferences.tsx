export type TextSize = "standard" | "large" | "extra_large";
export const textScale: Record<TextSize, number> = { standard: 1, large: 1.125, extra_large: 1.25 };
export function isTextSize(value: unknown): value is TextSize {
  return value === "standard" || value === "large" || value === "extra_large";
}
export function TextSizeControl({ value, onChange, label = "字号", disabled = false }: { value: TextSize; onChange: (value: TextSize) => void; label?: string; disabled?: boolean }) {
  return <label className="text-size-control"><span>{label}</span><select disabled={disabled} aria-label={label} value={value} onChange={event => {
    if (isTextSize(event.target.value)) onChange(event.target.value);
  }}><option value="standard">标准</option><option value="large">较大</option><option value="extra_large">大</option></select></label>;
}
