"""Heuristic scoring logic for AKS SKU recommendations.

Implements an explainable scoring model that evaluates VM SKUs
for AKS node-pool suitability based on publicly available metadata.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Capability keys from the Compute Resource SKUs API
# ---------------------------------------------------------------------------

CAP_VCPUS = "vCPUs"
CAP_MEMORY_GB = "MemoryGB"
CAP_MAX_DATA_DISKS = "MaxDataDiskCount"
CAP_PREMIUM_IO = "PremiumIO"
CAP_ENCRYPTION_AT_HOST = "EncryptionAtHostSupported"
CAP_ACCELERATED_NETWORKING = "AcceleratedNetworkingEnabled"
CAP_EPHEMERAL_OS_DISK = "EphemeralOSDiskSupported"

# SKU families known to be problematic for AKS node pools
_UNSUITABLE_FAMILIES: frozenset[str] = frozenset(
    {
        "standardLSv2Family",  # storage-optimised, NVMe-only
    }
)

# SKU name prefixes commonly used for AKS workloads
_PREFERRED_PREFIXES: tuple[str, ...] = (
    "Standard_D",
    "Standard_E",
    "Standard_F",
    "Standard_B",
)

# ---------------------------------------------------------------------------
# Score weights (out of ~100)
# ---------------------------------------------------------------------------

_BASE_SCORE = 40.0
_ZONE_BONUS = 20.0
_MULTI_ZONE_BONUS = 15.0  # ≥3 zones — critical for HA deployments
_VMSS_BONUS = 10.0
_RICH_CAPS_BONUS = 5.0
_QUOTA_BONUS = 10.0
_PREFERRED_FAMILY_BONUS = 5.0
_RESTRICTION_PENALTY = -25.0
_NO_ZONE_PENALTY = -20.0  # when zones required but missing
_UNSUITABLE_PENALTY = -30.0

# Confidence thresholds
_HIGH_THRESHOLD = 75.0
_MEDIUM_THRESHOLD = 50.0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def extract_capability_int(
    capabilities: dict[str, str],
    key: str,
    default: int = 0,
) -> int:
    """Safely extract an integer capability value."""
    try:
        return int(capabilities.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def extract_capability_float(
    capabilities: dict[str, str],
    key: str,
    default: float = 0.0,
) -> float:
    """Safely extract a float capability value."""
    try:
        return float(capabilities.get(key, str(default)))
    except (ValueError, TypeError):
        return default


def _has_zone_restriction(sku: dict[str, Any]) -> bool:
    """Check if a SKU has zone-level restrictions."""
    return len(sku.get("restrictions", [])) > 0


# ---------------------------------------------------------------------------
# Core scoring function
# ---------------------------------------------------------------------------


def score_sku(
    sku: dict[str, Any],
    *,
    require_zones: bool = False,
    require_vmss: bool = True,
    has_quota_data: bool = False,
    quota_remaining: int | None = None,
    vcpus: int = 0,
) -> tuple[float, str, list[str]]:
    """Compute a heuristic score, confidence bucket, and warnings list.

    Parameters
    ----------
    sku:
        SKU dict as returned by ``az_scout.azure_api.get_skus()``.
    require_zones:
        Whether zone support is required.
    require_vmss:
        Whether VMSS support is desired (inferred, not proven).
    has_quota_data:
        Whether quota information is available.
    quota_remaining:
        Remaining vCPU quota (from ``enrich_skus_with_quotas``).
    vcpus:
        Number of vCPUs for this SKU (used for quota comparison).

    Returns
    -------
    tuple[float, str, list[str]]
        ``(score, confidence, warnings)`` where score is 0–100,
        confidence is ``"high"`` / ``"medium"`` / ``"low"``.
    """
    score = _BASE_SCORE
    warnings: list[str] = []
    capabilities = sku.get("capabilities", {})
    zones = sku.get("zones", [])

    # --- Zone support ---
    if zones:
        score += _ZONE_BONUS
        if len(zones) >= 3:
            score += _MULTI_ZONE_BONUS
    elif require_zones:
        score += _NO_ZONE_PENALTY
        warnings.append("Zone support required but not available for this SKU in this region")

    # --- VMSS support ---
    # All SKUs returned by the AKS VM SKUs API support VMSS-based node pools.
    if require_vmss:
        score += _VMSS_BONUS

    # --- Capabilities richness ---
    caps_present = sum(
        1
        for k in (
            CAP_VCPUS,
            CAP_MEMORY_GB,
            CAP_MAX_DATA_DISKS,
            CAP_PREMIUM_IO,
            CAP_ACCELERATED_NETWORKING,
            CAP_EPHEMERAL_OS_DISK,
        )
        if k in capabilities
    )
    if caps_present >= 4:
        score += _RICH_CAPS_BONUS

    # --- Preferred family bonus ---
    sku_name = sku.get("name", "")
    if any(sku_name.startswith(p) for p in _PREFERRED_PREFIXES):
        score += _PREFERRED_FAMILY_BONUS

    # --- Restrictions penalty ---
    if _has_zone_restriction(sku):
        score += _RESTRICTION_PENALTY
        warnings.append("SKU has zone-level restrictions in this region")

    # --- Quota enrichment ---
    if has_quota_data:
        if quota_remaining is not None and quota_remaining >= vcpus:
            score += _QUOTA_BONUS
        elif quota_remaining is not None and quota_remaining < vcpus:
            warnings.append(
                f"Insufficient quota: {quota_remaining} vCPUs remaining, "
                f"{vcpus} required per instance"
            )
    else:
        warnings.append("Quota data not available — provide subscription_id for enrichment")

    # --- Unsuitable family penalty ---
    family = sku.get("family", "")
    if family in _UNSUITABLE_FAMILIES:
        score += _UNSUITABLE_PENALTY
        warnings.append(f"SKU family '{family}' may not be suitable for general AKS workloads")

    # Clamp score to [0, 100]
    score = max(0.0, min(100.0, score))

    # Confidence bucket
    if score >= _HIGH_THRESHOLD:
        confidence = "high"
    elif score >= _MEDIUM_THRESHOLD:
        confidence = "medium"
    else:
        confidence = "low"

    return round(score, 1), confidence, warnings


# ---------------------------------------------------------------------------
# Fallback suggestions
# ---------------------------------------------------------------------------


def suggest_fallbacks(
    sku_name: str,
    all_names: list[str],
    max_fallbacks: int = 3,
) -> list[str]:
    """Suggest alternative SKUs from the same size family.

    If the SKU is ``Standard_D4s_v5``, suggest other ``Standard_D*`` SKUs.
    """
    parts = sku_name.split("_")
    if len(parts) < 2:
        return []

    prefix = f"{parts[0]}_{parts[1][:1]}"  # e.g. "Standard_D"
    candidates = [name for name in all_names if name.startswith(prefix) and name != sku_name]
    return candidates[:max_fallbacks]
