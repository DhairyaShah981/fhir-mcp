"""CDS Hooks bridge — production-grade clinical decision support over MCP."""

from .client import CdsHooksClient, get_client
from .mock_service import MOCK_HOOKS, mock_invoke

__all__ = ["MOCK_HOOKS", "CdsHooksClient", "get_client", "mock_invoke"]
