"""AKS Placement Advisor plugin for az-scout.

Evaluates and recommends VM SKUs for AKS node pools, with heuristic
scoring for zone support, VMSS suitability, and quota availability.
"""

from collections.abc import Callable
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version
from pathlib import Path
from typing import Any

from az_scout.plugin_api import ChatMode, NavbarAction, TabDefinition, get_plugin_logger
from fastapi import APIRouter

logger = get_plugin_logger("aks-placement-advisor")

_STATIC_DIR = Path(__file__).parent / "static"

try:
    __version__ = _pkg_version("az-scout-plugin-aks-placement-advisor")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"


class AksPlacementAdvisorPlugin:
    """AKS Placement Advisor az-scout plugin."""

    name = "aks-placement-advisor"
    version = __version__

    def get_router(self) -> APIRouter | None:
        """Return API routes mounted at /plugins/aks-placement-advisor/."""
        from az_scout_aks_placement_advisor.routes import router

        return router

    def get_mcp_tools(self) -> list[Callable[..., Any]] | None:
        """Return MCP tool functions for the az-scout MCP server."""
        from az_scout_aks_placement_advisor.tools import (
            compare_aks_regions,
            recommend_aks_skus,
        )

        return [recommend_aks_skus, compare_aks_regions]

    def get_static_dir(self) -> Path | None:
        """Return path to static assets directory."""
        return _STATIC_DIR

    def get_tabs(self) -> list[TabDefinition] | None:
        """Return UI tab definitions."""
        return [
            TabDefinition(
                id="aks-placement-advisor",
                label="AKS Placement",
                icon="bi bi-diagram-3",
                js_entry="js/aks-placement-advisor-tab.js",
                css_entry="css/aks-placement-advisor.css",
            )
        ]

    def get_chat_modes(self) -> list[ChatMode] | None:
        """Return chat mode definitions."""
        from az_scout_aks_placement_advisor.chat_mode import AKS_CHAT_MODE

        return [AKS_CHAT_MODE]

    def get_navbar_actions(self) -> list[NavbarAction] | None:
        """Return navbar action definitions, or None to skip."""
        return None

    def get_system_prompt_addendum(self) -> str | None:
        """Return extra guidance for the default discussion chat mode."""
        return (
            "The AKS Placement Advisor plugin can recommend VM SKUs for "
            "AKS node pools. Use the recommend_aks_skus or compare_aks_regions "
            "tools when the user asks about VM sizing for AKS or Kubernetes "
            "node pools."
        )


plugin = AksPlacementAdvisorPlugin()
