# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tests for the update/remove, asset link, targeting, research and conversion tools."""

import json
import unittest
from unittest.mock import MagicMock

from fastmcp.exceptions import ToolError

from ads_mcp.tools import (
    ads,
    asset_links,
    campaigns,
    conversions,
    keywords,
    targeting,
)
from tests.tools.write_tools_test import WriteToolsBase, _resp


def _req(svc, method):
    return getattr(svc, method).call_args.kwargs["request"]


class ManageAdsAndKeywordsTests(WriteToolsBase):
    def test_update_ad_status(self):
        svc = self._get_service("AdGroupAdService")
        _, result = self.propose_then_commit(
            ads.update_ad_group_ad,
            customer_id="1",
            ad_group_id="2",
            ad_id="3",
            status="ENABLED",
        )
        op = _req(svc, "mutate_ad_group_ads").operations[0]
        self.assertEqual(op.update.resource_name, "customers/1/adGroupAds/2~3")
        self.assertEqual(list(op.update_mask.paths), ["status"])
        self.assertIn("ENABLED", result)

    def test_remove_ad(self):
        svc = self._get_service("AdGroupAdService")
        self.propose_then_commit(
            ads.remove_ad_group_ad, customer_id="1", ad_group_id="2", ad_id="3"
        )
        self.assertEqual(
            _req(svc, "mutate_ad_group_ads").operations[0].remove,
            "customers/1/adGroupAds/2~3",
        )

    def test_update_keyword_mask(self):
        svc = self._get_service("AdGroupCriterionService")
        self.propose_then_commit(
            keywords.update_keyword,
            customer_id="1",
            ad_group_id="2",
            criterion_id="9",
            status="PAUSED",
            cpc_bid=0.3,
        )
        op = _req(svc, "mutate_ad_group_criteria").operations[0]
        self.assertEqual(
            op.update.resource_name, "customers/1/adGroupCriteria/2~9"
        )
        self.assertEqual(sorted(op.update_mask.paths), ["cpc_bid_micros", "status"])
        self.assertEqual(op.update.cpc_bid_micros, 300_000)

    def test_update_keyword_needs_a_field(self):
        with self.assertRaises(ToolError):
            keywords.update_keyword(
                customer_id="1", ad_group_id="2", criterion_id="9"
            )

    def test_remove_keywords(self):
        svc = self._get_service("AdGroupCriterionService")
        svc.mutate_ad_group_criteria.return_value = _resp("a", "b")
        _, result = self.propose_then_commit(
            keywords.remove_keywords,
            customer_id="1",
            ad_group_id="2",
            criterion_ids=[7, "8"],
        )
        ops = _req(svc, "mutate_ad_group_criteria").operations
        self.assertEqual(
            [o.remove for o in ops],
            ["customers/1/adGroupCriteria/2~7", "customers/1/adGroupCriteria/2~8"],
        )
        self.assertIn("Removed 2", result)

    def test_remove_keywords_rejects_bad_ids(self):
        for bad in ([], ["x"], list(range(101))):
            with self.assertRaises(ToolError):
                keywords.remove_keywords(
                    customer_id="1", ad_group_id="2", criterion_ids=bad
                )


class UpdateCampaignTests(WriteToolsBase):
    def test_masks_per_field(self):
        svc = self._get_service("CampaignService")
        self.propose_then_commit(
            campaigns.update_campaign,
            customer_id="1",
            campaign_id="5",
            name=" New ",
            bidding_strategy="MAXIMIZE_CLICKS",
            include_search_partners=True,
        )
        op = _req(svc, "mutate_campaigns").operations[0]
        self.assertEqual(op.update.name, "New")
        self.assertEqual(
            sorted(op.update_mask.paths),
            ["name", "network_settings.target_search_network", "target_spend.cpc_bid_ceiling_micros"],
        )
        self.assertIn("target_spend", op.update)

    def test_manual_cpc_and_conversions(self):
        svc = self._get_service("CampaignService")
        for strategy, path in (
            ("MANUAL_CPC", "manual_cpc.enhanced_cpc_enabled"),
            ("MAXIMIZE_CONVERSIONS", "maximize_conversions.target_cpa_micros"),
        ):
            self.propose_then_commit(
                campaigns.update_campaign,
                customer_id="1",
                campaign_id="5",
                bidding_strategy=strategy,
            )
            op = _req(svc, "mutate_campaigns").operations[0]
            self.assertEqual(list(op.update_mask.paths), [path])

    def test_requires_a_field(self):
        with self.assertRaises(ToolError):
            campaigns.update_campaign(customer_id="1", campaign_id="5")


class AssetLinkTests(WriteToolsBase):
    def test_link_campaign_level(self):
        svc = self._get_service("CampaignAssetService")
        self.propose_then_commit(
            asset_links.link_asset,
            customer_id="1",
            asset_id="77",
            level="CAMPAIGN",
            field_type="SITELINK",
            target_id="5",
        )
        op = _req(svc, "mutate_campaign_assets").operations[0]
        self.assertEqual(op.create.campaign, "customers/1/campaigns/5")
        self.assertEqual(op.create.asset, "customers/1/assets/77")
        self.assertEqual(op.create.field_type.name, "SITELINK")

    def test_link_customer_level_needs_no_target(self):
        svc = self._get_service("CustomerAssetService")
        self.propose_then_commit(
            asset_links.link_asset,
            customer_id="1",
            asset_id="77",
            level="CUSTOMER",
            field_type="CALLOUT",
        )
        op = _req(svc, "mutate_customer_assets").operations[0]
        self.assertEqual(op.create.asset, "customers/1/assets/77")

    def test_campaign_level_requires_target(self):
        with self.assertRaises(ToolError):
            asset_links.link_asset(
                customer_id="1", asset_id="77", level="CAMPAIGN", field_type="SITELINK"
            )

    def test_unlink_resource_names(self):
        cases = [
            ("CAMPAIGN", "5", "CampaignAssetService", "mutate_campaign_assets",
             "customers/1/campaignAssets/5~77~SITELINK"),
            ("AD_GROUP", "6", "AdGroupAssetService", "mutate_ad_group_assets",
             "customers/1/adGroupAssets/6~77~SITELINK"),
            ("CUSTOMER", None, "CustomerAssetService", "mutate_customer_assets",
             "customers/1/customerAssets/77~SITELINK"),
        ]
        for level, target, svc_name, method, expected in cases:
            svc = self._get_service(svc_name)
            self.propose_then_commit(
                asset_links.unlink_asset,
                customer_id="1",
                asset_id="77",
                level=level,
                field_type="SITELINK",
                target_id=target,
            )
            self.assertEqual(_req(svc, method).operations[0].remove, expected)


