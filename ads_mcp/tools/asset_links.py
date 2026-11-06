# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for attaching existing assets (sitelinks, callouts...) to campaigns,
ad groups or the whole account, and detaching them again."""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac

asset_links_mcp = FastMCP("asset_links")

Level = Literal["CAMPAIGN", "AD_GROUP", "CUSTOMER"]
FieldType = Literal[
    "SITELINK",
    "CALLOUT",
    "STRUCTURED_SNIPPET",
    "CALL",
    "PROMOTION",
    "PRICE",
    "BUSINESS_NAME",
    "BUSINESS_LOGO",
    "LOGO",
    "LEAD_FORM",
    "MARKETING_IMAGE",
    "SQUARE_MARKETING_IMAGE",
    "YOUTUBE_VIDEO",
]

# service, request type, operation type, mutate method, target field, resource path
_LEVELS = {
    "CAMPAIGN": (
        "CampaignAssetService",
        "MutateCampaignAssetsRequest",
        "CampaignAssetOperation",
        "mutate_campaign_assets",
        "campaign",
        "campaigns",
        "campaignAssets",
    ),
    "AD_GROUP": (
        "AdGroupAssetService",
        "MutateAdGroupAssetsRequest",
        "AdGroupAssetOperation",
        "mutate_ad_group_assets",
        "ad_group",
        "adGroups",
        "adGroupAssets",
    ),
    "CUSTOMER": (
        "CustomerAssetService",
        "MutateCustomerAssetsRequest",
        "CustomerAssetOperation",
        "mutate_customer_assets",
        None,
        None,
        "customerAssets",
    ),
}


def _parts(level: str, cid: str, target_id: str | int | None):
    if level not in _LEVELS:
        raise ToolError("Error: 'level' must be CAMPAIGN, AD_GROUP or CUSTOMER.")
    cfg = _LEVELS[level]
    if level == "CUSTOMER":
        return cfg, None
    return cfg, mh.require_numeric_id(target_id, "target_id")


@asset_links_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def link_asset(
    customer_id: str | int,
    asset_id: str | int,
    level: Level,
    field_type: FieldType,
    target_id: str | int | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Attaches an existing asset (e.g. one made with `create_asset`) to a campaign, ad group or the whole account.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        asset_id: Numeric ID of the existing asset.
        level: CAMPAIGN, AD_GROUP or CUSTOMER (account-wide).
        field_type: How the asset is used, e.g. SITELINK or CALLOUT.
        target_id: Campaign ID or ad group ID. Not used for CUSTOMER.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    asset = mh.require_numeric_id(asset_id, "asset_id")
    cfg, target = _parts(level, cid, target_id)
    service_name, req, op_type, method, target_field, target_path, _ = cfg
    ga_client, service = mh.get_client_and_service(login_customer_id, service_name)
    op = ga_client.get_type(op_type)
    op.create.asset = f"customers/{cid}/assets/{asset}"
    op.create.field_type = ga_client.enums.AssetFieldTypeEnum[field_type]
    if target_field:
        setattr(op.create, target_field, f"customers/{cid}/{target_path}/{target}")
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "link_asset",
            "asset": asset,
            "level": level,
            "target": target,
            "field_type": field_type,
        },
        change_items=pac.build_change_items(
            operation="CREATE",
            entity_readable="Asset link",
            primary_key_readable=f"asset {asset}",
            fields={
                "Level": level,
                "Target ID": target,
                "Field type": field_type,
            },
        ),
        mutate=lambda dry: getattr(service, method)(
            request=mh.build_request(ga_client, req, cid, [op], validate_only=dry)
        ),
        on_success=lambda _r: f"Linked asset {asset} as {field_type} at {level} level.",
        error_prefix="Failed to link asset",
    )


@asset_links_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def unlink_asset(
    customer_id: str | int,
    asset_id: str | int,
    level: Level,
    field_type: FieldType,
    target_id: str | int | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Detaches an asset from a campaign, ad group or the account. The asset itself is kept.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        asset_id: Numeric ID of the asset.
        level: CAMPAIGN, AD_GROUP or CUSTOMER.
        field_type: The field type the asset was linked with.
        target_id: Campaign ID or ad group ID. Not used for CUSTOMER.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    asset = mh.require_numeric_id(asset_id, "asset_id")
    cfg, target = _parts(level, cid, target_id)
    service_name, req, op_type, method, _tf, _tp, link_path = cfg
    ga_client, service = mh.get_client_and_service(login_customer_id, service_name)
    key = f"{asset}~{field_type}" if level == "CUSTOMER" else f"{target}~{asset}~{field_type}"
    resource_name = f"customers/{cid}/{link_path}/{key}"
    op = ga_client.get_type(op_type)
    op.remove = resource_name
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={"tool": "unlink_asset", "rn": resource_name},
        change_items=pac.build_change_items(
            operation="REMOVE",
            entity_readable="Asset link",
            primary_key_readable=resource_name,
            fields={"Resource": resource_name},
        ),
        mutate=lambda dry: getattr(service, method)(
            request=mh.build_request(ga_client, req, cid, [op], validate_only=dry)
        ),
        on_success=lambda _r: f"Unlinked asset {asset} ({field_type}, {level}).",
        error_prefix="Failed to unlink asset",
    )
