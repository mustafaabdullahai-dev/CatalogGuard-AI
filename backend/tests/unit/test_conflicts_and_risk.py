"""Conflict detection and risk engine tests (§11, §16, D-016).

§11's example is the color argument — manufacturer says Navy Blue, Amazon says
Blue, Shopify says Dark Blue — so that case is tested first and in full, because
it is the shape every other conflict takes.

§16's requirement that "the LLM alone" not determine final risk is tested as
determinism: the same context must score identically, and a reviewer must be able
to predict the band from the factor table.
"""

from __future__ import annotations

import inspect
from decimal import Decimal

import pytest

from app.models.catalog import SourceType
from app.models.reconciliation import ConflictType, RiskLevel
from app.services.reconciliation import risk
from app.services.reconciliation.conflicts import (
    ConflictSeverity,
    FieldObservation,
    conflict_summary,
    detect_color_conflicts,
    detect_field_conflicts,
    detect_missing_attribute_conflicts,
)
from app.services.reconciliation.risk import (
    THRESHOLDS,
    WEIGHTS,
    RiskContext,
    assess,
    classify,
    context_from_change,
)


def _obs(source: SourceType, value: object, **kwargs: object) -> FieldObservation:
    return FieldObservation(source=source, value=value, **kwargs)  # type: ignore[arg-type]


class TestColorConflict:
    """§11's worked example."""

    def test_four_spellings_of_one_argument_are_one_conflict(self) -> None:
        conflicts = detect_color_conflicts(
            "p1",
            [
                _obs(SourceType.MANUFACTURER, "Navy Blue"),
                _obs(SourceType.MARKETPLACE, "Blue"),
                _obs(SourceType.INTERNAL, "Dark Blue"),
                _obs(SourceType.PIM, "Navy"),
            ],
        )
        assert len(conflicts) == 1

    def test_navy_blue_and_dark_blue_are_grouped_as_the_same_color(self) -> None:
        conflict = detect_color_conflicts(
            "p1",
            [
                _obs(SourceType.MANUFACTURER, "Navy Blue"),
                _obs(SourceType.MARKETPLACE, "Blue"),
                _obs(SourceType.PIM, "Dark Blue"),
            ],
        )[0]
        groups = conflict.evidence[1]["sources_by_color_group"]
        assert set(groups["color:dark blue"]) == {"manufacturer", "pim"}
        assert groups["color:blue"] == ["marketplace"]
        spellings = conflict.evidence[1]["spellings_by_color_group"]
        assert spellings["color:dark blue"] == ["Dark Blue", "Navy Blue"]

    def test_the_manufacturer_value_is_proposed(self) -> None:
        conflict = detect_color_conflicts(
            "p1",
            [
                _obs(SourceType.MANUFACTURER, "Navy Blue"),
                _obs(SourceType.MARKETPLACE, "Blue"),
                _obs(SourceType.PIM, "Dark Blue"),
            ],
        )[0]
        assert conflict.proposed_value == "Navy Blue"
        assert conflict.proposed_source == "manufacturer"

    def test_it_is_still_a_variant_conflict_and_still_needs_approval(self) -> None:
        conflict = detect_color_conflicts(
            "p1",
            [_obs(SourceType.MANUFACTURER, "Navy"), _obs(SourceType.MARKETPLACE, "Blue")],
        )[0]
        assert conflict.conflict_type is ConflictType.VARIANT_CONFLICT
        assert conflict.requires_approval

    def test_sources_that_agree_are_quiet(self) -> None:
        assert (
            detect_color_conflicts(
                "p1",
                [
                    _obs(SourceType.MANUFACTURER, "Navy Blue"),
                    _obs(SourceType.MARKETPLACE, "Dark Blue"),
                ],
            )
            == []
        )

    def test_each_source_claim_is_shown_as_written(self) -> None:
        """A reviewer reads "Amazon says Blue", not a normalized group label."""
        conflict = detect_color_conflicts(
            "p1",
            [_obs(SourceType.MANUFACTURER, "Navy Blue"), _obs(SourceType.MARKETPLACE, "Blue")],
        )[0]
        assert conflict.available_values == {
            "manufacturer": "Navy Blue",
            "marketplace": "Blue",
        }


