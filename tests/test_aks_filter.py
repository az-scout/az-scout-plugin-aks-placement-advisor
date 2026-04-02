"""Tests for AKS eligibility filter — pure logic, no Azure calls."""

from __future__ import annotations

from az_scout_aks_placement_advisor._aks_filter import (
    EligibilityResult,
    annotate_skus,
    check_aks_eligibility,
)


def _make_sku(
    name: str = "Standard_D2s_v5",
    vcpus: str = "2",
    memory_gb: str = "8",
    premium_io: str = "True",
    accel_net: str = "True",
    ephemeral: str = "True",
    hyper_v: str = "V1,V2",
    low_priority: str = "True",
    trusted_launch_disabled: str = "False",
    encryption_at_host: str = "True",
    cpu_arch: str = "x64",
    zones: list[str] | None = None,
    restrictions: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "family": "standardDSv5Family",
        "capabilities": {
            "vCPUs": vcpus,
            "MemoryGB": memory_gb,
            "PremiumIO": premium_io,
            "AcceleratedNetworkingEnabled": accel_net,
            "EphemeralOSDiskSupported": ephemeral,
            "HyperVGenerations": hyper_v,
            "LowPriorityCapable": low_priority,
            "TrustedLaunchDisabled": trusted_launch_disabled,
            "EncryptionAtHostSupported": encryption_at_host,
            "CpuArchitectureType": cpu_arch,
        },
        "zones": zones if zones is not None else ["1", "2", "3"],
        "restrictions": restrictions or [],
    }


class TestSystemPool:
    """System pool eligibility rules."""

    def test_standard_d_series_eligible(self) -> None:
        result = check_aks_eligibility(_make_sku(), "system", series="D")
        assert result.status == "eligible"
        assert result.eligible
        assert result.errors == []
        assert result.warnings == []

    def test_b_series_blocked(self) -> None:
        result = check_aks_eligibility(_make_sku(name="Standard_B2s"), "system", series="B")
        assert result.status == "ineligible"
        assert not result.eligible
        assert any("burstable" in e.lower() for e in result.errors)

    def test_a_series_blocked(self) -> None:
        result = check_aks_eligibility(_make_sku(name="Standard_A2_v2"), "system", series="A")
        assert not result.eligible
        assert any("A-series" in e for e in result.errors)

    def test_promo_blocked(self) -> None:
        result = check_aks_eligibility(
            _make_sku(name="Standard_D2s_v5_Promo"), "system", series="D"
        )
        assert not result.eligible
        assert any("Promo" in e for e in result.errors)

    def test_1_vcpu_blocked(self) -> None:
        result = check_aks_eligibility(_make_sku(vcpus="1"), "system", series="D")
        assert not result.eligible
        assert any("vCPUs" in e for e in result.errors)

    def test_low_memory_blocked(self) -> None:
        result = check_aks_eligibility(_make_sku(memory_gb="2"), "system", series="D")
        assert not result.eligible
        assert any("RAM" in e for e in result.errors)

    def test_no_premium_io_blocked(self) -> None:
        result = check_aks_eligibility(_make_sku(premium_io="False"), "system", series="D")
        assert not result.eligible
        assert any("Premium" in e for e in result.errors)

    def test_no_zones_blocked(self) -> None:
        result = check_aks_eligibility(_make_sku(zones=[]), "system", series="D")
        assert not result.eligible
        assert any("zone" in e.lower() for e in result.errors)

    def test_no_accel_net_warning(self) -> None:
        result = check_aks_eligibility(_make_sku(accel_net="False"), "system", series="D")
        assert result.status == "warning"  # not eligible, not ineligible
        assert result.eligible  # still eligible (warnings only)
        assert any("Accelerated" in w for w in result.warnings)

    def test_no_ephemeral_warning(self) -> None:
        result = check_aks_eligibility(_make_sku(ephemeral="False"), "system", series="D")
        assert result.eligible
        assert any("Ephemeral" in w for w in result.warnings)

    def test_zone_restrictions_warning(self) -> None:
        result = check_aks_eligibility(_make_sku(restrictions=["2"]), "system", series="D")
        assert result.eligible
        assert any("restriction" in w.lower() for w in result.warnings)


