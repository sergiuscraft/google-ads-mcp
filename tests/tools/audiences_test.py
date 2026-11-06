# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tests for the audience tools."""

import json
import unittest
from unittest.mock import MagicMock

from fastmcp.exceptions import ToolError

from ads_mcp.tools import audiences, scheduling
from tests.tools.write_tools_test import WriteToolsBase, _resp


def _req(svc, method):
    return getattr(svc, method).call_args.kwargs["request"]


class AudienceBase(WriteToolsBase):
    def user_list(self, list_id, name="Visitors", ltype="RULE_BASED", size=4800):
        u = MagicMock()
        u.id = list_id
        u.name = name
        u.type_.name = ltype
        u.membership_status.name = "OPEN"
        u.size_for_search = size
        row = MagicMock()
        row.user_list = u
        return row

    def settings_row(self, level, restrictions):
        """restrictions: list of (dimension, bid_only) built from the real type."""
        entity = self.real.get_type("Campaign" if level == "campaign" else "AdGroup")
        for dim, bid_only in restrictions:
            r = self.real.get_type("TargetRestriction")
            r.targeting_dimension = self.real.enums.TargetingDimensionEnum[dim]
            r.bid_only = bid_only
            entity.targeting_setting.target_restrictions.append(r)
        row = MagicMock()
        setattr(row, level if level == "campaign" else "ad_group", entity)
        return row

    def setup(self, level, restrictions, lists=(("111", "Visitors"),)):
        lookups = [self.user_list(i, n) for i, n in lists]
        settings = self.settings_row(level, restrictions)

        def search(customer_id, query):
            return lookups if "FROM user_list" in query else [settings]

        svc = self._get_service("GoogleAdsService")
        svc.search.side_effect = search
        svc.mutate.return_value = MagicMock()
        return svc

    def ops(self, svc):
        return _req(svc, "mutate").mutate_operations


class CampaignAudienceTests(AudienceBase):
    def test_switches_targeting_to_observation_and_attaches(self):
        svc = self.setup("campaign", [("AUDIENCE", False)])  # currently TARGETING
        proposal, result = self.propose_then_commit(
            audiences.add_campaign_audience,
            customer_id="1",
            campaign_id="5",
            user_list_ids=["111"],
            percent_change=20,
        )
        ops = self.ops(svc)
        self.assertEqual(len(ops), 2)  # mode change + 1 criterion
        mode = ops[0].campaign_operation
        self.assertEqual(mode.update.resource_name, "customers/1/campaigns/5")
        self.assertEqual(list(mode.update_mask.paths), ["targeting_setting.target_restriction_operations"])
        restriction = mode.update.targeting_setting.target_restriction_operations[0].value
        self.assertEqual(restriction.targeting_dimension.name, "AUDIENCE")
        self.assertTrue(restriction.bid_only)  # observation
        crit = ops[1].campaign_criterion_operation.create
        self.assertEqual(crit.campaign, "customers/1/campaigns/5")
        self.assertEqual(crit.user_list.user_list, "customers/1/userLists/111")
        self.assertAlmostEqual(crit.bid_modifier, 1.2, places=5)
        text = json.dumps(proposal)
        self.assertIn("OBSERVATION (was TARGETING)", text)
        self.assertIn("Visitors", text)
        self.assertIn("in OBSERVATION mode", result)

    def test_no_mode_change_when_already_that_mode(self):
        svc = self.setup("campaign", [("AUDIENCE", True)])
        proposal, _ = self.propose_then_commit(
            audiences.add_campaign_audience,
            customer_id="1",
            campaign_id="5",
            user_list_ids=[111],
        )
        ops = self.ops(svc)
        self.assertEqual(len(ops), 1)
        self.assertEqual(ops[0].campaign_criterion_operation.create.bid_modifier, 0)
        self.assertIn("OBSERVATION (unchanged)", json.dumps(proposal))

    def test_targeting_mode_warns(self):
        svc = self.setup("campaign", [("AUDIENCE", True)])
        proposal, _ = self.propose_then_commit(
            audiences.add_campaign_audience,
            customer_id="1",
            campaign_id="5",
            user_list_ids=["111"],
            mode="TARGETING",
        )
        restriction = self.ops(svc)[0].campaign_operation.update.targeting_setting.target_restriction_operations[0].value
        self.assertFalse(restriction.bid_only)
        self.assertIn("ONLY to members", json.dumps(proposal))

    def test_unset_mode_is_set_explicitly(self):
        svc = self.setup("campaign", [])
        proposal, _ = self.propose_then_commit(
            audiences.add_campaign_audience, customer_id="1", campaign_id="5", user_list_ids=["111"]
        )
        self.assertEqual(len(self.ops(svc)), 2)
        self.assertIn("was NOT_SET", json.dumps(proposal))

    def test_multiple_lists(self):
        svc = self.setup("campaign", [("AUDIENCE", True)], lists=(("111", "A"), ("222", "B")))
        self.propose_then_commit(
            audiences.add_campaign_audience, customer_id="1", campaign_id="5", user_list_ids=["111", "222"]
        )
        self.assertEqual(
            [o.campaign_criterion_operation.create.user_list.user_list for o in self.ops(svc)],
            ["customers/1/userLists/111", "customers/1/userLists/222"],
        )

    def test_validation(self):
        self.setup("campaign", [("AUDIENCE", True)])
        bad = [
            {"user_list_ids": []},
            {"user_list_ids": ["abc"]},
            {"user_list_ids": ["111"], "mode": "BOTH"},
            {"user_list_ids": ["111"], "percent_change": -100},
            {"user_list_ids": ["111"], "percent_change": 901},
            {"user_list_ids": ["111", "999"]},  # 999 does not exist
        ]
        for kwargs in bad:
            with self.assertRaises(ToolError, msg=str(kwargs)):
                audiences.add_campaign_audience(customer_id="1", campaign_id="5", **kwargs)

    def test_unknown_campaign(self):
        svc = self.setup("campaign", [])
        lookups = [self.user_list("111")]
        svc.search.side_effect = lambda customer_id, query: lookups if "FROM user_list" in query else []
        with self.assertRaises(ToolError):
            audiences.add_campaign_audience(customer_id="1", campaign_id="5", user_list_ids=["111"])

    def test_changed_mode_after_approval_is_rejected(self):
        self.setup("campaign", [("AUDIENCE", True)])
        proposal = json.loads(
            audiences.add_campaign_audience(customer_id="1", campaign_id="5", user_list_ids=["111"])
        )
        with self.assertRaises(ToolError):
            audiences.add_campaign_audience(
                customer_id="1",
                campaign_id="5",
                user_list_ids=["111"],
                mode="TARGETING",  # not what was approved
                validate_only=False,
                checksum=proposal["checksum"],
            )


