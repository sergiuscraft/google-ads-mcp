# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tests for the shared negative keyword list tools."""

import json
import unittest
from unittest.mock import MagicMock

from fastmcp.exceptions import ToolError

from ads_mcp.tools import shared_sets
from tests.tools.write_tools_test import WriteToolsBase, _resp


def _req(svc, method):
    return getattr(svc, method).call_args.kwargs["request"]


class SharedSetTests(WriteToolsBase):
    def test_create_list_is_one_atomic_request(self):
        svc = self._get_service("GoogleAdsService")
        response = MagicMock()
        first = MagicMock()
        first.shared_set_result.resource_name = "customers/1/sharedSets/99"
        response.mutate_operation_responses = [first]
        svc.mutate.return_value = response

        _, result = self.propose_then_commit(
            shared_sets.create_negative_keyword_list,
            customer_id="1",
            name=" Junk ",
            keywords=["бесплатно", "скачать"],
            match_type="EXACT",
            campaign_ids=[5, "6"],
        )

        ops = _req(svc, "mutate").mutate_operations
        self.assertEqual(len(ops), 1 + 2 + 2)  # list + 2 words + 2 campaign links
        created = ops[0].shared_set_operation.create
        self.assertEqual(created.name, "Junk")
        self.assertEqual(created.type_.name, "NEGATIVE_KEYWORDS")
        temp = created.resource_name
        self.assertTrue(temp.endswith("/sharedSets/-1"))
        words = [o.shared_criterion_operation.create for o in ops[1:3]]
        self.assertEqual([w.keyword.text for w in words], ["бесплатно", "скачать"])
        self.assertTrue(all(w.shared_set == temp for w in words))
        exact = self.real.enums.KeywordMatchTypeEnum.EXACT
        self.assertTrue(all(w.keyword.match_type == exact for w in words))
        links = [o.campaign_shared_set_operation.create for o in ops[3:]]
        self.assertEqual(
            [l.campaign for l in links],
            ["customers/1/campaigns/5", "customers/1/campaigns/6"],
        )
        self.assertTrue(all(l.shared_set == temp for l in links))
        self.assertIn("ID: 99", result)
        self.assertIn("2 campaign(s)", result)

    def test_create_list_without_campaigns(self):
        svc = self._get_service("GoogleAdsService")
        proposal = json.loads(
            shared_sets.create_negative_keyword_list(
                customer_id="1", name="L", keywords=["a"]
            )
        )
        self.assertEqual(proposal["status"], "PROPOSED")
        self.assertEqual(len(_req(svc, "mutate").mutate_operations), 2)

    def test_create_list_validation(self):
        for kwargs in (
            {"name": " ", "keywords": ["a"]},
            {"name": "L", "keywords": []},
            {"name": "L", "keywords": ["a", "A"]},
            {"name": "L", "keywords": ["x" * 81]},
            {"name": "L", "keywords": ["a"], "campaign_ids": ["abc"]},
        ):
            with self.assertRaises(ToolError, msg=str(kwargs)):
                shared_sets.create_negative_keyword_list(customer_id="1", **kwargs)

    def test_add_words(self):
        svc = self._get_service("SharedCriterionService")
        svc.mutate_shared_criteria.return_value = _resp("a", "b")
        _, result = self.propose_then_commit(
            shared_sets.add_list_negative_keywords,
            customer_id="1",
            shared_set_id="99",
            keywords=["x", "y"],
        )
        ops = _req(svc, "mutate_shared_criteria").operations
        self.assertEqual([o.create.keyword.text for o in ops], ["x", "y"])
        self.assertEqual(ops[0].create.shared_set, "customers/1/sharedSets/99")
        self.assertEqual(ops[0].create.keyword.match_type.name, "PHRASE")
        self.assertIn("Added 2", result)

    def test_remove_words(self):
        svc = self._get_service("SharedCriterionService")
        svc.mutate_shared_criteria.return_value = _resp("a")
        self.propose_then_commit(
            shared_sets.remove_list_negative_keywords,
            customer_id="1",
            shared_set_id="99",
            criterion_ids=[7],
        )
        self.assertEqual(
            _req(svc, "mutate_shared_criteria").operations[0].remove,
            "customers/1/sharedCriteria/99~7",
        )

    def test_attach_and_detach(self):
        svc = self._get_service("CampaignSharedSetService")
        svc.mutate_campaign_shared_sets.return_value = _resp("a", "b")
        self.propose_then_commit(
            shared_sets.attach_negative_keyword_list,
            customer_id="1",
            shared_set_id="99",
            campaign_ids=[5, 6],
        )
        ops = _req(svc, "mutate_campaign_shared_sets").operations
        self.assertEqual(
            [(o.create.campaign, o.create.shared_set) for o in ops],
            [
                ("customers/1/campaigns/5", "customers/1/sharedSets/99"),
                ("customers/1/campaigns/6", "customers/1/sharedSets/99"),
            ],
        )
        self.propose_then_commit(
            shared_sets.detach_negative_keyword_list,
            customer_id="1",
            shared_set_id="99",
            campaign_ids=[5],
        )
        self.assertEqual(
            _req(svc, "mutate_campaign_shared_sets").operations[0].remove,
            "customers/1/campaignSharedSets/5~99",
        )

    def test_remove_list(self):
        svc = self._get_service("SharedSetService")
        self.propose_then_commit(
            shared_sets.remove_negative_keyword_list,
            customer_id="1",
            shared_set_id="99",
        )
        self.assertEqual(
            _req(svc, "mutate_shared_sets").operations[0].remove,
            "customers/1/sharedSets/99",
        )

    def test_changed_campaigns_after_approval_are_rejected(self):
        proposal = json.loads(
            shared_sets.attach_negative_keyword_list(
                customer_id="1", shared_set_id="99", campaign_ids=[5]
            )
        )
        with self.assertRaises(ToolError):
            shared_sets.attach_negative_keyword_list(
                customer_id="1",
                shared_set_id="99",
                campaign_ids=[5, 6],  # extra campaign not approved
                validate_only=False,
                checksum=proposal["checksum"],
            )


if __name__ == "__main__":
    unittest.main()
