# az-scout-plugin-aks-placement-advisor

AKS Placement Advisor plugin for [az-scout](https://github.com/az-scout/az-scout) — evaluates and recommends VM SKUs for AKS node pools with heuristic scoring, AKS eligibility checks, pricing data, and deployment confidence scoring.

## Purpose

This plugin helps answer:

- Which VM SKUs are accepted by AKS for node pools in a given region?
- Which SKUs have availability zone support without restrictions?
- Is a SKU eligible for system or user node pools?
- Are quotas sufficient for the selected SKU family?
- What is the estimated cost (pay-as-you-go and spot pricing)?
- What is the deployment confidence level for a SKU in a specific region?
- What fallback SKUs should be considered?

This is a **decision-support tool**, not a real-time capacity guarantee tool.

## How It Works

### Data Sources

The plugin combines multiple Azure APIs and enrichment stages to build recommendations:

```
┌─────────────────────────┐
│  AKS VM SKUs API        │  Primary source — confirms AKS compatibility
│  (ContainerService)     │  aksCompatible = True, vmssSupported = True
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  Compute SKUs API       │  Enrichment — zone restrictions per subscription
│  (Microsoft.Compute)    │  Removes restricted zones, adds warnings
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  Compute Usages API     │  Enrichment — vCPU quota per family
│  (Microsoft.Compute)    │  Shows remaining/used/limit quota, warns if insufficient
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  Pricing API            │  Enrichment — pay-as-you-go and spot prices
│                         │  Adds per-hour cost estimates
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  AKS Eligibility Filter │  System vs user pool eligibility (3-state)
│  (_aks_filter.py)       │  → eligible / warning / ineligible
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  Heuristic Scoring      │  Score 0–100 per SKU (scoring.py)
│                         │  → confidence: high / medium / low
└───────────┬─────────────┘
            ▼
┌─────────────────────────┐
│  Deployment Confidence  │  Weighted score from quota pressure, zone breadth,
│  (az-scout core)        │  restriction density, and price pressure
└─────────────────────────┘
```

1. **AKS VM SKUs API** (`Microsoft.ContainerService/locations/{location}/vmSkus`, `2026-01-02-preview`) — returns all VM SKUs accepted by AKS for node pool creation. Every SKU in this list is confirmed AKS-compatible.

2. **Compute Resource SKUs API** (`Microsoft.Compute/skus`) — provides per-subscription zone restrictions. A SKU may be listed as available in zones 1, 2, 3 by the AKS API, but restricted in zones 1 and 3 for your specific subscription. The plugin merges these restrictions and removes unavailable zones.

3. **Compute Usages API** (`Microsoft.Compute/locations/{location}/usages`) — provides remaining, used, and limit vCPU quota per SKU family.

4. **Pricing enrichment** — adds pay-as-you-go and spot pricing per SKU.

5. **Deployment Confidence** (from az-scout core) — a weighted score combining quota pressure, zone breadth, restriction density, and price pressure signals.

### Capability Normalisation

The AKS API returns capability names in camelCase (`memoryGB`, `premiumIO`) while the Compute API uses PascalCase (`MemoryGB`, `PremiumIO`). The plugin normalises all keys to PascalCase internally.

## AKS Eligibility Rules

Each SKU is checked for AKS node pool eligibility with a 3-state result: **eligible**, **warning**, or **ineligible**. Rules differ by pool type.

### System Node Pools

**Ineligible** (blocking):
- A-series VMs
- B-series (burstable) VMs
- Promo/Preview SKUs
- < 2 vCPUs or < 4 GB RAM
- No Premium Storage (PremiumIO)
- No availability zones

**Warning** (non-blocking):
- No Accelerated Networking
- No Ephemeral OS Disk support
- Gen1-only / Trusted Launch unavailable
- No host encryption support
- Zone restrictions in the region

### User Node Pools

**Ineligible** (blocking):
- A-series VMs
- Promo/Preview SKUs
- < 2 vCPUs
- No availability zones

**Warning** (non-blocking):
- B-series (CPU credit exhaustion risk)
- No Premium Storage
- No Accelerated Networking
- No Ephemeral OS Disk support
- Gen1-only / Trusted Launch unavailable
- No host encryption support
- Not Spot-capable
- ARM64 architecture (image compatibility)
- Low memory (< 4 GB)
- Zone restrictions

## Scoring Model

Each SKU receives a heuristic score from 0 to 100, computed as a sum of bonuses and penalties:

### Score Components

| Factor | Points | Condition |
|--------|--------|-----------|
| Base score | **+40** | Every valid AKS VM SKU |
| Zone support | **+20** | Has at least 1 availability zone |
| Multi-zone (≥3 zones) | **+15** | Available in 3+ zones — critical for HA |
| VMSS support | **+10** | Always true for AKS VM SKUs |
| Rich capabilities | **+5** | Has ≥4 of: vCPUs, MemoryGB, MaxDataDiskCount, PremiumIO, AcceleratedNetworking, EphemeralOSDisk |
| Preferred family | **+5** | SKU name starts with `Standard_D`, `Standard_E`, `Standard_F`, or `Standard_B` |
| Quota sufficient | **+10** | Remaining vCPU quota ≥ SKU vCPU count |
| **Maximum possible** | **105 → clamped to 100** | |

### Penalties

| Factor | Points | Condition |
|--------|--------|-----------|
| Zone restrictions | **-25** | SKU has zone-level restrictions for your subscription |
| No zones (when required) | **-20** | `require_zones=true` but SKU has no zone support |
| Unsuitable family | **-30** | SKU family known to be problematic (e.g. `standardLSv2Family`) |

### Confidence Buckets

| Score Range | Confidence | Meaning |
|-------------|------------|---------|
| ≥ 75 | **high** | Strong candidate — zones available, no restrictions, quota OK |
| 50 – 74 | **medium** | Usable but with caveats (partial zones, missing quota data) |
| < 50 | **low** | Significant issues — restrictions, no zones, unsuitable family |

## Deployment Confidence

In addition to the heuristic score, each SKU receives a **deployment confidence** score (0–100) from the az-scout core scoring engine. This uses weighted signals:

| Signal | Weight | Description |
|--------|--------|-------------|
| Quota Pressure | 38.5% | Non-linear penalty when family vCPU usage exceeds 80% |
| Zone Breadth | 23.1% | Available zones out of 3 (unrestricted) |
| Restriction Density | 23.1% | Fraction of zones without restrictions |
| Price Pressure | 15.4% | Spot-to-PAYGO ratio (lower = better savings potential) |

## Limitations

- **Scores are heuristic estimates.** They do not guarantee actual AKS VMSS capacity or VM availability.
- **Zone restrictions reflect subscription-level constraints**, not real-time capacity. A zone may be unrestricted but temporarily out of stock.
- **The AKS VM SKUs API is a preview API** (`2026-01-02-preview`). Behaviour may change.
- **Quota data depends on `subscription_id`** being provided. Without it, quota enrichment is skipped.
- **Pricing and deployment confidence enrichment** are best-effort — failures are logged and the pipeline continues without them.
- **No real-time capacity signal.** No Azure public API exposes VM stock levels.

## Available Routes

All routes are mounted under `/plugins/aks-placement-advisor/`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/health` | Plugin health status |
| GET | `/regions` | Azure regions suitable for Compute SKU lookup |
| GET | `/recommendations` | Scored AKS SKU recommendations for a region |

### GET `/recommendations` Query Parameters

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `region` | string | **yes** | — | Azure region name (e.g. `westeurope`) |
| `subscriptionId` | string | no | — | Azure subscription ID (required for API calls) |
| `tenantId` | string | no | — | Azure tenant ID |
| `require_zones` | bool | no | `false` | Only return SKUs with zone support |
| `require_vmss` | bool | no | `true` | Include VMSS bonus in scoring |
| `min_vcpus` | int | no | — | Minimum vCPU count filter |
| `min_memory_gb` | float | no | — | Minimum memory in GB filter |
| `sku_name_filter` | string | no | — | Substring filter on SKU name |
| `max_results` | int | no | `0` (all) | Maximum results (0–2000, 0 = no limit) |
| `pool_type` | string | no | `system` | Node pool type: `system` or `user` |

### Response Shape

```json
{
  "region": "westeurope",
  "count": 20,
  "disclaimer": "SKU data comes from the AKS VM SKUs API (preview)...",
  "recommendations": [
    {
      "skuName": "Standard_D4s_v6",
      "region": "westeurope",
      "resourceType": "virtualMachines",
      "family": "standardDSv6Family",
      "size": "D4s_v6",
      "vcpus": 4,
      "memoryGb": 16.0,
      "zones": ["1", "2", "3"],
      "vmssSupported": true,
      "aksCompatible": true,
      "quotaAvailable": 80,
      "quotaLimit": 100,
      "quotaUsed": 20,
      "score": 100.0,
      "confidence": "high",
      "warnings": [],
      "fallbackSkus": ["Standard_D8s_v6", "Standard_D2s_v6"],
      "aks": {
        "status": "eligible",
        "eligible": true,
        "pool_type": "system",
        "errors": [],
        "warnings": []
      },
      "pricing": {
        "paygo": 0.192,
        "spot": 0.038,
        "currency": "USD"
      },
      "deploymentConfidence": {
        "score": 92.0,
        "label": "high",
        "signals": {}
      }
    }
  ]
}
```

## Chat Mode

The plugin registers an **AKS Advisor** chat mode that provides guided SKU recommendations through conversation. It automatically calls the MCP tools with appropriate filters and presents results as ranked tables with rationale.

Suggested prompts:
- "Find the best SKU for a 3-node system pool"
- "What D-series SKUs are available in westeurope?"
- "Compare westeurope vs northeurope for AKS"
- "Recommend a GPU SKU for ML training"

## Available MCP Tools

| Tool | Description |
|------|-------------|
| `recommend_aks_skus` | Top AKS-oriented SKU recommendations for a region |
| `compare_aks_regions` | Compare multiple regions for AKS SKU placement suitability |

### `recommend_aks_skus`

```python
recommend_aks_skus(
    region="westeurope",
    subscription_id="...",
    tenant_id="...",
    require_zones=True,
    min_vcpus=4,
    min_memory_gb=8,
    max_results=10,
)
```

### `compare_aks_regions`

```python
compare_aks_regions(
    regions=["westeurope", "northeurope", "francecentral"],
    subscription_id="...",
    tenant_id="...",
    min_vcpus=4,
    min_memory_gb=8,
)
```

## Project Structure

```
src/az_scout_aks_placement_advisor/
├── __init__.py        # Plugin class + module-level `plugin` instance
├── _aks_filter.py     # AKS node pool eligibility rules (system vs user)
├── chat_mode.py       # AKS Advisor chat mode definition
├── models.py          # SkuRecommendation dataclass + DISCLAIMER
├── scoring.py         # Heuristic scoring engine (score_sku, suggest_fallbacks)
├── service.py         # Data pipeline: AKS API → enrichment → scoring
├── routes.py          # FastAPI routes (/health, /regions, /recommendations)
├── tools.py           # MCP tools (recommend_aks_skus, compare_aks_regions)
└── static/
    ├── css/aks-placement-advisor.css
    ├── html/aks-placement-advisor-tab.html
    └── js/aks-placement-advisor-tab.js
tests/
├── test_aks_filter.py
└── test_aks_placement_advisor.py
```

## Installation

```bash
# From the az-scout directory
uv pip install -e ../az-scout-plugin-aks-placement-advisor
```

The plugin is auto-discovered via the `az_scout.plugins` entry point.

## Development

```bash
uv pip install -e ".[dev]"

# Quality checks
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
uv run mypy src/
uv run pytest
```

## Versioning

Version is derived from git tags via `hatch-vcs` — never hardcoded.
Tags follow CalVer: `v2026.2.0`, `v2026.2.1`, etc.

## License

[MIT](LICENSE.txt)

## Disclaimer

> **This tool is not affiliated with Microsoft.** Scores are heuristic estimates based on publicly available SKU metadata. They do not guarantee actual AKS capacity, availability, or quota. Zone restrictions reflect subscription-level constraints, not real-time stock. Always verify with the Azure portal or CLI before provisioning.
