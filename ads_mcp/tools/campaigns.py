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

_BIDDING = (
    "MANUAL_CPC",
    "MAXIMIZE_CLICKS",
    "MAXIMIZE_CONVERSIONS",
    "MAXIMIZE_CONVERSION_VALUE",
)
BiddingStrategy = Literal[
    "MANUAL_CPC",
    "MAXIMIZE_CLICKS",
    "MAXIMIZE_CONVERSIONS",
    "MAXIMIZE_CONVERSION_VALUE",
]


def _check_bidding(
    strategy: str | None,
    target_cpa: float | None,
    target_roas: float | None,
    max_cpc_limit: float | None,
) -> tuple[int | None, float | None, int | None]:
    """Validates a strategy with its optional goal; returns (cpa_micros, roas, ceiling_micros).

    A goal is only valid together with the strategy it belongs to, so what
    changes is always explicit:
    MAXIMIZE_CONVERSIONS + target_cpa, MAXIMIZE_CONVERSION_VALUE + target_roas,
    MAXIMIZE_CLICKS + max_cpc_limit.
    """
    if strategy is not None and strategy not in _BIDDING:
        raise ToolError(f"Error: 'bidding_strategy' must be one of {_BIDDING}.")
    if target_cpa is not None and strategy != "MAXIMIZE_CONVERSIONS":
        raise ToolError(
            "Error: 'target_cpa' needs bidding_strategy=MAXIMIZE_CONVERSIONS."
        )
    if target_roas is not None and strategy != "MAXIMIZE_CONVERSION_VALUE":
        raise ToolError(
            "Error: 'target_roas' needs bidding_strategy=MAXIMIZE_CONVERSION_VALUE."
        )
    if max_cpc_limit is not None and strategy != "MAXIMIZE_CLICKS":
        raise ToolError(
            "Error: 'max_cpc_limit' needs bidding_strategy=MAXIMIZE_CLICKS."
        )
    cpa = mh.to_micros(target_cpa, "target_cpa") if target_cpa is not None else None
    ceiling = (
        mh.to_micros(max_cpc_limit, "max_cpc_limit")
        if max_cpc_limit is not None
        else None
    )
    if target_roas is not None and not 0 < target_roas <= 1000:
        raise ToolError(
            "Error: 'target_roas' must be a ratio between 0 and 1000 (4.0 = 400%)."
        )
    return cpa, target_roas, ceiling


