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
    quota_limit: int | None = None
    quota_used: int | None = None
    # AKS eligibility (from _aks_filter)
    eligibility_status: str = "eligible"  # eligible | warning | ineligible
    eligibility_errors: list[str] = field(default_factory=list)
    eligibility_warnings: list[str] = field(default_factory=list)
    pool_type: str = "system"
    # Pricing (from enrich_skus_with_prices)
    pricing_paygo: float | None = None
    pricing_spot: float | None = None
    pricing_currency: str = "USD"
    # Deployment confidence (from enrich_skus_with_confidence)
    deployment_confidence: dict[str, object] | None = None
    # AKS score breakdown (from scoring.score_sku)
    score_breakdown: list[dict[str, object]] = field(default_factory=list)

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
            "quotaLimit": self.quota_limit,
            "quotaUsed": self.quota_used,
            "score": self.score,
            "confidence": self.confidence,
            "scoreBreakdown": self.score_breakdown,
            "warnings": self.warnings,
            "fallbackSkus": self.fallback_skus,
            "aks": {
                "status": self.eligibility_status,
                "eligible": self.eligibility_status != "ineligible",
                "pool_type": self.pool_type,
                "errors": self.eligibility_errors,
                "warnings": self.eligibility_warnings,
            },
            "pricing": {
                "paygo": self.pricing_paygo,
                "spot": self.pricing_spot,
                "currency": self.pricing_currency,
            },
            "deploymentConfidence": self.deployment_confidence,
        }


# Disclaimer text included in every response
DISCLAIMER = (
    "SKU data comes from the AKS VM SKUs API (preview). "
    "Scores are heuristic estimates and do not guarantee actual capacity, "
    "availability, or quota. Always verify before provisioning."
)
