# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for adding keywords and negative keywords."""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils

keywords_mcp = FastMCP("keywords")

MatchType = Literal["EXACT", "PHRASE", "BROAD"]


@keywords_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def add_keywords(
    customer_id: str | int,
    ad_group_id: str | int,
    keywords: list[str],
    match_type: MatchType = "PHRASE",
    cpc_bid: float | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Adds positive keywords to an ad group (all with the same match type).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        ad_group_id: Numeric ID of the ad group.
        keywords: Keyword texts (1-100, up to 80 characters each), without match-type punctuation.
        match_type: EXACT, PHRASE (default) or BROAD.
        cpc_bid: Optional max CPC for these keywords in account currency units.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    ag_id = mh.require_numeric_id(ad_group_id, "ad_group_id")
    texts = mh.check_texts(
        keywords, "keywords", min_count=1, max_count=100, max_len=80
    )
    bid_micros = mh.to_micros(cpc_bid, "cpc_bid") if cpc_bid is not None else None

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(
        ga_client, "AdGroupCriterionService"
    )

    ops = []
    for text in texts:
        op = ga_client.get_type("AdGroupCriterionOperation")
        crit = op.create
        crit.ad_group = f"customers/{cid}/adGroups/{ag_id}"
        crit.status = ga_client.enums.AdGroupCriterionStatusEnum.ENABLED
        crit.keyword.text = text
        crit.keyword.match_type = ga_client.enums.KeywordMatchTypeEnum[
            match_type
        ]
        if bid_micros is not None:
            crit.cpc_bid_micros = bid_micros
        ops.append(op)

    change_items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Keyword",
        primary_key_readable=f"ad group {ag_id}",
        fields={
            "Keywords": texts,
            "Match type": match_type,
            "Max CPC": cpc_bid,
        },
    )

    def _mutate(dry_run: bool):
        return service.mutate_ad_group_criteria(
            request=mh.build_request(
                ga_client, "MutateAdGroupCriteriaRequest", cid, ops, validate_only=dry_run
            )
        )

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "add_keywords",
            "ad_group_id": ag_id,
            "keywords": texts,
            "match_type": match_type,
            "cpc_bid_micros": bid_micros,
        },
        change_items=change_items,
        mutate=_mutate,
        on_success=lambda r: (
            f"Added {len(r.results)} keyword(s) to ad group {ag_id}."
        ),
        error_prefix="Failed to add keywords",
    )


@keywords_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def add_negative_keywords(
    customer_id: str | int,
    keywords: list[str],
    campaign_id: str | int | None = None,
    ad_group_id: str | int | None = None,
    match_type: MatchType = "PHRASE",
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Adds negative keywords at campaign level or ad group level (give exactly one).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        keywords: Negative keyword texts (1-100, up to 80 characters each).
        campaign_id: Numeric campaign ID for a campaign-level negative.
        ad_group_id: Numeric ad group ID for an ad-group-level negative.
        match_type: EXACT, PHRASE (default) or BROAD.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    if (campaign_id is None) == (ad_group_id is None):
        raise ToolError(
            "Error: provide exactly one of 'campaign_id' or 'ad_group_id'."
        )
    texts = mh.check_texts(
        keywords, "keywords", min_count=1, max_count=100, max_len=80
    )

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    ops = []
    if campaign_id is not None:
        target_id = mh.require_numeric_id(campaign_id, "campaign_id")
        level = "campaign"
        service = utils.get_googleads_service_from_client(
            ga_client, "CampaignCriterionService"
        )
        for text in texts:
            op = ga_client.get_type("CampaignCriterionOperation")
            crit = op.create
            crit.campaign = f"customers/{cid}/campaigns/{target_id}"
            crit.negative = True
            crit.keyword.text = text
            crit.keyword.match_type = ga_client.enums.KeywordMatchTypeEnum[
                match_type
            ]
            ops.append(op)
        mutate_method = "mutate_campaign_criteria"
        request_type = "MutateCampaignCriteriaRequest"
    else:
        target_id = mh.require_numeric_id(ad_group_id, "ad_group_id")
        level = "ad group"
        service = utils.get_googleads_service_from_client(
            ga_client, "AdGroupCriterionService"
        )
        for text in texts:
            op = ga_client.get_type("AdGroupCriterionOperation")
            crit = op.create
            crit.ad_group = f"customers/{cid}/adGroups/{target_id}"
            crit.negative = True
            crit.keyword.text = text
            crit.keyword.match_type = ga_client.enums.KeywordMatchTypeEnum[
                match_type
            ]
            ops.append(op)
        mutate_method = "mutate_ad_group_criteria"
        request_type = "MutateAdGroupCriteriaRequest"

    change_items = pac.build_change_items(
        operation="CREATE",
        entity_readable=f"Negative keyword ({level})",
        primary_key_readable=f"{level} {target_id}",
        fields={"Keywords": texts, "Match type": match_type},
    )

    def _mutate(dry_run: bool):
        return getattr(service, mutate_method)(
            request=mh.build_request(
                ga_client, request_type, cid, ops, validate_only=dry_run
            )
        )

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "add_negative_keywords",
            "level": level,
            "target_id": target_id,
            "keywords": texts,
            "match_type": match_type,
        },
        change_items=change_items,
        mutate=_mutate,
        on_success=lambda r: (
            f"Added {len(r.results)} negative keyword(s) to {level} {target_id}."
        ),
        error_prefix="Failed to add negative keywords",
    )
