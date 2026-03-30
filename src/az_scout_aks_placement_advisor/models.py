"""Recommendation model for AKS Placement Advisor."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SkuRecommendation:
    """A single SKU recommendation for AKS node pool placement."""

    sku_name: str
    region: str
    resource_type: str
    family: str
    size: str
    vcpus: int
    memory_gb: float
    zones: list[str]
    vmss_supported: bool | None
    aks_compatible: bool | None
    quota_available: int | None
    score: float
    confidence: str
    warnings: list[str] = field(default_factory=list)
    fallback_skus: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        """Serialise to a JSON-compatible dict with camelCase keys."""
        return {
            "skuName": self.sku_name,
            "region": self.region,
            "resourceType": self.resource_type,
            "family": self.family,
            "size": self.size,
            "vcpus": self.vcpus,
            "memoryGb": self.memory_gb,
            "zones": self.zones,
            "vmssSupported": self.vmss_supported,
            "aksCompatible": self.aks_compatible,
            "quotaAvailable": self.quota_available,
            "score": self.score,
            "confidence": self.confidence,
            "warnings": self.warnings,
            "fallbackSkus": self.fallback_skus,
        }


# Disclaimer text included in every response
DISCLAIMER = (
    "SKU data comes from the AKS VM SKUs API (preview). "
    "Scores are heuristic estimates and do not guarantee actual capacity, "
    "availability, or quota. Always verify before provisioning."
)
