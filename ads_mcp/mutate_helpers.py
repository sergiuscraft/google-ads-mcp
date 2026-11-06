# Fork addition (not part of upstream googleads/google-ads-mcp).
# Licensed under the Apache License, Version 2.0, same as the rest of this repo.
# Tool design is informed by MaxGhenis/google-ads-mcp-rw (MIT).

"""Shared helpers for the write tools (campaigns, ad groups, keywords, ads).

All write tools follow the repository's Propose & Confirm workflow: a first call
with ``validate_only=True`` runs a Google Ads API dry run and returns a checksum;
the commit call must repeat the same parameters plus that checksum.
"""

from collections.abc import Callable, Sequence
import re
from typing import Any

from fastmcp.exceptions import ToolError

from ads_mcp import propose_and_confirm as pac
import ads_mcp.utils as utils


def require_customer_id(customer_id: str | int) -> str:
    cleaned = utils.clean_customer_id(customer_id)
    if not cleaned:
        raise ToolError("Error: 'customer_id' must contain digits.")
    return cleaned


def require_numeric_id(value: str | int, name: str) -> str:
    """Returns ``value`` as a digits-only string or raises a ToolError."""
    text = str(value).strip()
    if not re.fullmatch(r"\d+", text):
        raise ToolError(f"Error: '{name}' must be a numeric ID, got '{value}'.")
    return text


def to_micros(amount: float | int | None, name: str) -> int:
    """Converts a currency amount (e.g. 12.5) to micros; must be > 0."""
    if amount is None or isinstance(amount, bool) or amount <= 0:
        raise ToolError(f"Error: '{name}' must be a positive number.")
    return int(round(float(amount) * 1_000_000))


def check_texts(
    values: Sequence[str] | None,
    name: str,
    *,
    min_count: int,
    max_count: int,
    max_len: int,
) -> list[str]:
    """Validates a list of short texts (headlines, descriptions, keywords)."""
    cleaned = [v.strip() for v in (values or []) if v and v.strip()]
    if not min_count <= len(cleaned) <= max_count:
        raise ToolError(
            f"Error: '{name}' needs between {min_count} and {max_count} "
            f"non-empty items, got {len(cleaned)}."
        )
    too_long = [v for v in cleaned if len(v) > max_len]
    if too_long:
        raise ToolError(
            f"Error: '{name}' items must be at most {max_len} characters; "
            f"too long: {too_long}."
        )
    if len(set(v.lower() for v in cleaned)) != len(cleaned):
        raise ToolError(f"Error: '{name}' contains duplicate items.")
    return cleaned


def build_request(
    ga_client: Any,
    request_type: str,
    customer_id: str,
    operations: Sequence[Any],
    *,
    validate_only: bool,
    operations_field: str = "operations",
) -> Any:
    """Builds a ``Mutate*Request``.

    The generated service methods only accept ``validate_only`` through a
    request object (it is not one of their flattened keyword arguments).
    """
    request = ga_client.get_type(request_type)
    request.customer_id = customer_id
    getattr(request, operations_field).extend(operations)
    request.validate_only = validate_only
    return request


def run_mutation(
    *,
    customer_id: str,
    validate_only: bool,
    checksum: str | None,
    payload: Any,
    change_items: Sequence[pac.ChangeItem],
    mutate: Callable[[bool], Any],
    on_success: Callable[[Any], str],
    error_prefix: str,
) -> str:
    """Runs ``mutate(validate_only)`` through the Propose & Confirm workflow.

    Args:
        mutate: Called with ``True`` for the API dry run and ``False`` to
            commit; returns the raw API response.
        on_success: Turns the commit response into the tool's result message.
    """
    return pac.propose_and_confirm(
        customer_id=customer_id,
        validate_only=validate_only,
        checksum=checksum,
        operation_payload=payload,
        change_items=change_items,
        validate_fn=lambda: mutate(True),
        commit_fn=lambda: on_success(mutate(False)),
        error_prefix=error_prefix,
    )

