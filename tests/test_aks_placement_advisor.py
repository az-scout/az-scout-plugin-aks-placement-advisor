"""Tests for the AKS Placement Advisor plugin."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

import pytest
from az_scout.plugin_api import PluginError
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from az_scout_aks_placement_advisor.models import DISCLAIMER, SkuRecommendation
from az_scout_aks_placement_advisor.routes import router
from az_scout_aks_placement_advisor.scoring import score_sku
from az_scout_aks_placement_advisor.service import clear_cache

# ---------------------------------------------------------------------------
# Test app fixture — mirrors the core app's PluginError handler
# ---------------------------------------------------------------------------

_app = FastAPI()


@_app.exception_handler(PluginError)
async def _plugin_error_handler(_request: Request, exc: PluginError) -> JSONResponse:
    return JSONResponse(
        {"error": str(exc), "detail": str(exc)},
        status_code=exc.status_code,
    )


_app.include_router(router, prefix="/plugins/aks-placement-advisor")
_client = TestClient(_app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clear_caches() -> None:
    """Clear service caches before each test."""
    clear_cache()


# ---------------------------------------------------------------------------
# Sample AKS VM SKUs API raw response items
# (mimicking Microsoft.ContainerService/locations/{location}/vmSkus)
# ---------------------------------------------------------------------------

SAMPLE_RAW_AKS_SKUS: list[dict[str, Any]] = [
    {
        "name": "Standard_D4s_v5",
        "tier": "Standard",
        "size": "D4s_v5",
        "family": "standardDSv5Family",
        "resourceType": "virtualMachines",
        "locations": ["eastus"],
        "locationInfo": [
            {"location": "eastus", "zones": ["1", "2", "3"]},
        ],
        "restrictions": [],
        "capabilities": [
            {"name": "vCPUs", "value": "4"},
            {"name": "MemoryGB", "value": "16"},
            {"name": "MaxDataDiskCount", "value": "8"},
            {"name": "PremiumIO", "value": "True"},
            {"name": "AcceleratedNetworkingEnabled", "value": "True"},
            {"name": "EphemeralOSDiskSupported", "value": "True"},
        ],
    },
    {
        "name": "Standard_E8s_v5",
        "tier": "Standard",
        "size": "E8s_v5",
        "family": "standardESv5Family",
        "resourceType": "virtualMachines",
        "locations": ["eastus"],
        "locationInfo": [
            {"location": "eastus", "zones": ["1", "2"]},
        ],
        "restrictions": [],
        "capabilities": [
            {"name": "vCPUs", "value": "8"},
            {"name": "MemoryGB", "value": "64"},
            {"name": "MaxDataDiskCount", "value": "16"},
            {"name": "PremiumIO", "value": "True"},
        ],
    },
    {
        "name": "Standard_B2s",
        "tier": "Standard",
        "size": "B2s",
        "family": "standardBSFamily",
        "resourceType": "virtualMachines",
        "locations": ["eastus"],
        "locationInfo": [
            {"location": "eastus", "zones": []},
        ],
        "restrictions": [],
        "capabilities": [
            {"name": "vCPUs", "value": "2"},
            {"name": "MemoryGB", "value": "4"},
        ],
    },
    {
        "name": "Standard_F4s_v2",
        "tier": "Standard",
        "size": "F4s_v2",
        "family": "standardFSv2Family",
        "resourceType": "virtualMachines",
        "locations": ["eastus"],
        "locationInfo": [
            {"location": "eastus", "zones": ["1", "2", "3"]},
        ],
        "restrictions": [{"type": "Zone", "restrictionInfo": {"zones": ["3"]}}],
        "capabilities": [
            {"name": "vCPUs", "value": "4"},
            {"name": "MemoryGB", "value": "8"},
            {"name": "MaxDataDiskCount", "value": "8"},
            {"name": "PremiumIO", "value": "True"},
            {"name": "AcceleratedNetworkingEnabled", "value": "True"},
            {"name": "EphemeralOSDiskSupported", "value": "True"},
        ],
    },
]

# Parsed format (after _parse_aks_sku) for scoring unit tests
SAMPLE_SKUS: list[dict[str, Any]] = [
    {
        "name": "Standard_D4s_v5",
        "tier": "Standard",
        "size": "D4s_v5",
        "family": "standardDSv5Family",
        "zones": ["1", "2", "3"],
        "restrictions": [],
        "capabilities": {
            "vCPUs": "4",
            "MemoryGB": "16",
            "MaxDataDiskCount": "8",
            "PremiumIO": "True",
            "AcceleratedNetworkingEnabled": "True",
            "EphemeralOSDiskSupported": "True",
        },
    },
    {
        "name": "Standard_F4s_v2",
        "tier": "Standard",
        "size": "F4s_v2",
        "family": "standardFSv2Family",
        "zones": ["1", "2", "3"],
        "restrictions": [{"type": "Zone", "restrictionInfo": {"zones": ["3"]}}],
        "capabilities": {
            "vCPUs": "4",
            "MemoryGB": "8",
            "MaxDataDiskCount": "8",
            "PremiumIO": "True",
            "AcceleratedNetworkingEnabled": "True",
            "EphemeralOSDiskSupported": "True",
        },
    },
    {
        "name": "Standard_B2s",
        "tier": "Standard",
        "size": "B2s",
        "family": "standardBSFamily",
        "zones": [],
        "restrictions": [],
        "capabilities": {
            "vCPUs": "2",
            "MemoryGB": "4",
        },
    },
]


# ---------------------------------------------------------------------------
# Health route tests
# ---------------------------------------------------------------------------


class TestHealthRoute:
    def test_health_returns_ok(self) -> None:
        resp = _client.get("/plugins/aks-placement-advisor/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["plugin"] == "aks-placement-advisor"


# ---------------------------------------------------------------------------
# Recommendations route — validation tests
# ---------------------------------------------------------------------------


class TestRecommendationsValidation:
    def test_missing_region(self) -> None:
        resp = _client.get("/plugins/aks-placement-advisor/recommendations")
        # FastAPI returns 422 for missing required query params
        assert resp.status_code == 422

    def test_invalid_max_results(self) -> None:
        resp = _client.get(
            "/plugins/aks-placement-advisor/recommendations",
            params={"region": "eastus", "subscriptionId": "sub1", "max_results": -1},
        )
        assert resp.status_code == 422

    def test_invalid_min_vcpus(self) -> None:
        resp = _client.get(
            "/plugins/aks-placement-advisor/recommendations",
            params={"region": "eastus", "subscriptionId": "sub1", "min_vcpus": -1},
        )
        assert resp.status_code == 422

    def test_missing_subscription_id(self) -> None:
        resp = _client.get(
            "/plugins/aks-placement-advisor/recommendations",
            params={"region": "eastus"},
        )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Recommendations route — with mocked ARM data
# ---------------------------------------------------------------------------


class TestRecommendationsWithMock:
    @patch("az_scout.azure_api.enrich_skus_with_quotas")
    @patch(
        "az_scout_aks_placement_advisor.service._fetch_aks_vm_skus",
    )
    def test_returns_recommendations(
        self,
        mock_fetch: Any,
        mock_enrich: Any,
    ) -> None:
        mock_fetch.return_value = [sku.copy() for sku in SAMPLE_RAW_AKS_SKUS]

        resp = _client.get(
            "/plugins/aks-placement-advisor/recommendations",
            params={
                "region": "eastus",
                "subscriptionId": "00000000-0000-0000-0000-000000000001",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["region"] == "eastus"
        assert "disclaimer" in data
        assert data["disclaimer"] == DISCLAIMER
        assert data["count"] > 0

        recs = data["recommendations"]
        assert isinstance(recs, list)
        assert len(recs) > 0

        # Check recommendation shape (camelCase keys)
        first = recs[0]
        assert "skuName" in first
        assert "score" in first
        assert "confidence" in first
        assert first["confidence"] in ("high", "medium", "low")
        assert "warnings" in first
        assert isinstance(first["warnings"], list)
        assert "zones" in first
        assert "vcpus" in first
        assert "memoryGb" in first
        assert "fallbackSkus" in first
        # AKS API confirms compatibility
        assert first["aksCompatible"] is True
        assert first["vmssSupported"] is True

    @patch("az_scout.azure_api.enrich_skus_with_quotas")
    @patch(
        "az_scout_aks_placement_advisor.service._fetch_aks_vm_skus",
    )
    def test_require_zones_filters(
        self,
        mock_fetch: Any,
        mock_enrich: Any,
    ) -> None:
        mock_fetch.return_value = [sku.copy() for sku in SAMPLE_RAW_AKS_SKUS]

        resp = _client.get(
            "/plugins/aks-placement-advisor/recommendations",
            params={
                "region": "eastus",
                "subscriptionId": "sub1",
                "require_zones": True,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        # All returned recommendations should have zones
        for rec in data["recommendations"]:
            assert len(rec["zones"]) > 0

    @patch("az_scout.azure_api.enrich_skus_with_quotas")
    @patch(
        "az_scout_aks_placement_advisor.service._fetch_aks_vm_skus",
    )
    def test_max_results_limits_output(
        self,
        mock_fetch: Any,
        mock_enrich: Any,
    ) -> None:
        mock_fetch.return_value = [sku.copy() for sku in SAMPLE_RAW_AKS_SKUS]

        resp = _client.get(
            "/plugins/aks-placement-advisor/recommendations",
            params={
                "region": "eastus",
                "subscriptionId": "sub1",
                "max_results": 2,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["recommendations"]) <= 2

    @patch(
        "az_scout_aks_placement_advisor.service._fetch_aks_vm_skus",
    )
    def test_arm_failure_returns_502(self, mock_fetch: Any) -> None:
        mock_fetch.side_effect = Exception("AKS API is down")

        resp = _client.get(
            "/plugins/aks-placement-advisor/recommendations",
            params={
                "region": "eastus",
                "subscriptionId": "sub1",
            },
        )
        assert resp.status_code == 502


# ---------------------------------------------------------------------------
# Scoring unit tests
# ---------------------------------------------------------------------------


class TestScoring:
    def test_high_confidence_sku(self) -> None:
        sku = SAMPLE_SKUS[0]  # Standard_D4s_v5 — zones, rich caps
        score, confidence, warnings, breakdown = score_sku(
            sku, has_quota_data=True, quota_remaining=80, vcpus=4
        )
        assert score > 0
        assert confidence in ("high", "medium", "low")
        assert isinstance(warnings, list)
        assert isinstance(breakdown, list)
        assert len(breakdown) > 0
        assert all("label" in b and "points" in b and "applied" in b for b in breakdown)

    def test_restricted_sku_penalised(self) -> None:
        sku = SAMPLE_SKUS[1]  # Standard_F4s_v2 — has restrictions
        score_restricted, _, _, _ = score_sku(sku)
        sku_clean = {**sku, "restrictions": []}
        score_clean, _, _, _ = score_sku(sku_clean)
        assert score_restricted < score_clean

    def test_no_zones_with_require_zones(self) -> None:
        sku = SAMPLE_SKUS[2]  # Standard_B2s — no zones
        _, _, warnings, _ = score_sku(sku, require_zones=True)
        assert any("zone" in w.lower() for w in warnings)

    def test_score_clamped(self) -> None:
        score, _, _, _ = score_sku(SAMPLE_SKUS[0])
        assert 0 <= score <= 100


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestModel:
    def test_to_dict_shape(self) -> None:
        rec = SkuRecommendation(
            sku_name="Standard_D2s_v5",
            region="eastus",
            resource_type="virtualMachines",
            family="standardDSv5Family",
            size="D2s_v5",
            vcpus=2,
            memory_gb=8.0,
            zones=["1", "2", "3"],
            vmss_supported=True,
            aks_compatible=True,
            quota_available=80,
            score=75.0,
            confidence="high",
            warnings=["Test warning"],
            fallback_skus=["Standard_D4s_v5"],
        )
        d = rec.to_dict()
        assert d["skuName"] == "Standard_D2s_v5"
        assert d["score"] == 75.0
        assert d["confidence"] == "high"
        assert d["vmssSupported"] is True
        assert d["aksCompatible"] is True
        assert len(d["warnings"]) == 1
        assert len(d["fallbackSkus"]) == 1


# ---------------------------------------------------------------------------
# MCP tool tests
# ---------------------------------------------------------------------------


class TestMCPTools:
    @patch("az_scout.azure_api.enrich_skus_with_quotas")
    @patch(
        "az_scout_aks_placement_advisor.service._fetch_aks_vm_skus",
    )
    def test_recommend_aks_skus_shape(
        self,
        mock_fetch: Any,
        mock_enrich: Any,
    ) -> None:
        mock_fetch.return_value = [sku.copy() for sku in SAMPLE_RAW_AKS_SKUS]

        from az_scout_aks_placement_advisor.tools import recommend_aks_skus

        raw = recommend_aks_skus(
            region="eastus",
            subscription_id="sub1",
        )
        result = json.loads(raw)
        assert "recommendations" in result
        assert "disclaimer" in result
        assert isinstance(result["recommendations"], list)

    @patch("az_scout.azure_api.enrich_skus_with_quotas")
    @patch(
        "az_scout_aks_placement_advisor.service._fetch_aks_vm_skus",
    )
    def test_compare_aks_regions_shape(
        self,
        mock_fetch: Any,
        mock_enrich: Any,
    ) -> None:
        mock_fetch.return_value = [sku.copy() for sku in SAMPLE_RAW_AKS_SKUS]

        from az_scout_aks_placement_advisor.tools import compare_aks_regions

        raw = compare_aks_regions(
            regions=["eastus", "westus2"],
            subscription_id="sub1",
        )
        result = json.loads(raw)
        assert "results" in result
        assert "disclaimer" in result
        assert result["regionsCompared"] == 2


# ---------------------------------------------------------------------------
# Plugin registration test
# ---------------------------------------------------------------------------


class TestPluginRegistration:
    def test_plugin_instance_exists(self) -> None:
        from az_scout_aks_placement_advisor import plugin

        assert plugin.name == "aks-placement-advisor"
        assert plugin.version is not None

    def test_plugin_has_router(self) -> None:
        from az_scout_aks_placement_advisor import plugin

        assert plugin.get_router() is not None

    def test_plugin_has_mcp_tools(self) -> None:
        from az_scout_aks_placement_advisor import plugin

        tools = plugin.get_mcp_tools()
        assert tools is not None
        assert len(tools) == 2

    def test_plugin_has_static_dir(self) -> None:
        from az_scout_aks_placement_advisor import plugin

        static_dir = plugin.get_static_dir()
        assert static_dir is not None
        assert static_dir.is_dir()

    def test_plugin_has_tabs(self) -> None:
        from az_scout_aks_placement_advisor import plugin

        tabs = plugin.get_tabs()
        assert tabs is not None
        assert len(tabs) == 1
        assert tabs[0].id == "aks-placement-advisor"
