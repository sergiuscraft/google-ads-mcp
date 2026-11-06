# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tests for replace_responsive_search_ad."""

import json
import unittest
from unittest.mock import MagicMock

from fastmcp.exceptions import ToolError

from ads_mcp.tools import ads
from tests.tools.write_tools_test import WriteToolsBase

OLD_HEADS = ["Old one", "Old two", "Old three"]
OLD_DESCS = ["Old description one", "Old description two"]


def _req(svc, method):
    return getattr(svc, method).call_args.kwargs["request"]


class ReplaceAdTests(WriteToolsBase):
    def old_ad_row(self, *, status="ENABLED", ad_type="RESPONSIVE_SEARCH_AD", pinned=False, url="https://old.example.com", path1="eco", path2=""):
        gaa = self.real.get_type("AdGroupAd")
        gaa.status = self.real.enums.AdGroupAdStatusEnum[status]
        gaa.ad.id = 99
        gaa.ad.type_ = self.real.enums.AdTypeEnum[ad_type]
        if url:
            gaa.ad.final_urls.append(url)
        rsa = gaa.ad.responsive_search_ad
        for i, text in enumerate(OLD_HEADS):
            a = self.real.get_type("AdTextAsset")
            a.text = text
            if pinned and i == 0:
                a.pinned_field = self.real.enums.ServedAssetFieldTypeEnum.HEADLINE_1
            rsa.headlines.append(a)
        for text in OLD_DESCS:
            a = self.real.get_type("AdTextAsset")
            a.text = text
            rsa.descriptions.append(a)
        rsa.path1 = path1
        if path2:
            rsa.path2 = path2
        row = MagicMock()
        row.ad_group_ad = gaa
        return row

    def setup_old(self, **kwargs):
        self._get_service("GoogleAdsService").search.return_value = [self.old_ad_row(**kwargs)]
        svc = self._get_service("GoogleAdsService")
        response = MagicMock()
        created = MagicMock()
        created.ad_group_ad_result.resource_name = "customers/1/adGroupAds/2~123"
        response.mutate_operation_responses = [created, MagicMock()]
        svc.mutate.return_value = response
        return svc

    def run_replace(self, old_kwargs=None, **kwargs):
        svc = self.setup_old(**(old_kwargs or {}))
        proposal, result = self.propose_then_commit(
            ads.replace_responsive_search_ad,
            customer_id="1",
            ad_group_id="2",
            old_ad_id="99",
            **kwargs,
        )
        ops = _req(svc, "mutate").mutate_operations
        return proposal, result, ops

    def test_new_headlines_copy_the_rest_and_pause_old(self):
        proposal, result, ops = self.run_replace(
            headlines=["New one", "New two", "New three", "New four"]
        )
        self.assertEqual(len(ops), 2)
        new = ops[0].ad_group_ad_operation.create
        rsa = new.ad.responsive_search_ad
        self.assertEqual([h.text for h in rsa.headlines], ["New one", "New two", "New three", "New four"])
        self.assertEqual([d.text for d in rsa.descriptions], OLD_DESCS)  # copied
        self.assertEqual(list(new.ad.final_urls), ["https://old.example.com"])
        self.assertEqual(rsa.path1, "eco")
        self.assertEqual(new.ad_group, "customers/1/adGroups/2")
        old = ops[1].ad_group_ad_operation
        self.assertEqual(old.update.resource_name, "customers/1/adGroupAds/2~99")
        self.assertEqual(old.update.status.name, "PAUSED")
        self.assertEqual(list(old.update_mask.paths), ["status"])
        self.assertIn("old ad 99 paused", result)
        text = json.dumps(proposal)
        self.assertIn("Old one", text)  # shows what is being replaced
        self.assertIn("PAUSED (not removed)", text)

    def test_new_ad_mirrors_old_status_by_default(self):
        _, _, ops = self.run_replace(old_kwargs={"status": "ENABLED"}, descriptions=["Fresh description one", "Fresh description two"])
        self.assertEqual(ops[0].ad_group_ad_operation.create.status.name, "ENABLED")
        _, _, ops = self.run_replace(old_kwargs={"status": "PAUSED"}, descriptions=["Fresh description one", "Fresh description two"])
        self.assertEqual(ops[0].ad_group_ad_operation.create.status.name, "PAUSED")

    def test_new_status_override(self):
        _, _, ops = self.run_replace(
            old_kwargs={"status": "ENABLED"},
            final_url="https://new.example.com",
            new_status="PAUSED",
        )
        create = ops[0].ad_group_ad_operation.create
        self.assertEqual(create.status.name, "PAUSED")
        self.assertEqual(list(create.ad.final_urls), ["https://new.example.com"])

    def test_pinned_position_is_kept_for_unchanged_headlines(self):
        _, _, ops = self.run_replace(
            old_kwargs={"pinned": True}, descriptions=["Fresh description one", "Fresh description two"]
        )
        heads = ops[0].ad_group_ad_operation.create.ad.responsive_search_ad.headlines
        self.assertEqual(heads[0].pinned_field.name, "HEADLINE_1")
        self.assertEqual(heads[1].pinned_field.value if hasattr(heads[1].pinned_field, "value") else int(heads[1].pinned_field), 0)

    def test_clear_path(self):
        _, _, ops = self.run_replace(path1="")
        rsa = ops[0].ad_group_ad_operation.create.ad.responsive_search_ad
        self.assertEqual(rsa.path1, "")

    def test_requires_a_change(self):
        with self.assertRaises(ToolError):
            ads.replace_responsive_search_ad(customer_id="1", ad_group_id="2", old_ad_id="99")

    def test_input_validation(self):
        bad = [
            {"headlines": ["a", "b"]},
            {"headlines": ["x" * 31, "b", "c"]},
            {"descriptions": ["only one"]},
            {"final_url": "example.com"},
            {"path1": "x" * 16},
            {"new_status": "REMOVED"},
        ]
        for kwargs in bad:
            with self.assertRaises(ToolError, msg=str(kwargs)):
                ads.replace_responsive_search_ad(
                    customer_id="1", ad_group_id="2", old_ad_id="99", **kwargs
                )

    def test_path2_without_path1_is_rejected(self):
        self.setup_old(path1="")
        with self.assertRaises(ToolError):
            ads.replace_responsive_search_ad(
                customer_id="1", ad_group_id="2", old_ad_id="99", path2="x"
            )

    def test_unknown_removed_and_wrong_type_ads(self):
        svc = self._get_service("GoogleAdsService")
        svc.search.return_value = []
        with self.assertRaises(ToolError):
            ads.replace_responsive_search_ad(
                customer_id="1", ad_group_id="2", old_ad_id="99", final_url="https://x.com"
            )
        svc.search.return_value = [self.old_ad_row(ad_type="EXPANDED_TEXT_AD")]
        with self.assertRaises(ToolError):
            ads.replace_responsive_search_ad(
                customer_id="1", ad_group_id="2", old_ad_id="99", final_url="https://x.com"
            )
        svc.search.return_value = [self.old_ad_row(status="REMOVED")]
        with self.assertRaises(ToolError):
            ads.replace_responsive_search_ad(
                customer_id="1", ad_group_id="2", old_ad_id="99", final_url="https://x.com"
            )

    def test_changed_text_after_approval_is_rejected(self):
        self.setup_old()
        proposal = json.loads(
            ads.replace_responsive_search_ad(
                customer_id="1",
                ad_group_id="2",
                old_ad_id="99",
                headlines=["Aaa", "Bbb", "Ccc"],
            )
        )
        with self.assertRaises(ToolError):
            ads.replace_responsive_search_ad(
                customer_id="1",
                ad_group_id="2",
                old_ad_id="99",
                headlines=["Aaa", "Bbb", "Different"],
                validate_only=False,
                checksum=proposal["checksum"],
            )


if __name__ == "__main__":
    unittest.main()
