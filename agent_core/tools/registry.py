from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class ToolRegistration:
    name: str
    description: str
    enabled: bool
    execution: str
    approval_required: bool


class ToolRegistry:
    """Metadata-only registry. REVIEW-028 deliberately exposes no executable tools."""

    def __init__(self) -> None:
        self._tools = {
            "stage2_execute": ToolRegistration(
                name="stage2_execute",
                description="Prior inert acceptance tool; not reusable by the product UI.",
                enabled=False,
                execution="restricted",
                approval_required=True,
            )
        }

    def list(self) -> list[dict[str, object]]:
        return [asdict(item) for item in self._tools.values()]

    def invoke(self, name: str, arguments: dict[str, object]) -> None:
        del name, arguments
        raise PermissionError(
            "Tool execution is restricted in REVIEW-028; no prior grant or permit is reusable."
        )


def inert_mcp_descriptors(descriptors=None):
    """Bounded display metadata only; never adds model functions or execution paths."""
    if descriptors is None:
        return []
    if not isinstance(descriptors, list) or len(descriptors) > 128:
        raise ValueError('MCP descriptors must be a list of at most 128 items')
    result = []
    for descriptor in descriptors:
        if not isinstance(descriptor, dict):
            raise ValueError('invalid MCP descriptor')
        name = descriptor.get('name')
        description = descriptor.get('description', '')
        if not isinstance(name, str) or not 0 < len(name) <= 128 or not isinstance(description, str):
            raise ValueError('invalid MCP descriptor metadata')
        clean = lambda value: ''.join(c for c in value if c >= ' ' and c != '\x7f')
        if not clean(name).strip():
            raise ValueError('invalid MCP descriptor name')
        result.append({'name':clean(name),'description':clean(description[:2048]),'source':'mcp','enabled':False,'executable':False,'approval_required':True,'execution':'disabled'})
    return result
