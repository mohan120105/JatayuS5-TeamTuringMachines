"""Standalone LLM tool registry for function calling.

This module is intentionally framework-agnostic so it can be reused inside a
larger agentic system later.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Optional, get_args, get_origin

from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError, create_model


load_dotenv()


@dataclass(frozen=True)
class ToolSpec:
    """Metadata for a registered tool."""

    name: str
    description: str
    function: Callable[..., Any]
    args_model: type[BaseModel]


def _normalize_docstring(docstring: Optional[str]) -> str:
    if not docstring:
        return ""
    return inspect.cleandoc(docstring).strip()


def _parse_docstring(docstring: str) -> tuple[str, dict[str, str]]:
    """Extract a summary and parameter descriptions from a docstring."""

    if not docstring:
        return "", {}

    lines = docstring.splitlines()
    summary = lines[0].strip()
    param_descriptions: dict[str, str] = {}

    in_args_section = False
    current_name: Optional[str] = None
    current_description: list[str] = []

    def commit() -> None:
        nonlocal current_name, current_description
        if current_name:
            param_descriptions[current_name] = " ".join(current_description).strip()
        current_name = None
        current_description = []

    param_line_pattern = re.compile(r"^(?P<name>[A-Za-z_][\w]*)\s*(?:\([^)]*\))?\s*:\s*(?P<desc>.*)$")

    for raw_line in lines[1:]:
        stripped = raw_line.strip()

        if stripped.lower() in {"args:", "arguments:", "parameters:"}:
            in_args_section = True
            commit()
            continue

        if not in_args_section:
            continue

        if not stripped:
            if current_name:
                current_description.append("")
            continue

        match = param_line_pattern.match(stripped)
        if match:
            commit()
            current_name = match.group("name").strip()
            current_description = [match.group("desc").strip()]
            continue

        if raw_line.startswith((" ", "\t")) and current_name:
            current_description.append(stripped)

    commit()
    return summary, param_descriptions


def _annotation_for_model(annotation: Any) -> Any:
    """Return an annotation suitable for a Pydantic model field."""

    if annotation is inspect._empty:
        return Any

    origin = get_origin(annotation)
    if origin is None:
        return annotation

    if origin is list:
        args = get_args(annotation)
        item_annotation = _annotation_for_model(args[0]) if args else Any
        return list[item_annotation] if hasattr(list, "__class_getitem__") else list

    if origin is dict:
        return dict[str, Any] if hasattr(dict, "__class_getitem__") else dict

    return annotation


def _build_args_model(function: Callable[..., Any], param_descriptions: dict[str, str]) -> type[BaseModel]:
    """Create a Pydantic model that mirrors a function signature."""

    signature = inspect.signature(function)
    fields: dict[str, tuple[Any, Any]] = {}

    for parameter_name, parameter in signature.parameters.items():
        if parameter.kind in {inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD}:
            continue

        annotation = _annotation_for_model(parameter.annotation)
        default_value = ... if parameter.default is inspect._empty else parameter.default
        description = param_descriptions.get(parameter_name, "")
        fields[parameter_name] = (annotation, Field(default=default_value, description=description))

    model_name = f"{function.__name__.title().replace('_', '')}Args"
    return create_model(model_name, __base__=BaseModel, **fields)  # type: ignore[call-overload]


def _escape_atlassian_query(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


_JIRA_STOPWORDS = {
    "show",
    "me",
    "find",
    "give",
    "all",
    "the",
    "a",
    "an",
    "of",
    "to",
    "for",
    "related",
    "relateds",
    "relatedto",
    "about",
    "please",
    "tickets",
    "ticket",
    "issue",
    "issues",
    "open",
    "opened",
    "assigned",
    "assign",
    "assignment",
    "are",
    "is",
    "with",
    "team",
    "teams",
    "assigned",
    "assign",
    "assignedto",
}


def _build_jira_jql(query: str) -> str:
    """Convert a natural-language ticket query into a compact JQL expression."""

    normalized = (query or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9\s_-]+", " ", normalized)
    tokens = [token for token in cleaned.split() if token and token not in _JIRA_STOPWORDS]

    jql_clauses: list[str] = []

    if any(token in {"open", "opened", "active", "pending"} for token in normalized.split()):
        jql_clauses.append("statusCategory != Done")

    if any(token in {"closed", "done", "resolved", "completed"} for token in normalized.split()):
        jql_clauses.append("statusCategory = Done")

    if "payments team" in normalized or "payment team" in normalized or "payments" in tokens:
        jql_clauses.append('labels = "payments" OR text ~ "payments"')

    if "kyc" in tokens or "onboarding" in tokens:
        jql_clauses.append('text ~ "KYC" OR text ~ "onboarding" OR labels = "kyc"')

    if any(token in {"fraud", "aml", "compliance", "dispute", "incident", "bug"} for token in tokens):
        focused_tokens = [token for token in tokens if token not in {"fraud", "aml", "compliance", "dispute", "incident", "bug"}]
        if focused_tokens:
            token_clause = " AND ".join(f'text ~ "{_escape_atlassian_query(token)}"' for token in focused_tokens[:4])
            jql_clauses.append(token_clause)
        jql_clauses.append("text ~ \"fraud\" OR text ~ \"aml\" OR text ~ \"compliance\" OR text ~ \"dispute\" OR text ~ \"incident\" OR text ~ \"bug\"")

    for token in tokens[:5]:
        jql_clauses.append(f'text ~ "{_escape_atlassian_query(token)}"')

    if not jql_clauses:
        jql_clauses.append(f'text ~ "{_escape_atlassian_query(query)}"')

    # De-duplicate while preserving order.
    deduped_clauses: list[str] = []
    seen = set()
    for clause in jql_clauses:
        key = clause.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        deduped_clauses.append(clause)

    return " AND ".join(f"({clause})" for clause in deduped_clauses)


def _build_jira_fallback_jql(query: str) -> str:
    """Build a broader Jira query that favors recall over precision."""

    normalized = (query or "").strip().lower()
    cleaned = re.sub(r"[^a-z0-9\s_-]+", " ", normalized)
    tokens = [token for token in cleaned.split() if token and token not in _JIRA_STOPWORDS]

    clauses: list[str] = []

    if any(token in {"open", "opened", "active", "pending"} for token in tokens):
        clauses.append("statusCategory != Done")

    if any(token in {"closed", "done", "resolved", "completed"} for token in tokens):
        clauses.append("statusCategory = Done")

    if tokens:
        text_clauses = [f'text ~ "{_escape_atlassian_query(token)}"' for token in tokens[:6]]
        clauses.append(" OR ".join(text_clauses))

    if "payments" in tokens or "payment" in tokens:
        clauses.append('labels = "payments" OR text ~ "payments" OR text ~ "payment"')

    if "kyc" in tokens or "onboarding" in tokens:
        clauses.append('text ~ "KYC" OR text ~ "onboarding" OR labels = "kyc"')

    if not clauses:
        clauses.append(f'text ~ "{_escape_atlassian_query(query)}"')

    deduped: list[str] = []
    seen = set()
    for clause in clauses:
        key = clause.lower().strip()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(clause)

    return " AND ".join(f"({clause})" for clause in deduped)


class ToolRegistry:
    """Registry for callable tools and their generated JSON schemas."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(
        self,
        function: Optional[Callable[..., Any]] = None,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Callable[..., Any]:
        """Register a function directly or use this method as a decorator."""

        def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
            docstring = _normalize_docstring(target.__doc__)
            summary, param_descriptions = _parse_docstring(docstring)
            tool_name = name or target.__name__
            tool_description = description or summary or f"Tool for {tool_name}."
            args_model = _build_args_model(target, param_descriptions)
            self._tools[tool_name] = ToolSpec(
                name=tool_name,
                description=tool_description,
                function=target,
                args_model=args_model,
            )
            return target

        if function is not None:
            return decorator(function)
        return decorator

    def register_tool(
        self,
        function: Optional[Callable[..., Any]] = None,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> Callable[..., Any]:
        """Alias for register so decorator usage reads naturally."""

        return self.register(function, name=name, description=description)

    def get_tool(self, name: str) -> ToolSpec:
        if name not in self._tools:
            raise KeyError(f"Unknown tool: {name}")
        return self._tools[name]

    def get_tool_schemas(self, format: str = "openai") -> list[dict[str, Any]]:
        """Return tool schemas for standard LLM tool-calling APIs.

        Args:
            format: Output format. Supported values are ``openai`` and ``gemini``.
        """

        schemas: list[dict[str, Any]] = []
        output_format = format.lower().strip()

        for tool in self._tools.values():
            parameters_schema = tool.args_model.model_json_schema()
            if output_format == "gemini":
                schemas.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": parameters_schema,
                    }
                )
            else:
                schemas.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": parameters_schema,
                        },
                    }
                )

        return schemas

    def execute_tool(self, name: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Validate arguments, execute a registered tool, and return a safe result."""

        try:
            tool = self.get_tool(name)
        except KeyError as exc:
            return {"ok": False, "tool": name, "error": str(exc)}

        try:
            validated_args = tool.args_model.model_validate(kwargs).model_dump()
        except ValidationError as exc:
            return {
                "ok": False,
                "tool": name,
                "error": "Argument validation failed.",
                "details": exc.errors(),
            }

        try:
            if inspect.iscoroutinefunction(tool.function):
                try:
                    asyncio.get_running_loop()
                except RuntimeError:
                    result = asyncio.run(tool.function(**validated_args))
                else:
                    return {
                        "ok": False,
                        "tool": name,
                        "error": "Async tools are not supported from a running event loop in the sync executor.",
                    }
            else:
                result = tool.function(**validated_args)

            return {"ok": True, "tool": name, "result": result}
        except Exception as exc:
            return {"ok": False, "tool": name, "error": f"Tool execution failed: {exc}"}


default_registry = ToolRegistry()


def register_tool(
    function: Optional[Callable[..., Any]] = None,
    *,
    name: Optional[str] = None,
    description: Optional[str] = None,
    registry: ToolRegistry = default_registry,
) -> Callable[..., Any]:
    """Decorator for registering a function as a tool."""

    return registry.register(function, name=name, description=description)


@register_tool
def calculator(operation: str, a: float, b: float) -> float:
    """Perform a basic arithmetic operation.

    Args:
        operation: One of add, subtract, multiply, divide.
        a: The first numeric operand.
        b: The second numeric operand.
    """

    operations = {
        "add": lambda x, y: x + y,
        "subtract": lambda x, y: x - y,
        "multiply": lambda x, y: x * y,
        "divide": lambda x, y: x / y,
    }

    if operation not in operations:
        raise ValueError(f"Unsupported operation: {operation}")

    if operation == "divide" and b == 0:
        raise ValueError("Division by zero is not allowed.")

    return operations[operation](a, b)


@register_tool
def mock_api_fetcher(resource_id: str, include_metadata: bool = False) -> dict[str, Any]:
    """Fetch a mock resource payload.

    Args:
        resource_id: Identifier for the resource to fetch.
        include_metadata: Whether to include extra metadata in the response.
    """

    payload: dict[str, Any] = {
        "resource_id": resource_id,
        "status": "ok",
        "data": {
            "name": f"Resource {resource_id}",
            "value": len(resource_id) * 10,
        },
    }

    if include_metadata:
        payload["metadata"] = {
            "source": "mock-api",
            "etag": f"mock-{resource_id.lower()}",
        }

    return payload


@default_registry.register_tool
def search_confluence_policies(query: str) -> str:
    """Searches the Confluence knowledge base for banking SOPs, compliance policies, or onboarding guides.

    Args:
        query: The search term to look for.
    """

    confluence_url = os.getenv("CONFLUENCE_URL")
    confluence_user = os.getenv("ATLASSIAN_USER_EMAIL")
    confluence_token = os.getenv("ATLASSIAN_API_TOKEN")

    if not confluence_url or not confluence_user or not confluence_token:
        return (
            "Confluence connection error: missing one or more required environment variables "
            "(CONFLUENCE_URL, ATLASSIAN_USER_EMAIL, ATLASSIAN_API_TOKEN)."
        )

    try:
        from atlassian import Confluence
    except Exception as exc:
        return f"Confluence connection error: unable to import atlassian-python-api ({exc})."

    try:
        confluence = Confluence(
            url=confluence_url,
            username=confluence_user,
            password=confluence_token,
            cloud=True,
        )
        cql_query = f'type = page AND text ~ "{_escape_atlassian_query(query)}"'
        response = confluence.cql(cql_query, limit=3)
        results = response.get("results", []) if isinstance(response, dict) else []

        if not results:
            return f'No Confluence pages found for query: "{query}".'

        lines = [f'Confluence results for "{query}":']
        for item in results[:3]:
            title = item.get("title", "Untitled page")
            links = item.get("_links", {}) if isinstance(item, dict) else {}
            base_url = links.get("base", confluence_url.rstrip("/"))
            webui = links.get("webui", "")
            page_url = f"{base_url}{webui}" if webui else base_url
            lines.append(f"- {title}: {page_url}")

        return "\n".join(lines)
    except Exception as exc:
        return f"Confluence connection error: {exc}"


@default_registry.register_tool
def search_jira_tickets(query: str) -> str:
    """Searches Jira for compliance incident records or exception handling tickets.

    Args:
        query: The search term (e.g., "exception handling" or a specific ticket ID).
    """

    jira_server = os.getenv("JIRA_SERVER")
    jira_user = os.getenv("ATLASSIAN_USER_EMAIL")
    jira_token = os.getenv("ATLASSIAN_API_TOKEN")

    if not jira_server or not jira_user or not jira_token:
        return (
            "Jira connection error: missing one or more required environment variables "
            "(JIRA_SERVER, ATLASSIAN_USER_EMAIL, ATLASSIAN_API_TOKEN)."
        )

    try:
        from jira import JIRA
    except Exception as exc:
        return f"Jira connection error: unable to import jira library ({exc})."

    try:
        jira = JIRA(server=jira_server, basic_auth=(jira_user, jira_token))
        strict_jql = _build_jira_jql(query) + " ORDER BY updated DESC"
        issues = jira.search_issues(strict_jql, maxResults=5)

        used_jql = strict_jql

        if not issues:
            fallback_jql = _build_jira_fallback_jql(query) + " ORDER BY updated DESC"
            fallback_issues = jira.search_issues(fallback_jql, maxResults=5)
            if fallback_issues:
                issues = fallback_issues
                used_jql = fallback_jql

        if not issues:
            return f'No Jira tickets found for query: "{query}". Tried JQL: {strict_jql}'

        lines = [f'Jira results for "{query}":']
        if used_jql != strict_jql:
            lines.append(f"- Note: used relaxed search because exact match was empty.")
        for issue in issues[:3]:
            summary = getattr(issue.fields, "summary", "No summary available")
            status = getattr(getattr(issue.fields, "status", None), "name", "Unknown")
            lines.append(f"- {issue.key} | {summary} | {status}")

        return "\n".join(lines)
    except Exception as exc:
        return f"Jira connection error: {exc}"


if __name__ == "__main__":
    simulated_llm_tool_call = {
        "name": "calculator",
        "arguments": {
            "operation": "multiply",
            "a": 12,
            "b": 8,
        },
    }

    print("Registered tool schemas:")
    print(json.dumps(default_registry.get_tool_schemas(), indent=2))
    print()

    execution_result = default_registry.execute_tool(
        simulated_llm_tool_call["name"],
        simulated_llm_tool_call["arguments"],
    )
    print("Simulated LLM tool call result:")
    print(json.dumps(execution_result, indent=2, default=str))

    fetched_result = default_registry.execute_tool(
        "mock_api_fetcher",
        {"resource_id": "acct_1042", "include_metadata": True},
    )
    print()
    print("Mock API fetch result:")
    print(json.dumps(fetched_result, indent=2, default=str))