class TestUserPool:
    """User pool eligibility rules."""

    def test_b_series_allowed_with_warning(self) -> None:
        result = check_aks_eligibility(_make_sku(name="Standard_B2s"), "user", series="B")
        assert result.status == "warning"
        assert result.eligible
        assert any("burstable" in w.lower() for w in result.warnings)

    def test_no_premium_io_warning(self) -> None:
        result = check_aks_eligibility(_make_sku(premium_io="False"), "user", series="D")
        assert result.eligible  # warning for user pool
        assert any("Premium" in w for w in result.warnings)

    def test_a_series_still_blocked(self) -> None:
        result = check_aks_eligibility(_make_sku(name="Standard_A2_v2"), "user", series="A")
        assert not result.eligible

    def test_standard_e_series_eligible(self) -> None:
        result = check_aks_eligibility(
            _make_sku(name="Standard_E4s_v5", memory_gb="32"), "user", series="E"
        )
        assert result.eligible


class TestAnnotateSkus:
    """Test batch annotation."""

    def test_annotate_adds_aks_field(self) -> None:
        skus = [_make_sku(), _make_sku(name="Standard_B2s")]
        annotate_skus(skus, "system")
        assert "aks" in skus[0]
        assert "aks" in skus[1]
        assert skus[0]["aks"]["status"] == "eligible"
        assert skus[1]["aks"]["status"] == "ineligible"

    def test_annotate_adds_series_field(self) -> None:
        skus = [_make_sku(name="Standard_NC24ads_A100_v4")]
        annotate_skus(skus, "user")
        assert skus[0]["series"] == "NC"


class TestEligibilityResult:
    """Test result serialisation."""

    def test_to_dict_ineligible(self) -> None:
        r = EligibilityResult(
            status="ineligible",
            pool_type="system",
            errors=["test error"],
            warnings=["test warning"],
        )
        d = r.to_dict()
        assert d["status"] == "ineligible"
        assert d["eligible"] is False
        assert d["pool_type"] == "system"
        assert d["errors"] == ["test error"]
        assert d["warnings"] == ["test warning"]

    def test_to_dict_warning(self) -> None:
        r = EligibilityResult(
            status="warning",
            pool_type="user",
            warnings=["some caveat"],
        )
        d = r.to_dict()
        assert d["status"] == "warning"
        assert d["eligible"] is True

    def test_to_dict_eligible(self) -> None:
        r = EligibilityResult(status="eligible", pool_type="system")
        d = r.to_dict()
        assert d["status"] == "eligible"
        assert d["eligible"] is True
        assert d["errors"] == []
        assert d["warnings"] == []


class TestVmssChecks:
    """VMSS-specific eligibility checks."""

    def test_gen1_only_warning(self) -> None:
        result = check_aks_eligibility(_make_sku(hyper_v="V1"), "system", series="D")
        assert result.status == "warning"
        assert any("Gen1" in w for w in result.warnings)

    def test_trusted_launch_disabled_warning(self) -> None:
        result = check_aks_eligibility(
            _make_sku(trusted_launch_disabled="True"), "system", series="D"
        )
        assert result.status == "warning"
        assert any("Trusted Launch" in w for w in result.warnings)

    def test_no_spot_capability_warning_user_pool(self) -> None:
        result = check_aks_eligibility(_make_sku(low_priority="False"), "user", series="D")
        assert result.status == "warning"
        assert any("Spot" in w for w in result.warnings)

    def test_no_spot_capability_no_warning_system_pool(self) -> None:
        """System pools can't be Spot anyway, so no warning."""
        result = check_aks_eligibility(_make_sku(low_priority="False"), "system", series="D")
        assert not any("Spot" in w for w in result.warnings)

    def test_no_host_encryption_warning(self) -> None:
        result = check_aks_eligibility(_make_sku(encryption_at_host="False"), "system", series="D")
        assert result.status == "warning"
        assert any("encryption" in w.lower() for w in result.warnings)

    def test_arm64_architecture_warning(self) -> None:
        result = check_aks_eligibility(_make_sku(cpu_arch="Arm64"), "user", series="D")
        assert result.status == "warning"
        assert any("ARM64" in w for w in result.warnings)

    def test_x64_no_arch_warning(self) -> None:
        result = check_aks_eligibility(_make_sku(cpu_arch="x64"), "system", series="D")
        assert not any("ARM64" in w for w in result.warnings)
