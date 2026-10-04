"""FastMCP adapter for the UI automation framework."""


from .protocol import (
    PROTOCOL_VERSION,
    ErrorCode,
    failure_response,
    success_response,
)

__all__ = [
    'ErrorCode',
    'PROTOCOL_VERSION',
    'failure_response',
    'success_response',
]
