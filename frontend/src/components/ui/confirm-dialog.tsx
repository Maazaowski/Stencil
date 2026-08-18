"use client";

import * as React from "react";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

// A styled replacement for the browser's native confirm()/prompt(). Mounted
// once (see ConfirmProvider in the root layout); call sites use the useConfirm()
// / usePrompt() hooks, which return a promise that resolves when the user acts.

interface ConfirmOptions {
  title: string;
  description?: React.ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  variant?: "default" | "destructive";
}

interface PromptOptions extends ConfirmOptions {
  placeholder?: string;
  defaultValue?: string;
  /** Render a multi-line textarea instead of a single-line input. */
  multiline?: boolean;
  /** Require a non-empty value before the confirm button enables. */
  required?: boolean;
}

type PendingConfirm = {
  kind: "confirm";
  options: ConfirmOptions;
  resolve: (value: boolean) => void;
};
type PendingPrompt = {
  kind: "prompt";
  options: PromptOptions;
  resolve: (value: string | null) => void;
};
type Pending = PendingConfirm | PendingPrompt;

interface ConfirmContextValue {
  confirm: (options: ConfirmOptions) => Promise<boolean>;
  prompt: (options: PromptOptions) => Promise<string | null>;
}

const ConfirmContext = React.createContext<ConfirmContextValue | null>(null);

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [pending, setPending] = React.useState<Pending | null>(null);
  const [inputValue, setInputValue] = React.useState("");

  const confirm = React.useCallback(
    (options: ConfirmOptions) =>
      new Promise<boolean>((resolve) => {
        setPending({ kind: "confirm", options, resolve });
      }),
    [],
  );

  const prompt = React.useCallback(
    (options: PromptOptions) =>
      new Promise<string | null>((resolve) => {
        setInputValue(options.defaultValue ?? "");
        setPending({ kind: "prompt", options, resolve });
      }),
    [],
  );

  const close = React.useCallback(
    (result: boolean) => {
      if (!pending) return;
      if (pending.kind === "confirm") {
        pending.resolve(result);
      } else {
        pending.resolve(result ? inputValue : null);
      }
      setPending(null);
    },
    [pending, inputValue],
  );

  const value = React.useMemo(() => ({ confirm, prompt }), [confirm, prompt]);
  const options = pending?.options;
  const isPrompt = pending?.kind === "prompt";
  const promptOptions = isPrompt ? (pending.options as PromptOptions) : null;
  const confirmDisabled = !!(promptOptions?.required && inputValue.trim() === "");

  return (
    <ConfirmContext.Provider value={value}>
      {children}
      <Dialog
        open={pending !== null}
        onOpenChange={(open) => {
          // Closing via backdrop / Esc counts as a cancel.
          if (!open) close(false);
        }}
      >
        {options && (
          <DialogContent showCloseButton={false}>
            <DialogHeader>
              <DialogTitle>{options.title}</DialogTitle>
              {options.description && (
                <DialogDescription>{options.description}</DialogDescription>
              )}
            </DialogHeader>

            {isPrompt &&
              (promptOptions?.multiline ? (
                <textarea
                  autoFocus
                  className="min-h-24 w-full rounded-md border bg-transparent p-2 text-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
                  placeholder={promptOptions?.placeholder}
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                />
              ) : (
                <Input
                  autoFocus
                  placeholder={promptOptions?.placeholder}
                  value={inputValue}
                  onChange={(e) => setInputValue(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !confirmDisabled) close(true);
                  }}
                />
              ))}

            <DialogFooter>
              <Button variant="outline" onClick={() => close(false)}>
                {options.cancelLabel ?? "Cancel"}
              </Button>
              <Button
                variant={options.variant === "destructive" ? "destructive" : "default"}
                disabled={confirmDisabled}
                onClick={() => close(true)}
              >
                {options.confirmLabel ?? "Confirm"}
              </Button>
            </DialogFooter>
          </DialogContent>
        )}
      </Dialog>
    </ConfirmContext.Provider>
  );
}

function useConfirmContext(): ConfirmContextValue {
  const ctx = React.useContext(ConfirmContext);
  if (!ctx) throw new Error("useConfirm/usePrompt must be used within a ConfirmProvider");
  return ctx;
}

/** Returns an async `confirm(options)` that resolves to true if the user confirms. */
export function useConfirm(): ConfirmContextValue["confirm"] {
  return useConfirmContext().confirm;
}

/** Returns an async `prompt(options)` that resolves to the entered text, or null if cancelled. */
export function usePrompt(): ConfirmContextValue["prompt"] {
  return useConfirmContext().prompt;
}
