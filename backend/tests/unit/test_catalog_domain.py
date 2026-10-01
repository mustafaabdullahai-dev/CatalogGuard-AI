"""Catalog domain tests (D-015): normalization, authority, identity, relationships.

The scenarios are the ones that produce support tickets: `colour` vs `color`,
"EAN-13 in one file, zero-padded GTIN-14 in another", two equal-rank sources
disagreeing, and the cycle that makes a storefront recurse. Each test names the
failure it prevents rather than the function it calls.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.models.catalog import SourceType
from app.services.catalog.authority import (
    AuthorityDecision,
    Candidate,
    conflict_evidence,
    rank_for,
    resolve,
)
from app.services.catalog.identity import (
    IdentityCandidate,
    MatchKind,
    blocking_keys,
    needs_review,
    normalize_color,
    resolve_identity,
)
from app.services.catalog.normalization import (
    canonical_field,
    convert_measure,
    normalize_category_path,
    normalize_currency,
    normalize_decimal,
    normalize_identifier,
    normalize_record,
    normalize_url,
    propose_aliases,
)
from app.services.catalog.relationships import (
    IssueKind,
    RelationshipEdge,
    TreeNode,
    summarize,
    validate_relationships,
)


class TestFieldAliasing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("Product Colour", "color"),
            ("variant_color", "color"),
            ("colour", "color"),
            ("Product-Name", "title"),
            ("EAN13", "gtin"),
            ("productCode", "sku"),
            ("Unit Price", "price"),
            ("supplier_cost", "cost"),
            ("ISO_CURRENCY", "currency"),
            ("packagelength", "length"),
        ],
    )
    def test_known_aliases_resolve(self, raw: str, expected: str) -> None:
        assert canonical_field(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["colourway", "vendorColorName", "mystery_column", "", "___"]
    )
    def test_unknown_columns_are_not_guessed(self, raw: str) -> None:
        """An unmapped column stays unmapped.

        Mapping it to a plausible field is worse than leaving it out: the result
        looks authoritative and no one knows to check it.
        """
        assert canonical_field(raw) is None

    def test_namespaced_prefix_is_stripped_only_from_known_names(self) -> None:
        assert canonical_field("product_mystery") is None
        assert canonical_field("vendor_gtin") == "gtin"


class TestValueNormalization:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1,299.00 USD", Decimal("1299.00")),
            (" $45.50 ", Decimal("45.50")),
            (1299, Decimal("1299")),
            (12.5, Decimal("12.5")),
            ("no number here", None),
            (None, None),
            (True, None),
        ],
    )
    def test_numbers_written_for_humans(self, raw: object, expected: Decimal | None) -> None:
        assert normalize_decimal(raw) == expected

    def test_booleans_are_not_numbers(self) -> None:
        """`True` is 1 in Python. A price of 1.00 from a boolean is a real bug."""
        assert normalize_decimal(True) is None

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            (["Home", "Garden"], "Home / Garden"),
            ("Home>Garden", "Home / Garden"),
            ("Home / Garden", "Home / Garden"),
            ("Home,Garden", "Home / Garden"),
            ("Home / ", "Home"),
            ("N/A", None),
            ([], None),
        ],
    )
    def test_category_paths_converge_on_one_format(self, raw: object, expected: str | None) -> None:
        """Two spellings of one taxonomy must not read as a category conflict."""
        assert normalize_category_path(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("4006381333931", "04006381333931"),
            ("4006381333931", "04006381333931"),
            ("EAN-13: 4006381333931", "04006381333931"),
            ("123", None),
            ("", None),
        ],
    )
    def test_gtins_are_digits(self, raw: str, expected: str | None) -> None:
        assert normalize_identifier(raw) == expected

    def test_short_digit_runs_are_rejected_as_gtins(self) -> None:
        """`123` is a quantity or a line number, not a product code."""
        assert normalize_identifier("123") is None

    @pytest.mark.parametrize(
        ("raw", "expected"), [("$", "USD"), ("EUR", "EUR"), ("usd", "USD"), ("₺", None)]
    )
    def test_currency_codes(self, raw: str, expected: str | None) -> None:
        assert normalize_currency(raw) == expected

    def test_protocol_is_added_to_bare_urls(self) -> None:
        assert normalize_url("cdn.example.com/a.jpg") == "https://cdn.example.com/a.jpg"
        assert normalize_url("//cdn.example.com/a.jpg") == "https://cdn.example.com/a.jpg"

    def test_measures_convert_within_a_dimension(self) -> None:
        assert convert_measure(Decimal("12.5"), "cm", "length", "mm") == Decimal("125.00")

    def test_cross_dimension_conversion_is_refused(self) -> None:
        """30 cm is not 30 g. Refusing beats returning a plausible number."""
        assert convert_measure(Decimal("30"), "cm", "length", "kg") is None

    def test_colour_spelling_does_not_double_count(self) -> None:
        record = normalize_record({"Product Colour": "Navy Blue", "Color": "Blue"})
        assert record.fields["color"] == "Blue"
        assert any(rule.rule == "duplicate.collision" for rule in record.rules)


class TestNullishValues:
    @pytest.mark.parametrize("value", ["", "  ", "N/A", "n/a", "none", "NULL", "TBD", "-"])
    def test_absence_is_not_a_value(self, value: str) -> None:
        """A source saying "N/A" and another saying "Blue" is not a conflict."""
        record = normalize_record({"color": value})
        assert "color" not in record.fields
        assert record.dropped_fields == ("color",)


class TestRecordNormalization:
    def test_a_supplier_export_normalizes_end_to_end(self) -> None:
        record = normalize_record(
            {
                "ItemID": "TEE-001",
                "Product Name": "Navy Cotton T-Shirt",
                "EAN13": "4006381333931",
                "Unit Price": "1,299.00 USD",
                "Supplier Cost": "310.00",
                "Weight": "2 lb",
                "Category": ["Clothing", "T-Shirts"],
                "Primary Image": "//cdn.example.com/1.jpg",
                "Legacy Code": "abc",
            }
        )
        assert record.fields["sku"] == "TEE-001"
        assert record.fields["title"] == "Navy Cotton T-Shirt"
        assert record.fields["gtin"] == "04006381333931"
        assert record.fields["price"] == Decimal("1299.00")
        assert record.fields["cost"] == Decimal("310.00")
        assert record.fields["weight"] == Decimal("2")
        assert record.fields["category_path"] == "Clothing / T-Shirts"
        assert record.fields["image_url"] == "https://cdn.example.com/1.jpg"
        assert record.unknown_fields == ("Legacy Code",)
        assert not record.is_clean

    def test_every_rule_records_which_column_produced_the_value(self) -> None:
        """A mapping that cannot be audited cannot be trusted."""
        record = normalize_record({"Product Colour": "Navy Blue"})
        assert record.rules == (
            record.rules[0],
        )
        assert record.rules[0].field == "color"
        assert record.rules[0].detail == "Product Colour"

    def test_empty_record_is_clean_not_broken(self) -> None:
        record = normalize_record({})
        assert record.is_clean
        assert record.fields == {}

    def test_nullish_value_under_unknown_column_is_dropped_not_flagged(self) -> None:
        record = normalize_record({"legacy": "", "colour": "Navy"})
        assert record.unknown_fields == ()
        assert record.dropped_fields == ("legacy",)


class TestAliasProposals:
    def test_candidates_are_suggested_not_applied(self) -> None:
        """`colourway` matches the `colour` alias and nothing else."""
        proposals = propose_aliases([{"colourway": "Navy"}, {"colourway": "Blue"}])
        assert proposals == {"colourway": "color"}

    def test_ambiguous_names_are_left_alone(self) -> None:
        """`style_size_code` contains two canonical names; a human decides."""
        assert propose_aliases([{"style_size_code": "x"}]) == {}

    def test_a_name_matching_two_spellings_is_not_proposed(self) -> None:
        """`colourway_name` contains `colour` (color) and `name` (title).

        Guessing one of them would put a colour into the title field, so the
        candidate is refused and a human picks.
        """
        assert propose_aliases([{"colourway_name": "Navy"}]) == {}


class TestAuthorityDefaults:
    def test_content_goes_to_the_pim_and_money_to_the_erp(self) -> None:
        assert rank_for("title", SourceType.PIM) < rank_for("title", SourceType.ERP)
        assert rank_for("cost", SourceType.ERP) < rank_for("cost", SourceType.PIM)

    def test_a_marketplace_price_outranks_a_pim_price(self) -> None:
        """The marketplace price is the only one a shopper sees."""
        assert rank_for("price", SourceType.MARKETPLACE) < rank_for("price", SourceType.PIM)

    def test_unchanged_fields_keep_the_global_ladder(self) -> None:
        assert rank_for("color", SourceType.MANUFACTURER) == 1
        assert rank_for("color", SourceType.MARKETPLACE) == 5


class TestAuthorityResolution:
    def test_highest_rank_wins_and_the_reason_names_the_source(self) -> None:
        result = resolve(
            "color",
            [
                Candidate(SourceType.MARKETPLACE, "Navy"),
                Candidate(SourceType.MANUFACTURER, "Navy Blue"),
            ],
        )
        assert result.decision is AuthorityDecision.RESOLVED
        assert result.winner is not None
        assert result.winner.source is SourceType.MANUFACTURER
        assert "manufacturer" in result.reason

    def test_a_tie_escalates_instead_of_choosing(self) -> None:
        """ERP and PIM share rank 2 for color; picking one would be a coin flip."""
        result = resolve(
            "color",
            [
                Candidate(SourceType.ERP, "Navy Blue"),
                Candidate(SourceType.PIM, "Dark Blue"),
                Candidate(SourceType.MARKETPLACE, "Navy"),
            ],
        )
        assert result.decision is AuthorityDecision.TIED
        assert result.winner is None
        assert result.needs_escalation
        assert result.competing_values == {
            "erp": "Navy Blue",
            "pim": "Dark Blue",
            "marketplace": "Navy",
        }

    def test_equal_rank_that_agrees_is_not_a_conflict(self) -> None:
        result = resolve(
            "color", [Candidate(SourceType.ERP, "Blue"), Candidate(SourceType.PIM, "blue ")]
        )
        assert result.decision is AuthorityDecision.RESOLVED

    def test_agreement_from_lower_sources_is_recorded_as_corroboration(self) -> None:
        result = resolve(
            "price",
            [
                Candidate(SourceType.ERP, Decimal("10.00")),
                Candidate(SourceType.PIM, Decimal("10.0")),
                Candidate(SourceType.MARKETPLACE, "10.00"),
            ],
        )
        assert result.resolved
        assert "corroborated" in result.reason

    def test_ai_inference_never_becomes_authoritative(self) -> None:
        """Even ranked against nothing, an inferred value is not canonical."""
        result = resolve("title", [Candidate(SourceType.AI_INFERRED, "Probably a shirt")])
        assert result.decision is AuthorityDecision.NO_AUTHORITATIVE_SOURCE
        assert result.winner is None

    def test_a_tenant_cannot_override_the_ai_bar_by_ranking_it_first(self) -> None:
        result = resolve(
            "title",
            [
                Candidate(SourceType.AI_INFERRED, "inferred"),
                Candidate(SourceType.MANUFACTURER, "real"),
            ],
            overrides={"title": {SourceType.AI_INFERRED: 0}},
        )
        assert result.winner is not None
        assert result.winner.source is SourceType.MANUFACTURER

    def test_workspace_overrides_replace_defaults(self) -> None:
        result = resolve(
            "title",
            [Candidate(SourceType.PIM, "PIM title"), Candidate(SourceType.ERP, "ERP title")],
            overrides={"title": {SourceType.ERP: 1, SourceType.PIM: 9}},
        )
        assert result.winner is not None
        assert result.winner.source is SourceType.ERP

    def test_no_value_is_a_distinct_answer_from_no_source(self) -> None:
        empty = resolve("color", [Candidate(SourceType.MANUFACTURER, None)])
        assert empty.decision is AuthorityDecision.NO_VALUE
        assert not empty.needs_escalation

    def test_evidence_carries_ranks_for_the_conflict_record(self) -> None:
        result = resolve(
            "color",
            [Candidate(SourceType.ERP, "a"), Candidate(SourceType.MANUFACTURER, "b")],
        )
        evidence = conflict_evidence(result)
        assert evidence["ranks"] == {"manufacturer": 1, "erp": 2}
        assert evidence["winner_source"] == "manufacturer"
        assert evidence["decision"] == "resolved"


class TestIdentityMatches:
    def test_identical_gtin_links_without_approval(self) -> None:
        """Identity was stated, not inferred — the only automatic case."""
        incoming = IdentityCandidate(product_id=None, gtin="04006381333931", title="Navy Tee")
        existing = IdentityCandidate(product_id="p1", gtin="4006381333931", title="Navy T-Shirt")
        match = resolve_identity(incoming, [existing])
        assert match.match
        assert match.kind is MatchKind.EXACT_IDENTIFIER
        assert not match.requires_approval
        assert not needs_review(match)
        assert match.proposed_product_id == "p1"

    def test_ean13_and_padded_gtin14_are_the_same_code(self) -> None:
        """The same code written at two widths is the commonest GTIN trap."""
        incoming = IdentityCandidate(product_id=None, gtin="04006381333931")
        existing = IdentityCandidate(product_id="p1", gtin="4006381333931")
        assert resolve_identity(incoming, [existing]).kind is MatchKind.EXACT_IDENTIFIER

    def test_a_different_gtin_disqualifies_however_similar_the_text(self) -> None:
        incoming = IdentityCandidate(
            product_id=None, gtin="04006381333931", title="Navy Cotton Tee"
        )
        existing = IdentityCandidate(
            product_id="p1", gtin="00000000000019", title="Navy Cotton Tee"
        )
        match = resolve_identity(incoming, [existing])
        assert not match.match
        assert match.kind is MatchKind.DIFFERENT
        assert "gtin differs" in match.reason

    def test_a_missing_identifier_is_not_a_disagreement(self) -> None:
        """Absence of evidence about identity is not evidence of difference."""
        incoming = IdentityCandidate(product_id=None, title="Navy Cotton Tee Large")
        existing = IdentityCandidate(
            product_id="p1", gtin="04006381333931", title="Navy Cotton Tee Large"
        )
        match = resolve_identity(incoming, [existing])
        assert match.requires_approval
        assert match.match

    def test_identical_titles_still_require_approval(self) -> None:
        """A merge is not reversible, so text alone never clears itself."""
        incoming = IdentityCandidate(product_id=None, title="Navy Cotton Tee Large")
        existing = IdentityCandidate(product_id="p1", title="Navy Cotton Tee Large")
        match = resolve_identity(incoming, [existing])
        assert match.confidence >= 0.97
        assert match.requires_approval
        assert needs_review(match)

    def test_text_plus_agreeing_mpn_is_strong_but_still_a_proposal(self) -> None:
        incoming = IdentityCandidate(product_id=None, mpn="MP-9", title="Navy Cotton Tee Large")
        existing = IdentityCandidate(product_id="p1", mpn="mp-9", title="Navy Cotton Tee Large")
        match = resolve_identity(incoming, [existing])
        assert match.kind is MatchKind.STRONG_TEXT
        assert match.requires_approval

    def test_different_brands_cap_the_score(self) -> None:
        incoming = IdentityCandidate(product_id=None, brand="Acme", title="Navy Cotton Tee")
        existing = IdentityCandidate(product_id="p1", brand="Zephyr", title="Navy Cotton Tee")
        match = resolve_identity(incoming, [existing])
        assert not match.match
        assert "brand differs" in match.reason or any(
            e.kind == "brand_conflict" for e in match.evidence
        )

    def test_conflicting_size_pulls_the_score_down(self) -> None:
        """Same title, size M vs L: different variants of one product."""
        incoming = IdentityCandidate(
            product_id=None, title="Navy Cotton Tee", attributes={"size": "M"}
        )
        existing = IdentityCandidate(
            product_id="p1", title="Navy Cotton Tee", attributes={"size": "L"}
        )
        match = resolve_identity(incoming, [existing])
        assert not match.match

    def test_empty_catalogue_yields_a_non_match_not_a_weak_one(self) -> None:
        match = resolve_identity(IdentityCandidate(product_id=None, title="Anything"), [])
        assert not match.match
        assert not needs_review(match)

    def test_candidates_without_an_id_are_ignored(self) -> None:
        match = resolve_identity(
            IdentityCandidate(product_id=None, title="Navy Cotton Tee"),
            [IdentityCandidate(product_id=None, title="Navy Cotton Tee")],
        )
        assert not match.match

    def test_payload_matches_the_documented_contract(self) -> None:
        payload = resolve_identity(
            IdentityCandidate(product_id=None, gtin="04006381333931"),
            [IdentityCandidate(product_id="p1", gtin="04006381333931")],
        ).to_payload()
        assert set(payload) >= {"match", "confidence", "reason", "evidence"}
        assert isinstance(payload["confidence"], float)


class TestIdentityColorAndBlocking:
    @pytest.mark.parametrize(
        ("a", "b"), [("Navy Blue", "Dark Blue"), ("Gray", "Grey"), ("White", "Ivory")]
    )
    def test_color_groups_collapse(self, a: str, b: str) -> None:
        assert normalize_color(a) == normalize_color(b)

    def test_unrelated_colors_do_not_collapse(self) -> None:
        assert normalize_color("Red") != normalize_color("Blue")

    def test_blocking_keys_include_identifier_and_brand(self) -> None:
        keys = blocking_keys(
            IdentityCandidate(
                product_id=None, gtin="04006381333931", title="Navy Cotton Tee", brand="Acme"
            )
        )
        assert "gtin:04006381333931" in keys
        assert "brand:acme" in keys
        assert any(k.startswith("title:") for k in keys)


def _tree(**kwargs: object) -> list[TreeNode]:
    return validate_relationships(list(kwargs.values()))  # type: ignore[arg-type]


class TestRelationshipValidation:
    def test_a_healthy_tree_produces_nothing(self) -> None:
        findings = _tree(
            parent=TreeNode(
                id="p", sku="TEE", is_parent=True, variation_attributes={"color": "navy"}
            ),
            navy=TreeNode(
                id="v1", sku="TEE-NVY", parent_id="p", variation_attributes={"color": "navy"}
            ),
            red=TreeNode(
                id="v2", sku="TEE-RED", parent_id="p", variation_attributes={"color": "red"}
            ),
        )
        assert findings == []

    def test_a_child_without_a_parent_is_an_orphan(self) -> None:
        findings = _tree(loose=TreeNode(id="v", sku="TEE-1"))
        assert [f.kind for f in findings] == [IssueKind.ORPHAN_CHILD]
        assert findings[0].severity == "error"

    def test_a_missing_parent_is_an_invalid_parent(self) -> None:
        findings = _tree(child=TreeNode(id="v", sku="TEE-1", parent_id="missing"))
        assert [f.kind for f in findings] == [IssueKind.INVALID_PARENT]
        assert "does not exist" in findings[0].detail

    def test_a_parent_not_marked_as_a_family_is_flagged(self) -> None:
        findings = _tree(
            child=TreeNode(
                id="v", sku="TEE-1", parent_id="p", variation_attributes={"color": "navy"}
            ),
            supposed_parent=TreeNode(id="p", sku="TEE"),
        )
        assert IssueKind.INVALID_PARENT in {f.kind for f in findings}

    def test_two_identical_variants_under_one_parent(self) -> None:
        findings = _tree(
            parent=TreeNode(
                id="p", sku="TEE", is_parent=True, variation_attributes={"color": "navy"}
            ),
            one=TreeNode(
                id="v1", sku="A", parent_id="p", variation_attributes={"color": "navy"}
            ),
            two=TreeNode(
                id="v2", sku="B", parent_id="p", variation_attributes={"color": "Navy"}
            ),
        )
        kinds = {f.kind for f in findings}
        assert IssueKind.DUPLICATE_CHILD_VARIANT in kinds
        assert any("identical variants" in f.detail for f in findings)

    def test_a_child_missing_the_parents_axis(self) -> None:
        findings = _tree(
            parent=TreeNode(
                id="p", sku="TEE", is_parent=True, variation_attributes={"color": "navy"}
            ),
            blank=TreeNode(id="v1", sku="A", parent_id="p", variation_attributes={}),
        )
        assert any(f.kind is IssueKind.MISSING_VARIATION for f in findings)
        assert any("cannot be distinguished" in f.detail for f in findings)

    def test_a_child_declaring_an_axis_the_parent_does_not(self) -> None:
        findings = _tree(
            parent=TreeNode(id="p", sku="TEE", is_parent=True, variation_attributes={"size": "L"}),
            child=TreeNode(
                id="v1", sku="A", parent_id="p",
                variation_attributes={"size": "L", "color": "navy"},
            ),
        )
        assert any(f.kind is IssueKind.INCONSISTENT_VARIATION for f in findings)

    def test_a_declared_axis_no_child_supplies(self) -> None:
        """The parent's schema says it varies by size; the catalogue does not."""
        findings = _tree(
            parent=TreeNode(
                id="p", sku="TEE", is_parent=True,
                variation_attributes={"color": "navy", "size": "L"},
            ),
            one=TreeNode(
                id="v1", sku="A", parent_id="p", variation_attributes={"color": "navy"}
            ),
            two=TreeNode(
                id="v2", sku="B", parent_id="p", variation_attributes={"color": "navy"}
            ),
        )
        assert any("no child supplies" in f.detail for f in findings)

    def test_self_parenting(self) -> None:
        findings = _tree(node=TreeNode(id="x", sku="X", parent_id="x"))
        assert IssueKind.SELF_PARENT in {f.kind for f in findings}

    def test_a_cycle_is_reported_once_not_per_starting_node(self) -> None:
        findings = _tree(
            a=TreeNode(id="a", sku="A", parent_id="b"),
            b=TreeNode(id="b", sku="B", parent_id="a"),
            c=TreeNode(id="c", sku="C", parent_id="a"),
        )
        cycles = [f for f in findings if f.kind is IssueKind.CYCLE]
        assert len(cycles) == 1
        assert cycles[0].cycle_path == ("A", "B")

    def test_a_bundle_containing_itself(self) -> None:
        findings = validate_relationships(
            [TreeNode(id="KIT", sku="KIT", is_parent=True)],
            [RelationshipEdge(parent_id="KIT", child_id="KIT", relationship_type="bundle")],
        )
        assert any(f.kind is IssueKind.BUNDLE_CHILD_CYCLE for f in findings)

    def test_nested_bundles_that_recurse(self) -> None:
        """Two bundles containing each other: expanding either never ends."""
        findings = validate_relationships(
            [
                TreeNode(id="outer", sku="OUTER", is_parent=True),
                TreeNode(id="inner", sku="INNER", is_parent=True),
            ],
            [
                RelationshipEdge(parent_id="outer", child_id="inner", relationship_type="bundle"),
                RelationshipEdge(parent_id="inner", child_id="outer", relationship_type="bundle"),
            ],
        )
        cycles = [f for f in findings if f.kind is IssueKind.BUNDLE_CHILD_CYCLE]
        assert cycles and "never ends" in cycles[0].detail

    def test_two_parents_of_different_relationship_types(self) -> None:
        """Only the edge table can express this: `products.parent_id` is single."""
        findings = validate_relationships(
            [
                TreeNode(id="fam", sku="TEE", is_parent=True),
                TreeNode(id="kit", sku="KIT", is_parent=True),
                TreeNode(id="v", sku="A"),
            ],
            [
                RelationshipEdge(parent_id="fam", child_id="v", relationship_type="parent"),
                RelationshipEdge(parent_id="kit", child_id="v", relationship_type="bundle"),
            ],
        )
        multiple = [f for f in findings if f.kind is IssueKind.MULTIPLE_PARENTS]
        assert multiple and multiple[0].severity == "error"

    def test_the_same_relationship_twice_is_a_warning(self) -> None:
        """A bundle item in two bundles is legitimate; a duplicated import is not."""
        findings = validate_relationships(
            [TreeNode(id="fam", sku="TEE", is_parent=True), TreeNode(id="v", sku="A")],
            [
                RelationshipEdge(parent_id="fam", child_id="v", relationship_type="bundle"),
                RelationshipEdge(parent_id="fam", child_id="v", relationship_type="bundle"),
            ],
        )
        multiple = [f for f in findings if f.kind is IssueKind.MULTIPLE_PARENTS]
        assert multiple and multiple[0].severity == "warning"

    def test_an_unapproved_relationship_is_reported(self) -> None:
        """§9: no relationship is established without deterministic validation
        and approval. An edge with no approval reference is a finding."""
        findings = validate_relationships(
            [TreeNode(id="fam", sku="TEE", is_parent=True), TreeNode(id="v", sku="A")],
            [
                RelationshipEdge(
                    parent_id="fam", child_id="v", established_by="import-job", approved=False
                )
            ],
        )
        assert any("no approval reference" in f.detail for f in findings)

    def test_an_approved_relationship_is_not_reported(self) -> None:
        findings = validate_relationships(
            [
                TreeNode(id="fam", sku="TEE", is_parent=True),
                TreeNode(id="v", sku="A", parent_id="fam"),
            ],
            [
                RelationshipEdge(
                    parent_id="fam", child_id="v", established_by="import-job", approved=True
                )
            ],
        )
        assert findings == []

    def test_deep_trees_are_flagged(self) -> None:
        nodes = [TreeNode(id="root", is_parent=True)]
        for level in range(1, 9):
            nodes.append(
                TreeNode(id=f"n{level}", parent_id="root" if level == 1 else f"n{level - 1}")
            )
        assert any("levels below its root" in f.detail for f in validate_relationships(nodes))

    def test_findings_carry_locations_a_diff_view_can_open(self) -> None:
        findings = _tree(child=TreeNode(id="v", sku="TEE-1", parent_id="missing"))
        payload = findings[0].as_dict()
        assert payload["child_id"] == "v"
        assert payload["parent_id"] == "missing"
        assert payload["severity"] == "error"


class TestRelationshipSummary:
    def test_counts_are_shaped_for_health_scores(self) -> None:
        findings = _tree(
            orphan=TreeNode(id="v"),
            self_ref=TreeNode(id="x", parent_id="x"),
        )
        counts = summarize(findings)
        assert counts["total"] == len(findings)
        assert counts["errors"] >= 2
        assert any(key.startswith("kind.") for key in counts)
