# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.

"""Tools for shared negative keyword lists.

A list is created once and attached to any number of campaigns, instead of
repeating the same negatives in every campaign. List the existing ones with
``search_search`` on ``shared_set`` (filter ``shared_set.type = 'NEGATIVE_KEYWORDS'``),
their words on ``shared_criterion`` and their campaigns on ``campaign_shared_set``.
"""

from typing import Literal

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from ads_mcp import mutate_helpers as mh
from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils

shared_sets_mcp = FastMCP("shared_sets")

MatchType = Literal["EXACT", "PHRASE", "BROAD"]


def _clean_name(name: str) -> str:
    if not name or not name.strip():
        raise ToolError("Error: 'name' is required.")
    name = name.strip()
    if len(name) > 255:
        raise ToolError("Error: 'name' must be at most 255 characters.")
    return name


@shared_sets_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def create_negative_keyword_list(
    customer_id: str | int,
    name: str,
    keywords: list[str],
    match_type: MatchType = "PHRASE",
    campaign_ids: list[str | int] | None = None,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Creates a shared negative keyword list with its words and optionally attaches it to campaigns, all in one atomic request.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        name: List name, unique in the account.
        keywords: 1-100 negative keyword texts (up to 80 characters each).
        match_type: EXACT, PHRASE (default) or BROAD, applied to all words.
        campaign_ids: Optional campaign IDs (up to 100) to attach the list to.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    name = _clean_name(name)
    texts = mh.check_texts(
        keywords, "keywords", min_count=1, max_count=100, max_len=80
    )
    campaigns = mh.require_ids(campaign_ids, "campaign_ids") if campaign_ids else []
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "GoogleAdsService"
    )

    temp = f"customers/{cid}/sharedSets/-1"
    ops = []
    set_op = ga_client.get_type("MutateOperation")
    shared_set = set_op.shared_set_operation.create
    shared_set.resource_name = temp
    shared_set.name = name
    shared_set.type_ = ga_client.enums.SharedSetTypeEnum.NEGATIVE_KEYWORDS
    ops.append(set_op)
    for text in texts:
        op = ga_client.get_type("MutateOperation")
        crit = op.shared_criterion_operation.create
        crit.shared_set = temp
        crit.keyword.text = text
        crit.keyword.match_type = ga_client.enums.KeywordMatchTypeEnum[match_type]
        ops.append(op)
    for camp in campaigns:
        op = ga_client.get_type("MutateOperation")
        link = op.campaign_shared_set_operation.create
        link.campaign = f"customers/{cid}/campaigns/{camp}"
        link.shared_set = temp
        ops.append(op)

    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "create_negative_keyword_list",
            "name": name,
            "keywords": texts,
            "match_type": match_type,
            "campaigns": campaigns,
        },
        change_items=pac.build_change_items(
            operation="CREATE",
            entity_readable="Negative keyword list",
            primary_key_readable=name,
            fields={
                "Name": name,
                "Keywords": texts,
                "Match type": match_type,
                "Attached to campaigns": campaigns or None,
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
            f"Created list '{name}' with {len(texts)} keyword(s)"
            + (f", attached to {len(campaigns)} campaign(s)" if campaigns else "")
            + f". ID: {utils.extract_id(r.mutate_operation_responses[0].shared_set_result.resource_name)}"
        ),
        error_prefix="Failed to create negative keyword list",
    )


