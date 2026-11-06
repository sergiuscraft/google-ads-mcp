# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tests for bidding strategy goals (target CPA / ROAS, max CPC limit)."""

import json
import unittest
from unittest.mock import MagicMock

from fastmcp.exceptions import ToolError

from ads_mcp.tools import campaigns
from tests.tools.write_tools_test import WriteToolsBase


def _req(svc, method):
    return getattr(svc, method).call_args.kwargs["request"]


class UpdateGoalsTests(WriteToolsBase):
    def _update(self, **kwargs):
        svc = self._get_service("CampaignService")
        proposal, _ = self.propose_then_commit(
            campaigns.update_campaign,
            customer_id="1",
            campaign_id="5",
            **kwargs,
        )
        return proposal, _req(svc, "mutate_campaigns").operations[0]

    def test_target_cpa(self):
        proposal, op = self._update(
            bidding_strategy="MAXIMIZE_CONVERSIONS", target_cpa=12.5
        )
        self.assertEqual(op.update.maximize_conversions.target_cpa_micros, 12_500_000)
        self.assertEqual(
            list(op.update_mask.paths), ["maximize_conversions.target_cpa_micros"]
        )
        fields = {i["field_readable"]: i["value_readable"] for i in proposal["change_items"]}
        self.assertEqual(fields["Target CPA"], "12.5")

    def test_target_roas(self):
        _, op = self._update(
            bidding_strategy="MAXIMIZE_CONVERSION_VALUE", target_roas=4.0
        )
        self.assertAlmostEqual(
            op.update.maximize_conversion_value.target_roas, 4.0, places=5
        )
        self.assertEqual(
            list(op.update_mask.paths), ["maximize_conversion_value.target_roas"]
        )

    def test_roas_without_goal_still_sets_strategy(self):
        _, op = self._update(bidding_strategy="MAXIMIZE_CONVERSION_VALUE")
        self.assertIn("maximize_conversion_value", op.update)

    def test_max_cpc_limit(self):
        _, op = self._update(bidding_strategy="MAXIMIZE_CLICKS", max_cpc_limit=1.5)
        self.assertEqual(op.update.target_spend.cpc_bid_ceiling_micros, 1_500_000)
        self.assertEqual(
            list(op.update_mask.paths), ["target_spend.cpc_bid_ceiling_micros"]
        )

    def test_plain_strategies_unchanged(self):
        _, op = self._update(bidding_strategy="MAXIMIZE_CONVERSIONS")
        self.assertEqual(op.update.maximize_conversions.target_cpa_micros, 0)

    def test_goal_requires_its_strategy(self):
        bad = [
            {"target_cpa": 5},  # no strategy at all
            {"bidding_strategy": "MAXIMIZE_CLICKS", "target_cpa": 5},
            {"bidding_strategy": "MAXIMIZE_CONVERSIONS", "target_roas": 3},
            {"bidding_strategy": "MAXIMIZE_CONVERSION_VALUE", "max_cpc_limit": 1},
            {"bidding_strategy": "MANUAL_CPC", "target_cpa": 5},
        ]
        for kwargs in bad:
            with self.assertRaises(ToolError, msg=str(kwargs)):
                campaigns.update_campaign(customer_id="1", campaign_id="5", **kwargs)

    def test_goal_ranges(self):
        for kwargs in (
            {"bidding_strategy": "MAXIMIZE_CONVERSIONS", "target_cpa": 0},
            {"bidding_strategy": "MAXIMIZE_CONVERSIONS", "target_cpa": -3},
            {"bidding_strategy": "MAXIMIZE_CONVERSION_VALUE", "target_roas": 0},
            {"bidding_strategy": "MAXIMIZE_CONVERSION_VALUE", "target_roas": 1001},
            {"bidding_strategy": "MAXIMIZE_CLICKS", "max_cpc_limit": 0},
        ):
            with self.assertRaises(ToolError, msg=str(kwargs)):
                campaigns.update_campaign(customer_id="1", campaign_id="5", **kwargs)

    def test_changed_goal_after_approval_is_rejected(self):
        proposal = json.loads(
            campaigns.update_campaign(
                customer_id="1",
                campaign_id="5",
                bidding_strategy="MAXIMIZE_CONVERSIONS",
                target_cpa=10,
            )
        )
        with self.assertRaises(ToolError):
            campaigns.update_campaign(
                customer_id="1",
                campaign_id="5",
                bidding_strategy="MAXIMIZE_CONVERSIONS",
                target_cpa=1,  # tighter than approved
                validate_only=False,
                checksum=proposal["checksum"],
            )


class CreateGoalsTests(WriteToolsBase):
    def _create(self, **kwargs):
        svc = self._get_service("GoogleAdsService")
        response = MagicMock()
        budget_r, camp_r = MagicMock(), MagicMock()
        camp_r.campaign_result.resource_name = "customers/1/campaigns/7"
        response.mutate_operation_responses = [budget_r, camp_r]
        svc.mutate.return_value = response
        self.propose_then_commit(
            campaigns.create_campaign,
            customer_id="1",
            name="C",
            daily_budget=10,
            **kwargs,
        )
        return _req(svc, "mutate").mutate_operations[1].campaign_operation.create

    def test_create_with_target_cpa(self):
        camp = self._create(bidding_strategy="MAXIMIZE_CONVERSIONS", target_cpa=8)
        self.assertEqual(camp.maximize_conversions.target_cpa_micros, 8_000_000)

    def test_create_with_target_roas(self):
        camp = self._create(
            bidding_strategy="MAXIMIZE_CONVERSION_VALUE", target_roas=2.5
        )
        self.assertAlmostEqual(
            camp.maximize_conversion_value.target_roas, 2.5, places=5
        )

    def test_create_with_click_limit(self):
        camp = self._create(bidding_strategy="MAXIMIZE_CLICKS", max_cpc_limit=0.9)
        self.assertEqual(camp.target_spend.cpc_bid_ceiling_micros, 900_000)

    def test_create_default_has_no_goal(self):
        camp = self._create()
        self.assertEqual(camp.target_spend.cpc_bid_ceiling_micros, 0)

    def test_create_goal_mismatch(self):
        with self.assertRaises(ToolError):
            campaigns.create_campaign(
                customer_id="1", name="C", daily_budget=10, target_cpa=5
            )


if __name__ == "__main__":
    unittest.main()
