"""Parent-child relationship validation (D-015, §9).

Checks the shape of the product tree and reports what is wrong with it. It
**never** mutates a relationship — §9 forbids automatic modification without
deterministic validation and approval, and the strongest form of that rule is
that this module has no write path at all. It returns findings; the approval
flow turns findings into `ProductRelationship` rows.

Every finding is a plain, checkable statement. "Navy variant M is under a parent
whose only variation axis is size, so the colour axis is unrepresented" is
actionable. "Relationship integrity may be suboptimal" is not, and a system that
emits the second kind of message gets its output ignored.
"""

from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from enum import StrEnum

# Variation axes that make a child distinct from its siblings. Colour and size
# are the obvious ones; material and style are equally real, and a catalogue
# that varies on style but not colour is common in footwear.
VARIATION_AXES: frozenset[str] = frozenset({"color", "size", "material", "style"})
# Axes a parent must itself declare when it varies on them.
MAX_TREE_DEPTH = 6


class IssueKind(StrEnum):
    ORPHAN_CHILD = "orphan_child"
    INVALID_PARENT = "invalid_parent"
    DUPLICATE_PARENT = "duplicate_parent"
    MULTIPLE_PARENTS = "multiple_parents"
    INCONSISTENT_VARIATION = "inconsistent_variation"
    MISSING_VARIATION = "missing_variation"
    BROKEN_RELATIONSHIP = "broken_relationship"
    CYCLE = "cycle"
    SELF_PARENT = "self_parent"
    DUPLICATE_CHILD_VARIANT = "duplicate_child_variant"
    BUNDLE_CHILD_CYCLE = "bundle_child_cycle"


@dataclass(frozen=True, slots=True)
class TreeNode:
    """A product as the tree sees it. Deliberately not the ORM model."""

    id: uuid.UUID | str
    sku: str | None = None
    title: str | None = None
    is_parent: bool = False
    parent_id: uuid.UUID | str | None = None
    variation_attributes: dict[str, object] = field(default_factory=dict)
    relationship_type: str = "parent"
    source: str = "internal"

    @property
    def axes(self) -> frozenset[str]:
        """Variation axes this node actually varies on."""
        return frozenset(
            axis
            for axis, value in self.variation_attributes.items()
            if axis in VARIATION_AXES and value not in (None, "")
        )

    def axis_signature(self) -> tuple[tuple[str, str], ...]:
        """The variation values that make this node unique among its siblings."""
        return tuple(
            sorted(
                (axis, str(value).strip().casefold())
                for axis, value in self.variation_attributes.items()
                if axis in VARIATION_AXES
            )
        )