@shared_sets_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def add_list_negative_keywords(
    customer_id: str | int,
    shared_set_id: str | int,
    keywords: list[str],
    match_type: MatchType = "PHRASE",
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Adds negative keywords to an existing shared list; every campaign using the list gets them.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        shared_set_id: Numeric ID of the negative keyword list.
        keywords: 1-100 negative keyword texts (up to 80 characters each).
        match_type: EXACT, PHRASE (default) or BROAD.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    list_id = mh.require_numeric_id(shared_set_id, "shared_set_id")
    texts = mh.check_texts(
        keywords, "keywords", min_count=1, max_count=100, max_len=80
    )
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "SharedCriterionService"
    )
    ops = []
    for text in texts:
        op = ga_client.get_type("SharedCriterionOperation")
        op.create.shared_set = f"customers/{cid}/sharedSets/{list_id}"
        op.create.keyword.text = text
        op.create.keyword.match_type = ga_client.enums.KeywordMatchTypeEnum[
            match_type
        ]
        ops.append(op)
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "add_list_negative_keywords",
            "list": list_id,
            "keywords": texts,
            "match_type": match_type,
        },
        change_items=pac.build_change_items(
            operation="CREATE",
            entity_readable="Negative keyword (list)",
            primary_key_readable=f"list {list_id}",
            fields={"Keywords": texts, "Match type": match_type},
        ),
        mutate=lambda dry: service.mutate_shared_criteria(
            request=mh.build_request(
                ga_client, "MutateSharedCriteriaRequest", cid, ops, validate_only=dry
            )
        ),
        on_success=lambda r: f"Added {len(r.results)} keyword(s) to list {list_id}.",
        error_prefix="Failed to add keywords to list",
    )


@shared_sets_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def remove_list_negative_keywords(
    customer_id: str | int,
    shared_set_id: str | int,
    criterion_ids: list[str | int],
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Removes words from a shared negative keyword list (affects every campaign using it). Find `criterion_id` with `search_search` on `shared_criterion`.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        shared_set_id: Numeric ID of the negative keyword list.
        criterion_ids: 1-100 numeric criterion IDs to remove.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    list_id = mh.require_numeric_id(shared_set_id, "shared_set_id")
    crit_ids = mh.require_ids(criterion_ids, "criterion_ids")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "SharedCriterionService"
    )
    names = [f"customers/{cid}/sharedCriteria/{list_id}~{c}" for c in crit_ids]
    ops = []
    for rn in names:
        op = ga_client.get_type("SharedCriterionOperation")
        op.remove = rn
        ops.append(op)
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={"tool": "remove_list_negative_keywords", "rns": names},
        change_items=pac.build_change_items(
            operation="REMOVE",
            entity_readable="Negative keyword (list)",
            primary_key_readable=f"list {list_id}",
            fields={"Criterion IDs": crit_ids},
        ),
        mutate=lambda dry: service.mutate_shared_criteria(
            request=mh.build_request(
                ga_client, "MutateSharedCriteriaRequest", cid, ops, validate_only=dry
            )
        ),
        on_success=lambda r: f"Removed {len(r.results)} keyword(s) from list {list_id}.",
        error_prefix="Failed to remove keywords from list",
    )


@shared_sets_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False)
)
def attach_negative_keyword_list(
    customer_id: str | int,
    shared_set_id: str | int,
    campaign_ids: list[str | int],
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Attaches a shared negative keyword list to campaigns.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        shared_set_id: Numeric ID of the negative keyword list.
        campaign_ids: 1-100 campaign IDs.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    list_id = mh.require_numeric_id(shared_set_id, "shared_set_id")
    campaigns = mh.require_ids(campaign_ids, "campaign_ids")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "CampaignSharedSetService"
    )
    ops = []
    for camp in campaigns:
        op = ga_client.get_type("CampaignSharedSetOperation")
        op.create.campaign = f"customers/{cid}/campaigns/{camp}"
        op.create.shared_set = f"customers/{cid}/sharedSets/{list_id}"
        ops.append(op)
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={
            "tool": "attach_negative_keyword_list",
            "list": list_id,
            "campaigns": campaigns,
        },
        change_items=pac.build_change_items(
            operation="CREATE",
            entity_readable="Negative keyword list link",
            primary_key_readable=f"list {list_id}",
            fields={"Campaign IDs": campaigns},
        ),
        mutate=lambda dry: service.mutate_campaign_shared_sets(
            request=mh.build_request(
                ga_client,
                "MutateCampaignSharedSetsRequest",
                cid,
                ops,
                validate_only=dry,
            )
        ),
        on_success=lambda r: f"Attached list {list_id} to {len(r.results)} campaign(s).",
        error_prefix="Failed to attach negative keyword list",
    )


