# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for creating Google Ads responsive search ads."""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils

ads_mcp_server = FastMCP("ads")


@ads_mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def create_responsive_search_ad(
    customer_id: str | int,
    ad_group_id: str | int,
    headlines: list[str],
    descriptions: list[str],
    final_url: str,
    path1: str | None = None,
    path2: str | None = None,
    status: Literal["PAUSED", "ENABLED"] = "PAUSED",
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Creates a responsive search ad (PAUSED by default).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        ad_group_id: Numeric ID of the ad group.
        headlines: 3-15 unique headlines, up to 30 characters each.
        descriptions: 2-4 unique descriptions, up to 90 characters each.
        final_url: Landing page URL, must start with http:// or https://.
        path1: Optional display path part 1 (up to 15 characters).
        path2: Optional display path part 2 (up to 15 characters; requires path1).
        status: PAUSED (default, safe) or ENABLED.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    ag_id = mh.require_numeric_id(ad_group_id, "ad_group_id")
    heads = mh.check_texts(
        headlines, "headlines", min_count=3, max_count=15, max_len=30
    )
    descs = mh.check_texts(
        descriptions, "descriptions", min_count=2, max_count=4, max_len=90
    )
    if not final_url.startswith(("http://", "https://")):
        raise ToolError("Error: 'final_url' must start with http:// or https://.")
    for label, path in (("path1", path1), ("path2", path2)):
        if path is not None and len(path) > 15:
            raise ToolError(f"Error: '{label}' must be at most 15 characters.")
    if path2 and not path1:
        raise ToolError("Error: 'path2' requires 'path1'.")

    ga_client = utils.get_googleads_client(login_customer_id=login_customer_id)
    service = utils.get_googleads_service_from_client(
        ga_client, "AdGroupAdService"
    )

    op = ga_client.get_type("AdGroupAdOperation")
    ad_group_ad = op.create
    ad_group_ad.ad_group = f"customers/{cid}/adGroups/{ag_id}"
    ad_group_ad.status = ga_client.enums.AdGroupAdStatusEnum[status]
    ad_group_ad.ad.final_urls.append(final_url)
    rsa = ad_group_ad.ad.responsive_search_ad
    for text in heads:
        asset = ga_client.get_type("AdTextAsset")
        asset.text = text
        rsa.headlines.append(asset)
    for text in descs:
        asset = ga_client.get_type("AdTextAsset")
        asset.text = text
        rsa.descriptions.append(asset)
    if path1:
        rsa.path1 = path1
    if path2:
        rsa.path2 = path2

    change_items = pac.build_change_items(
        operation="CREATE",
        entity_readable="Responsive search ad",
        primary_key_readable=f"ad group {ag_id}",
        fields={
            "Headlines": heads,
            "Descriptions": descs,
            "Final URL": final_url,
            "Path": "/".join(p for p in (path1, path2) if p) or None,
            "Status": status,
        },
    )

    def _mutate(dry_run: bool):
        return service.mutate_ad_group_ads(
            request=mh.build_request(
                ga_client, "MutateAdGroupAdsRequest", cid, [op], validate_only=dry_run
            )
        )

    def _done(response) -> str:
        rn = response.results[0].resource_name
        return f"Successfully created responsive search ad ({status}): {rn}"

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "create_responsive_search_ad",
            "ad_group_id": ag_id,
            "headlines": heads,
            "descriptions": descs,
            "final_url": final_url,
            "path1": path1,
            "path2": path2,
            "status": status,
        },
        change_items=change_items,
        mutate=_mutate,
        on_success=_done,
        error_prefix="Failed to create responsive search ad",
    )


@ads_mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def update_ad_group_ad(
    customer_id: str | int,
    ad_group_id: str | int,
    ad_id: str | int,
    status: Literal["PAUSED", "ENABLED"],
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Pauses or enables an existing ad. Enabling can start spending money.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        ad_group_id: Numeric ID of the ad group that contains the ad.
        ad_id: Numeric ad ID (`ad_group_ad.ad.id`).
        status: PAUSED or ENABLED.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    ag_id = mh.require_numeric_id(ad_group_id, "ad_group_id")
    ad = mh.require_numeric_id(ad_id, "ad_id")
    if status not in ("PAUSED", "ENABLED"):
        raise ToolError("Error: 'status' must be PAUSED or ENABLED.")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "AdGroupAdService"
    )
    resource_name = f"customers/{cid}/adGroupAds/{ag_id}~{ad}"
    op = ga_client.get_type("AdGroupAdOperation")
    op.update.resource_name = resource_name
    op.update.status = ga_client.enums.AdGroupAdStatusEnum[status]
    op.update_mask.paths.append("status")
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={"tool": "update_ad_group_ad", "rn": resource_name, "status": status},
        change_items=pac.build_change_items(
            operation="UPDATE",
            entity_readable="Ad",
            primary_key_readable=resource_name,
            fields={"Status": status},
        ),
        mutate=lambda dry: service.mutate_ad_group_ads(
            request=mh.build_request(
                ga_client, "MutateAdGroupAdsRequest", cid, [op], validate_only=dry
            )
        ),
        on_success=lambda _r: f"Ad {ad} is now {status}.",
        error_prefix="Failed to update ad",
    )


