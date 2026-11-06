# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tests for the ad schedule and bid adjustment tools."""

import json
import unittest
from unittest.mock import MagicMock

from fastmcp.exceptions import ToolError

from ads_mcp.tools import scheduling
from ads_mcp.tools.scheduling import ScheduleSlot
from tests.tools.write_tools_test import WriteToolsBase, _resp


def _req(svc, method):
    return getattr(svc, method).call_args.kwargs["request"]


def _row(real, criterion_id, ctype, modifier=None):
    """A search row built from the real CampaignCriterion type (so presence works)."""
    crit = real.get_type("CampaignCriterion")
    crit.criterion_id = criterion_id
    crit.type_ = real.enums.CriterionTypeEnum[ctype]
    if modifier is not None:
        crit.bid_modifier = modifier
    row = MagicMock()
    row.campaign_criterion = crit
    return row


class ModifierMathTests(unittest.TestCase):
    def test_percent_to_multiplier(self):
        m = scheduling._modifier
        self.assertEqual(m(20, allow_exclude=False), 1.2)
        self.assertEqual(m(-30, allow_exclude=False), 0.7)
        self.assertEqual(m(0, allow_exclude=False), 1.0)
        self.assertEqual(m(900, allow_exclude=False), 10.0)
        self.assertEqual(m(-100, allow_exclude=True), 0.0)

    def test_limits(self):
        m = scheduling._modifier
        for pct, excl in ((-100, False), (-91, False), (901, True), (-101, True)):
            with self.assertRaises(ToolError, msg=f"{pct} {excl}"):
                m(pct, allow_exclude=excl)


class AdScheduleTests(WriteToolsBase):
    def test_add_windows(self):
        svc = self._get_service("CampaignCriterionService")
        svc.mutate_campaign_criteria.return_value = _resp("a", "b")
        slots = [
            {"day_of_week": "MONDAY", "start_hour": 9, "end_hour": 18, "percent_change": 25},
            ScheduleSlot(day_of_week="SATURDAY", start_hour=10, start_minute=30, end_hour=14),
        ]
        _, result = self.propose_then_commit(
            scheduling.add_campaign_ad_schedule,
            customer_id="1",
            campaign_id="5",
            slots=slots,
        )
        ops = _req(svc, "mutate_campaign_criteria").operations
        first, second = ops[0].create, ops[1].create
        self.assertEqual(first.campaign, "customers/1/campaigns/5")
        self.assertEqual(first.ad_schedule.day_of_week.name, "MONDAY")
        self.assertEqual((first.ad_schedule.start_hour, first.ad_schedule.end_hour), (9, 18))
        self.assertEqual(first.ad_schedule.start_minute.name, "ZERO")
        self.assertAlmostEqual(first.bid_modifier, 1.25, places=5)
        self.assertEqual(second.ad_schedule.start_minute.name, "THIRTY")
        self.assertAlmostEqual(second.bid_modifier, 0.0, places=5)  # unset
        self.assertIn("Added 2", result)

    def test_invalid_windows(self):
        bad = [
            [],
            [{"day_of_week": "MONDAY", "start_hour": 18, "end_hour": 9}],
            [{"day_of_week": "MONDAY", "start_hour": 9, "end_hour": 9}],
            [{"day_of_week": "FUNDAY", "start_hour": 9, "end_hour": 10}],
            [{"day_of_week": "MONDAY", "start_hour": 9, "end_hour": 25}],
            [{"day_of_week": "MONDAY", "start_hour": 9, "end_hour": 10, "percent_change": -100}],
        ]
        for slots in bad:
            with self.assertRaises(Exception, msg=str(slots)):
                scheduling.add_campaign_ad_schedule(
                    customer_id="1", campaign_id="5", slots=slots
                )


