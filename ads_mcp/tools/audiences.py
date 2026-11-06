# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for attaching existing audience (user) lists to campaigns and ad groups.

List the available lists with ``search_search`` on ``user_list`` and what is
attached on ``campaign_criterion`` / ``ad_group_criterion`` (type USER_LIST).

Audience mode matters for Search:
* OBSERVATION: ads show to everyone as before; the list only lets you adjust
  bids for its members.
* TARGETING: ads show ONLY to members of the attached lists, which can shrink
  reach drastically.
The mode is an explicit parameter and is set in the same request, because an
existing campaign or ad group may already be in TARGETING mode.
"""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils

audiences_mcp = FastMCP("audiences")

Mode = Literal["OBSERVATION", "TARGETING"]


def _lookup_lists(search, cid: str, ids: list[str]) -> list:
    rows = list(
        search.search(
            customer_id=cid,
            query=(
                "SELECT user_list.id, user_list.name, user_list.type, "
                "user_list.membership_status, user_list.size_for_search "
                f"FROM user_list WHERE user_list.id IN ({', '.join(ids)})"
            ),
        )
    )
    found = {str(r.user_list.id): r.user_list for r in rows}
    missing = [i for i in ids if i not in found]
    if missing:
        raise ToolError(f"Error: user list(s) not found in {cid}: {missing}.")
    return [found[i] for i in ids]


def _describe(lists: list) -> list[str]:
    return [
        f"{u.id} '{u.name}' ({u.type_.name}, {u.membership_status.name}, "
        f"~{u.size_for_search} users in search)"
        for u in lists
    ]


def _current_mode(restrictions) -> str:
    """OBSERVATION / TARGETING / NOT_SET for the AUDIENCE dimension."""
    for r in restrictions:
        if r.targeting_dimension.name == "AUDIENCE":
            return "OBSERVATION" if r.bid_only else "TARGETING"
    return "NOT_SET"


def _mode_operation(ga_client, mode: str):
    op = ga_client.get_type("TargetRestrictionOperation")
    op.operator = type(op).Operator.ADD
    op.value.targeting_dimension = ga_client.enums.TargetingDimensionEnum.AUDIENCE
    op.value.bid_only = mode == "OBSERVATION"
    return op


def _add_audiences(
    *,
    tool: str,
    level: str,
    customer_id,
    target_id,
    user_list_ids,
    mode: str,
    percent_change,
    login_customer_id,
    validate_only,
    checksum,
):
    cid = mh.require_customer_id(customer_id)
    tid = mh.require_numeric_id(target_id, f"{level}_id")
    ids = mh.require_ids(user_list_ids, "user_list_ids")
    if mode not in ("OBSERVATION", "TARGETING"):
        raise ToolError("Error: 'mode' must be OBSERVATION or TARGETING.")
    modifier = (
        mh.percent_to_modifier(percent_change, allow_exclude=False)
        if percent_change is not None
        else None
    )
    ga_client, search = mh.get_client_and_service(login_customer_id, "GoogleAdsService")
    lists = _lookup_lists(search, cid, ids)

    table, setting = ("campaign", "campaign") if level == "campaign" else ("ad_group", "ad_group")
    rows = list(
        search.search(
            customer_id=cid,
            query=(
                f"SELECT {setting}.targeting_setting.target_restrictions "
                f"FROM {table} WHERE {table}.id = {tid}"
            ),
        )
    )
    if not rows:
        raise ToolError(f"Error: {level} {tid} not found in {cid}.")
    current = _current_mode(getattr(rows[0], setting).targeting_setting.target_restrictions)

    ops = []
    if current != mode:
        mode_op = ga_client.get_type("MutateOperation")
        entity_op = (
            mode_op.campaign_operation if level == "campaign" else mode_op.ad_group_operation
        )
        entity_op.update.resource_name = (
            f"customers/{cid}/campaigns/{tid}"
            if level == "campaign"
            else f"customers/{cid}/adGroups/{tid}"
        )
        entity_op.update.targeting_setting.target_restriction_operations.append(
            _mode_operation(ga_client, mode)
        )
        entity_op.update_mask.paths.append("targeting_setting.target_restriction_operations")
        ops.append(mode_op)
    for lst in lists:
        op = ga_client.get_type("MutateOperation")
        if level == "campaign":
            crit = op.campaign_criterion_operation.create
            crit.campaign = f"customers/{cid}/campaigns/{tid}"
        else:
            crit = op.ad_group_criterion_operation.create
            crit.ad_group = f"customers/{cid}/adGroups/{tid}"
            crit.status = ga_client.enums.AdGroupCriterionStatusEnum.ENABLED
        crit.user_list.user_list = f"customers/{cid}/userLists/{lst.id}"
        if modifier is not None:
            crit.bid_modifier = modifier
        ops.append(op)

    service = utils.get_googleads_service_from_client(ga_client, "GoogleAdsService")
    mode_text = f"{mode} (was {current})" if current != mode else f"{mode} (unchanged)"
    warning = (
        "TARGETING: ads will show ONLY to members of these lists; reach can drop sharply"
        if mode == "TARGETING"
        else None
    )
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": tool,
            "level": level,
            "target": tid,
            "lists": [str(u.id) for u in lists],
            "mode": mode,
            "current_mode": current,
            "modifier": modifier,
        },
        change_items=pac.build_change_items(
            operation="CREATE",
            entity_readable=f"Audience ({level})",
            primary_key_readable=f"{level} {tid}",
            fields={
                "Lists": _describe(lists),
                "Audience mode": mode_text,
                "Bid adjustment": (
                    f"{percent_change:+g}%" if percent_change is not None else None
                ),
                "WARNING": warning,
            },
        ),
        mutate=lambda dry: service.mutate(
            request=mh.build_request(
                ga_client,
                "MutateGoogleAdsRequest",
                cid,
                ops,
                validate_only=dry,
                operations_field="mutate_operations",
            )
        ),
        on_success=lambda r: (
            f"Attached {len(lists)} list(s) to {level} {tid} in {mode} mode."
        ),
        error_prefix="Failed to attach audiences",
    )


@audiences_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def add_campaign_audience(
    customer_id: str | int,
    campaign_id: str | int,
    user_list_ids: list[str | int],
    mode: Mode = "OBSERVATION",
    percent_change: float | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Attaches existing audience lists to a campaign, in the same request setting the campaign's audience mode.

    OBSERVATION (default) keeps reach unchanged and only lets you adjust bids for list members. TARGETING shows ads only to list members. The mode is always set explicitly because a campaign may already be in TARGETING mode (the proposal shows the old and new mode).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID.
        user_list_ids: 1-100 audience list IDs (`user_list.id`, see `search_search` on `user_list`).
        mode: OBSERVATION (default) or TARGETING.
        percent_change: Optional bid adjustment for members, -90..+900 (percent).
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    return _add_audiences(
        tool="add_campaign_audience",
        level="campaign",
        customer_id=customer_id,
        target_id=campaign_id,
        user_list_ids=user_list_ids,
        mode=mode,
        percent_change=percent_change,
        login_customer_id=login_customer_id,
        validate_only=validate_only,
        checksum=checksum,
    )


@audiences_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def add_ad_group_audience(
    customer_id: str | int,
    ad_group_id: str | int,
    user_list_ids: list[str | int],
    mode: Mode = "OBSERVATION",
    percent_change: float | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Attaches existing audience lists to an ad group, in the same request setting the ad group's audience mode.

    OBSERVATION (default) keeps reach unchanged and only lets you adjust bids for list members. TARGETING shows ads only to list members. The mode is always set explicitly (the proposal shows the old and new mode).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        ad_group_id: Numeric ad group ID.
        user_list_ids: 1-100 audience list IDs (`user_list.id`).
        mode: OBSERVATION (default) or TARGETING.
        percent_change: Optional bid adjustment for members, -90..+900 (percent).
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    return _add_audiences(
        tool="add_ad_group_audience",
        level="ad_group",
        customer_id=customer_id,
        target_id=ad_group_id,
        user_list_ids=user_list_ids,
        mode=mode,
        percent_change=percent_change,
        login_customer_id=login_customer_id,
        validate_only=validate_only,
        checksum=checksum,
    )


@audiences_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def remove_ad_group_audiences(
    customer_id: str | int,
    ad_group_id: str | int,
    criterion_ids: list[str | int],
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Detaches audience lists from an ad group (the lists themselves are kept). For campaign-level audiences use `remove_campaign_criteria`. Find `criterion_id` with `search_search` on `ad_group_criterion` (type USER_LIST).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        ad_group_id: Numeric ad group ID.
        criterion_ids: 1-100 numeric criterion IDs of the attached audiences.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    ag_id = mh.require_numeric_id(ad_group_id, "ad_group_id")
    crit_ids = mh.require_ids(criterion_ids, "criterion_ids")
    ga_client, service = mh.get_client_and_service(login_customer_id, "AdGroupCriterionService")
    names = [f"customers/{cid}/adGroupCriteria/{ag_id}~{c}" for c in crit_ids]
    ops = []
    for rn in names:
        op = ga_client.get_type("AdGroupCriterionOperation")
        op.remove = rn
        ops.append(op)
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={"tool": "remove_ad_group_audiences", "rns": names},
        change_items=pac.build_change_items(
            operation="REMOVE",
            entity_readable="Audience (ad group)",
            primary_key_readable=f"ad group {ag_id}",
            fields={"Criterion IDs": crit_ids},
        ),
        mutate=lambda dry: service.mutate_ad_group_criteria(
            request=mh.build_request(
                ga_client, "MutateAdGroupCriteriaRequest", cid, ops, validate_only=dry
            )
        ),
        on_success=lambda r: f"Detached {len(r.results)} audience(s) from ad group {ag_id}.",
        error_prefix="Failed to remove audiences",
    )
