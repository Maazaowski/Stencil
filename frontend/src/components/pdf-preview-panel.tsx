"use client";

import { useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Loader2, Upload } from "lucide-react";
import { ExtractionPreviewView } from "@/components/extraction-preview";
import { formatApiError } from "@/lib/api/errors";
import type { ExtractionPreview } from "@/types";

/**
 * Upload a sample PDF and preview how it extracts. Backs the profile config
 * preview (AI) and the model workbench "try an invoice" (deterministic replay) —
 * the parent supplies the upload call.
 */
export function PdfPreviewPanel({
  description,
  buttonLabel = "Upload a sample PDF",
  runningLabel = "Extracting…",
  onPreview,
}: {
  description?: string;
  buttonLabel?: string;
  runningLabel?: string;
  onPreview: (file: File) => Promise<ExtractionPreview>;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [preview, setPreview] = useState<ExtractionPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fileName, setFileName] = useState<string | null>(null);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    setFileName(file.name);
    setLoading(true);
    setError(null);
    try {
      setPreview(await onPreview(file));
    } catch (e) {
      setError(formatApiError(e, "Preview failed"));
      setPreview(null);
    } finally {
      setLoading(false);
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <div className="space-y-4">
      {description && <p className="text-xs text-muted-foreground">{description}</p>}
      <div className="flex items-center gap-3">
        <input
          ref={inputRef}
          type="file"
          accept="application/pdf"
          className="hidden"
          onChange={(e) => handleFile(e.target.files?.[0])}
        />
        <Button
          variant="outline"
          size="sm"
          disabled={loading}
          onClick={() => inputRef.current?.click()}
        >
          {loading ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Upload className="h-4 w-4" />
          )}
          {loading ? runningLabel : buttonLabel}
        </Button>
        {fileName && !loading && (
          <span className="text-xs text-muted-foreground">{fileName}</span>
        )}
      </div>

      {error && (
        <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {error}
        </div>
      )}

      {preview && <ExtractionPreviewView preview={preview} />}
    </div>
  );
}
