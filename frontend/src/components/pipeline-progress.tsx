"use client";

import { ProcessingTimeline } from "@/components/processing-timeline";

/** Upload-page card wrapper around the unified pipeline timeline. */
export function PipelineProgress({
  intakeId,
  showCancel = true,
}: {
  intakeId: string;
  showCancel?: boolean;
}) {
  return (
    <ProcessingTimeline
      intakeId={intakeId}
      variant="card"
      showCancel={showCancel}
    />
  );
}
