"use client";

import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import type { ProfileEnumOption } from "@/lib/profile-options";

type EnumSelectFieldProps = {
  value: string | null | undefined;
  onChange: (value: string | null) => void;
  options: ProfileEnumOption[];
  placeholder?: string;
  /** When true, adds an explicit unset option that maps to null. */
  allowUnset?: boolean;
  unsetLabel?: string;
  disabled?: boolean;
};

export function EnumSelectField({
  value,
  onChange,
  options,
  placeholder = "Select…",
  allowUnset = false,
  unsetLabel = "Auto (unset)",
  disabled = false,
}: EnumSelectFieldProps) {
  const known = new Set(options.map((o) => o.value));
  const isUnknown = !!value && !known.has(value);
  const selectValue = value && known.has(value) ? value : allowUnset ? "__unset__" : value ?? "";

  // Base UI's SelectValue renders the raw selected value unless given a render
  // function; map it to the option's human label (and the sentinel to its label)
  // so triggers never show "calculate" / "__unset__".
  const labelFor = (current: string | null): string => {
    if (!current || current === "__unset__") return unsetLabel;
    return options.find((o) => o.value === current)?.label ?? current;
  };

  return (
    <div className="space-y-1">
      <Select
        value={selectValue || undefined}
        onValueChange={(v) => onChange(v === "__unset__" ? null : v ?? null)}
        disabled={disabled}
      >
        <SelectTrigger>
          <SelectValue placeholder={placeholder}>
            {(current: string | null) => labelFor(current)}
          </SelectValue>
        </SelectTrigger>
        <SelectContent>
          {allowUnset && (
            <SelectItem value="__unset__">{unsetLabel}</SelectItem>
          )}
          {isUnknown && value && (
            <SelectItem value={value} disabled>
              Unknown value: {value}
            </SelectItem>
          )}
          {options.map((o) => (
            <SelectItem key={o.value} value={o.value}>
              {o.label}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      {isUnknown && (
        <p className="text-xs text-warning">
          Stored value &quot;{value}&quot; is not recognized — pick a valid option and save.
        </p>
      )}
    </div>
  );
}

export function EnumFieldHint({
  options,
  value,
}: {
  options: ProfileEnumOption[];
  value: string | null | undefined;
}) {
  const hint = options.find((o) => o.value === value)?.hint;
  if (!hint) return null;
  return <p className="text-xs text-muted-foreground mt-1">{hint}</p>;
}
