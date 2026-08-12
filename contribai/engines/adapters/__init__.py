"""Optional external coding-engine drivers.

Imports in this package are dependency-light.  Runtime-specific SDKs and
executables are discovered only when their driver is explicitly selected.
"""

from contribai.engines.adapters.base import (
    AdapterResult,
    AdapterUnavailableError,
    ExternalEngineDriver,
)
from contribai.engines.adapters.codex_app_server import CodexAppServerDriver
from contribai.engines.adapters.codex_exec import CodexExecDriver
from contribai.engines.adapters.mini_swe import MiniSWEInProcessDriver
from contribai.engines.adapters.opencode_server import OpenCodeServerDriver
from contribai.engines.adapters.openhands_sdk import OpenHandsSDKDriver

__all__ = [
    "AdapterResult",
    "AdapterUnavailableError",
    "CodexAppServerDriver",
    "CodexExecDriver",
    "ExternalEngineDriver",
    "MiniSWEInProcessDriver",
    "OpenCodeServerDriver",
    "OpenHandsSDKDriver",
]
