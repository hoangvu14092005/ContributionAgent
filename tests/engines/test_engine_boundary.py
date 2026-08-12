"""Boundary tests for external engine drivers."""

from __future__ import annotations

import inspect
from typing import get_type_hints

from contribai.engines.models import EngineOutcome, EngineRequest, ExecutionLease
from contribai.engines.protocol import EngineDriver


def test_engine_driver_protocol_has_only_run_boundary() -> None:
    methods = {
        name
        for name, member in inspect.getmembers(EngineDriver)
        if not name.startswith("_") and callable(member)
    }

    assert methods == {"run"}
    signature = inspect.signature(EngineDriver.run)
    hints = get_type_hints(EngineDriver.run)
    assert list(signature.parameters) == ["self", "request", "execution"]
    assert hints["return"] is EngineOutcome
    assert hints["execution"] is ExecutionLease
    assert hints["request"] is EngineRequest
