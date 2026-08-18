import { cn } from "@/lib/utils";

function Progress({
  value,
  indeterminate = false,
  className,
  indicatorClassName,
  ...props
}: React.ComponentProps<"div"> & {
  value?: number | null;
  indeterminate?: boolean;
  indicatorClassName?: string;
}) {
  const pct = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div
      data-slot="progress"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={indeterminate ? undefined : pct}
      className={cn(
        "relative h-2 w-full overflow-hidden rounded-sm bg-primary/15",
        className,
      )}
      {...props}
    >
      {indeterminate ? (
        <div
          className={cn(
            "absolute inset-y-0 left-0 w-1/3 animate-[progress-slide_1.2s_ease-in-out_infinite] rounded-sm bg-primary",
            indicatorClassName,
          )}
        />
      ) : (
        <div
          className={cn(
            "h-full rounded-sm bg-primary transition-[width] duration-500 ease-out",
            indicatorClassName,
          )}
          style={{ width: `${pct}%` }}
        />
      )}
    </div>
  );
}

export { Progress };