class AdGroupAudienceTests(AudienceBase):
    def test_ad_group_level(self):
        svc = self.setup("ad_group", [("AUDIENCE", False)])
        self.propose_then_commit(
            audiences.add_ad_group_audience,
            customer_id="1",
            ad_group_id="7",
            user_list_ids=["111"],
            percent_change=-30,
        )
        ops = self.ops(svc)
        mode = ops[0].ad_group_operation
        self.assertEqual(mode.update.resource_name, "customers/1/adGroups/7")
        crit = ops[1].ad_group_criterion_operation.create
        self.assertEqual(crit.ad_group, "customers/1/adGroups/7")
        self.assertEqual(crit.user_list.user_list, "customers/1/userLists/111")
        self.assertEqual(crit.status.name, "ENABLED")
        self.assertAlmostEqual(crit.bid_modifier, 0.7, places=5)

    def test_remove(self):
        svc = self._get_service("AdGroupCriterionService")
        svc.mutate_ad_group_criteria.return_value = _resp("a")
        self.propose_then_commit(
            audiences.remove_ad_group_audiences,
            customer_id="1",
            ad_group_id="7",
            criterion_ids=[333],
        )
        self.assertEqual(
            _req(svc, "mutate_ad_group_criteria").operations[0].remove,
            "customers/1/adGroupCriteria/7~333",
        )


class UserListBidModifierTests(WriteToolsBase):
    def test_set_criterion_bid_modifier_accepts_user_list(self):
        crit = self.real.get_type("CampaignCriterion")
        crit.criterion_id = 555
        crit.type_ = self.real.enums.CriterionTypeEnum.USER_LIST
        row = MagicMock()
        row.campaign_criterion = crit
        self._get_service("GoogleAdsService").search.return_value = [row]
        svc = self._get_service("CampaignCriterionService")
        self.propose_then_commit(
            scheduling.set_criterion_bid_modifier,
            customer_id="1",
            campaign_id="5",
            criterion_id="555",
            percent_change=25,
        )
        op = _req(svc, "mutate_campaign_criteria").operations[0]
        self.assertAlmostEqual(op.update.bid_modifier, 1.25, places=5)


if __name__ == "__main__":
    unittest.main()
