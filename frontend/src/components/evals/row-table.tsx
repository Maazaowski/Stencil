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
    <div>
      <p className="mb-1 font-medium">{title} ({rows.length})</p>
      <div className="max-h-72 overflow-auto rounded-md border">
        <table className="w-full font-mono text-[11px]">
          <thead className="sticky top-0 bg-muted text-muted-foreground">
            <tr>{columns.map((c) => <th key={c} className="px-1 text-left">{c}</th>)}</tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-t">
                {row.map((cell, j) => <td key={j} className="px-1">{cell === null ? "" : String(cell)}</td>)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
