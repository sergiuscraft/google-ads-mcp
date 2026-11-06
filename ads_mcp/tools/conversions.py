# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for creating and updating conversion actions."""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac

conversions_mcp = FastMCP("conversions")

ActionType = Literal["WEBPAGE", "UPLOAD_CLICKS", "UPLOAD_CALLS", "CLICK_TO_CALL"]
Category = Literal[
    "DEFAULT",
    "PURCHASE",
    "SIGNUP",
    "CONTACT",
    "SUBMIT_LEAD_FORM",
    "REQUEST_QUOTE",
    "BOOK_APPOINTMENT",
    "PHONE_CALL_LEAD",
    "QUALIFIED_LEAD",
    "CONVERTED_LEAD",
    "PAGE_VIEW",
]


@conversions_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def create_conversion_action(
    customer_id: str | int,
    name: str,
    action_type: ActionType = "WEBPAGE",
    category: Category = "DEFAULT",
    counting: Literal["ONE_PER_CLICK", "MANY_PER_CLICK"] = "ONE_PER_CLICK",
    default_value: float | None = None,
    currency_code: str | None = None,
    click_lookback_days: int = 30,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Creates a conversion action. For WEBPAGE actions read the tag snippet afterwards with `search_search` on `conversion_action` (field `conversion_action.tag_snippets`) and install it on the site.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        name: Conversion action name (unique in the account).
        action_type: WEBPAGE (site tag), UPLOAD_CLICKS, UPLOAD_CALLS or CLICK_TO_CALL.
        category: What the conversion represents, e.g. PURCHASE or SUBMIT_LEAD_FORM.
        counting: ONE_PER_CLICK (leads) or MANY_PER_CLICK (purchases).
        default_value: Optional fixed value per conversion; requires `currency_code`.
        currency_code: ISO currency code such as "USD" or "UAH".
        click_lookback_days: Click-through conversion window in days (1-90).
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    if not name or not name.strip():
        raise ToolError("Error: 'name' is required.")
    name = name.strip()
    if not 1 <= click_lookback_days <= 90:
        raise ToolError("Error: 'click_lookback_days' must be between 1 and 90.")
    if (default_value is None) != (currency_code is None):
        raise ToolError(
            "Error: 'default_value' and 'currency_code' must be given together."
        )
    if default_value is not None and default_value < 0:
        raise ToolError("Error: 'default_value' must not be negative.")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "ConversionActionService"
    )
    op = ga_client.get_type("ConversionActionOperation")
    action = op.create
    action.name = name
    action.type_ = ga_client.enums.ConversionActionTypeEnum[action_type]
    action.category = ga_client.enums.ConversionActionCategoryEnum[category]
    action.status = ga_client.enums.ConversionActionStatusEnum.ENABLED
    action.counting_type = ga_client.enums.ConversionActionCountingTypeEnum[counting]
    action.click_through_lookback_window_days = click_lookback_days
    if default_value is not None:
        action.value_settings.default_value = default_value
        action.value_settings.always_use_default_value = True
        action.value_settings.default_currency_code = currency_code.upper()
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "create_conversion_action",
            "name": name,
            "type": action_type,
            "category": category,
            "counting": counting,
            "default_value": default_value,
            "currency": currency_code,
            "lookback": click_lookback_days,
        },
        change_items=pac.build_change_items(
            operation="CREATE",
            entity_readable="Conversion action",
            primary_key_readable=name,
            fields={
                "Name": name,
                "Type": action_type,
                "Category": category,
                "Counting": counting,
                "Default value": (
                    f"{default_value} {currency_code.upper()}"
                    if default_value is not None
                    else None
                ),
                "Click lookback days": click_lookback_days,
            },
        ),
        mutate=lambda dry: service.mutate_conversion_actions(
            request=mh.build_request(
                ga_client,
                "MutateConversionActionsRequest",
                cid,
                [op],
                validate_only=dry,
            )
        ),
        on_success=lambda r: (
            f"Created conversion action '{name}'. "
            f"Resource: {r.results[0].resource_name}"
        ),
        error_prefix="Failed to create conversion action",
    )


@conversions_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def update_conversion_action(
    customer_id: str | int,
    conversion_action_id: str | int,
    name: str | None = None,
    status: Literal["ENABLED", "HIDDEN"] | None = None,
    primary_for_goal: bool | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Renames, hides/enables, or marks a conversion action as primary (used for bidding). At least one field is required.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        conversion_action_id: Numeric conversion action ID.
        name: Optional new name.
        status: Optional ENABLED or HIDDEN.
        primary_for_goal: Optional; True = optimise bidding for it, False = observation only.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    action_id = mh.require_numeric_id(conversion_action_id, "conversion_action_id")
    if name is None and status is None and primary_for_goal is None:
        raise ToolError("Error: provide at least one field to update.")
    if name is not None and not name.strip():
        raise ToolError("Error: 'name' must not be empty.")
    if status is not None and status not in ("ENABLED", "HIDDEN"):
        raise ToolError("Error: 'status' must be ENABLED or HIDDEN.")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "ConversionActionService"
    )
    resource_name = f"customers/{cid}/conversionActions/{action_id}"
    op = ga_client.get_type("ConversionActionOperation")
    op.update.resource_name = resource_name
    if name is not None:
        op.update.name = name.strip()
        op.update_mask.paths.append("name")
    if status is not None:
        op.update.status = ga_client.enums.ConversionActionStatusEnum[status]
        op.update_mask.paths.append("status")
    if primary_for_goal is not None:
        op.update.primary_for_goal = primary_for_goal
        op.update_mask.paths.append("primary_for_goal")
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "update_conversion_action",
            "rn": resource_name,
            "name": name,
            "status": status,
            "primary": primary_for_goal,
        },
        change_items=pac.build_change_items(
            operation="UPDATE",
            entity_readable="Conversion action",
            primary_key_readable=resource_name,
            fields={
                "Name": name,
                "Status": status,
                "Primary for goal": primary_for_goal,
            },
        ),
        mutate=lambda dry: service.mutate_conversion_actions(
            request=mh.build_request(
                ga_client,
                "MutateConversionActionsRequest",
                cid,
                [op],
                validate_only=dry,
            )
        ),
        on_success=lambda _r: f"Conversion action {action_id} updated.",
        error_prefix="Failed to update conversion action",
    )