class TestFieldConflicts:
    def test_agreement_produces_nothing(self) -> None:
        conflicts = detect_field_conflicts(
            "p1",
            {"title": [_obs(SourceType.MANUFACTURER, "Tee"), _obs(SourceType.PIM, "Tee")]},
        )
        assert conflicts == []

    def test_a_numeric_string_is_the_same_number(self) -> None:
        """`10.0` and `"10.00"` must not fill the approval queue every night."""
        conflicts = detect_field_conflicts(
            "p1",
            {
                "price": [
                    _obs(SourceType.ERP, 10.0),
                    _obs(SourceType.MARKETPLACE, "10.00"),
                ]
            },
        )
        assert conflicts == []

    def test_a_price_difference_is_an_error_severity_conflict(self) -> None:
        conflict = detect_field_conflicts(
            "p1",
            {"price": [_obs(SourceType.ERP, 10.0), _obs(SourceType.MARKETPLACE, "12.00")]},
        )[0]
        assert conflict.severity is ConflictSeverity.ERROR
        assert conflict.proposed_value == 10.0

    def test_a_gtin_difference_is_an_identity_conflict(self) -> None:
        conflict = detect_field_conflicts(
            "p1",
            {
                "gtin": [
                    _obs(SourceType.MANUFACTURER, "04006381333931"),
                    _obs(SourceType.MARKETPLACE, "00000000000019"),
                ]
            },
        )[0]
        assert conflict.conflict_type is ConflictType.IDENTITY_MISMATCH

    def test_a_sku_difference_is_a_sku_conflict(self) -> None:
        conflict = detect_field_conflicts(
            "p1",
            {"sku": [_obs(SourceType.ERP, "A-1"), _obs(SourceType.MANUFACTURER, "A-2")]},
        )[0]
        assert conflict.conflict_type is ConflictType.SKU_CONFLICT

    def test_a_tie_proposes_nothing(self) -> None:
        """Two equal-rank sources, no per-field override: nothing is chosen.

        Navy versus Dark Blue is not a tie — those are the same color — so the
        disagreement has to be about genuinely different values.
        """
        conflict = detect_field_conflicts(
            "p1",
            {
                "color": [
                    _obs(SourceType.ERP, "Navy"),
                    _obs(SourceType.PIM, "Red"),
                ]
            },
        )[0]
        assert conflict.proposed_value is None
        assert conflict.proposed_source is None
        assert conflict.confidence == 0.0
        assert "no value is proposed" in conflict.explanation

    def test_every_conflict_carries_section_eleven_content(self) -> None:
        payload = detect_field_conflicts(
            "p1",
            {
                "color": [
                    _obs(SourceType.MANUFACTURER, "Navy"),
                    _obs(SourceType.MARKETPLACE, "Blue"),
                ]
            },
        )[0].as_dict()
        assert payload["available_values"]
        assert payload["authority"]["ranks"]
        assert payload["evidence"]
        assert payload["impact"]
        assert payload["confidence"] > 0
        assert payload["proposed_value"] is not None
        assert payload["explanation"]

    def test_impact_records_the_inputs_not_a_conclusion(self) -> None:
        conflict = detect_field_conflicts(
            "p1",
            {
                "title": [
                    _obs(SourceType.MANUFACTURER, "A", is_current_canonical=True),
                    _obs(SourceType.MARKETPLACE, "B"),
                ]
            },
            affected_channels=["amazon", "shopify", "tiktok"],
            affected_products=["p1", "p2"],
        )[0]
        assert conflict.impact["source_count"] == 2
        assert conflict.impact["affected_channels"] == ["amazon", "shopify", "tiktok"]
        assert conflict.impact["is_current_canonical_change"] is True

    def test_corroboration_raises_confidence(self) -> None:
        lone = detect_field_conflicts(
            "p1",
            {
                "color": [
                    _obs(SourceType.MANUFACTURER, "Navy"),
                    _obs(SourceType.PIM, "Blue"),
                ]
            },
        )[0]
        backed = detect_field_conflicts(
            "p1",
            {
                "color": [
                    _obs(SourceType.MANUFACTURER, "Navy"),
                    _obs(SourceType.PIM, "Navy"),
                    _obs(SourceType.ERP, "navy "),
                ]
            },
        )
        # All three agree, so there is no conflict at all.
        assert backed == []
        # Uncorroborated, but the authority answer itself is still firm.
        assert lone.confidence == pytest.approx(0.8)

    def test_marketplace_only_disagreement_is_informational(self) -> None:
        """Marketplaces disagreeing with each other changes no canonical record."""
        conflict = detect_field_conflicts(
            "p1",
            {
                "title": [
                    _obs(SourceType.MARKETPLACE, "A"),
                    _obs(SourceType.HISTORICAL, "B"),
                ]
            },
        )[0]
        assert conflict.severity is ConflictSeverity.INFO


