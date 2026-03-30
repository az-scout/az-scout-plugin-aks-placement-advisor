"""FastAPI routes for AKS Placement Advisor plugin."""

from __future__ import annotations

import asyncio
from typing import Any

from az_scout.plugin_api import PluginValidationError
from fastapi import APIRouter

from az_scout_aks_placement_advisor.models import DISCLAIMER

router = APIRouter()


@router.get("/health")
async def health() -> dict[str, str]:
    """Return plugin health status."""
    return {"status": "ok", "plugin": "aks-placement-advisor"}


@router.get("/regions")
async def regions(
    tenant_id: str | None = None,
    subscription_id: str | None = None,
) -> dict[str, Any]:
    """Return Azure regions suitable for Compute SKU lookup."""
    from az_scout_aks_placement_advisor.service import get_regions

    result = await asyncio.to_thread(
        get_regions,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
    )
    return {"regions": result}


@router.get("/recommendations")
async def recommendations(
    region: str,
    tenant_id: str | None = None,
    subscription_id: str | None = None,
    require_zones: bool = False,
    require_vmss: bool = True,
    min_vcpus: int | None = None,
    min_memory_gb: float | None = None,
    sku_name_filter: str | None = None,
    max_results: int = 20,
) -> dict[str, Any]:
    """Return scored AKS SKU recommendations for a region.

    Requires ``subscription_id`` to query the Compute Resource SKUs API.
    """
    if not region:
        raise PluginValidationError("region is required")
    if max_results < 1 or max_results > 200:
        raise PluginValidationError("max_results must be between 1 and 200")
    if min_vcpus is not None and min_vcpus < 1:
        raise PluginValidationError("min_vcpus must be >= 1")
    if min_memory_gb is not None and min_memory_gb < 0:
        raise PluginValidationError("min_memory_gb must be >= 0")

    from az_scout_aks_placement_advisor.service import get_recommendations

    recs = await asyncio.to_thread(
        get_recommendations,
        region,
        tenant_id=tenant_id,
        subscription_id=subscription_id,
        require_zones=require_zones,
        require_vmss=require_vmss,
        min_vcpus=min_vcpus,
        min_memory_gb=min_memory_gb,
        sku_name_filter=sku_name_filter,
        max_results=max_results,
    )
    return {
        "region": region,
        "count": len(recs),
        "disclaimer": DISCLAIMER,
        "recommendations": [r.to_dict() for r in recs],
    }
