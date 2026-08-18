"use client";

import { useState, type ClipboardEvent, type KeyboardEvent } from "react";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { X } from "lucide-react";

interface TagInputProps {
  value: string[];
  onChange: (value: string[]) => void;
  placeholder?: string;
  disabled?: boolean;
}

export function TagInput({
  value,
  onChange,
  placeholder = "Type and press Enter...",
  disabled,
}: TagInputProps) {
  const [input, setInput] = useState("");

  function addTokens(raw: string) {
    const tokens = raw
      .split(/[\n,]/)
      .map((token) => token.trim())
      .filter(Boolean);
    if (tokens.length === 0) return;

    const next = [...value];
    for (const token of tokens) {
      if (!next.includes(token)) next.push(token);
    }
    onChange(next);
  }

  function commitInput() {
    if (!input.trim()) return;
    addTokens(input);
    setInput("");
  }

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if ((e.key === "Enter" || e.key === "Tab") && input.trim()) {
      e.preventDefault();
      commitInput();
    }
    if (e.key === "Backspace" && !input && value.length > 0) {
      onChange(value.slice(0, -1));
    }
  }

  function handlePaste(e: ClipboardEvent<HTMLInputElement>) {
    const text = e.clipboardData.getData("text");
    if (!text || !/[\n,]/.test(text)) return;
    e.preventDefault();
    addTokens(text);
  }

  function removeTag(index: number) {
    onChange(value.filter((_, i) => i !== index));
  }

  return (
    <div className="flex flex-wrap items-center gap-1.5 rounded-md border bg-background px-3 py-2 min-h-10">
      {value.map((tag, i) => (
        <Badge key={i} variant="secondary" className="gap-1 text-xs">
          {tag}
          {!disabled && (
            <button
              type="button"
              onClick={() => removeTag(i)}
              className="ml-0.5 rounded-sm hover:bg-muted-foreground/20"
            >
              <X className="h-3 w-3" />
            </button>
          )}
        </Badge>
      ))}
      <Input
        value={input}
        onChange={(e) => setInput(e.target.value)}
        onKeyDown={handleKeyDown}
        onBlur={commitInput}
        onPaste={handlePaste}
        placeholder={value.length === 0 ? placeholder : ""}
        disabled={disabled}
        className="border-0 bg-transparent p-0 shadow-none focus-visible:ring-0 h-6 min-w-[120px] flex-1 text-sm"
      />
    </div>
  );
}