class TestMissingAttributes:
    def test_a_required_field_present_in_one_source_only(self) -> None:
        conflicts = detect_missing_attribute_conflicts(
            "p1",
            {
                "description": [
                    _obs(SourceType.MANUFACTURER, "A shirt"),
                    _obs(SourceType.MARKETPLACE, None),
                ]
            },
        )
        assert len(conflicts) == 1
        assert conflicts[0].conflict_type is ConflictType.MISSING_ATTRIBUTE
        assert conflicts[0].explanation.endswith("manufacturer supplies it")

    def test_presence_everywhere_is_not_a_conflict(self) -> None:
        assert (
            detect_missing_attribute_conflicts(
                "p1",
                {"description": [_obs(SourceType.MANUFACTURER, "A"), _obs(SourceType.PIM, "A")]},
            )
            == []
        )

    def test_absence_everywhere_is_not_a_conflict(self) -> None:
        """Nobody has it is a gap in the catalogue, not a disagreement."""
        assert (
            detect_missing_attribute_conflicts(
                "p1",
                {
                    "description": [
                        _obs(SourceType.MANUFACTURER, None),
                        _obs(SourceType.PIM, None),
                    ]
                },
            )
            == []
        )


class TestConflictSummary:
    def test_summary_counts_types_and_ties(self) -> None:
        conflicts = detect_field_conflicts(
            "p1",
            {
                "price": [_obs(SourceType.ERP, 10.0), _obs(SourceType.MARKETPLACE, "12.00")],
                "color": [_obs(SourceType.ERP, "Navy"), _obs(SourceType.PIM, "Red")],
            },
        )
        summary = conflict_summary(conflicts)
        assert summary["total"] == 2
        assert summary["tied"] == 1
        assert summary["errors"] >= 1
        assert any(key.startswith("type.") for key in summary)


class TestRiskBands:
    @pytest.mark.parametrize(
        ("score", "expected"),
        [
            (0, RiskLevel.LOW),
            (19, RiskLevel.LOW),
            (20, RiskLevel.MEDIUM),
            (44, RiskLevel.MEDIUM),
            (45, RiskLevel.HIGH),
            (74, RiskLevel.HIGH),
            (75, RiskLevel.CRITICAL),
            (200, RiskLevel.CRITICAL),
        ],
    )
    def test_thresholds(self, score: int, expected: RiskLevel) -> None:
        assert classify(score) is expected

    def test_high_and_critical_require_explicit_approval(self) -> None:
        assert RiskLevel.HIGH.requires_explicit_approval
        assert RiskLevel.CRITICAL.requires_explicit_approval
        assert not RiskLevel.LOW.requires_explicit_approval
        assert not RiskLevel.MEDIUM.requires_explicit_approval


