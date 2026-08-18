"use client";

import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { Plus, Trash2 } from "lucide-react";

interface KeyValueEditorProps {
  value: Record<string, string>;
  onChange: (value: Record<string, string>) => void;
  keyPlaceholder?: string;
  valuePlaceholder?: string;
  disabled?: boolean;
}

export function KeyValueEditor({
  value,
  onChange,
  keyPlaceholder = "Key",
  valuePlaceholder = "Value",
  disabled,
}: KeyValueEditorProps) {
  const entries = Object.entries(value);

  function addRow() {
    onChange({ ...value, "": "" });
  }

  function updateKey(oldKey: string, newKey: string, index: number) {
    const newEntries = [...entries];
    newEntries[index] = [newKey, entries[index][1]];
    onChange(Object.fromEntries(newEntries));
  }

  function updateValue(key: string, newValue: string, index: number) {
    const newEntries = [...entries];
    newEntries[index] = [entries[index][0], newValue];
    onChange(Object.fromEntries(newEntries));
  }

  function removeRow(index: number) {
    const newEntries = entries.filter((_, i) => i !== index);
    onChange(Object.fromEntries(newEntries));
  }

  return (
    <div className="space-y-2">
      {entries.map(([key, val], i) => (
        <div key={i} className="flex items-center gap-2">
          <Input
            value={key}
            onChange={(e) => updateKey(key, e.target.value, i)}
            placeholder={keyPlaceholder}
            disabled={disabled}
            className="flex-1 text-sm"
          />
          <span className="text-muted-foreground text-sm">→</span>
          <Input
            value={val}
            onChange={(e) => updateValue(key, e.target.value, i)}
            placeholder={valuePlaceholder}
            disabled={disabled}
            className="flex-1 text-sm"
          />
          {!disabled && (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="h-8 w-8 shrink-0"
              onClick={() => removeRow(i)}
            >
              <Trash2 className="h-3.5 w-3.5 text-muted-foreground" />
            </Button>
          )}
        </div>
      ))}
      {!disabled && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          onClick={addRow}
          className="w-full"
        >
          <Plus className="h-3.5 w-3.5" />
          Add mapping
        </Button>
      )}
    </div>
  );
}
