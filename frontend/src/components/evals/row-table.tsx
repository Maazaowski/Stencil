import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

/**
 * A raw row dump for eval diffing.
 *
 * Deliberately kept dense and capped in height — this is for comparing
 * extracted rows side by side, so more rows on screen beats comfort. It now
 * goes through the Table primitive so the sticky mono header and tabular
 * figures match every other table in the product.
 */
export function RowTable({
  title,
  columns,
  rows,
}: {
  title: string;
  columns: string[];
  rows: (string | number | null)[][];
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <p className="label-mono">
        {title} <span className="text-foreground">({rows.length})</span>
      </p>
      <div className="max-h-72 overflow-auto">
        <Table className="text-[0.6875rem]">
          <TableHeader>
            <TableRow>
              {columns.map((c) => (
                <TableHead key={c} className="h-6 px-1.5">
                  {c}
                </TableHead>
              ))}
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.length === 0 ? (
              <TableRow>
                <TableCell
                  colSpan={Math.max(columns.length, 1)}
                  className="h-auto py-4 text-center text-muted-foreground"
                >
                  No rows.
                </TableCell>
              </TableRow>
            ) : (
              rows.map((row, i) => (
                <TableRow key={i}>
                  {row.map((cell, j) => (
                    <TableCell key={j} className="h-6 px-1.5 font-mono">
                      {cell === null ? "" : String(cell)}
                    </TableCell>
                  ))}
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}
