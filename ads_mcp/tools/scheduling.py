# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for campaign ad schedules and bid adjustments (device, schedule, location).

Adjustments are given as a percent change: +20 raises bids 20% (modifier 1.2),
-30 lowers them 30% (0.7), -100 (devices only) stops showing ads there.
Inspect what is set with ``search_search`` on ``campaign_criterion``
(``campaign_criterion.type`` = AD_SCHEDULE / DEVICE / LOCATION and ``campaign_criterion.bid_modifier``).
"""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils

scheduling_mcp = FastMCP("scheduling")

Day = Literal[
    "MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY"
]
Device = Literal["MOBILE", "DESKTOP", "TABLET"]
_DEVICE_CRITERION_IDS = {"DESKTOP": "30000", "MOBILE": "30001", "TABLET": "30002"}
_MINUTES = {0: "ZERO", 15: "FIFTEEN", 30: "THIRTY", 45: "FORTY_FIVE"}


class ScheduleSlot(BaseModel):
    """One time window on one weekday (account time zone)."""

    day_of_week: Day
    start_hour: int = Field(ge=0, le=23, description="0-23")
    end_hour: int = Field(ge=0, le=24, description="0-24 (24 = midnight)")
    start_minute: Literal[0, 15, 30, 45] = 0
    end_minute: Literal[0, 15, 30, 45] = 0
    percent_change: float | None = Field(
        default=None,
        description="Optional bid adjustment for this window, -90..+900 (percent).",
    )


_modifier = mh.percent_to_modifier


def _current_percent(criterion) -> float:
    """Current adjustment in percent; a criterion without bid_modifier means none (0%).

    proto-plus returns 0.0 for an unset bid_modifier, which would read as -100%,
    so presence is checked first.
    """
    if "bid_modifier" not in criterion:
        return 0.0
    return round((float(criterion.bid_modifier) - 1) * 100, 2)


def _fetch_criterion(service, cid: str, campaign_id: str, where: str):
    rows = list(
        service.search(
            customer_id=cid,
            query=(
                "SELECT campaign_criterion.criterion_id, campaign_criterion.type, "
                "campaign_criterion.bid_modifier FROM campaign_criterion "
                f"WHERE campaign.id = {campaign_id} AND {where}"
            ),
        )
    )
    return rows[0] if rows else None


def _slot_text(s: ScheduleSlot) -> str:
    return (
        f"{s.day_of_week} {s.start_hour:02d}:{s.start_minute:02d}-"
        f"{s.end_hour:02d}:{s.end_minute:02d}"
        + (f" ({s.percent_change:+g}%)" if s.percent_change is not None else "")
    )


@scheduling_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def add_campaign_ad_schedule(
    customer_id: str | int,
    campaign_id: str | int,
    slots: list[ScheduleSlot],
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Adds ad schedule windows to a campaign, each optionally with a bid adjustment.

    Once a campaign has any schedule window, ads run only inside the scheduled windows, so add every window you want (for example the whole week, with adjustments on the busy hours). To remove a window use `remove_campaign_criteria`.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID.
        slots: 1-84 windows: day_of_week, start_hour, end_hour, optional start_minute/end_minute (0, 15, 30, 45) and optional percent_change (-90..+900).
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    camp_id = mh.require_numeric_id(campaign_id, "campaign_id")
    parsed = [ScheduleSlot.model_validate(s) for s in (slots or [])]
    if not 1 <= len(parsed) <= 84:
        raise ToolError("Error: 'slots' needs between 1 and 84 windows.")
    for s in parsed:
        start = s.start_hour * 60 + s.start_minute
        end = s.end_hour * 60 + s.end_minute
        if end <= start or end > 24 * 60:
            raise ToolError(f"Error: invalid window {_slot_text(s)}: end must be after start.")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "CampaignCriterionService"
    )
    ops = []
    for s in parsed:
        op = ga_client.get_type("CampaignCriterionOperation")
        crit = op.create
        crit.campaign = f"customers/{cid}/campaigns/{camp_id}"
        sched = crit.ad_schedule
        sched.day_of_week = ga_client.enums.DayOfWeekEnum[s.day_of_week]
        sched.start_hour = s.start_hour
        sched.end_hour = s.end_hour
        sched.start_minute = ga_client.enums.MinuteOfHourEnum[_MINUTES[s.start_minute]]
        sched.end_minute = ga_client.enums.MinuteOfHourEnum[_MINUTES[s.end_minute]]
        if s.percent_change is not None:
            crit.bid_modifier = _modifier(s.percent_change, allow_exclude=False)
        ops.append(op)
    texts = [_slot_text(s) for s in parsed]
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "add_campaign_ad_schedule",
            "campaign_id": camp_id,
            "slots": [s.model_dump() for s in parsed],
        },
        change_items=pac.build_change_items(
            operation="CREATE",
            entity_readable="Ad schedule",
            primary_key_readable=f"campaign {camp_id}",
            fields={"Windows": texts},
        ),
        mutate=lambda dry: service.mutate_campaign_criteria(
            request=mh.build_request(
                ga_client, "MutateCampaignCriteriaRequest", cid, ops, validate_only=dry
            )
        ),
        on_success=lambda r: f"Added {len(r.results)} schedule window(s) to campaign {camp_id}.",
        error_prefix="Failed to add ad schedule",
    )


@scheduling_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def set_campaign_device_bid_modifier(
    customer_id: str | int,
    campaign_id: str | int,
    device: Device,
    percent_change: float,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Sets the bid adjustment for a device type on a campaign (creates it or updates the existing one).

    Use -100 to stop showing ads on that device, +20 to bid 20% more, 0 to reset to no adjustment.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID.
        device: MOBILE, DESKTOP or TABLET.
        percent_change: -100..+900 (percent).
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    camp_id = mh.require_numeric_id(campaign_id, "campaign_id")
    if device not in _DEVICE_CRITERION_IDS:
        raise ToolError("Error: 'device' must be MOBILE, DESKTOP or TABLET.")
    modifier = _modifier(percent_change, allow_exclude=True)
    ga_client, search = mh.get_client_and_service(login_customer_id, "GoogleAdsService")
    service = utils.get_googleads_service_from_client(ga_client, "CampaignCriterionService")
    existing = _fetch_criterion(
        search,
        cid,
        camp_id,
        f"campaign_criterion.type = 'DEVICE' AND campaign_criterion.device.type = '{device}'",
    )
    op = ga_client.get_type("CampaignCriterionOperation")
    if existing is not None:
        op.update.resource_name = (
            f"customers/{cid}/campaignCriteria/{camp_id}~{existing.campaign_criterion.criterion_id}"
        )
        op.update.bid_modifier = modifier
        op.update_mask.paths.append("bid_modifier")
        old_percent = _current_percent(existing.campaign_criterion)
        verb, originals = "UPDATE", {"Bid adjustment": f"{old_percent:+g}%"}
    else:
        op.create.campaign = f"customers/{cid}/campaigns/{camp_id}"
        op.create.device.type_ = ga_client.enums.DeviceEnum[device]
        op.create.bid_modifier = modifier
        verb, originals = "CREATE", None
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "set_campaign_device_bid_modifier",
            "campaign_id": camp_id,
            "device": device,
            "modifier": modifier,
            "op": verb,
        },
        change_items=pac.build_change_items(
            operation=verb,
            entity_readable=f"Device bid adjustment ({device})",
            primary_key_readable=f"campaign {camp_id}",
            fields={"Bid adjustment": f"{percent_change:+g}%"},
            original_fields=originals,
        ),
        mutate=lambda dry: service.mutate_campaign_criteria(
            request=mh.build_request(
                ga_client, "MutateCampaignCriteriaRequest", cid, [op], validate_only=dry
            )
        ),
        on_success=lambda _r: f"{device} bid adjustment on campaign {camp_id} set to {percent_change:+g}%.",
        error_prefix="Failed to set device bid adjustment",
    )


@scheduling_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def set_criterion_bid_modifier(
    customer_id: str | int,
    campaign_id: str | int,
    criterion_id: str | int,
    percent_change: float,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Changes the bid adjustment of an existing campaign criterion: a location, ad schedule window, device or audience list. Find `criterion_id` with `search_search` on `campaign_criterion`.

    Range -90..+900 percent (-100 only for devices).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID.
        criterion_id: Numeric criterion ID of a LOCATION, AD_SCHEDULE, DEVICE or USER_LIST criterion.
        percent_change: Percent change, e.g. 25 or -40.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    camp_id = mh.require_numeric_id(campaign_id, "campaign_id")
    crit_id = mh.require_numeric_id(criterion_id, "criterion_id")
    ga_client, search = mh.get_client_and_service(login_customer_id, "GoogleAdsService")
    service = utils.get_googleads_service_from_client(ga_client, "CampaignCriterionService")
    existing = _fetch_criterion(
        search, cid, camp_id, f"campaign_criterion.criterion_id = {crit_id}"
    )
    if existing is None:
        raise ToolError(f"Error: criterion {crit_id} not found in campaign {camp_id}.")
    ctype = existing.campaign_criterion.type_.name
    if ctype not in ("LOCATION", "AD_SCHEDULE", "DEVICE", "USER_LIST"):
        raise ToolError(
            f"Error: criterion {crit_id} is {ctype}; only LOCATION, AD_SCHEDULE, DEVICE and USER_LIST adjustments are supported."
        )
    modifier = _modifier(percent_change, allow_exclude=(ctype == "DEVICE"))
    resource_name = f"customers/{cid}/campaignCriteria/{camp_id}~{crit_id}"
    op = ga_client.get_type("CampaignCriterionOperation")
    op.update.resource_name = resource_name
    op.update.bid_modifier = modifier
    op.update_mask.paths.append("bid_modifier")
    old_percent = _current_percent(existing.campaign_criterion)
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "set_criterion_bid_modifier",
            "rn": resource_name,
            "modifier": modifier,
        },
        change_items=pac.build_change_items(
            operation="UPDATE",
            entity_readable=f"Bid adjustment ({ctype})",
            primary_key_readable=resource_name,
            fields={"Bid adjustment": f"{percent_change:+g}%"},
            original_fields={"Bid adjustment": f"{old_percent:+g}%"},
        ),
        mutate=lambda dry: service.mutate_campaign_criteria(
            request=mh.build_request(
                ga_client, "MutateCampaignCriteriaRequest", cid, [op], validate_only=dry
            )
        ),
        on_success=lambda _r: f"Bid adjustment of {ctype} criterion {crit_id} set to {percent_change:+g}%.",
        error_prefix="Failed to set bid adjustment",
    )