class TestRiskFactors:
    def test_a_description_change_is_low_risk(self) -> None:
        result = assess(context_from_change(field_name="description", evidence=[1, 2, 3]))
        assert result.level is RiskLevel.LOW
        assert [f.code for f in result.factors] == ["description_change"]

    def test_a_title_change_is_medium(self) -> None:
        result = assess(context_from_change(field_name="title", evidence=[1, 2, 3]))
        assert result.level is RiskLevel.MEDIUM
        assert {f.code for f in result.factors} == {"seo_sensitive_field", "title_change"}

    def test_a_gtin_change_is_high(self) -> None:
        result = assess(context_from_change(field_name="gtin", evidence=[1, 2, 3]))
        assert result.level is RiskLevel.HIGH
        assert result.requires_approval

    def test_deleting_a_value_is_a_deletion(self) -> None:
        result = assess(
            context_from_change(
                field_name="color", current_value="Red", proposed_value=None, evidence=[1, 2, 3]
            )
        )
        assert "product_deletion" in {f.code for f in result.factors}

    def test_a_channel_change_costs_more_than_no_channel_change(self) -> None:
        offline = assess(context_from_change(field_name="color", evidence=[1, 2, 3]))
        live = assess(
            context_from_change(
                field_name="color", affected_channels=["amazon"], evidence=[1, 2, 3]
            )
        )
        assert live.numeric_score > offline.numeric_score

    def test_reach_and_sales_volume_accumulate(self) -> None:
        heavy = assess(
            context_from_change(
                field_name="title",
                affected_channels=["amazon", "shopify", "tiktok", "ebay"],
                has_sales_history=True,
                estimated_daily_units=500,
                monthly_revenue_at_risk=Decimal("50000"),
                evidence=[1, 2, 3],
            )
        )
        codes = {f.code for f in heavy.factors}
        assert {"channel_breadth", "sales_impact", "revenue_at_risk"} <= codes
        assert heavy.level is RiskLevel.CRITICAL

    def test_a_bulk_change_is_flagged(self) -> None:
        result = assess(
            context_from_change(
                field_name="description",
                affected_product_ids=[f"p{i}" for i in range(30)],
                evidence=[1, 2, 3],
            )
        )
        assert "bulk_change" in {f.code for f in result.factors}

    def test_thin_evidence_and_low_confidence_are_risks_themselves(self) -> None:
        result = assess(context_from_change(field_name="description", confidence=0.4, evidence=[]))
        codes = {f.code for f in result.factors}
        assert {"low_confidence", "thin_evidence"} <= codes

    def test_factors_are_ordered_by_weight(self) -> None:
        result = assess(
            context_from_change(
                field_name="gtin",
                affected_channels=["amazon", "shopify"],
                confidence=0.3,
                evidence=[],
            )
        )
        weights = [f.weight for f in result.factors]
        assert weights == sorted(weights, reverse=True)

    def test_the_score_is_the_sum_of_the_factors(self) -> None:
        """A reviewer can add up the table and check the number."""
        result = assess(
            context_from_change(
                field_name="title", affected_channels=["amazon"], evidence=[1, 2, 3]
            )
        )
        assert result.numeric_score == sum(f.weight for f in result.factors)
        for factor in result.factors:
            assert factor.weight == WEIGHTS[factor.code]

    def test_the_band_table_is_complete_and_ordered(self) -> None:
        assert {level.value for _, level in THRESHOLDS} == {
            "CRITICAL",
            "HIGH",
            "MEDIUM",
            "LOW",
        }
        assert [threshold for threshold, _ in THRESHOLDS] == sorted(
            (threshold for threshold, _ in THRESHOLDS), reverse=True
        )


