"""Core service logic for AKS Placement Advisor.

Responsible for:
- Fetching VM SKUs from the AKS VM SKUs API
- Client-side filtering, scoring, and ranking
- Optional quota enrichment
"""

from __future__ import annotations

import logging
import time
from typing import Any

from az_scout_aks_placement_advisor.models import SkuRecommendation
from az_scout_aks_placement_advisor.scoring import (
    CAP_MEMORY_GB,
    CAP_VCPUS,
    extract_capability_float,
    extract_capability_int,
    score_sku,
    suggest_fallbacks,
)

logger = logging.getLogger(__name__)


def _serialize_confidence(conf: Any) -> dict[str, Any] | None:
    """Serialize a DeploymentConfidence pydantic model to a dict, or pass through."""
    if conf is None:
        return None
    if hasattr(conf, "model_dump"):
        return conf.model_dump()  # type: ignore[no-any-return]
    if isinstance(conf, dict):
        return conf
    return None


# AKS VM SKUs API version (preview)
_AKS_VM_SKUS_API_VERSION = "2026-01-02-preview"

# ---------------------------------------------------------------------------
# Cache — mirrors the pattern used by other az-scout plugins
# ---------------------------------------------------------------------------

_CACHE_TTL = 600  # 10 minutes
_result_cache: dict[str, tuple[float, list[SkuRecommendation]]] = {}


def _cache_key(
    region: str,
    subscription_id: str,
    tenant_id: str | None,
    require_zones: bool,
    require_vmss: bool,
    min_vcpus: int | None,
    min_memory_gb: float | None,
    sku_name_filter: str | None,
    pool_type: str = "system",
) -> str:
    """Build a deterministic cache key from all query parameters."""
    return (
        f"{region}:{subscription_id}:{tenant_id or ''}:"
        f"z={require_zones}:v={require_vmss}:"
        f"cpu={min_vcpus}:mem={min_memory_gb}:f={sku_name_filter or ''}:"
        f"pool={pool_type}"
    )


def clear_cache() -> None:
    """Clear the recommendation cache (useful for tests)."""
    _result_cache.clear()


# ---------------------------------------------------------------------------
# Main recommendation pipeline
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# AKS VM SKUs fetcher
# ---------------------------------------------------------------------------