def _apply_bidding(
    ga_client,
    campaign,
    strategy: str,
    cpa_micros: int | None,
    roas: float | None,
    ceiling_micros: int | None,
) -> str:
    """Sets the strategy (and goal) on ``campaign``; returns the update-mask path.

    Google rejects masks that name a whole message, so the path names a leaf
    of the strategy message; copy_from marks the oneof as set.
    """
    if strategy == "MANUAL_CPC":
        campaign.manual_cpc.enhanced_cpc_enabled = False
        return "manual_cpc.enhanced_cpc_enabled"
    if strategy == "MAXIMIZE_CLICKS":
        # In the API, "Maximize clicks" is the `target_spend` strategy.
        ga_client.copy_from(campaign.target_spend, ga_client.get_type("TargetSpend"))
        if ceiling_micros is not None:
            campaign.target_spend.cpc_bid_ceiling_micros = ceiling_micros
        return "target_spend.cpc_bid_ceiling_micros"
    if strategy == "MAXIMIZE_CONVERSIONS":
        ga_client.copy_from(
            campaign.maximize_conversions, ga_client.get_type("MaximizeConversions")
        )
        if cpa_micros is not None:
            campaign.maximize_conversions.target_cpa_micros = cpa_micros
        return "maximize_conversions.target_cpa_micros"
    ga_client.copy_from(
        campaign.maximize_conversion_value,
        ga_client.get_type("MaximizeConversionValue"),
    )
    if roas is not None:
        campaign.maximize_conversion_value.target_roas = roas
    return "maximize_conversion_value.target_roas"


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def create_campaign(
    customer_id: str | int,
    name: str,
    daily_budget: float,
    bidding_strategy: BiddingStrategy = "MAXIMIZE_CLICKS",
    target_cpa: float | None = None,
    target_roas: float | None = None,
    max_cpc_limit: float | None = None,
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
        bidding_strategy: MANUAL_CPC, MAXIMIZE_CLICKS, MAXIMIZE_CONVERSIONS or MAXIMIZE_CONVERSION_VALUE. The conversion-based strategies need working conversion tracking.
        target_cpa: Optional target cost per conversion (account currency); only with MAXIMIZE_CONVERSIONS.
        target_roas: Optional target return on ad spend as a ratio (4.0 = 400%); only with MAXIMIZE_CONVERSION_VALUE.
        max_cpc_limit: Optional ceiling for a single click's bid (account currency); only with MAXIMIZE_CLICKS.
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
    cpa_micros, roas, ceiling_micros = _check_bidding(
        bidding_strategy, target_cpa, target_roas, max_cpc_limit
    )

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
    _apply_bidding(
        ga_client, campaign, bidding_strategy, cpa_micros, roas, ceiling_micros
    )

    payload = {
        "tool": "create_campaign",
        "name": name,
        "daily_budget_micros": budget_micros,
        "bidding_strategy": bidding_strategy,
        "target_cpa_micros": cpa_micros,
        "target_roas": roas,
        "max_cpc_limit_micros": ceiling_micros,
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
            "Target CPA": target_cpa,
            "Target ROAS": target_roas,
            "Max CPC limit": max_cpc_limit,
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


@campaigns_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def update_campaign(
    customer_id: str | int,
    campaign_id: str | int,
    name: str | None = None,
    bidding_strategy: BiddingStrategy | None = None,
    target_cpa: float | None = None,
    target_roas: float | None = None,
    max_cpc_limit: float | None = None,
    include_search_partners: bool | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Updates an existing campaign's name, bidding strategy and/or search-partner setting.

    Changing the bidding strategy changes how money is spent. At least one field is required.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID.
        name: Optional new name.
        bidding_strategy: Optional new strategy: MANUAL_CPC, MAXIMIZE_CLICKS, MAXIMIZE_CONVERSIONS or MAXIMIZE_CONVERSION_VALUE. A goal below must always be sent together with its strategy.
        target_cpa: Optional target cost per conversion (account currency); only with MAXIMIZE_CONVERSIONS.
        target_roas: Optional target return on ad spend as a ratio (4.0 = 400%); only with MAXIMIZE_CONVERSION_VALUE.
        max_cpc_limit: Optional ceiling for a single click's bid; only with MAXIMIZE_CLICKS.
        include_search_partners: Optional; serve on Google search partner sites.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    camp_id = mh.require_numeric_id(campaign_id, "campaign_id")
    if name is None and bidding_strategy is None and include_search_partners is None:
        raise ToolError("Error: provide at least one field to update.")
    if name is not None and not name.strip():
        raise ToolError("Error: 'name' must not be empty.")
    cpa_micros, roas, ceiling_micros = _check_bidding(
        bidding_strategy, target_cpa, target_roas, max_cpc_limit
    )

    ga_client, service = mh.get_client_and_service(
        login_customer_id, "CampaignService"
    )
    resource_name = f"customers/{cid}/campaigns/{camp_id}"
    op = ga_client.get_type("CampaignOperation")
    campaign = op.update
    campaign.resource_name = resource_name
    paths = op.update_mask.paths
    if name is not None:
        campaign.name = name.strip()
        paths.append("name")
    if include_search_partners is not None:
        campaign.network_settings.target_search_network = include_search_partners
        paths.append("network_settings.target_search_network")
    if bidding_strategy is not None:
        paths.append(
            _apply_bidding(
                ga_client, campaign, bidding_strategy, cpa_micros, roas, ceiling_micros
            )
        )

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "update_campaign",
            "rn": resource_name,
            "name": name,
            "bidding_strategy": bidding_strategy,
            "target_cpa_micros": cpa_micros,
            "target_roas": roas,
            "max_cpc_limit_micros": ceiling_micros,
            "include_search_partners": include_search_partners,
        },
        change_items=pac.build_change_items(
            operation="UPDATE",
            entity_readable="Campaign",
            primary_key_readable=resource_name,
            fields={
                "Name": name,
                "Bidding strategy": bidding_strategy,
                "Target CPA": target_cpa,
                "Target ROAS": target_roas,
                "Max CPC limit": max_cpc_limit,
                "Search partners": include_search_partners,
            },
        ),
        mutate=lambda dry: service.mutate_campaigns(
            request=mh.build_request(
                ga_client, "MutateCampaignsRequest", cid, [op], validate_only=dry
            )
        ),
        on_success=lambda _r: f"Campaign {camp_id} updated.",
        error_prefix="Failed to update campaign",
    )