class DeviceModifierTests(WriteToolsBase):
    def test_creates_when_absent(self):
        self._get_service("GoogleAdsService").search.return_value = []
        svc = self._get_service("CampaignCriterionService")
        proposal, _ = self.propose_then_commit(
            scheduling.set_campaign_device_bid_modifier,
            customer_id="1",
            campaign_id="5",
            device="MOBILE",
            percent_change=-100,
        )
        crit = _req(svc, "mutate_campaign_criteria").operations[0].create
        self.assertEqual(crit.device.type_.name, "MOBILE")
        self.assertAlmostEqual(crit.bid_modifier, 0.0, places=5)
        self.assertEqual(proposal["change_items"][0]["operation"], "CREATE")

    def test_updates_when_present_and_shows_old_value(self):
        self._get_service("GoogleAdsService").search.return_value = [
            _row(self.real, 30001, "DEVICE", 1.5)
        ]
        svc = self._get_service("CampaignCriterionService")
        proposal, _ = self.propose_then_commit(
            scheduling.set_campaign_device_bid_modifier,
            customer_id="1",
            campaign_id="5",
            device="MOBILE",
            percent_change=20,
        )
        op = _req(svc, "mutate_campaign_criteria").operations[0]
        self.assertEqual(op.update.resource_name, "customers/1/campaignCriteria/5~30001")
        self.assertAlmostEqual(op.update.bid_modifier, 1.2, places=5)
        self.assertEqual(list(op.update_mask.paths), ["bid_modifier"])
        item = proposal["change_items"][0]
        self.assertEqual(item["operation"], "UPDATE")
        self.assertEqual(item["original_value_readable"], "+50%")

    def test_unset_modifier_reads_as_no_adjustment(self):
        self._get_service("GoogleAdsService").search.return_value = [
            _row(self.real, 30000, "DEVICE")  # bid_modifier not set
        ]
        proposal = json.loads(
            scheduling.set_campaign_device_bid_modifier(
                customer_id="1", campaign_id="5", device="DESKTOP", percent_change=-10
            )
        )
        self.assertEqual(proposal["change_items"][0]["original_value_readable"], "+0%")

    def test_rejects_unknown_device(self):
        with self.assertRaises(ToolError):
            scheduling.set_campaign_device_bid_modifier(
                customer_id="1", campaign_id="5", device="WATCH", percent_change=5
            )


class CriterionModifierTests(WriteToolsBase):
    def test_updates_location(self):
        self._get_service("GoogleAdsService").search.return_value = [
            _row(self.real, 2804, "LOCATION", 1.0)
        ]
        svc = self._get_service("CampaignCriterionService")
        self.propose_then_commit(
            scheduling.set_criterion_bid_modifier,
            customer_id="1",
            campaign_id="5",
            criterion_id="2804",
            percent_change=-30,
        )
        op = _req(svc, "mutate_campaign_criteria").operations[0]
        self.assertEqual(op.update.resource_name, "customers/1/campaignCriteria/5~2804")
        self.assertAlmostEqual(op.update.bid_modifier, 0.7, places=5)

    def test_unknown_criterion(self):
        self._get_service("GoogleAdsService").search.return_value = []
        with self.assertRaises(ToolError):
            scheduling.set_criterion_bid_modifier(
                customer_id="1", campaign_id="5", criterion_id="1", percent_change=5
            )

    def test_unsupported_type_and_exclusion_rules(self):
        search = self._get_service("GoogleAdsService").search
        search.return_value = [_row(self.real, 7, "KEYWORD", 1.0)]
        with self.assertRaises(ToolError):
            scheduling.set_criterion_bid_modifier(
                customer_id="1", campaign_id="5", criterion_id="7", percent_change=5
            )
        search.return_value = [_row(self.real, 8, "LOCATION")]
        with self.assertRaises(ToolError):  # -100 only allowed for devices
            scheduling.set_criterion_bid_modifier(
                customer_id="1", campaign_id="5", criterion_id="8", percent_change=-100
            )

    def test_changed_value_after_approval_is_rejected(self):
        self._get_service("GoogleAdsService").search.return_value = [
            _row(self.real, 2804, "LOCATION", 1.0)
        ]
        proposal = json.loads(
            scheduling.set_criterion_bid_modifier(
                customer_id="1", campaign_id="5", criterion_id="2804", percent_change=10
            )
        )
        with self.assertRaises(ToolError):
            scheduling.set_criterion_bid_modifier(
                customer_id="1",
                campaign_id="5",
                criterion_id="2804",
                percent_change=200,
                validate_only=False,
                checksum=proposal["checksum"],
            )


if __name__ == "__main__":
    unittest.main()