@ads_mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def remove_ad_group_ad(
    customer_id: str | int,
    ad_group_id: str | int,
    ad_id: str | int,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Permanently removes an ad (cannot be undone; prefer pausing with `update_ad_group_ad`).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        ad_group_id: Numeric ID of the ad group that contains the ad.
        ad_id: Numeric ad ID.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    ag_id = mh.require_numeric_id(ad_group_id, "ad_group_id")
    ad = mh.require_numeric_id(ad_id, "ad_id")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "AdGroupAdService"
    )
    resource_name = f"customers/{cid}/adGroupAds/{ag_id}~{ad}"
    op = ga_client.get_type("AdGroupAdOperation")
    op.remove = resource_name
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={"tool": "remove_ad_group_ad", "rn": resource_name},
        change_items=pac.build_change_items(
            operation="REMOVE",
            entity_readable="Ad",
            primary_key_readable=resource_name,
            fields={"Resource": resource_name},
        ),
        mutate=lambda dry: service.mutate_ad_group_ads(
            request=mh.build_request(
                ga_client, "MutateAdGroupAdsRequest", cid, [op], validate_only=dry
            )
        ),
        on_success=lambda _r: f"Ad {ad} removed.",
        error_prefix="Failed to remove ad",
    )


@ads_mcp_server.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def replace_responsive_search_ad(
    customer_id: str | int,
    ad_group_id: str | int,
    old_ad_id: str | int,
    headlines: list[str] | None = None,
    descriptions: list[str] | None = None,
    final_url: str | None = None,
    path1: str | None = None,
    path2: str | None = None,
    new_status: Literal["PAUSED", "ENABLED"] | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Replaces a responsive search ad: creates a new one and pauses the old one, in one atomic request.

    RSA text cannot be edited after creation, so this is the way to change headlines or descriptions. Anything not given is copied from the old ad (including pinned positions of unchanged headlines/descriptions). Pass an empty string for `path1`/`path2` to clear a display path. The old ad is paused, not removed, so it can be re-enabled; its performance history is not carried over to the new ad.

    By default the new ad gets the same status as the old one, so a live ad is really replaced. Pass `new_status` to override (PAUSED = nothing keeps serving).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        ad_group_id: Numeric ID of the ad group that contains the old ad.
        old_ad_id: Numeric ID of the responsive search ad to replace (`ad_group_ad.ad.id`).
        headlines: Optional new list of 3-15 unique headlines, up to 30 characters each.
        descriptions: Optional new list of 2-4 unique descriptions, up to 90 characters each.
        final_url: Optional new landing page URL (http:// or https://).
        path1: Optional display path part 1 (up to 15 characters); "" clears it.
        path2: Optional display path part 2 (up to 15 characters, needs path1); "" clears it.
        new_status: Optional status of the new ad; default = the old ad's status.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    ag_id = mh.require_numeric_id(ad_group_id, "ad_group_id")
    old_id = mh.require_numeric_id(old_ad_id, "old_ad_id")
    if all(v is None for v in (headlines, descriptions, final_url, path1, path2)):
        raise ToolError("Error: give at least one thing to change.")
    new_heads = (
        mh.check_texts(headlines, "headlines", min_count=3, max_count=15, max_len=30)
        if headlines is not None
        else None
    )
    new_descs = (
        mh.check_texts(descriptions, "descriptions", min_count=2, max_count=4, max_len=90)
        if descriptions is not None
        else None
    )
    if final_url is not None and not final_url.startswith(("http://", "https://")):
        raise ToolError("Error: 'final_url' must start with http:// or https://.")
    for label, path in (("path1", path1), ("path2", path2)):
        if path is not None and len(path) > 15:
            raise ToolError(f"Error: '{label}' must be at most 15 characters.")
    if new_status is not None and new_status not in ("PAUSED", "ENABLED"):
        raise ToolError("Error: 'new_status' must be PAUSED or ENABLED.")

    ga_client, search = mh.get_client_and_service(login_customer_id, "GoogleAdsService")
    rows = list(
        search.search(
            customer_id=cid,
            query=(
                "SELECT ad_group_ad.status, ad_group_ad.ad.id, ad_group_ad.ad.type, "
                "ad_group_ad.ad.final_urls, "
                "ad_group_ad.ad.responsive_search_ad.headlines, "
                "ad_group_ad.ad.responsive_search_ad.descriptions, "
                "ad_group_ad.ad.responsive_search_ad.path1, "
                "ad_group_ad.ad.responsive_search_ad.path2 FROM ad_group_ad "
                f"WHERE ad_group.id = {ag_id} AND ad_group_ad.ad.id = {old_id}"
            ),
        )
    )
    if not rows:
        raise ToolError(f"Error: ad {old_id} not found in ad group {ag_id}.")
    old = rows[0].ad_group_ad
    if old.ad.type_.name != "RESPONSIVE_SEARCH_AD":
        raise ToolError(
            f"Error: ad {old_id} is {old.ad.type_.name}; only responsive search ads can be replaced."
        )
    old_status = old.status.name
    if old_status == "REMOVED":
        raise ToolError(f"Error: ad {old_id} is already removed.")
    old_rsa = old.ad.responsive_search_ad

    target_url = final_url if final_url is not None else (
        old.ad.final_urls[0] if old.ad.final_urls else None
    )
    if not target_url:
        raise ToolError("Error: the old ad has no final URL; pass 'final_url'.")
    target_p1 = (path1 if path1 is not None else old_rsa.path1) or None
    target_p2 = (path2 if path2 is not None else old_rsa.path2) or None
    if target_p2 and not target_p1:
        raise ToolError("Error: 'path2' requires 'path1'.")
    status = new_status or (old_status if old_status in ("PAUSED", "ENABLED") else "PAUSED")

    new_ad = ga_client.get_type("MutateOperation")
    created = new_ad.ad_group_ad_operation.create
    created.ad_group = f"customers/{cid}/adGroups/{ag_id}"
    created.status = ga_client.enums.AdGroupAdStatusEnum[status]
    created.ad.final_urls.append(target_url)
    rsa = created.ad.responsive_search_ad

    def _assets(target, new_texts, old_assets):
        if new_texts is not None:
            sources = [(t, None) for t in new_texts]
        else:  # keep, including pinned positions
            sources = [(a.text, a.pinned_field) for a in old_assets]
        for text, pin in sources:
            asset = ga_client.get_type("AdTextAsset")
            asset.text = text
            if pin:
                asset.pinned_field = pin
            target.append(asset)
        return [t for t, _ in sources]

    final_heads = _assets(rsa.headlines, new_heads, old_rsa.headlines)
    final_descs = _assets(rsa.descriptions, new_descs, old_rsa.descriptions)
    if target_p1:
        rsa.path1 = target_p1
    if target_p2:
        rsa.path2 = target_p2

    pause_old = ga_client.get_type("MutateOperation")
    old_rn = f"customers/{cid}/adGroupAds/{ag_id}~{old_id}"
    pause_old.ad_group_ad_operation.update.resource_name = old_rn
    pause_old.ad_group_ad_operation.update.status = (
        ga_client.enums.AdGroupAdStatusEnum.PAUSED
    )
    pause_old.ad_group_ad_operation.update_mask.paths.append("status")

    old_heads = [a.text for a in old_rsa.headlines]
    old_descs = [a.text for a in old_rsa.descriptions]
    old_url = old.ad.final_urls[0] if old.ad.final_urls else None
    old_path = "/".join(p for p in (old_rsa.path1, old_rsa.path2) if p) or None
    new_path = "/".join(p for p in (target_p1, target_p2) if p) or None
    service = utils.get_googleads_service_from_client(ga_client, "GoogleAdsService")

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "replace_responsive_search_ad",
            "ad_group_id": ag_id,
            "old_ad_id": old_id,
            "headlines": final_heads,
            "descriptions": final_descs,
            "final_url": target_url,
            "path1": target_p1,
            "path2": target_p2,
            "new_status": status,
        },
        change_items=pac.build_change_items(
            operation="REPLACE",
            entity_readable="Responsive search ad",
            primary_key_readable=old_rn,
            fields={
                "New headlines": final_heads,
                "New descriptions": final_descs,
                "New final URL": target_url,
                "New path": new_path,
                "New ad status": status,
                "Old ad": f"{old_id} will be PAUSED (not removed)",
            },
            original_fields={
                "New headlines": old_heads,
                "New descriptions": old_descs,
                "New final URL": old_url,
                "New path": old_path,
                "New ad status": old_status,
            },
        ),
        mutate=lambda dry: service.mutate(
            request=mh.build_request(
                ga_client,
                "MutateGoogleAdsRequest",
                cid,
                [new_ad, pause_old],
                validate_only=dry,
                operations_field="mutate_operations",
            )
        ),
        on_success=lambda r: (
            f"Created new ad ({status}): "
            f"{r.mutate_operation_responses[0].ad_group_ad_result.resource_name}; "
            f"old ad {old_id} paused."
        ),
        error_prefix="Failed to replace responsive search ad",
    )