@shared_sets_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def detach_negative_keyword_list(
    customer_id: str | int,
    shared_set_id: str | int,
    campaign_ids: list[str | int],
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Detaches a shared negative keyword list from campaigns (the list and its words are kept).

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        shared_set_id: Numeric ID of the negative keyword list.
        campaign_ids: 1-100 campaign IDs to detach it from.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    list_id = mh.require_numeric_id(shared_set_id, "shared_set_id")
    campaigns = mh.require_ids(campaign_ids, "campaign_ids")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "CampaignSharedSetService"
    )
    names = [f"customers/{cid}/campaignSharedSets/{c}~{list_id}" for c in campaigns]
    ops = []
    for rn in names:
        op = ga_client.get_type("CampaignSharedSetOperation")
        op.remove = rn
        ops.append(op)
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={"tool": "detach_negative_keyword_list", "rns": names},
        change_items=pac.build_change_items(
            operation="REMOVE",
            entity_readable="Negative keyword list link",
            primary_key_readable=f"list {list_id}",
            fields={"Campaign IDs": campaigns},
        ),
        mutate=lambda dry: service.mutate_campaign_shared_sets(
            request=mh.build_request(
                ga_client,
                "MutateCampaignSharedSetsRequest",
                cid,
                ops,
                validate_only=dry,
            )
        ),
        on_success=lambda r: f"Detached list {list_id} from {len(r.results)} campaign(s).",
        error_prefix="Failed to detach negative keyword list",
    )


@shared_sets_mcp.tool(
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True)
)
def remove_negative_keyword_list(
    customer_id: str | int,
    shared_set_id: str | int,
    login_customer_id: str | int | None = None,
    validate_only: bool = True,
    checksum: str | None = None,
) -> str:
    """Permanently removes a shared negative keyword list. Detach it from all campaigns first (use `detach_negative_keyword_list`). Cannot be undone.

    Propose & Confirm Workflow (`validate_only` and `checksum`):
    1. Call with `validate_only=True` (the default). After a Google Ads dry run passes, the tool returns structured `change_items` and a `checksum` without committing anything.
    2. Present the `change_items` to the user and wait for explicit user approval.
    3. Only after user confirmation, call again with `validate_only=False`, the exact same parameters and the `checksum` returned by step 1.

    Args:
        customer_id: Google Ads Customer ID (digits or hyphenated).
        shared_set_id: Numeric ID of the negative keyword list.
        login_customer_id: Optional manager customer ID for the login-customer-id header.
        validate_only: If True (default), only validates and returns a proposal with a `checksum`.
        checksum: Checksum from the prior `validate_only=True` call. Required to commit.
    """
    cid = mh.require_customer_id(customer_id)
    list_id = mh.require_numeric_id(shared_set_id, "shared_set_id")
    ga_client, service = mh.get_client_and_service(
        login_customer_id, "SharedSetService"
    )
    resource_name = f"customers/{cid}/sharedSets/{list_id}"
    op = ga_client.get_type("SharedSetOperation")
    op.remove = resource_name
    return mh.run_mutation(
        customer_id=cid,
        validate_only=validate_only,
        checksum=checksum,
        payload={"tool": "remove_negative_keyword_list", "rn": resource_name},
        change_items=pac.build_change_items(
            operation="REMOVE",
            entity_readable="Negative keyword list",
            primary_key_readable=resource_name,
            fields={"Resource": resource_name},
        ),
        mutate=lambda dry: service.mutate_shared_sets(
            request=mh.build_request(
                ga_client, "MutateSharedSetsRequest", cid, [op], validate_only=dry
            )
        ),
        on_success=lambda _r: f"Removed list {list_id}.",
        error_prefix="Failed to remove negative keyword list",
    )
