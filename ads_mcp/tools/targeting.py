# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for campaign location/language targeting and removing campaign criteria."""

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac

targeting_mcp = FastMCP("targeting")

@targeting_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False)
)
def suggest_geo_targets(
    customer_id: str | int,
    location_names: list[str],
    country_code: str | None = None,
    locale: str = "en",
    login_customer_id: str | int | None = None,
) -> list[dict]:
    """Looks up geo target IDs by place name (read-only). Use the returned `id` with `add_campaign_locations`.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        location_names: Place names to look up, e.g. ["Kyiv", "Ukraine"] (1-25).
        country_code: Optional ISO country code to narrow results, e.g. "UA".
        locale: Language of the returned names, e.g. "en" or "uk".
        login_customer_id: Optional manager customer ID for the login-customer-id header.
    """
    mh.require_customer_id(customer_id)
    names = mh.check_texts(
        location_names, "location_names", min_count=1, max_count=25, max_len=100
    )
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "GeoTargetConstantService"
    )
    request = ga_client.get_type("SuggestGeoTargetConstantsRequest")
    request.locale = locale
    if country_code:
        request.country_code = country_code.upper()
    request.location_names.names.extend(names)
    response = service.suggest_geo_target_constants(request=request)
    return [
        {
            "id": s.geo_target_constant.id,
            "name": s.geo_target_constant.name,
            "canonical_name": s.geo_target_constant.canonical_name,
            "target_type": s.geo_target_constant.target_type,
            "country_code": s.geo_target_constant.country_code,
            "reach": s.reach,
            "search_term": s.search_term,
        }
        for s in response.geo_target_constant_suggestions
    ]


def _add_criteria(
    *,
    tool: str,
    entity: str,
    customer_id,
    campaign_id,
    ids,
    negative: bool,
    login_customer_id,
    validate_only,
    checksum,
    fill,
    resource_prefix: str,
):
    cid = mh.require_customer_id(customer_id)
    camp_id = mh.require_numeric_id(campaign_id, "campaign_id")
    constant_ids = mh.require_ids(ids, "ids")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "CampaignCriterionService"
    )
    ops = []
    for constant_id in constant_ids:
        op = ga_client.get_type("CampaignCriterionOperation")
        crit = op.create
        crit.campaign = f"customers/{cid}/campaigns/{camp_id}"
        crit.negative = negative
        fill(crit, f"{resource_prefix}/{constant_id}")
        ops.append(op)
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": tool,
            "campaign_id": camp_id,
            "ids": constant_ids,
            "negative": negative,
        },
        change_items=pac.build_change_items(
            operation="CREATE",
            entity_readable=entity,
            primary_key_readable=f"campaign {camp_id}",
            fields={"IDs": constant_ids, "Excluded": negative if negative else None},
        ),
        mutate=lambda dry: service.mutate_campaign_criteria(
            request=mh.build_request(
                ga_client, "MutateCampaignCriteriaRequest", cid, ops, validate_only=dry
            )
        ),
        on_success=lambda r: f"Added {len(r.results)} {entity.lower()}(s) to campaign {camp_id}.",
        error_prefix=f"Failed to add {entity.lower()}",
    )


@targeting_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def add_campaign_locations(
    customer_id: str | int,
    campaign_id: str | int,
    geo_target_ids: list[str | int],
    exclude: bool = False,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Targets (or excludes) locations for a campaign. Find IDs with `suggest_geo_targets` (e.g. Ukraine = 2804).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID.
        geo_target_ids: 1-100 geo target constant IDs.
        exclude: If True, the locations are excluded instead of targeted.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """

    def fill(crit, rn):
        crit.location.geo_target_constant = rn

    return _add_criteria(
        tool="add_campaign_locations",
        entity="Location",
        customer_id=customer_id,
        campaign_id=campaign_id,
        ids=geo_target_ids,
        negative=exclude,
        login_customer_id=login_customer_id,
        validate_only=validate_only,
        checksum=checksum,
        fill=fill,
        resource_prefix="geoTargetConstants",
    )


@targeting_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def add_campaign_languages(
    customer_id: str | int,
    campaign_id: str | int,
    language_ids: list[str | int],
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Targets languages for a campaign. Common IDs: Russian 1031, Ukrainian 1036, English 1000 (others: query `language_constant` with `search_search`).

    Heads-up: Google announced (13 Aug 2026, effective late September 2026) that campaign-level language targeting is being removed for Search campaigns, where the language of the ad copy decides instead; adding or editing a language criterion on a Search campaign is then expected to fail with OPERATION_NOT_PERMITTED_FOR_CONTEXT. Still applies to Performance Max (non-Search channels). On this fork's test account the dry run was still accepted for Search campaigns, so write language-clear ad copy and treat this tool as possibly obsolete for Search.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID.
        language_ids: 1-100 language constant IDs.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """

    def fill(crit, rn):
        crit.language.language_constant = rn

    return _add_criteria(
        tool="add_campaign_languages",
        entity="Language",
        customer_id=customer_id,
        campaign_id=campaign_id,
        ids=language_ids,
        negative=False,
        login_customer_id=login_customer_id,
        validate_only=validate_only,
        checksum=checksum,
        fill=fill,
        resource_prefix="languageConstants",
    )


@targeting_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def remove_campaign_criteria(
    customer_id: str | int,
    campaign_id: str | int,
    criterion_ids: list[str | int],
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Removes campaign-level criteria: locations, languages or campaign negative keywords. Find `criterion_id` with `search_search` on `campaign_criterion`.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        campaign_id: Numeric campaign ID.
        criterion_ids: 1-100 numeric criterion IDs to remove.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    camp_id = mh.require_numeric_id(campaign_id, "campaign_id")
    crit_ids = mh.require_ids(criterion_ids, "criterion_ids")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "CampaignCriterionService"
    )
    names = [f"customers/{cid}/campaignCriteria/{camp_id}~{c}" for c in crit_ids]
    ops = []
    for rn in names:
        op = ga_client.get_type("CampaignCriterionOperation")
        op.remove = rn
        ops.append(op)
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={"tool": "remove_campaign_criteria", "rns": names},
        change_items=pac.build_change_items(
            operation="REMOVE",
            entity_readable="Campaign criterion",
            primary_key_readable=f"campaign {camp_id}",
            fields={"Criterion IDs": crit_ids},
        ),
        mutate=lambda dry: service.mutate_campaign_criteria(
            request=mh.build_request(
                ga_client, "MutateCampaignCriteriaRequest", cid, ops, validate_only=dry
            )
        ),
        on_success=lambda r: f"Removed {len(r.results)} criterion/criteria.",
        error_prefix="Failed to remove campaign criteria",
    )
