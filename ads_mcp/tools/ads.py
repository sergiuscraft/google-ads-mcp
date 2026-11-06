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
