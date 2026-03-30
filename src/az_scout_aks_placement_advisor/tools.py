"""MCP tools for AKS Placement Advisor.

These are plain Python functions with type annotations and docstrings.
They are registered on the az-scout MCP server automatically.

For authenticated Azure ARM API calls, use the public helpers::

    from az_scout.azure_api import arm_get, arm_paginate, get_headers
"""

from __future__ import annotations

from typing import Any

from az_scout_aks_placement_advisor.models import DISCLAIMER


def recommend_aks_skus(
    region: str,
    require_zones: bool = False,
    min_vcpus: int | None = None,
    min_memory_gb: float | None = None,
    max_results: int = 10,
    tenant_id: str | None = None,
    subscription_id: str | None = None,
) -> dict[str, Any]:
    """Recommend VM SKUs suitable for AKS node pools in a given Azure region.

    Returns the top-ranked SKUs with a heuristic confidence score based on
    zone support, capabilities, and optional quota data. Requires a
    ``subscription_id`` to query the Compute Resource SKUs API.

    **Important:** Scores are heuristic estimates based on publicly available
    SKU metadata. They do not guarantee actual AKS VMSS capacity.

    Parameters
    ----------
    region:
        Azure region name (e.g. ``"eastus"``).
    require_zones:
        If ``True``, only return SKUs with availability zone support.
    min_vcpus:
        Minimum vCPU count filter (inclusive).
    min_memory_gb:
        Minimum memory in GB filter (inclusive).
    max_results:
        Maximum number of recommendations to return (default 10).
    tenant_id:
        Optional Azure tenant ID for authentication.
    subscription_id:
        Azure subscription ID (required for ARM API calls).

    Returns
    -------
    dict
        Contains ``region``, ``count``, ``disclaimer``, and
        ``recommendations`` (list of scored SKU dicts).
    """
    from az_scout_aks_placement_advisor.service import get_recommendations

    recs = get_recommendations(
        region,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        require_zones=require_zones,
        require_vmss=True,
        min_vcpus=min_vcpus,
        min_memory_gb=min_memory_gb,
        max_results=max_results,
    )
    return {
        "region": region,
        "count": len(recs),
        "disclaimer": DISCLAIMER,
        "recommendations": [r.to_dict() for r in recs],
    }


def compare_aks_regions(
    regions: list[str],
    min_vcpus: int | None = None,
    min_memory_gb: float | None = None,
    tenant_id: str | None = None,
    subscription_id: str | None = None,
) -> dict[str, Any]:
    """Compare multiple Azure regions for AKS SKU placement suitability.

    For each region, retrieves the top 5 recommended SKUs and returns a
    summary with the best candidates per region. Requires a ``subscription_id``.

    **Important:** Scores are heuristic estimates based on publicly available
    SKU metadata. They do not guarantee actual AKS VMSS capacity.

    Parameters
    ----------
    regions:
        List of Azure region names to compare (e.g. ``["eastus", "westus2"]``).
    min_vcpus:
        Minimum vCPU count filter (inclusive).
    min_memory_gb:
        Minimum memory in GB filter (inclusive).
    tenant_id:
        Optional Azure tenant ID for authentication.
    subscription_id:
        Azure subscription ID (required for ARM API calls).

    Returns
    -------
    dict
        Contains ``disclaimer``, ``regionsCompared``, ``results``
        (list of per-region summaries), and ``errors``.
    """
    from az_scout_aks_placement_advisor.service import get_recommendations

    region_results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for region in regions:
        try:
            recs = get_recommendations(
                region,
                tenant_id=tenant_id,
                subscription_id=subscription_id,
                require_vmss=True,
                min_vcpus=min_vcpus,
                min_memory_gb=min_memory_gb,
                max_results=5,
            )
            region_results.append(
                {
                    "region": region,
                    "count": len(recs),
                    "topSkus": [r.to_dict() for r in recs],
                    "bestScore": recs[0].score if recs else 0.0,
                    "bestSku": recs[0].sku_name if recs else None,
                }
            )
        except Exception as exc:
            errors.append({"region": region, "error": str(exc)})

    # Sort regions by best score descending
    region_results.sort(key=lambda r: -r["bestScore"])

    return {
        "disclaimer": DISCLAIMER,
        "regionsCompared": len(regions),
        "results": region_results,
        "errors": errors,
    }