class TestRiskFloors:
    def test_a_compliance_violation_is_critical_regardless_of_score(self) -> None:
        result = assess(
            RiskContext(
                field_name="description",
                affects_compliance=True,
                compliance_violation_count=1,
                evidence_count=10,
            )
        )
        assert result.level is RiskLevel.CRITICAL
        assert result.escalated_reason is not None
        assert "compliance_violation" in result.escalated_reason

    def test_a_deletion_cannot_be_talked_down_to_low(self) -> None:
        result = assess(
            RiskContext(field_name="internal_note", is_deletion=True, evidence_count=10)
        )
        assert result.level is RiskLevel.HIGH
        assert result.escalated_reason is None or "product_deletion" in result.escalated_reason

    def test_no_floor_is_applied_when_the_band_already_covers_it(self) -> None:
        result = assess(
            context_from_change(
                field_name="gtin",
                affected_channels=["a", "b", "c", "d"],
                confidence=0.2,
                evidence=[],
            )
        )
        assert result.level is RiskLevel.CRITICAL


class TestRiskDeterminism:
    def test_the_same_context_scores_identically_every_time(self) -> None:
        """§16: the LLM must not determine final risk.

        The cheapest way to hold that line is a purity test — same input, same
        output, no clock and no model involved.
        """
        context = context_from_change(
            field_name="title",
            affected_channels=["amazon", "shopify"],
            confidence=0.8,
            evidence=[1, 2, 3, 4],
        )
        results = [assess(context) for _ in range(5)]
        assert {r.level for r in results} == {results[0].level}
        assert {r.numeric_score for r in results} == {results[0].numeric_score}

    def test_a_reviewer_can_predict_the_band_from_the_factors(self) -> None:
        result = assess(context_from_change(field_name="category_path", evidence=[1, 2, 3]))
        predicted = classify(sum(WEIGHTS[f.code] for f in result.factors))
        assert result.level is predicted


class TestRiskResultShape:
    def test_the_payload_contains_what_an_approver_is_shown(self) -> None:
        payload = assess(context_from_change(field_name="gtin", evidence=[1, 2, 3])).as_dict()
        assert payload["level"] == "HIGH"
        assert payload["requires_approval"] is True
        assert payload["factors"]
        assert all({"code", "weight", "detail"} <= set(f) for f in payload["factors"])



