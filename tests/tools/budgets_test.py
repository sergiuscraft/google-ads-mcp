# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tests for the budget tools and create_campaign on an existing budget."""

import json
import unittest
from unittest.mock import MagicMock

from fastmcp.exceptions import ToolError

from ads_mcp.tools import budgets, campaigns
from tests.tools.write_tools_test import WriteToolsBase, _resp


def _req(svc, method):
    return getattr(svc, method).call_args.kwargs["request"]


class BudgetBase(WriteToolsBase):
    def budget_row(self, budget_id=55, name="Main", micros=10_000_000, shared=False, uses=1, status="ENABLED"):
        row = MagicMock()
        b = row.campaign_budget
        b.id = budget_id
        b.name = name
        b.amount_micros = micros
        b.explicitly_shared = shared
        b.reference_count = uses
        b.status.name = status
        return row

    def set_search(self, *rows):
        self._get_service("GoogleAdsService").search.return_value = list(rows)


class CreateBudgetTests(BudgetBase):
    def test_create_shared(self):
        svc = self._get_service("CampaignBudgetService")
        svc.mutate_campaign_budgets.return_value = _resp("customers/1/campaignBudgets/77")
        _, result = self.propose_then_commit(
            budgets.create_campaign_budget,
            customer_id="1",
            name=" Shared ",
            daily_budget=30,
        )
        op = _req(svc, "mutate_campaign_budgets").operations[0].create
        self.assertEqual(op.name, "Shared")
        self.assertEqual(op.amount_micros, 30_000_000)
        self.assertTrue(op.explicitly_shared)
        self.assertIn("ID: 77", result)

    def test_create_not_shared(self):
        svc = self._get_service("CampaignBudgetService")
        svc.mutate_campaign_budgets.return_value = _resp("x/1")
        self.propose_then_commit(
            budgets.create_campaign_budget,
            customer_id="1",
            name="Solo",
            daily_budget=5,
            shared=False,
        )
        self.assertFalse(_req(svc, "mutate_campaign_budgets").operations[0].create.explicitly_shared)

    def test_validation(self):
        for kwargs in ({"name": " ", "daily_budget": 5}, {"name": "x", "daily_budget": 0}):
            with self.assertRaises(ToolError):
                budgets.create_campaign_budget(customer_id="1", **kwargs)


class UpdateBudgetTests(BudgetBase):
    def test_update_shows_old_and_warns_when_shared(self):
        self.set_search(self.budget_row(shared=True, uses=3))
        svc = self._get_service("CampaignBudgetService")
        proposal, result = self.propose_then_commit(
            budgets.update_budget, customer_id="1", budget_id="55", daily_budget=20
        )
        op = _req(svc, "mutate_campaign_budgets").operations[0]
        self.assertEqual(op.update.resource_name, "customers/1/campaignBudgets/55")
        self.assertEqual(op.update.amount_micros, 20_000_000)
        self.assertEqual(list(op.update_mask.paths), ["amount_micros"])
        text = json.dumps(proposal)
        self.assertIn("used by 3 campaign", text)
        self.assertIn("10.0", text)
        self.assertIn("10.00 to 20.00", result)

    def test_missing_or_removed(self):
        self.set_search()
        with self.assertRaises(ToolError):
            budgets.update_budget(customer_id="1", budget_id="55", daily_budget=5)
        self.set_search(self.budget_row(status="REMOVED"))
        with self.assertRaises(ToolError):
            budgets.update_budget(customer_id="1", budget_id="55", daily_budget=5)