class TargetingTests(WriteToolsBase):
    def test_suggest_geo_targets(self):
        svc = self._get_service("GeoTargetConstantService")
        s = MagicMock()
        s.geo_target_constant.id = 2804
        s.geo_target_constant.name = "Ukraine"
        s.geo_target_constant.canonical_name = "Ukraine"
        s.geo_target_constant.target_type = "Country"
        s.geo_target_constant.country_code = "UA"
        s.reach = 1000
        s.search_term = "ukraine"
        svc.suggest_geo_target_constants.return_value.geo_target_constant_suggestions = [s]
        out = targeting.suggest_geo_targets(
            customer_id="1", location_names=["Ukraine"], country_code="ua"
        )
        self.assertEqual(out[0]["id"], 2804)
        req = _req(svc, "suggest_geo_target_constants")
        self.assertEqual(req.country_code, "UA")
        self.assertEqual(list(req.location_names.names), ["Ukraine"])

    def test_add_locations_and_exclude(self):
        svc = self._get_service("CampaignCriterionService")
        svc.mutate_campaign_criteria.return_value = _resp("x")
        self.propose_then_commit(
            targeting.add_campaign_locations,
            customer_id="1",
            campaign_id="5",
            geo_target_ids=[2804],
            exclude=True,
        )
        crit = _req(svc, "mutate_campaign_criteria").operations[0].create
        self.assertEqual(crit.location.geo_target_constant, "geoTargetConstants/2804")
        self.assertTrue(crit.negative)

    def test_add_languages(self):
        svc = self._get_service("CampaignCriterionService")
        svc.mutate_campaign_criteria.return_value = _resp("x", "y")
        self.propose_then_commit(
            targeting.add_campaign_languages,
            customer_id="1",
            campaign_id="5",
            language_ids=[1031, 1036],
        )
        ops = _req(svc, "mutate_campaign_criteria").operations
        self.assertEqual(
            [o.create.language.language_constant for o in ops],
            ["languageConstants/1031", "languageConstants/1036"],
        )
        self.assertFalse(any(o.create.negative for o in ops))

    def test_remove_campaign_criteria(self):
        svc = self._get_service("CampaignCriterionService")
        svc.mutate_campaign_criteria.return_value = _resp("x")
        self.propose_then_commit(
            targeting.remove_campaign_criteria,
            customer_id="1",
            campaign_id="5",
            criterion_ids=[2804],
        )
        self.assertEqual(
            _req(svc, "mutate_campaign_criteria").operations[0].remove,
            "customers/1/campaignCriteria/5~2804",
        )


class ConversionTests(WriteToolsBase):
    def test_create(self):
        svc = self._get_service("ConversionActionService")
        svc.mutate_conversion_actions.return_value = _resp(
            "customers/1/conversionActions/9"
        )
        _, result = self.propose_then_commit(
            conversions.create_conversion_action,
            customer_id="1",
            name="Lead form",
            category="SUBMIT_LEAD_FORM",
            default_value=15,
            currency_code="uah",
        )
        action = _req(svc, "mutate_conversion_actions").operations[0].create
        self.assertEqual(action.name, "Lead form")
        self.assertEqual(action.type_.name, "WEBPAGE")
        self.assertEqual(action.category.name, "SUBMIT_LEAD_FORM")
        self.assertEqual(action.counting_type.name, "ONE_PER_CLICK")
        self.assertEqual(action.value_settings.default_currency_code, "UAH")
        self.assertIn("conversionActions/9", result)

    def test_value_needs_currency(self):
        with self.assertRaises(ToolError):
            conversions.create_conversion_action(
                customer_id="1", name="x", default_value=5
            )

    def test_update_mask(self):
        svc = self._get_service("ConversionActionService")
        self.propose_then_commit(
            conversions.update_conversion_action,
            customer_id="1",
            conversion_action_id="9",
            status="HIDDEN",
            primary_for_goal=False,
        )
        op = _req(svc, "mutate_conversion_actions").operations[0]
        self.assertEqual(
            sorted(op.update_mask.paths), ["primary_for_goal", "status"]
        )
        self.assertEqual(op.update.resource_name, "customers/1/conversionActions/9")


class ChecksumBindingTests(WriteToolsBase):
    def test_changed_target_is_rejected(self):
        proposal = json.loads(
            asset_links.link_asset(
                customer_id="1",
                asset_id="77",
                level="CAMPAIGN",
                field_type="SITELINK",
                target_id="5",
            )
        )
        with self.assertRaises(ToolError):
            asset_links.link_asset(
                customer_id="1",
                asset_id="77",
                level="CAMPAIGN",
                field_type="SITELINK",
                target_id="6",  # different campaign than approved
                validate_only=False,
                checksum=proposal["checksum"],
            )


if __name__ == "__main__":
    unittest.main()
