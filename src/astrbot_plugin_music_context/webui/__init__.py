"""Management observations and WebUI adapters, independent of Providers."""

from .service import DashboardService
from .state import ManagementState

__all__ = ["DashboardService", "ManagementState"]