class SetCampaignBudgetTests(BudgetBase):
    def _search_by_query(self, target_row, current_rn="customers/1/campaignBudgets/11"):
        campaign_row = MagicMock()
        campaign_row.campaign.campaign_budget = current_rn

        def search(customer_id, query):
            return [campaign_row] if "FROM campaign WHERE" in query else [target_row]

        self._get_service("GoogleAdsService").search.side_effect = search

    def test_move_to_shared_budget(self):
        self._search_by_query(self.budget_row(shared=True, uses=2, name="Common"))
        svc = self._get_service("CampaignService")
        proposal, result = self.propose_then_commit(
            budgets.set_campaign_budget, customer_id="1", campaign_id="5", budget_id="55"
        )
        op = _req(svc, "mutate_campaigns").operations[0]
        self.assertEqual(op.update.resource_name, "customers/1/campaigns/5")
        self.assertEqual(op.update.campaign_budget, "customers/1/campaignBudgets/55")
        self.assertEqual(list(op.update_mask.paths), ["campaign_budget"])
        text = json.dumps(proposal)
        self.assertIn("campaignBudgets/11", text)  # old budget shown
        self.assertIn("2 campaign(s) already", text)

    def test_refuses_unshared_budget_in_use(self):
        self._search_by_query(self.budget_row(shared=False, uses=1))
        with self.assertRaises(ToolError):
            budgets.set_campaign_budget(customer_id="1", campaign_id="5", budget_id="55")

    def test_allows_unshared_unused_budget(self):
        self._search_by_query(self.budget_row(shared=False, uses=0))
        proposal = json.loads(
            budgets.set_campaign_budget(customer_id="1", campaign_id="5", budget_id="55")
        )
        self.assertEqual(proposal["status"], "PROPOSED")

    def test_same_budget_rejected(self):
        self._search_by_query(
            self.budget_row(shared=True), current_rn="customers/1/campaignBudgets/55"
        )
        with self.assertRaises(ToolError):
            budgets.set_campaign_budget(customer_id="1", campaign_id="5", budget_id="55")


class RemoveBudgetTests(BudgetBase):
    def test_remove_unused(self):
        self.set_search(self.budget_row(uses=0))
        svc = self._get_service("CampaignBudgetService")
        self.propose_then_commit(budgets.remove_budget, customer_id="1", budget_id="55")
        self.assertEqual(
            _req(svc, "mutate_campaign_budgets").operations[0].remove,
            "customers/1/campaignBudgets/55",
        )

    def test_refuses_used_budget(self):
        self.set_search(self.budget_row(uses=2))
        with self.assertRaises(ToolError):
            budgets.remove_budget(customer_id="1", budget_id="55")


class CreateCampaignExistingBudgetTests(BudgetBase):
    def _service(self):
        svc = self._get_service("GoogleAdsService")
        response = MagicMock()
        camp = MagicMock()
        camp.campaign_result.resource_name = "customers/1/campaigns/9"
        response.mutate_operation_responses = [camp]
        svc.mutate.return_value = response
        return svc

    def test_uses_existing_budget_without_creating_one(self):
        svc = self._service()
        _, result = self.propose_then_commit(
            campaigns.create_campaign,
            customer_id="1",
            name="C",
            existing_budget_id="55",
        )
        ops = _req(svc, "mutate").mutate_operations
        self.assertEqual(len(ops), 1)
        camp = ops[0].campaign_operation.create
        self.assertEqual(camp.campaign_budget, "customers/1/campaignBudgets/55")
        self.assertIn("ID: 9", result)

    def test_exactly_one_budget_source(self):
        for kwargs in ({}, {"daily_budget": 5, "existing_budget_id": 55}):
            with self.assertRaises(ToolError, msg=str(kwargs)):
                campaigns.create_campaign(customer_id="1", name="C", **kwargs)

    def test_new_budget_still_created_with_amount(self):
        svc = self._get_service("GoogleAdsService")
        response = MagicMock()
        camp = MagicMock()
        camp.campaign_result.resource_name = "customers/1/campaigns/9"
        response.mutate_operation_responses = [MagicMock(), camp]
        svc.mutate.return_value = response
        self.propose_then_commit(
            campaigns.create_campaign, customer_id="1", name="C", daily_budget=7
        )
        ops = _req(svc, "mutate").mutate_operations
        self.assertEqual(len(ops), 2)
        self.assertEqual(ops[0].campaign_budget_operation.create.amount_micros, 7_000_000)


if __name__ == "__main__":
    unittest.main()