@dataclass(frozen=True, slots=True)
class RelationshipEdge:
    """One edge from `product_relationships`.

    A `TreeNode` carries at most one parent, which is what `products.parent_id`
    allows. A child under two parents is representable only in the edge table,
    so the checks that need it read edges. Keeping the two apart is what stops
    the single-parent assumption from hiding a broken tree.
    """

    parent_id: uuid.UUID | str
    child_id: uuid.UUID | str
    relationship_type: str = "parent"
    established_by: str = "internal"
    approved: bool = False


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem, located precisely enough to open a diff view on it."""

    kind: IssueKind
    severity: str  # "error" | "warning"
    detail: str
    child_id: uuid.UUID | str | None = None
    parent_id: uuid.UUID | str | None = None
    cycle_path: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind.value,
            "severity": self.severity,
            "detail": self.detail,
            "child_id": str(self.child_id) if self.child_id else None,
            "parent_id": str(self.parent_id) if self.parent_id else None,
            "cycle_path": list(self.cycle_path),
        }


def detect_cycles(nodes: dict[object, TreeNode]) -> list[Finding]:
    """Find every cycle in the parent chain.

    Iterative DFS with a colouring pass, O(n) across all nodes: a catalogue is
    every product in the workspace, so a per-node walk that revisits shared
    ancestors is quadratic on exactly the inputs most likely to be broken.

    Each distinct loop is reported once, normalized by rotation so `A -> B -> A`
    and `B -> A -> B` are a single finding rather than two.
    """
    unvisited, on_path, done = 0, 1, 2
    colour: dict[object, int] = dict.fromkeys(nodes, unvisited)
    findings: list[Finding] = []
    seen_cycles: set[tuple[str, ...]] = set()

    def label(node_id: object) -> str:
        node = nodes[node_id]
        return str(node.sku or node.id)

    for start in nodes:
        if colour[start] != unvisited:
            continue
        path: list[object] = []
        current: object | None = start
        cycle: tuple[str, ...] | None = None
        while current is not None and current in nodes and colour[current] != done:
            if colour[current] == on_path:
                loop = path[path.index(current) :]
                labels = tuple(label(node_id) for node_id in loop)
                pivot = labels.index(min(labels))
                cycle = labels[pivot:] + labels[:pivot]
                break
            colour[current] = on_path
            path.append(current)
            current = nodes[current].parent_id
        # Every node on this walk is finished: a cycle was reported from it, or
        # it led to an already-finished node. Leaving anything on_path would
        # make the next walk mistake it for a cycle of its own.
        for node_id in path:
            colour[node_id] = done
        if cycle is None or cycle in seen_cycles:
            continue
        seen_cycles.add(cycle)
        loop_ids = [node_id for node_id in path if label(node_id) in cycle]
        findings.append(
            Finding(
                kind=IssueKind.CYCLE,
                severity="error",
                detail=(
                    "parent chain forms a cycle: " + " -> ".join([*cycle, cycle[0]])
                ),
                child_id=loop_ids[-1] if loop_ids else None,
                parent_id=loop_ids[0] if loop_ids else None,
                cycle_path=cycle,
            )
        )
    return findings


def _index(nodes: list[TreeNode]) -> dict[object, TreeNode]:
    return {node.id: node for node in nodes}


def _duplicate_parent_finding(node: TreeNode, twin: TreeNode) -> Finding:
    return Finding(
        kind=IssueKind.DUPLICATE_PARENT,
        severity="error",
        detail=(
            f"parent {node.sku or node.id} has the same variation signature "
            f"({_describe_signature(node)}) as {twin.sku or twin.id}; one of them "
            "is a duplicate family, and their children cannot both be trusted"
        ),
        parent_id=node.id,
    )


def _describe_signature(node: TreeNode) -> str:
    axes = node.axis_signature()
    return ", ".join(f"{axis}={value}" for axis, value in axes) or "(none)"


def validate_relationships(
    nodes: list[TreeNode], edges: list[RelationshipEdge] | None = None
) -> list[Finding]:
    """Run every structural check and return the findings, unsorted.

    Unsorted on purpose: the caller persists findings in this order, and
    ordering them here would hide the fact that two checks can report the same
    product for different reasons. `summarize` counts them instead.
    """
    index = _index(nodes)
    findings: list[Finding] = []
    children_by_parent: dict[object, list[TreeNode]] = {}
    for node in nodes:
        if node.parent_id is not None:
            children_by_parent.setdefault(node.parent_id, []).append(node)

    # Children per parent: orphans, invalid parents, self-parenting, duplicates.
    for node in nodes:
        parent_id = node.parent_id
        if parent_id is not None and parent_id == node.id:
            findings.append(
                Finding(
                    kind=IssueKind.SELF_PARENT,
                    severity="error",
                    detail=f"{node.sku or node.id} is its own parent",
                    child_id=node.id,
                    parent_id=parent_id,
                )
            )
            continue
        if parent_id is None:
            if not node.is_parent:
                findings.append(
                    Finding(
                        kind=IssueKind.ORPHAN_CHILD,
                        severity="error",
                        detail=(
                            f"{node.sku or node.id} has no parent and is not a "
                            "family; it will not appear under any parent listing"
                        ),
                        child_id=node.id,
                    )
                )
            continue
        parent = index.get(parent_id)
        if parent is None:
            findings.append(
                Finding(
                    kind=IssueKind.INVALID_PARENT,
                    severity="error",
                    detail=(
                        f"{node.sku or node.id} points at parent {parent_id}, which "
                        "does not exist in this workspace"
                    ),
                    child_id=node.id,
                    parent_id=parent_id,
                )
            )
            continue
        if not parent.is_parent:
            findings.append(
                Finding(
                    kind=IssueKind.INVALID_PARENT,
                    severity="error",
                    detail=(
                        f"parent {parent.sku or parent.id} is not marked as a family, "
                        f"but {node.sku or node.id} is attached to it"
                    ),
                    child_id=node.id,
                    parent_id=parent.id,
                )
            )

    # Two children with identical variation values are the same variant listed
    # twice — the single most common intake defect in apparel.
    for parent_id, children in children_by_parent.items():
        parent_label = index[parent_id].sku if parent_id in index else str(parent_id)
        signatures: dict[tuple[tuple[str, str], ...], TreeNode] = {}
        for child in children:
            signature = child.axis_signature()
            if not signature:
                continue
            existing = signatures.get(signature)
            if existing is None:
                signatures[signature] = child
                continue
            findings.append(
                Finding(
                    kind=IssueKind.DUPLICATE_CHILD_VARIANT,
                    severity="error",
                    detail=(
                        f"{existing.sku or existing.id} and {child.sku or child.id} are "
                        f"identical variants under {parent_label} "
                        f"({_describe_signature(child)})"
                    ),
                    child_id=child.id,
                    parent_id=parent_id,
                )
            )

    # A parent's children must vary along the axes the parent declares, and
    # every child must vary along those axes.
    for parent_id, children in children_by_parent.items():
        parent = index.get(parent_id)
        if parent is None:
            continue
        parent_axes = parent.axes
        observed: set[str] = set()
        for child in children:
            child_axes = child.axes
            observed |= child_axes
            missing = sorted(parent_axes - child_axes)
            if missing:
                findings.append(
                    Finding(
                        kind=IssueKind.MISSING_VARIATION,
                        severity="warning",
                        detail=(
                            f"{child.sku or child.id} lacks "
                            f"{', '.join(missing)}, which parent "
                            f"{parent.sku or parent.id} varies on; the variant cannot "
                            "be distinguished from its siblings"
                        ),
                        child_id=child.id,
                        parent_id=parent_id,
                    )
                )
            extra = sorted(child_axes - parent_axes)
            if extra:
                findings.append(
                    Finding(
                        kind=IssueKind.INCONSISTENT_VARIATION,
                        severity="warning",
                        detail=(
                            f"{child.sku or child.id} varies on "
                            f"{', '.join(extra)}, which parent "
                            f"{parent.sku or parent.id} does not declare; either the "
                            "parent's axes or this child is wrong"
                        ),
                        child_id=child.id,
                        parent_id=parent_id,
                    )
                )
        declared_but_absent = sorted(parent_axes - observed)
        if declared_but_absent and len(children) > 1:
            findings.append(
                Finding(
                    kind=IssueKind.INCONSISTENT_VARIATION,
                    severity="warning",
                    detail=(
                        f"parent {parent.sku or parent.id} declares "
                        f"{', '.join(declared_but_absent)} but no child supplies it, so "
                        "that axis exists in the schema and not in the catalogue"
                    ),
                    parent_id=parent_id,
                )
            )

    findings.extend(detect_cycles(index))
    findings.extend(_multi_parent_findings(nodes, edges))
    findings.extend(_bundle_findings(edges))
    findings.extend(_depth_findings(nodes, index))
    return findings


def _multi_parent_findings(
    nodes: list[TreeNode], edges: list[RelationshipEdge] | None
) -> list[Finding]:
    """A child under two parents of different relationship types is broken.

    Two edges of the *same* type is a different defect — a bundle item can
    legitimately belong to several bundles — so that case is reported separately
    and less severely. Edges that no longer have approval are called out, since
    §9 forbids establishing a relationship without one.
    """
    if not edges:
        return []
    index = _index(nodes)
    parents_by_child: dict[object, list[RelationshipEdge]] = {}
    for edge in edges:
        parents_by_child.setdefault(edge.child_id, []).append(edge)

    findings: list[Finding] = []
    for child_id, child_edges in parents_by_child.items():
        child = index.get(child_id)
        child_label = str(child.sku or child_id) if child else str(child_id)
        for edge in child_edges:
            if not edge.approved and edge.established_by != "system":
                findings.append(
                    Finding(
                        kind=IssueKind.BROKEN_RELATIONSHIP,
                        severity="warning",
                        detail=(
                            f"{child_label} -> {edge.parent_id} was established by "
                            f"{edge.established_by} with no approval reference; §9 "
                            "requires one"
                        ),
                        child_id=child_id,
                        parent_id=edge.parent_id,
                    )
                )
        if len(child_edges) < 2:
            continue
        types = {edge.relationship_type for edge in child_edges}
        parents = sorted({str(edge.parent_id) for edge in child_edges})
        labels = ", ".join(
            str(index[e.parent_id].sku or e.parent_id)
            for e in child_edges
            if e.parent_id in index
        ) or ", ".join(parents)
        if len(types) > 1:
            findings.append(
                Finding(
                    kind=IssueKind.MULTIPLE_PARENTS,
                    severity="error",
                    detail=(
                        f"{child_label} is attached to {labels} as "
                        f"{', '.join(sorted(types))}; a variant belongs to one family, "
                        "and two relationship types cannot both be true"
                    ),
                    child_id=child_id,
                )
            )
        else:
            findings.append(
                Finding(
                    kind=IssueKind.MULTIPLE_PARENTS,
                    severity="warning",
                    detail=(
                        f"{child_label} appears under {len(child_edges)} "
                        f"{next(iter(types))} parents ({labels}); confirm this is "
                        "intended rather than a duplicated import"
                    ),
                    child_id=child_id,
                )
            )
    return findings


def _bundle_findings(edges: list[RelationshipEdge] | None) -> list[Finding]:
    """A bundle cannot contain itself, directly or transitively.

    This is a different cycle from a parent cycle — the edge type differs, so the
    generic pass does not see it — and it is the one that turns a storefront's
    "add to cart" into infinite recursion.
    """
    if not edges:
        return []
    bundle_children: dict[object, list[object]] = {}
    for edge in edges:
        if edge.relationship_type == "bundle":
            bundle_children.setdefault(edge.parent_id, []).append(edge.child_id)

    findings: list[Finding] = []
    for bundle_id, members in bundle_children.items():
        if bundle_id in members:
            findings.append(
                Finding(
                    kind=IssueKind.BUNDLE_CHILD_CYCLE,
                    severity="error",
                    detail=(
                        f"bundle {bundle_id} lists itself as a member; a bundle "
                        "cannot contain itself"
                    ),
                    child_id=bundle_id,
                    parent_id=bundle_id,
                    cycle_path=(str(bundle_id),),
                )
            )
            continue
        for member in members:
            if _reaches(bundle_children, member, bundle_id):
                findings.append(
                    Finding(
                        kind=IssueKind.BUNDLE_CHILD_CYCLE,
                        severity="error",
                        detail=(
                            f"bundle {bundle_id} contains {member}, which contains "
                            f"{bundle_id} again; expanding this bundle never ends"
                        ),
                        child_id=member,
                        parent_id=bundle_id,
                        cycle_path=(str(bundle_id), str(member)),
                    )
                )
                break
    return findings


def _reaches(graph: dict[object, list[object]], start: object, target: object) -> bool:
    """Whether `target` is reachable from `start`, iteratively and once each."""
    stack = list(graph.get(start, []))
    seen: set[object] = set()
    while stack:
        node = stack.pop()
        if node == target:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(graph.get(node, []))
    return False


def _depth_findings(
    nodes: list[TreeNode], index: dict[object, TreeNode]
) -> list[Finding]:
    """Catalogue trees deeper than `MAX_TREE_DEPTH` are unreadable, not deep.

    Marketplace category trees and reporting both give up well before this, so
    a deeper structure is nearly always an import artifact.
    """
    findings: list[Finding] = []
    for node in nodes:
        depth = 0
        current = node.parent_id
        guard: set[object] = {node.id}
        while current is not None and current in index and depth <= MAX_TREE_DEPTH + 1:
            if current in guard:
                break
            guard.add(current)
            depth += 1
            current = index[current].parent_id
        if depth > MAX_TREE_DEPTH:
            findings.append(
                Finding(
                    kind=IssueKind.BROKEN_RELATIONSHIP,
                    severity="warning",
                    detail=(
                        f"{node.sku or node.id} sits {depth} levels below its root, "
                        f"past the {MAX_TREE_DEPTH}-level limit most marketplaces "
                        "and reports can represent"
                    ),
                    child_id=node.id,
                )
            )
    return findings


def summarize(findings: list[Finding]) -> dict[str, int]:
    """Counts by kind and severity, for `HealthScore.issue_counts`."""
    by_kind = Counter(f.kind.value for f in findings)
    by_severity = Counter(f.severity for f in findings)
    return {
        "total": len(findings),
        "errors": by_severity["error"],
        "warnings": by_severity["warning"],
        **{f"kind.{kind}": count for kind, count in sorted(by_kind.items())},
    }
