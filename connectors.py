"""Free-first connector abstraction for external knowledge sources.

This module is intentionally standalone so it can be added without changing
the current Render/Vercel deployment path. It provides a small registry and
planning layer for connecting external tools such as Gmail, Google Drive,
shared drives, SharePoint, Confluence, and ticketing systems.

Design goals:
- Keep the current policy RAG flow unchanged.
- Support read-only connectors first.
- Avoid paid middleware where possible.
- Allow optional MCP-style adapters later, but do not require them.

Recommended free path:
- Use direct provider APIs with OAuth / service-account auth.
- Sync content into the existing Neo4j / retrieval pipeline.
- Keep write actions gated behind explicit approval.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Protocol


class SourceType(str, Enum):
    """Supported source families for enterprise connectors."""

    GMAIL = "gmail"
    GOOGLE_DRIVE = "google_drive"
    GOOGLE_SHARED_DRIVE = "google_shared_drive"
    SHAREPOINT = "sharepoint"
    CONFLUENCE = "confluence"
    TICKETING = "ticketing"
    CUSTOM = "custom"


class ToolIntent(str, Enum):
    """High-level request types that the router can classify."""

    RETRIEVAL = "retrieval"
    DOCUMENT_SEARCH = "document_search"
    MAIL_SEARCH = "mail_search"
    TICKET_CREATE = "ticket_create"
    TICKET_LOOKUP = "ticket_lookup"
    ESCALATION = "escalation"
    UNKNOWN = "unknown"


class ConnectorMode(str, Enum):
    """Operational mode for a connector."""

    READ_ONLY = "read_only"
    WRITE_ALLOWED = "write_allowed"


@dataclass(slots=True)
class ConnectorConfig:
    """Configuration for a source connector.

    Attributes:
        name: Human-friendly connector name.
        source_type: Family of data source being connected.
        enabled: Whether the connector should be considered active.
        mode: Read-only or write-enabled.
        base_url: API endpoint or tenant URL, if applicable.
        scopes: OAuth scopes or permission labels required for access.
        metadata: Arbitrary connector-specific settings.
    """

    name: str
    source_type: SourceType
    enabled: bool = True
    mode: ConnectorMode = ConnectorMode.READ_ONLY
    base_url: str | None = None
    scopes: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ToolRequest:
    """Normalized request passed into the router."""

    query: str
    user_id: str | None = None
    source_hint: SourceType | None = None
    intent_hint: ToolIntent | None = None


@dataclass(slots=True)
class ToolPlan:
    """Result of routing a request to a connector or retrieval path."""

    intent: ToolIntent
    primary_source: SourceType | None = None
    connector_name: str | None = None
    requires_approval: bool = False
    notes: str = ""


@dataclass(slots=True)
class ConnectorResult:
    """Minimal connector result payload."""

    items: List[Dict[str, Any]] = field(default_factory=list)
    source: SourceType | None = None
    connector_name: str | None = None
    notes: str = ""


class Connector(Protocol):
    """Protocol for a source connector implementation."""

    config: ConnectorConfig

    def search(self, query: str, *, limit: int = 10) -> ConnectorResult:
        """Search the source and return normalized results."""

    def fetch(self, item_id: str) -> Dict[str, Any]:
        """Fetch a single item by ID."""


class BaseConnector:
    """Lightweight base class for direct-API connectors.

    The base class is intentionally small so provider-specific connectors can be
    added later without changing the orchestration layer.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config

    def search(self, query: str, *, limit: int = 10) -> ConnectorResult:
        raise NotImplementedError("search() must be implemented by a concrete connector")

    def fetch(self, item_id: str) -> Dict[str, Any]:
        raise NotImplementedError("fetch() must be implemented by a concrete connector")


class ConnectorRegistry:
    """In-memory registry for connector instances."""

    def __init__(self) -> None:
        self._connectors: Dict[str, Connector] = {}

    def register(self, connector: Connector) -> None:
        self._connectors[connector.config.name] = connector

    def get(self, name: str) -> Connector | None:
        return self._connectors.get(name)

    def enabled(self) -> List[Connector]:
        return [connector for connector in self._connectors.values() if connector.config.enabled]

    def names(self) -> List[str]:
        return list(self._connectors.keys())