def _fetch_aks_vm_skus(
    region: str,
    subscription_id: str,
    tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    """Fetch VM SKUs accepted by AKS from the Container Service API.

    Calls ``Microsoft.ContainerService/locations/{location}/vmSkus``
    and returns raw SKU dicts.  Falls back gracefully on auth/404 errors.
    """
    from az_scout.azure_api import AZURE_MGMT_URL, arm_paginate

    url = (
        f"{AZURE_MGMT_URL}/subscriptions/{subscription_id}/providers/"
        f"Microsoft.ContainerService/locations/{region}/vmSkus"
        f"?api-version={_AKS_VM_SKUS_API_VERSION}"
    )
    return arm_paginate(url, tenant_id=tenant_id, timeout=60)


def _parse_aks_sku(raw: dict[str, Any], region: str) -> dict[str, Any]:
    """Normalise a raw AKS VM SKU dict into the internal format.

    Extracts capabilities into a flat dict and resolves zones for the
    target region from ``locationInfo``.

    The AKS API returns capability names in camelCase (e.g. ``memoryGB``,
    ``premiumIO``) while the Compute SKUs API uses PascalCase
    (``MemoryGB``, ``PremiumIO``).  We normalise to PascalCase so the
    scoring module can use a single set of constants.
    """
    # Map AKS camelCase capability names → PascalCase (Compute convention)
    _aks_cap_aliases: dict[str, str] = {
        "memoryGB": "MemoryGB",
        "vCPUs": "vCPUs",
        "maxDataDiskCount": "MaxDataDiskCount",
        "premiumIO": "PremiumIO",
        "acceleratedNetworkingEnabled": "AcceleratedNetworkingEnabled",
        "ephemeralOSDiskSupported": "EphemeralOSDiskSupported",
        "encryptionAtHostSupported": "EncryptionAtHostSupported",
    }

    # Build flat capabilities dict with normalised keys
    capabilities: dict[str, str] = {}
    for cap in raw.get("capabilities", []):
        cap_name = cap.get("name", "")
        cap_value = cap.get("value", "")
        if cap_name:
            normalised = _aks_cap_aliases.get(cap_name, cap_name)
            capabilities[normalised] = cap_value

    # Resolve zones from locationInfo
    zones: list[str] = []
    for loc_info in raw.get("locationInfo", []):
        if loc_info.get("location", "").lower() == region.lower():
            zones = loc_info.get("zones", [])
            break

    # Collect restrictions
    restrictions: list[dict[str, Any]] = []
    for restriction in raw.get("restrictions", []):
        if restriction.get("type") == "Zone":
            restrictions.append(restriction)

    return {
        "name": raw.get("name", ""),
        "tier": raw.get("tier", ""),
        "size": raw.get("size", ""),
        "family": raw.get("family", ""),
        "zones": zones,
        "restrictions": restrictions,
        "capabilities": capabilities,
    }


def _filter_skus(
    skus: list[dict[str, Any]],
    *,
    name: str | None = None,
    min_vcpus: int | None = None,
    min_memory_gb: float | None = None,
) -> list[dict[str, Any]]:
    """Apply client-side filters (the AKS API does not support $filter)."""
    name_lower = name.lower() if name else None
    result: list[dict[str, Any]] = []

    for sku in skus:
        # Name substring filter
        if name_lower and name_lower not in (sku.get("name") or "").lower():
            continue

        caps = sku.get("capabilities", {})

        # vCPU filter
        if min_vcpus is not None:
            try:
                if int(caps.get(CAP_VCPUS, "0")) < min_vcpus:
                    continue
            except (ValueError, TypeError):
                continue

        # Memory filter
        if min_memory_gb is not None:
            try:
                if float(caps.get(CAP_MEMORY_GB, "0")) < min_memory_gb:
                    continue
            except (ValueError, TypeError):
                continue

        result.append(sku)

    return result


# ---------------------------------------------------------------------------
# Main recommendation pipeline
# ---------------------------------------------------------------------------


def get_recommendations(
    region: str,
    tenant_id: str | None = None,
    subscription_id: str | None = None,
    *,
    require_zones: bool = False,
    require_vmss: bool = True,
    min_vcpus: int | None = None,
    min_memory_gb: float | None = None,
    sku_name_filter: str | None = None,
    max_results: int = 0,
    pool_type: str = "system",
) -> list[SkuRecommendation]:
    """Build scored AKS SKU recommendations for a region.

    Uses the AKS VM SKUs API to retrieve SKUs confirmed as accepted
    by AKS for node pool creation.

    Requires a ``subscription_id`` for the API call.
    """
    from az_scout.plugin_api import PluginUpstreamError, PluginValidationError

    if not subscription_id:
        raise PluginValidationError("subscription_id is required to query AKS VM SKUs")

    # --- Check cache ---
    key = _cache_key(
        region,
        subscription_id,
        tenant_id,
        require_zones,
        require_vmss,
        min_vcpus,
        min_memory_gb,
        sku_name_filter,
        pool_type,
    )
    now = time.monotonic()
    cached = _result_cache.get(key)
    if cached is not None:
        ts, data = cached
        if now - ts < _CACHE_TTL:
            logger.debug("Cache HIT for %s (%d items)", key, len(data))
            return data[:max_results] if max_results else data

    # --- Fetch AKS VM SKUs ---
    try:
        raw_skus = _fetch_aks_vm_skus(region, subscription_id, tenant_id)
    except Exception as exc:
        raise PluginUpstreamError(f"Failed to fetch AKS VM SKUs: {exc}") from exc

    logger.info("Fetched %d raw AKS VM SKUs for %s", len(raw_skus), region)

    # Parse and filter
    skus = [_parse_aks_sku(raw, region) for raw in raw_skus]
    skus = _filter_skus(
        skus,
        name=sku_name_filter,
        min_vcpus=min_vcpus,
        min_memory_gb=min_memory_gb,
    )
    logger.info("After filtering: %d SKUs", len(skus))

    # --- Enrich with Compute SKU restrictions ---
    try:
        from az_scout.azure_api import get_skus as get_compute_skus

        compute_skus = get_compute_skus(
            region,
            subscription_id,
            tenant_id=tenant_id,
            resource_type="virtualMachines",
        )
        restriction_map: dict[str, list[str]] = {}
        for cs in compute_skus:
            restriction_map[cs.get("name", "")] = cs.get("restrictions", [])

        for sku in skus:
            name = sku.get("name", "")
            compute_restrictions = restriction_map.get(name, [])
            if compute_restrictions:
                sku["restrictions"] = compute_restrictions
                # Remove restricted zones from available zones
                restricted_zones: set[str] = set()
                for r in compute_restrictions:
                    if isinstance(r, str):
                        restricted_zones.add(r)
                available = [z for z in sku.get("zones", []) if z not in restricted_zones]
                sku["zones"] = available
        logger.info("Enriched SKUs with Compute restrictions (%d entries)", len(restriction_map))
    except Exception:
        logger.warning("Compute SKU restriction enrichment failed, continuing without")

    # --- Optional quota enrichment ---
    quota_map: dict[str, int | None] = {}
    has_quota = False
    try:
        from az_scout.azure_api import enrich_skus_with_quotas

        enrich_skus_with_quotas(skus, region, subscription_id, tenant_id)
        has_quota = True
        for sku in skus:
            quota_info = sku.get("quota", {})
            remaining = quota_info.get("remaining")
            family = sku.get("family", "")
            if family:
                quota_map[family] = remaining
    except Exception:
        logger.warning("Quota enrichment failed, continuing without quota data")

    # --- Optional pricing enrichment ---
    try:
        from az_scout.azure_api import enrich_skus_with_prices

        enrich_skus_with_prices(skus, region)
        logger.info("Enriched SKUs with pricing data")
    except Exception:
        logger.warning("Pricing enrichment failed, continuing without pricing data")

    # --- Compute deployment confidence (uses quota + pricing signals) ---
    try:
        from az_scout.scoring.deployment_confidence import enrich_skus_with_confidence

        enrich_skus_with_confidence(skus)
        logger.info("Computed deployment confidence for %d SKUs", len(skus))
    except Exception:
        logger.warning("Confidence scoring failed, continuing without")

    # --- Build recommendations ---
    all_names = [s.get("name", "") for s in skus]
    recommendations: list[SkuRecommendation] = []

    for sku in skus:
        capabilities = sku.get("capabilities", {})
        vcpus = extract_capability_int(capabilities, CAP_VCPUS)
        memory_gb = extract_capability_float(capabilities, CAP_MEMORY_GB)
        zones = sku.get("zones", [])

        # Apply require_zones filter
        if require_zones and not zones:
            continue

        family = sku.get("family", "")
        quota_remaining = quota_map.get(family)

        score_val, confidence, warnings = score_sku(
            sku,
            require_zones=require_zones,
            require_vmss=require_vmss,
            has_quota_data=has_quota,
            quota_remaining=quota_remaining,
            vcpus=vcpus,
        )

        fallbacks = suggest_fallbacks(sku.get("name", ""), all_names)

        # AKS eligibility check (system vs user pool rules + VMSS warnings)
        from az_scout_aks_placement_advisor._aks_filter import check_aks_eligibility

        eligibility = check_aks_eligibility(sku, pool_type)

        quota_info = sku.get("quota", {})

        rec = SkuRecommendation(
            sku_name=sku.get("name", ""),
            region=region,
            resource_type="virtualMachines",
            family=family,
            size=sku.get("size", ""),
            vcpus=vcpus,
            memory_gb=memory_gb,
            zones=zones,
            vmss_supported=True,
            aks_compatible=True,
            quota_available=quota_remaining,
            quota_limit=quota_info.get("limit"),
            quota_used=quota_info.get("used"),
            score=score_val,
            confidence=confidence,
            warnings=warnings,
            fallback_skus=fallbacks,
            eligibility_status=eligibility.status,
            eligibility_errors=eligibility.errors,
            eligibility_warnings=eligibility.warnings,
            pool_type=pool_type,
            pricing_paygo=pricing.get("paygo") if (pricing := sku.get("pricing", {})) else None,
            pricing_spot=pricing.get("spot") if pricing else None,
            pricing_currency=pricing.get("currency", "USD") if pricing else "USD",
            deployment_confidence=_serialize_confidence(sku.get("confidence")),
        )
        recommendations.append(rec)

    # Sort by score descending, then by name for stability
    recommendations.sort(key=lambda r: (-r.score, r.sku_name))

    # Store in cache (full list) and return truncated
    _result_cache[key] = (time.monotonic(), recommendations)
    return recommendations[:max_results] if max_results else recommendations


# ---------------------------------------------------------------------------
# Region listing
# ---------------------------------------------------------------------------


def get_regions(
    tenant_id: str | None = None,
    subscription_id: str | None = None,
) -> list[dict[str, str]]:
    """Return Azure regions suitable for Compute SKU lookup.

    If ``subscription_id`` is provided, lists regions from that subscription.
    Otherwise uses the first available subscription.
    """
    from az_scout.azure_api import list_regions, list_subscriptions
    from az_scout.plugin_api import PluginUpstreamError

    if not subscription_id:
        try:
            subs = list_subscriptions(tenant_id)
            if subs:
                subscription_id = subs[0].get("subscriptionId", "")
        except Exception as exc:
            raise PluginUpstreamError(f"Failed to list subscriptions: {exc}") from exc

    if not subscription_id:
        return []

    try:
        regions = list_regions(subscription_id, tenant_id)
    except Exception as exc:
        raise PluginUpstreamError(f"Failed to list regions: {exc}") from exc

    return [
        {"name": r.get("name", ""), "displayName": r.get("displayName", "")}
        for r in regions
        if r.get("name")
    ]
