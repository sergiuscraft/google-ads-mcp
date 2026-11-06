# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for campaign budgets, including shared budgets used by several campaigns.

List budgets with ``search_search`` on ``campaign_budget`` (fields such as
``campaign_budget.id``, ``.name``, ``.amount_micros``, ``.explicitly_shared``,
``.reference_count`` = number of campaigns using it).
"""

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils

budgets_mcp = FastMCP("budgets")

_BUDGET_FIELDS = (
    "campaign_budget.id, campaign_budget.name, campaign_budget.amount_micros, "
    "campaign_budget.explicitly_shared, campaign_budget.reference_count, "
    "campaign_budget.status"
)


def fetch_budget(search_service, cid: str, budget_id: str):
    """Returns the budget row (or raises if missing / removed)."""
    rows = list(
        search_service.search(
            customer_id=cid,
            query=(
                f"SELECT {_BUDGET_FIELDS} FROM campaign_budget "
                f"WHERE campaign_budget.id = {budget_id}"
            ),
        )
    )
    if not rows or rows[0].campaign_budget.status.name == "REMOVED":
        raise ToolError(f"Error: budget {budget_id} not found in {cid}.")
    return rows[0].campaign_budget


@budgets_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def create_campaign_budget(
    customer_id: str | int,
    name: str,
    daily_budget: float,
    shared: bool = True,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Creates a standalone daily budget. A shared budget (default) can be used by several campaigns; its limit then applies to all of them together.

    Use it afterwards with `set_campaign_budget`, or pass its ID as `existing_budget_id` to `create_campaign`.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        name: Budget name, unique in the account.
        daily_budget: Daily amount in the account's currency units.
        shared: True (default) = shareable by several campaigns; False = for one campaign only.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    if not name or not name.strip():
        raise ToolError("Error: 'name' is required.")
    name = name.strip()
    micros = mh.to_micros(daily_budget, "daily_budget")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "CampaignBudgetService"
    )
    op = ga_client.get_type("CampaignBudgetOperation")
    op.create.name = name
    op.create.amount_micros = micros
    op.create.delivery_method = ga_client.enums.BudgetDeliveryMethodEnum.STANDARD
    op.create.explicitly_shared = shared
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "create_campaign_budget",
            "name": name,
            "micros": micros,
            "shared": shared,
        },
        change_items=pac.build_change_items(
            operation="CREATE",
            entity_readable="Campaign budget",
            primary_key_readable=name,
            fields={"Name": name, "Daily budget": daily_budget, "Shared": shared},
        ),
        mutate=lambda dry: service.mutate_campaign_budgets(
            request=mh.build_request(
                ga_client, "MutateCampaignBudgetsRequest", cid, [op], validate_only=dry
            )
        ),
        on_success=lambda r: (
            f"Created budget '{name}'. ID: "
            f"{utils.extract_id(r.results[0].resource_name)}"
        ),
        error_prefix="Failed to create budget",
    )


@budgets_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def update_budget(
    customer_id: str | int,
    budget_id: str | int,
    daily_budget: float,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Changes the daily amount of a budget by its ID (also works for budgets not currently used by any campaign). Raising it can increase spending. Warns when several campaigns share it.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        budget_id: Numeric budget ID (`campaign_budget.id`).
        daily_budget: New daily amount in the account's currency units.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    bid = mh.require_numeric_id(budget_id, "budget_id")
    new_micros = mh.to_micros(daily_budget, "daily_budget")
    ga_client, search = mh.get_client_and_service(login_customer_id, "GoogleAdsService")
    service = utils.get_googleads_service_from_client(ga_client, "CampaignBudgetService")
    current = fetch_budget(search, cid, bid)
    old_micros = int(current.amount_micros)
    uses = int(current.reference_count)
    shared = bool(current.explicitly_shared) or uses > 1
    resource_name = f"customers/{cid}/campaignBudgets/{bid}"
    op = ga_client.get_type("CampaignBudgetOperation")
    op.update.resource_name = resource_name
    op.update.amount_micros = new_micros
    op.update_mask.paths.append("amount_micros")
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "update_budget",
            "budget": bid,
            "old_micros": old_micros,
            "new_micros": new_micros,
        },
        change_items=pac.build_change_items(
            operation="UPDATE",
            entity_readable="Campaign budget",
            primary_key_readable=f"{current.name} ({resource_name})",
            fields={
                "Daily budget": daily_budget,
                "WARNING": (
                    f"budget is used by {uses} campaign(s); the limit applies to all of them together"
                    if shared
                    else None
                ),
            },
            original_fields={"Daily budget": old_micros / 1_000_000},
        ),
        mutate=lambda dry: service.mutate_campaign_budgets(
            request=mh.build_request(
                ga_client, "MutateCampaignBudgetsRequest", cid, [op], validate_only=dry
            )
        ),
        on_success=lambda _r: (
            f"Budget {bid} changed from {old_micros / 1_000_000:.2f} to {daily_budget:.2f} per day."
        ),
        error_prefix="Failed to update budget",
    )


@budgets_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def set_campaign_budget(
    customer_id: str | int,
    campaign_id: str | int,
    budget_id: str | int,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Points a campaign at an existing budget (e.g. a shared one). The campaign's old budget stays in the account, unused.

    A budget that is not shared can serve only one campaign, so a non-shared budget already used elsewhere is refused. The new budget's amount applies to the campaign immediately, which can raise or lower spending.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID.
        budget_id: Numeric ID of the budget to use.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    camp_id = mh.require_numeric_id(campaign_id, "campaign_id")
    bid = mh.require_numeric_id(budget_id, "budget_id")
    ga_client, search = mh.get_client_and_service(login_customer_id, "GoogleAdsService")
    service = utils.get_googleads_service_from_client(ga_client, "CampaignService")
    target = fetch_budget(search, cid, bid)
    shared = bool(target.explicitly_shared)
    uses = int(target.reference_count)
    campaign_rows = list(
        search.search(
            customer_id=cid,
            query=f"SELECT campaign.campaign_budget FROM campaign WHERE campaign.id = {camp_id}",
        )
    )
    if not campaign_rows:
        raise ToolError(f"Error: campaign {camp_id} not found in {cid}.")
    current_rn = campaign_rows[0].campaign.campaign_budget
    new_rn = f"customers/{cid}/campaignBudgets/{bid}"
    if current_rn == new_rn:
        raise ToolError(f"Error: campaign {camp_id} already uses budget {bid}.")
    if not shared and uses > 0:
        raise ToolError(
            f"Error: budget {bid} is not shared and already used by {uses} campaign(s); "
            "create a shared budget with `create_campaign_budget` instead."
        )
    resource_name = f"customers/{cid}/campaigns/{camp_id}"
    op = ga_client.get_type("CampaignOperation")
    op.update.resource_name = resource_name
    op.update.campaign_budget = new_rn
    op.update_mask.paths.append("campaign_budget")
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "set_campaign_budget",
            "campaign": camp_id,
            "from": current_rn,
            "to": new_rn,
        },
        change_items=pac.build_change_items(
            operation="UPDATE",
            entity_readable="Campaign",
            primary_key_readable=resource_name,
            fields={
                "Budget": f"{target.name} ({new_rn})",
                "Budget daily amount": int(target.amount_micros) / 1_000_000,
                "Budget shared with": (
                    f"{uses} campaign(s) already" if shared and uses else None
                ),
            },
            original_fields={"Budget": current_rn},
        ),
        mutate=lambda dry: service.mutate_campaigns(
            request=mh.build_request(
                ga_client, "MutateCampaignsRequest", cid, [op], validate_only=dry
            )
        ),
        on_success=lambda _r: f"Campaign {camp_id} now uses budget {bid}.",
        error_prefix="Failed to set campaign budget",
    )


@budgets_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def remove_budget(
    customer_id: str | int,
    budget_id: str | int,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Permanently removes an unused budget (reference_count 0). Cannot be undone.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        budget_id: Numeric budget ID.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    bid = mh.require_numeric_id(budget_id, "budget_id")
    ga_client, search = mh.get_client_and_service(login_customer_id, "GoogleAdsService")
    service = utils.get_googleads_service_from_client(ga_client, "CampaignBudgetService")
    current = fetch_budget(search, cid, bid)
    uses = int(current.reference_count)
    if uses > 0:
        raise ToolError(
            f"Error: budget {bid} is still used by {uses} campaign(s); move them with "
            "`set_campaign_budget` first."
        )
    resource_name = f"customers/{cid}/campaignBudgets/{bid}"
    op = ga_client.get_type("CampaignBudgetOperation")
    op.remove = resource_name
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={"tool": "remove_budget", "rn": resource_name},
        change_items=pac.build_change_items(
            operation="REMOVE",
            entity_readable="Campaign budget",
            primary_key_readable=f"{current.name} ({resource_name})",
            fields={"Resource": resource_name},
        ),
        mutate=lambda dry: service.mutate_campaign_budgets(
            request=mh.build_request(
                ga_client, "MutateCampaignBudgetsRequest", cid, [op], validate_only=dry
            )
        ),
        on_success=lambda _r: f"Removed budget {bid}.",
        error_prefix="Failed to remove budget",
    )
