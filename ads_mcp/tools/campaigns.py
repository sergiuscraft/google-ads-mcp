# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for creating and changing the status of Google Ads campaigns."""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils

campaigns_mcp = FastMCP("campaigns")

_BIDDING = ("MANUAL_CPC", "MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS")


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def create_campaign(
    customer_id: str | int,
    name: str,
    daily_budget: float,
    bidding_strategy: Literal[
        "MANUAL_CPC", "MAXIMIZE_CLICKS", "MAXIMIZE_CONVERSIONS"
    ] = "MAXIMIZE_CLICKS",
    status: Literal["PAUSED", "ENABLED"] = "PAUSED",
    include_search_partners: bool = False,
    contains_eu_political_advertising: bool = False,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Creates a Search campaign together with its own daily budget.

    The campaign is created PAUSED by default and targets Google Search only
    (Display network is off; Search partners are optional).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        name: Campaign name. Must be unique among non-removed campaigns.
        daily_budget: Daily budget in the account's currency units (e.g. 25.5).
        bidding_strategy: MANUAL_CPC, MAXIMIZE_CLICKS or MAXIMIZE_CONVERSIONS.
        status: PAUSED (default, safe) or ENABLED.
        include_search_partners: Also serve on Google search partner sites.
        contains_eu_political_advertising: Declaration required by Google Ads.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    if not name or not name.strip():
        raise ToolError("Error: 'name' is required.")
    name = name.strip()
    budget_micros = mh.to_micros(daily_budget, "daily_budget")
    if bidding_strategy not in _BIDDING:
        raise ToolError(f"Error: 'bidding_strategy' must be one of {_BIDDING}.")

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(
        ga_client, "GoogleAdsService"
    )

    temp_budget = f"customers/{cid}/campaignBudgets/-1"

    budget_op = ga_client.get_type("MutateOperation")
    budget = budget_op.campaign_budget_operation.create
    budget.resource_name = temp_budget
    budget.name = f"{name} budget"
    budget.amount_micros = budget_micros
    budget.delivery_method = ga_client.enums.BudgetDeliveryMethodEnum.STANDARD
    budget.explicitly_shared = False

    campaign_op = ga_client.get_type("MutateOperation")
    campaign = campaign_op.campaign_operation.create
    campaign.name = name
    campaign.status = ga_client.enums.CampaignStatusEnum[status]
    campaign.advertising_channel_type = (
        ga_client.enums.AdvertisingChannelTypeEnum.SEARCH
    )
    campaign.campaign_budget = temp_budget
    campaign.network_settings.target_google_search = True
    campaign.network_settings.target_search_network = include_search_partners
    campaign.network_settings.target_content_network = False
    campaign.contains_eu_political_advertising = (
        ga_client.enums.EuPoliticalAdvertisingStatusEnum.CONTAINS_EU_POLITICAL_ADVERTISING
        if contains_eu_political_advertising
        else ga_client.enums.EuPoliticalAdvertisingStatusEnum.DOES_NOT_CONTAIN_EU_POLITICAL_ADVERTISING
    )
    if bidding_strategy == "MANUAL_CPC":
        campaign.manual_cpc.enhanced_cpc_enabled = False
    elif bidding_strategy == "MAXIMIZE_CLICKS":
        # In the API, "Maximize clicks" is the `target_spend` strategy.
        ga_client.copy_from(
            campaign.target_spend, ga_client.get_type("TargetSpend")
        )
    else:
        ga_client.copy_from(
            campaign.maximize_conversions,
            ga_client.get_type("MaximizeConversions"),
        )

    payload = {
        "tool": "create_campaign",
        "name": name,
        "daily_budget_micros": budget_micros,
        "bidding_strategy": bidding_strategy,
        "status": status,
        "include_search_partners": include_search_partners,
        "contains_eu_political_advertising": contains_eu_political_advertising,
    }
    change_items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Campaign",
        primary_key_readable=name,
        fields={
            "Name": name,
            "Daily budget": daily_budget,
            "Bidding strategy": bidding_strategy,
            "Status": status,
            "Channel": "SEARCH",
            "Search partners": include_search_partners,
            "Display network": False,
            "EU political advertising": contains_eu_political_advertising,
        },
    )

    def _mutate(dry_run: bool):
        return service.mutate(
            request=mh.build_request(
                ga_client,
                "MutateGoogleAdsRequest",
                cid,
                [budget_op, campaign_op],
                validate_only=dry_run,
                operations_field="mutate_operations",
            )
        )

    def _done(response) -> str:
        campaign_rn = response.mutate_operation_responses[
            1
        ].campaign_result.resource_name
        return (
            f"Successfully created campaign '{name}' ({status}). "
            f"ID: {utils.extract_id(campaign_rn)}"
        )

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload=payload,
        change_items=change_items,
        mutate=_mutate,
        on_success=_done,
        error_prefix="Failed to create campaign",
    )


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def set_campaign_status(
    customer_id: str | int,
    campaign_id: str | int,
    status: Literal["PAUSED", "ENABLED"],
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Pauses or enables an existing campaign. Enabling can start spending money.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID.
        status: PAUSED or ENABLED.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    camp_id = mh.require_numeric_id(campaign_id, "campaign_id")
    if status not in ("PAUSED", "ENABLED"):
        raise ToolError("Error: 'status' must be PAUSED or ENABLED.")

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(
        ga_client, "CampaignService"
    )

    resource_name = f"customers/{cid}/campaigns/{camp_id}"
    op = ga_client.get_type("CampaignOperation")
    op.update.resource_name = resource_name
    op.update.status = ga_client.enums.CampaignStatusEnum[status]
    op.update_mask.paths.append("status")

    change_items = pac.build_change_items(
        operation="UPDATE",
        entity_readable="Campaign",
        primary_key_readable=resource_name,
        fields={"Status": status},
    )

    def _mutate(dry_run: bool):
        return service.mutate_campaigns(
            request=mh.build_request(
                ga_client, "MutateCampaignsRequest", cid, [op], validate_only=dry_run
            )
        )

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "set_campaign_status",
            "campaign_id": camp_id,
            "status": status,
        },
        change_items=change_items,
        mutate=_mutate,
        on_success=lambda _r: f"Campaign {camp_id} is now {status}.",
        error_prefix="Failed to update campaign status",
    )


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def update_campaign_budget(
    customer_id: str | int,
    campaign_id: str | int,
    daily_budget: float,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Changes the daily budget of an existing campaign.

    Looks up the campaign's budget, shows the current and the new amount, and
    warns when the budget is shared with other campaigns (the change would
    then affect all of them). Raising a budget can increase spending.

    Idea for this tool: WEYERSK/Google-Ads-MCP `update_budget` (MIT).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID whose budget should change.
        daily_budget: New daily budget in the account's currency units.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    camp_id = mh.require_numeric_id(campaign_id, "campaign_id")
    new_micros = mh.to_micros(daily_budget, "daily_budget")

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    search_service = utils.get_googleads_service_from_client(
        ga_client, "GoogleAdsService"
    )
    rows = list(
        search_service.search(
            customer_id=cid,
            query=(
                "SELECT campaign.campaign_budget, campaign_budget.amount_micros, "
                "campaign_budget.explicitly_shared, campaign_budget.reference_count "
                f"FROM campaign WHERE campaign.id = {camp_id}"
            ),
        )
    )
    if not rows:
        raise ToolError(f"Error: campaign {camp_id} not found in {cid}.")
    row = rows[0]
    budget_rn = row.campaign.campaign_budget
    old_micros = int(row.campaign_budget.amount_micros)
    shared_with = int(row.campaign_budget.reference_count)
    is_shared = bool(row.campaign_budget.explicitly_shared) or shared_with > 1

    service = utils.get_googleads_service_from_client(
        ga_client, "CampaignBudgetService"
    )
    op = ga_client.get_type("CampaignBudgetOperation")
    op.update.resource_name = budget_rn
    op.update.amount_micros = new_micros
    op.update_mask.paths.append("amount_micros")

    change_items = pac.build_change_items(
        operation="UPDATE",
        entity_readable="Campaign budget",
        primary_key_readable=budget_rn,
        fields={
            "Daily budget": daily_budget,
            "WARNING": (
                f"budget is shared by {shared_with} campaigns; "
                "the change affects all of them"
                if is_shared
                else None
            ),
        },
        original_fields={"Daily budget": old_micros / 1_000_000},
    )

    def _mutate(dry_run: bool):
        return service.mutate_campaign_budgets(
            request=mh.build_request(
                ga_client, "MutateCampaignBudgetsRequest", cid, [op], validate_only=dry_run
            )
        )

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "update_campaign_budget",
            "campaign_id": camp_id,
            "budget": budget_rn,
            "old_micros": old_micros,
            "new_micros": new_micros,
        },
        change_items=change_items,
        mutate=_mutate,
        on_success=lambda _r: (
            f"Budget of campaign {camp_id} changed from "
            f"{old_micros / 1_000_000:.2f} to {daily_budget:.2f} per day."
        ),
        error_prefix="Failed to update campaign budget",
    )
