"""AKS node pool SKU eligibility rules.

Pure functions — no IO, no side effects, fully testable.

Status model
------------
- ``eligible``   – no errors, no warnings
- ``warning``    – no errors, but has warnings (eligible with caveats)
- ``ineligible`` – one or more blocking errors
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# Regex to extract the VM series prefix from an ARM SKU name.
_SERIES_RE = re.compile(r"^(?:Standard|Basic)_([A-Z]+)", re.IGNORECASE)


def _parse_series(sku_name: str) -> str:
    """Extract uppercase series prefix (e.g. 'D', 'NC', 'B')."""
    m = _SERIES_RE.match(sku_name)
    return m.group(1).upper() if m else ""

# Series blocked for all AKS node pool types.
_BLOCKED_SERIES: frozenset[str] = frozenset({"A"})

# Series blocked for system node pools only (allowed for user pools with warning).
_SYSTEM_BLOCKED_SERIES: frozenset[str] = frozenset({"B"})

# SKU name substrings indicating promo / preview SKUs.
_PROMO_SUFFIXES: tuple[str, ...] = ("_Promo", "_Preview")

# Minimum requirements per pool type.
_SYSTEM_MIN_VCPUS = 2
_SYSTEM_MIN_MEMORY_GB = 4.0
_USER_MIN_VCPUS = 2  # Practical minimum (AKS allows 1 but kubelet needs headroom)


@dataclass
class EligibilityResult:
    """Result of an AKS eligibility check.

    ``status`` is one of ``"eligible"``, ``"warning"``, ``"ineligible"``.
    """

    status: str  # "eligible" | "warning" | "ineligible"
    pool_type: str
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return self.status != "ineligible"

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "eligible": self.eligible,
            "pool_type": self.pool_type,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def check_aks_eligibility(
    sku: dict[str, Any],
    pool_type: str = "system",
    *,
    series: str = "",
) -> EligibilityResult:
    """Check whether a VM SKU is eligible for an AKS node pool.

    Parameters
    ----------
    sku:
        Enriched SKU dict (matches :class:`SkuDict` shape).
    pool_type:
        ``"system"`` or ``"user"``.
    series:
        Pre-parsed series prefix (e.g. ``"D"``).  If empty, the function
        will attempt to parse from the SKU name.

    Returns
    -------
    EligibilityResult:
        Contains ``status`` (eligible/warning/ineligible), ``errors``
        (blocking), ``warnings`` (non-blocking but noteworthy).
    """
    errors: list[str] = []
    warnings: list[str] = []
    name: str = sku.get("name", "")
    caps: dict[str, str] = sku.get("capabilities", {})
    zones: list[str] = sku.get("zones", [])
    is_system = pool_type == "system"

    # --- Series checks ---
    if not series:
        series = _parse_series(name)

    if series in _BLOCKED_SERIES:
        errors.append(f"{series}-series VMs are not supported on AKS")
    elif series in _SYSTEM_BLOCKED_SERIES:
        if is_system:
            errors.append(f"{series}-series (burstable) cannot be used for system node pools")
        else:
            warnings.append(
                f"{series}-series is burstable — CPU credits may exhaust under sustained load"
            )

    # --- Promo / Preview ---
    for suffix in _PROMO_SUFFIXES:
        if suffix in name:
            errors.append(f"Promo/preview SKUs ({suffix}) are not reliably available for AKS")
            break

    # --- vCPU minimum ---
    try:
        vcpus = int(caps.get("vCPUs", "0"))
    except ValueError:
        vcpus = 0

    min_vcpus = _SYSTEM_MIN_VCPUS if is_system else _USER_MIN_VCPUS
    if vcpus < min_vcpus:
        errors.append(f"Requires ≥{min_vcpus} vCPUs (has {vcpus})")

    # --- Memory minimum ---
    try:
        memory_gb = float(caps.get("MemoryGB", "0"))
    except ValueError:
        memory_gb = 0.0

    if is_system and memory_gb < _SYSTEM_MIN_MEMORY_GB:
        errors.append(f"System pool requires ≥{_SYSTEM_MIN_MEMORY_GB} GB RAM (has {memory_gb})")
    elif memory_gb < _SYSTEM_MIN_MEMORY_GB:
        warnings.append(f"Low memory ({memory_gb} GB) — may be insufficient for workloads")

    # --- Premium Storage ---
    premium_io = caps.get("PremiumIO", "").lower() == "true"
    if is_system and not premium_io:
        errors.append("System pools require Premium Storage (PremiumIO)")
    elif not premium_io:
        warnings.append("No Premium Storage — OS disk performance may be limited")

    # --- Accelerated Networking ---
    accel_net = caps.get("AcceleratedNetworkingEnabled", "").lower() == "true"
    if not accel_net:
        warnings.append("Accelerated Networking not supported — higher network latency expected")

    # --- Ephemeral OS Disk ---
    ephemeral = caps.get("EphemeralOSDiskSupported", "").lower() == "true"
    if not ephemeral:
        warnings.append("Ephemeral OS disk not supported — managed OS disk will be used")

    # --- Zone availability ---
    if not zones:
        errors.append("SKU not available in any availability zone in this region")

    # --- Restrictions ---
    raw_restrictions = sku.get("restrictions", [])
    if raw_restrictions:
        # Restrictions can be list[str] (zone IDs) or list[dict] (full objects)
        zone_ids: list[str] = []
        for r in raw_restrictions:
            if isinstance(r, str):
                zone_ids.append(r)
            elif isinstance(r, dict) and r.get("type") == "Zone":
                zone_ids.extend(r.get("zones", r.get("restrictionInfo", {}).get("zones", [])))
        if zone_ids:
            warnings.append(f"Zone restrictions: {', '.join(zone_ids)}")
        else:
            warnings.append("SKU has restrictions in this region")

    # -----------------------------------------------------------------
    # VMSS-specific checks (AKS node pools are backed by VMSS)
    # -----------------------------------------------------------------

    # --- Spot / Low-priority support ---
    low_priority = caps.get("LowPriorityCapable", "").lower() == "true"
    if not low_priority and not is_system:
        warnings.append("Not Spot-capable — cannot be used for Spot node pools")

    # --- Trusted Launch ---
    trusted_launch_disabled = caps.get("TrustedLaunchDisabled", "").lower() == "true"
    hyper_v = caps.get("HyperVGenerations", "")
    is_gen1_only = hyper_v and "V2" not in hyper_v.upper()
    if trusted_launch_disabled or is_gen1_only:
        warnings.append("Gen1-only / Trusted Launch unavailable — no Secure Boot or vTPM")

    # --- Host encryption ---
    host_encryption = caps.get("EncryptionAtHostSupported", "").lower() == "true"
    if not host_encryption:
        warnings.append("Host encryption not supported — may not meet compliance policies")

    # --- CPU architecture ---
    arch = caps.get("CpuArchitectureType", "")
    if arch and arch.lower() == "arm64":
        warnings.append("ARM64 architecture — ensure container images support arm64")

    # --- Ultra SSD ---
    ultra_ssd = caps.get("UltraSSDAvailable", "").lower() == "true"
    if not ultra_ssd:
        # This is informational, not a warning — only note if someone might care
        pass  # Kept for future use; too noisy as a default warning

    # -----------------------------------------------------------------
    # Determine 3-state status
    # -----------------------------------------------------------------
    if errors:
        status = "ineligible"
    elif warnings:
        status = "warning"
    else:
        status = "eligible"

    return EligibilityResult(
        status=status,
        pool_type=pool_type,
        errors=errors,
        warnings=warnings,
    )


def annotate_skus(
    skus: list[dict[str, Any]],
    pool_type: str = "system",
) -> list[dict[str, Any]]:
    """Add ``aks`` eligibility annotations to each SKU dict.

    Mutates dicts in place and returns the same list for convenience.
    """
    for sku in skus:
        series = _parse_series(sku.get("name", ""))
        result = check_aks_eligibility(sku, pool_type, series=series)
        sku["aks"] = result.to_dict()
        sku["series"] = series
    return skus
