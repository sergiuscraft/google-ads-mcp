# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for creating and updating Google Ads ad groups."""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils

ad_groups_mcp = FastMCP("ad_groups")


@ad_groups_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def create_ad_group(
    customer_id: str | int,
    campaign_id: str | int,
    name: str,
    cpc_bid: float | None = None,
    status: Literal["PAUSED", "ENABLED"] = "PAUSED",
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Creates a standard Search ad group in an existing campaign (PAUSED by default).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric ID of the parent campaign.
        name: Ad group name.
        cpc_bid: Optional default max CPC in account currency units (manual CPC campaigns).
        status: PAUSED (default, safe) or ENABLED.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    camp_id = mh.require_numeric_id(campaign_id, "campaign_id")
    if not name or not name.strip():
        raise ToolError("Error: 'name' is required.")
    name = name.strip()
    bid_micros = mh.to_micros(cpc_bid, "cpc_bid") if cpc_bid is not None else None

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(ga_client, "AdGroupService")

    op = ga_client.get_type("AdGroupOperation")
    ad_group = op.create
    ad_group.name = name
    ad_group.campaign = f"customers/{cid}/campaigns/{camp_id}"
    ad_group.status = ga_client.enums.AdGroupStatusEnum[status]
    ad_group.type_ = ga_client.enums.AdGroupTypeEnum.SEARCH_STANDARD
    if bid_micros is not None:
        ad_group.cpc_bid_micros = bid_micros

    change_items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Ad group",
        primary_key_readable=name,
        fields={
            "Name": name,
            "Campaign ID": camp_id,
            "Status": status,
            "Max CPC": cpc_bid,
        },
    )

    def _mutate(dry_run: bool):
        return service.mutate_ad_groups(
            customer_id=cid, operations=[op], validate_only=dry_run
        )

    def _done(response) -> str:
        rn = response.results[0].resource_name
        return (
            f"Successfully created ad group '{name}' ({status}). "
            f"ID: {utils.extract_id(rn)}"
        )

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "create_ad_group",
            "campaign_id": camp_id,
            "name": name,
            "cpc_bid_micros": bid_micros,
            "status": status,
        },
        change_items=change_items,
        mutate=_mutate,
        on_success=_done,
        error_prefix="Failed to create ad group",
    )


@ad_groups_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def update_ad_group(
    customer_id: str | int,
    ad_group_id: str | int,
    status: Literal["PAUSED", "ENABLED"] | None = None,
    cpc_bid: float | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Pauses/enables an ad group and/or changes its default max CPC bid.

    At least one of `status` or `cpc_bid` is required.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        ad_group_id: Numeric ad group ID.
        status: Optional new status, PAUSED or ENABLED.
        cpc_bid: Optional new default max CPC in account currency units.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    ag_id = mh.require_numeric_id(ad_group_id, "ad_group_id")
    if status is None and cpc_bid is None:
        raise ToolError("Error: provide at least one of 'status' or 'cpc_bid'.")
    if status is not None and status not in ("PAUSED", "ENABLED"):
        raise ToolError("Error: 'status' must be PAUSED or ENABLED.")
    bid_micros = mh.to_micros(cpc_bid, "cpc_bid") if cpc_bid is not None else None

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(ga_client, "AdGroupService")

    resource_name = f"customers/{cid}/adGroups/{ag_id}"
    op = ga_client.get_type("AdGroupOperation")
    op.update.resource_name = resource_name
    if status is not None:
        op.update.status = ga_client.enums.AdGroupStatusEnum[status]
        op.update_mask.paths.append("status")
    if bid_micros is not None:
        op.update.cpc_bid_micros = bid_micros
        op.update_mask.paths.append("cpc_bid_micros")

    change_items = pac.build_change_items(
        operation="UPDATE",
        entity_readable="Ad group",
        primary_key_readable=resource_name,
        fields={"Status": status, "Max CPC": cpc_bid},
    )

    def _mutate(dry_run: bool):
        return service.mutate_ad_groups(
            customer_id=cid, operations=[op], validate_only=dry_run
        )

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "update_ad_group",
            "ad_group_id": ag_id,
            "status": status,
            "cpc_bid_micros": bid_micros,
        },
        change_items=change_items,
        mutate=_mutate,
        on_success=lambda _r: f"Ad group {ag_id} updated.",
        error_prefix="Failed to update ad group",
    )
