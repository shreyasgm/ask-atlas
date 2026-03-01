const MAX_VISIBLE_ROWS = 100;

interface QueryResultTableProps {
  columns: Array<string>;
  rows: Array<Array<unknown>>;
}

export default function QueryResultTable({ columns, rows }: QueryResultTableProps) {
  const visibleRows = rows.slice(0, MAX_VISIBLE_ROWS);
  const hiddenCount = rows.length - visibleRows.length;

  return (
    <div className="overflow-x-auto rounded-lg border">
      <table className="w-full text-left font-mono text-xs">
        <thead>
          <tr className="border-b bg-muted">
            {columns.map((col) => (
              <th className="px-3 py-2 font-semibold text-muted-foreground" key={col}>
                {col}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((row, rowIdx) => (
            <tr className="border-b last:border-b-0" key={rowIdx}>
              {row.map((cell, cellIdx) => (
                <td className="max-w-[300px] truncate px-3 py-1.5 whitespace-nowrap" key={cellIdx}>
                  {cell == null ? (
                    <span className="text-muted-foreground">NULL</span>
                  ) : (
                    String(cell)
                  )}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {hiddenCount > 0 && (
        <p className="border-t px-3 py-2 text-center text-xs text-muted-foreground">
          Showing {visibleRows.length.toLocaleString()} of {rows.length.toLocaleString()} rows
        </p>
      )}
    </div>
  );
}
