# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tests for the campaign, ad group, keyword and ad write tools."""

import json
import unittest
from unittest.mock import MagicMock, create_autospec, patch

from fastmcp.exceptions import ToolError
from google.ads.googleads.client import GoogleAdsClient
from google.auth.credentials import AnonymousCredentials

from ads_mcp.tools import ad_groups, ads, campaigns, keywords


def _resp(*resource_names):
    resp = MagicMock()
    resp.results = [MagicMock(resource_name=rn) for rn in resource_names]
    return resp


class WriteToolsBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.real = GoogleAdsClient(
            credentials=AnonymousCredentials(),
            use_proto_plus=True,
        )

    def setUp(self):
        self.services: dict[str, MagicMock] = {}
        self.client = MagicMock()
        self.client.get_type.side_effect = self.real.get_type
        self.client.copy_from.side_effect = self.real.copy_from
        self.client.enums = self.real.enums
        self.client.get_service.side_effect = self._get_service
        patcher = patch(
            "ads_mcp.utils.get_googleads_client", return_value=self.client
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def _get_service(self, name, **kwargs):
        # Autospec'd against the real generated client, so a call with an
        # argument the real method does not accept (e.g. a flattened
        # ``validate_only``) fails here instead of only against the live API.
        if name not in self.services:
            real_service = self.real.get_service(name)
            self.services[name] = create_autospec(
                type(real_service), instance=True
            )
        return self.services[name]

    def propose_then_commit(self, fn, **kwargs):
        """Runs the two-phase flow; returns (proposal_dict, commit_result)."""
        proposal = json.loads(fn(**kwargs))
        self.assertEqual(proposal["status"], "PROPOSED")
        result = fn(
            **kwargs, validate_only=False, checksum=proposal["checksum"]
        )
        return proposal, result


class CampaignTests(WriteToolsBase):
    def test_create_campaign_flow(self):
        svc = self._get_service("GoogleAdsService")
        mutate_resp = MagicMock()
        budget_r, camp_r = MagicMock(), MagicMock()
        camp_r.campaign_result.resource_name = "customers/1/campaigns/777"
        mutate_resp.mutate_operation_responses = [budget_r, camp_r]
        svc.mutate.return_value = mutate_resp

        proposal, result = self.propose_then_commit(
            campaigns.create_campaign,
            customer_id="123-456-7890",
            name="Test",
            daily_budget=12.5,
        )

        self.assertIn("ID: 777", result)
        self.assertEqual(svc.mutate.call_count, 2)
        dry, commit = svc.mutate.call_args_list
        self.assertTrue(dry.kwargs["request"].validate_only)
        self.assertFalse(commit.kwargs["request"].validate_only)
        self.assertEqual(commit.kwargs["request"].customer_id, "1234567890")
        budget_op, camp_op = commit.kwargs["request"].mutate_operations
        budget = budget_op.campaign_budget_operation.create
        self.assertEqual(budget.amount_micros, 12_500_000)
        self.assertFalse(budget.explicitly_shared)
        camp = camp_op.campaign_operation.create
        self.assertEqual(camp.name, "Test")
        self.assertEqual(camp.status.name, "PAUSED")
        self.assertEqual(camp.campaign_budget, budget.resource_name)
        self.assertTrue(camp.network_settings.target_google_search)
        self.assertFalse(camp.network_settings.target_content_network)
        self.assertEqual(
            camp.contains_eu_political_advertising.name,
            "DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING",
        )
        self.assertIn("target_spend", camp)  # maximize clicks

    def test_commit_with_changed_params_is_rejected(self):
        proposal = json.loads(
            campaigns.create_campaign(
                customer_id="1", name="A", daily_budget=5
            )
        )
        with self.assertRaises(ToolError):
            campaigns.create_campaign(
                customer_id="1",
                name="A",
                daily_budget=500,  # changed after approval
                validate_only=False,
                checksum=proposal["checksum"],
            )
        # Only the dry run may have touched the API.
        svc = self.services["GoogleAdsService"]
        self.assertEqual(svc.mutate.call_count, 1)

    def test_commit_without_checksum_is_rejected(self):
        with self.assertRaises(ToolError):
            campaigns.create_campaign(
                customer_id="1", name="A", daily_budget=5, validate_only=False
            )

    def test_invalid_budget(self):
        with self.assertRaises(ToolError):
            campaigns.create_campaign(
                customer_id="1", name="A", daily_budget=0
            )

    def test_set_status(self):
        svc = self._get_service("CampaignService")
        _, result = self.propose_then_commit(
            campaigns.set_campaign_status,
            customer_id="1234567890",
            campaign_id="42",
            status="ENABLED",
        )
        op = svc.mutate_campaigns.call_args.kwargs["request"].operations[0]
        self.assertEqual(
            op.update.resource_name, "customers/1234567890/campaigns/42"
        )
        self.assertEqual(list(op.update_mask.paths), ["status"])
        self.assertIn("ENABLED", result)

    def test_set_status_rejects_non_numeric_id(self):
        with self.assertRaises(ToolError):
            campaigns.set_campaign_status(
                customer_id="1", campaign_id="1; DROP", status="PAUSED"
            )

    def _budget_row(self, old_micros=10_000_000, refs=1, shared=False):
        row = MagicMock()
        row.campaign.campaign_budget = "customers/1/campaignBudgets/55"
        row.campaign_budget.amount_micros = old_micros
        row.campaign_budget.reference_count = refs
        row.campaign_budget.explicitly_shared = shared
        return row

    def test_update_budget(self):
        self._get_service("GoogleAdsService").search.return_value = [
            self._budget_row()
        ]
        svc = self._get_service("CampaignBudgetService")
        proposal, result = self.propose_then_commit(
            campaigns.update_campaign_budget,
            customer_id="1",
            campaign_id="42",
            daily_budget=20,
        )
        op = svc.mutate_campaign_budgets.call_args.kwargs["request"].operations[0]
        self.assertEqual(
            op.update.resource_name, "customers/1/campaignBudgets/55"
        )
        self.assertEqual(op.update.amount_micros, 20_000_000)
        self.assertEqual(list(op.update_mask.paths), ["amount_micros"])
        self.assertIn("10.00 to 20.00", result)
        self.assertNotIn("WARNING", json.dumps(proposal))

    def test_update_budget_warns_when_shared(self):
        self._get_service("GoogleAdsService").search.return_value = [
            self._budget_row(refs=3, shared=True)
        ]
        proposal = json.loads(
            campaigns.update_campaign_budget(
                customer_id="1", campaign_id="42", daily_budget=20
            )
        )
        self.assertIn("shared by 3 campaigns", json.dumps(proposal))

    def test_update_budget_unknown_campaign(self):
        self._get_service("GoogleAdsService").search.return_value = []
        with self.assertRaises(ToolError):
            campaigns.update_campaign_budget(
                customer_id="1", campaign_id="42", daily_budget=20
            )


class AdGroupTests(WriteToolsBase):
    def test_create_ad_group(self):
        svc = self._get_service("AdGroupService")
        svc.mutate_ad_groups.return_value = _resp("customers/1/adGroups/9")
        _, result = self.propose_then_commit(
            ad_groups.create_ad_group,
            customer_id="1",
            campaign_id="42",
            name="Group",
            cpc_bid=0.75,
        )
        op = svc.mutate_ad_groups.call_args.kwargs["request"].operations[0]
        self.assertEqual(op.create.campaign, "customers/1/campaigns/42")
        self.assertEqual(op.create.cpc_bid_micros, 750_000)
        self.assertEqual(op.create.status.name, "PAUSED")
        self.assertEqual(op.create.type_.name, "SEARCH_STANDARD")
        self.assertIn("ID: 9", result)

    def test_update_requires_something(self):
        with self.assertRaises(ToolError):
            ad_groups.update_ad_group(customer_id="1", ad_group_id="2")

    def test_update_mask_matches_fields(self):
        svc = self._get_service("AdGroupService")
        self.propose_then_commit(
            ad_groups.update_ad_group,
            customer_id="1",
            ad_group_id="2",
            status="PAUSED",
            cpc_bid=1.2,
        )
        op = svc.mutate_ad_groups.call_args.kwargs["request"].operations[0]
        self.assertEqual(
            sorted(op.update_mask.paths), ["cpc_bid_micros", "status"]
        )
        self.assertEqual(op.update.cpc_bid_micros, 1_200_000)


class KeywordTests(WriteToolsBase):
    def test_add_keywords(self):
        svc = self._get_service("AdGroupCriterionService")
        svc.mutate_ad_group_criteria.return_value = _resp("a", "b")
        _, result = self.propose_then_commit(
            keywords.add_keywords,
            customer_id="1",
            ad_group_id="5",
            keywords=["эковата", "утепление"],
            match_type="EXACT",
        )
        ops = svc.mutate_ad_group_criteria.call_args.kwargs["request"].operations
        self.assertEqual(
            [o.create.keyword.text for o in ops], ["эковата", "утепление"]
        )
        self.assertTrue(all(o.create.keyword.match_type.name == "EXACT" for o in ops))
        self.assertFalse(any(o.create.negative for o in ops))
        self.assertIn("Added 2", result)

    def test_duplicate_keywords_rejected(self):
        with self.assertRaises(ToolError):
            keywords.add_keywords(
                customer_id="1", ad_group_id="5", keywords=["a", "A"]
            )

    def test_negative_campaign_level(self):
        svc = self._get_service("CampaignCriterionService")
        svc.mutate_campaign_criteria.return_value = _resp("x")
        self.propose_then_commit(
            keywords.add_negative_keywords,
            customer_id="1",
            campaign_id="42",
            keywords=["free"],
        )
        op = svc.mutate_campaign_criteria.call_args.kwargs["request"].operations[0]
        self.assertTrue(op.create.negative)
        self.assertEqual(op.create.campaign, "customers/1/campaigns/42")

    def test_negative_ad_group_level(self):
        svc = self._get_service("AdGroupCriterionService")
        svc.mutate_ad_group_criteria.return_value = _resp("x")
        self.propose_then_commit(
            keywords.add_negative_keywords,
            customer_id="1",
            ad_group_id="7",
            keywords=["free"],
        )
        op = svc.mutate_ad_group_criteria.call_args.kwargs["request"].operations[0]
        self.assertTrue(op.create.negative)

    def test_negative_needs_exactly_one_target(self):
        for kwargs in ({}, {"campaign_id": "1", "ad_group_id": "2"}):
            with self.assertRaises(ToolError):
                keywords.add_negative_keywords(
                    customer_id="1", keywords=["x"], **kwargs
                )


class ResponsiveSearchAdTests(WriteToolsBase):
    HEADS = ["One", "Two", "Three"]
    DESCS = ["Description one", "Description two"]

    def test_create_rsa(self):
        svc = self._get_service("AdGroupAdService")
        svc.mutate_ad_group_ads.return_value = _resp("customers/1/adGroupAds/1~2")
        _, result = self.propose_then_commit(
            ads.create_responsive_search_ad,
            customer_id="1",
            ad_group_id="5",
            headlines=self.HEADS,
            descriptions=self.DESCS,
            final_url="https://example.com",
            path1="eco",
        )
        op = svc.mutate_ad_group_ads.call_args.kwargs["request"].operations[0]
        rsa = op.create.ad.responsive_search_ad
        self.assertEqual([h.text for h in rsa.headlines], self.HEADS)
        self.assertEqual(rsa.path1, "eco")
        self.assertEqual(list(op.create.ad.final_urls), ["https://example.com"])
        self.assertEqual(op.create.status.name, "PAUSED")
        self.assertIn("PAUSED", result)

    def test_validation(self):
        base = dict(
            customer_id="1",
            ad_group_id="5",
            headlines=self.HEADS,
            descriptions=self.DESCS,
            final_url="https://example.com",
        )
        bad_cases = [
            {"headlines": ["a", "b"]},  # too few
            {"headlines": ["x" * 31, "b", "c"]},  # too long
            {"descriptions": ["only one"]},
            {"final_url": "example.com"},
            {"path2": "x"},  # path2 without path1
        ]
        for override in bad_cases:
            with self.assertRaises(ToolError, msg=str(override)):
                ads.create_responsive_search_ad(**{**base, **override})


if __name__ == "__main__":
    unittest.main()