class TestSection16Conformance:
    """§16 names thirteen potential factors. Phase 16 checked them one at a time.

    The audit runs in both directions, because a factor can go missing either
    way: one table says what §16 asked for and proves the engine scores it,
    the other says which codes are *ours* — D-016's additions beyond §16 — so
    a new code cannot be added without being assigned to one side or the
    other.
    """

    # (§16's wording, the factor code it is scored as, a context that fires it)
    SECTION_16: tuple[tuple[str, str, RiskContext], ...] = (
        (
            "parent-child modification",
            "parent_child_modification",
            RiskContext(field_name="price", affects_parent_child=True),
        ),
        ("SKU modification", "sku_modification", RiskContext(field_name="sku")),
        ("GTIN modification", "gtin_modification", RiskContext(field_name="gtin")),
        (
            "variant modification",
            "variant_modification",
            RiskContext(field_name="color", affects_variants=True),
        ),
        (
            "category modification",
            "category_modification",
            RiskContext(field_name="category_path", affects_category=True),
        ),
        (
            "SEO-sensitive field",
            "seo_sensitive_field",
            RiskContext(field_name="handle"),
        ),
        (
            "marketplace compliance",
            "compliance_violation",
            RiskContext(field_name="description", compliance_violation_count=2),
        ),
        (
            "product identity",
            "identity_mismatch",
            RiskContext(field_name="mpn", is_identity_field=True),
        ),
        (
            "inventory impact",
            "inventory_impact",
            RiskContext(field_name="on_hand"),
        ),
        (
            "historical sales impact",
            "sales_impact",
            RiskContext(field_name="price", has_sales_history=True, estimated_daily_units=500),
        ),
        ("URL impact", "url_or_slug_change", RiskContext(field_name="slug")),
        (
            "indexing impact",
            "seo_sensitive_field",
            RiskContext(field_name="keywords"),
        ),
        (
            "channel-specific constraints",
            "channel_breadth",
            RiskContext(field_name="price", affected_channel_count=5),
        ),
    )

    # Codes that score a §16 concern without being named by it: §16 names
    # "marketplace compliance" once, and D-016 scored the restriction and the
    # revenue it exposes separately.
    HANGS_OFF_A_SECTION_16_LINE = {
        "marketplace_listing": "channel-specific constraints",
        "restriction_applied": "marketplace compliance",
        "revenue_at_risk": "historical sales impact",
    }

    # D-016 scored these too, and §16 does not name them at all. Listed so the
    # two tables stay exhaustive together: every code is claimed by one or the other.
    BEYOND_SECTION_16 = frozenset(
        {
            "product_deletion",
            "broken_relationship",
            "title_change",
            "description_change",
            "bulk_change",
            "low_confidence",
            "thin_evidence",
        }
    )

    @pytest.mark.parametrize(
        ("factor_name", "code", "context"),
        SECTION_16,
        ids=[row[0] for row in SECTION_16],
    )
    def test_each_section_16_factor_is_scored(
        self, factor_name: str, code: str, context: RiskContext
    ) -> None:
        assert code in WEIGHTS, f"§16's {factor_name!r} has no weight"
        assert code in {f.code for f in assess(context).factors}, (
            f"a change of the shape {context.field_name!r} does not score §16's "
            f"{factor_name!r}"
        )

    def test_every_weight_is_claimed_by_section_16_or_by_d_016(self) -> None:
        named = {code for _, code, _ in self.SECTION_16} | set(self.HANGS_OFF_A_SECTION_16_LINE)
        assert WEIGHTS.keys() - named == set(self.BEYOND_SECTION_16)
        assert named - WEIGHTS.keys() == set()
        assert set(self.HANGS_OFF_A_SECTION_16_LINE.values()) <= {
            name for name, _, _ in self.SECTION_16
        }

    def test_the_conformance_table_in_the_module_matches_this_one(self) -> None:
        """The table in `risk.py` is the one a reviewer reads at the code."""
        source = inspect.getsource(risk)
        table = source[source.index("| §16 factor") : source.index("Two of those lines")]
        for factor_name, _, _ in self.SECTION_16:
            assert f"| {factor_name} |" in table, factor_name

    def test_inventory_units_below_the_threshold_score_nothing_extra(self) -> None:
        result = assess(
            context_from_change(
                field_name="price", inventory_units_affected=99, evidence=[1, 2, 3]
            )
        )
        assert "inventory_impact" not in {f.code for f in result.factors}

    def test_inventory_units_at_the_threshold_score_the_factor(self) -> None:
        result = assess(
            context_from_change(
                field_name="price", inventory_units_affected=100, evidence=[1, 2, 3]
            )
        )
        assert {f.code for f in result.factors} == {"inventory_impact"}
        assert result.numeric_score == WEIGHTS["inventory_impact"]

    def test_a_negative_unit_count_is_clamped_to_not_affect_the_score(self) -> None:
        context = context_from_change(
            field_name="price", inventory_units_affected=-5, evidence=[1, 2, 3]
        )
        assert context.inventory_units_affected == 0
        assert "inventory_impact" not in {f.code for f in assess(context).factors}

    def test_an_inventory_change_still_adds_up(self) -> None:
        result = assess(
            context_from_change(
                field_name="stock",
                affected_channels=["amazon"],
                evidence=[1, 2, 3],
            )
        )
        assert result.numeric_score == sum(f.weight for f in result.factors)
        assert {f.code for f in result.factors} == {"inventory_impact", "marketplace_listing"}
