import { SandboxPanel } from "@/components/SandboxPanel";
import { Shell } from "@/components/Shell";
import { DataSourceNotice, EmptyState } from "@/components/StateBlocks";
import { getProposals, pageContext } from "@/lib/api";
import { plural } from "@/lib/pagination";

export const dynamic = "force-dynamic";

/**
 * Sandbox.
 *
 * §18's chain runs on a copy, so nothing here writes a product; pressing
 * Simulate writes one `simulation_runs` row and the result comes back with its
 * stages, diff and impact report. A stage that could not answer is rendered as
 * "not run", which is the one distinction this screen has to get right: a
 * workspace with no compliance corpus has not passed compliance validation.
 */
export default async function SandboxPage() {
  const context = await pageContext();
  const proposals = await getProposals(context, { limit: 50 });

  return (
    <Shell
      title="Sandbox"
      subtitle={`${plural(proposals.data.total, "proposal")} available to simulate`}
    >
      <DataSourceNotice source={proposals.source} reason={proposals.reason} />

      {proposals.data.proposals.length === 0 ? (
        <EmptyState
          title="No proposal to simulate"
          body="Simulation runs against a change proposal. Once reconciliation has produced one, it appears here."
        />
      ) : (
        <SandboxPanel proposals={proposals.data.proposals} live={proposals.source === "live"} />
      )}
    </Shell>
  );
}