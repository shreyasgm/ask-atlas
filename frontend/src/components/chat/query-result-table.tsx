interface QueryResultTableProps {
  columns: Array<string>;
  rows: Array<Array<unknown>>;
}

export default function QueryResultTable({ columns, rows }: QueryResultTableProps) {
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
          {rows.map((row, rowIdx) => (
            <tr className="border-b last:border-b-0" key={rowIdx}>
              {row.map((cell, cellIdx) => (
                <td className="px-3 py-1.5 whitespace-nowrap" key={cellIdx}>
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
    </div>
  );
}