class ToolRouter:
    """Very small request router for deciding which system should handle a query.

    This is a heuristic layer, not a policy engine. It can sit in front of the
    existing RAG flow and route obvious connector requests away from retrieval.
    """

    def route(self, request: ToolRequest) -> ToolPlan:
        query = (request.query or "").strip().lower()

        if request.intent_hint is not None:
            return self._plan_from_hint(request.intent_hint, request.source_hint)

        if any(keyword in query for keyword in ("mail", "email", "gmail", "inbox")):
            return ToolPlan(
                intent=ToolIntent.MAIL_SEARCH,
                primary_source=request.source_hint or SourceType.GMAIL,
                notes="Route to mail connector or fallback retrieval if no mail source is configured.",
            )

        if any(keyword in query for keyword in ("sharepoint", "drive", "shared drive", "document", "file")):
            return ToolPlan(
                intent=ToolIntent.DOCUMENT_SEARCH,
                primary_source=request.source_hint or SourceType.GOOGLE_DRIVE,
                notes="Route to document connector or fall back to the policy graph.",
            )

        if any(keyword in query for keyword in ("ticket", "raise", "escalate", "incident", "case")):
            return ToolPlan(
                intent=ToolIntent.ESCALATION,
                primary_source=request.source_hint or SourceType.TICKETING,
                requires_approval=True,
                notes="Write action should be gated behind explicit user confirmation.",
            )

        return ToolPlan(
            intent=ToolIntent.RETRIEVAL,
            primary_source=request.source_hint,
            notes="Use the existing policy RAG stack.",
        )

    def _plan_from_hint(
        self,
        intent: ToolIntent,
        source_hint: SourceType | None,
    ) -> ToolPlan:
        requires_approval = intent in {ToolIntent.TICKET_CREATE, ToolIntent.ESCALATION}
        return ToolPlan(
            intent=intent,
            primary_source=source_hint,
            requires_approval=requires_approval,
        )


def build_free_connector_catalog() -> List[ConnectorConfig]:
    """Return a free-first connector catalog.

    This only describes what can be connected. It does not perform any network
    calls, so it is safe to use during deployment and local development.
    """

    return [
        ConnectorConfig(
            name="gmail_read_only",
            source_type=SourceType.GMAIL,
            mode=ConnectorMode.READ_ONLY,
            scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            metadata={"provider": "google", "priority": 10},
        ),
        ConnectorConfig(
            name="google_drive_read_only",
            source_type=SourceType.GOOGLE_DRIVE,
            mode=ConnectorMode.READ_ONLY,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
            metadata={"provider": "google", "priority": 9},
        ),
        ConnectorConfig(
            name="google_shared_drive_read_only",
            source_type=SourceType.GOOGLE_SHARED_DRIVE,
            mode=ConnectorMode.READ_ONLY,
            scopes=["https://www.googleapis.com/auth/drive.readonly"],
            metadata={"provider": "google", "shared_drive": True},
        ),
        ConnectorConfig(
            name="sharepoint_read_only",
            source_type=SourceType.SHAREPOINT,
            mode=ConnectorMode.READ_ONLY,
            scopes=["Files.Read.All", "Sites.Read.All"],
            metadata={"provider": "microsoft"},
        ),
        ConnectorConfig(
            name="confluence_read_only",
            source_type=SourceType.CONFLUENCE,
            mode=ConnectorMode.READ_ONLY,
            metadata={"provider": "atlassian"},
        ),
        ConnectorConfig(
            name="ticketing_write_guarded",
            source_type=SourceType.TICKETING,
            mode=ConnectorMode.WRITE_ALLOWED,
            metadata={"requires_approval": True, "provider": "generic"},
        ),
    ]


def connector_summary(configs: Iterable[ConnectorConfig]) -> List[str]:
    """Return a human-readable summary of configured connectors."""

    summary: List[str] = []
    for config in configs:
        scope_text = ", ".join(config.scopes) if config.scopes else "no explicit scopes"
        summary.append(
            f"{config.name} | {config.source_type.value} | {config.mode.value} | {scope_text}"
        )
    return summary


def _demo_self_check() -> None:
    """Print a small runtime check for the scaffold.

    This verifies that the connector catalog loads and that the router can
    classify a few representative requests without touching external systems.
    """

    catalog = build_free_connector_catalog()
    router = ToolRouter()

    print("Connector catalog:")
    for line in connector_summary(catalog):
        print(f"- {line}")

    print("\nRouting demo:")
    demo_requests = [
        ToolRequest(query="Find the latest leave policy in Google Drive"),
        ToolRequest(query="Check my Gmail for the approval email"),
        ToolRequest(query="Create a ticket for policy clarification"),
        ToolRequest(query="What is the policy for cash deposits?"),
    ]
    for request in demo_requests:
        plan = router.route(request)
        source = plan.primary_source.value if plan.primary_source else "none"
        print(
            f"- {request.query} -> intent={plan.intent.value}, source={source}, "
            f"approval={plan.requires_approval}, notes={plan.notes}"
        )


if __name__ == "__main__":
    _demo_self_check()
