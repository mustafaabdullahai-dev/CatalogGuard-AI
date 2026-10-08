import { parseDiff } from "@/lib/diff";
import { plural } from "@/lib/pagination";

/**
 * §19's before/after table.
 *
 * Server component: the diff arrives with the page, and nothing here needs a
 * browser. Each row carries its indicator as text (`added`/`removed`/
 * `modified`) rather than colour alone, because a diff read by colour is a diff
 * nobody with a colourblind reviewer can check.
 */
export function DiffTable({ diff }: { diff: Record<string, unknown> }) {
  const view = parseDiff(diff);
  return (
    <section className="card">
      <header className="card-header">
        <span className="card-title">Before / after</span>
        <span className="card-hint">
          {view.changed === 0 ? "no field changes" : `${plural(view.changed, "field")} changed`}
        </span>
      </header>
      <div className="card-body">
        {view.rows.length === 0 ? (
          <p className="empty">The simulation reported no diff.</p>
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Field</th>
                  <th>Current</th>
                  <th>Proposed</th>
                  <th>Indicator</th>
                </tr>
              </thead>
              <tbody>
                {view.rows.map((row) => (
                  <tr key={row.field} className={row.risky ? "row-risky" : undefined}>
                    <td className="mono">{row.field}</td>
                    <td className="cell-before">{row.current}</td>
                    <td className="cell-after">{row.proposed}</td>
                    <td>
                      <span
                        className={`badge badge-${
                          row.indicator === "modified"
                            ? "MEDIUM"
                            : row.indicator === "unchanged"
                              ? "neutral"
                              : "LOW"
                        }`}
                      >
                        {row.indicator}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </section>
  );
}