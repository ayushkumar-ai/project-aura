import json
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


def _sanitize_metadata(meta: dict[str, Any]) -> dict[str, Any]:
    """Recursively sanitize metadata dictionary to ensure no callables or untrusted permission overrides."""
    if not isinstance(meta, dict):
        raise TypeError("metadata must be a dictionary.")

    forbidden_keys = frozenset({
        "approved",
        "approval_status",
        "is_approved",
        "auto_approve",
        "permission",
        "authorized",
    })

    cleaned: dict[str, Any] = {}
    for k, v in meta.items():
        k_str = str(k)
        if k_str.lower() in forbidden_keys:
            continue
        if callable(v):
            continue
        if isinstance(v, dict):
            cleaned[k_str] = _sanitize_metadata(v)
        elif isinstance(v, (list, tuple)):
            cleaned[k_str] = [
                _sanitize_metadata(item) if isinstance(item, dict) else (str(item) if not callable(item) else "")
                for item in v
            ]
        elif isinstance(v, (str, int, float, bool)) or v is None:
            cleaned[k_str] = v
        else:
            cleaned[k_str] = repr(v)
    return cleaned


@dataclass(frozen=True)
class TaintedValue:
    """Represents a value tagged with provenance and untrusted status across AURA workflow execution."""

    raw_value: Any
    is_untrusted: bool = True
    source_type: str = "external_web"
    originating_step_id: str | None = None
    source_urls: tuple[str, ...] = field(default_factory=tuple)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if callable(self.raw_value):
            raise ValueError("TaintedValue raw_value cannot be callable.")

        if not isinstance(self.is_untrusted, bool):
            raise TypeError("is_untrusted must be a boolean.")

        if not isinstance(self.source_type, str) or not self.source_type.strip():
            raise ValueError("source_type must be a non-empty string.")
        object.__setattr__(self, "source_type", self.source_type.strip())

        if self.originating_step_id is not None:
            if not isinstance(self.originating_step_id, str) or not self.originating_step_id.strip():
                raise ValueError("originating_step_id must be a non-empty string or None.")
            object.__setattr__(self, "originating_step_id", self.originating_step_id.strip())

        # Normalize source_urls
        urls: list[str] = []
        if isinstance(self.source_urls, (list, tuple, set, frozenset)):
            for u in self.source_urls:
                if isinstance(u, str) and u.strip():
                    urls.append(u.strip())
        elif self.source_urls is not None:
            raise TypeError("source_urls must be a sequence of strings.")
        object.__setattr__(self, "source_urls", tuple(urls))

        cleaned_meta = _sanitize_metadata(self.metadata)
        object.__setattr__(self, "metadata", cleaned_meta)

    @property
    def value(self) -> Any:
        """Alias for raw_value."""
        return self.raw_value

    def unwrap(self) -> Any:
        """Return the underlying raw value."""
        return self.raw_value

    def to_prompt_text(self, wrap_untrusted: bool = True) -> str:
        """Render content safely for inclusion in an LLM prompt."""
        text = str(self.raw_value)
        if self.is_untrusted and wrap_untrusted:
            return f"<untrusted_source_content>\n{text}\n</untrusted_source_content>"
        return text

    def __str__(self) -> str:
        """Return string representation of the raw value."""
        return str(self.raw_value)

    def __repr__(self) -> str:
        val_repr = repr(self.raw_value)
        if len(val_repr) > 60:
            val_repr = val_repr[:57] + "..."
        return (
            f"TaintedValue(is_untrusted={self.is_untrusted}, source_type='{self.source_type}', "
            f"step={repr(self.originating_step_id)}, value={val_repr})"
        )

    def __contains__(self, item: Any) -> bool:
        if hasattr(self.raw_value, "__contains__"):
            return item in self.raw_value
        return False

    def __getitem__(self, key: Any) -> Any:
        if hasattr(self.raw_value, "__getitem__"):
            return self.raw_value[key]
        raise TypeError(f"'{type(self.raw_value).__name__}' object is not subscriptable")

    def __len__(self) -> int:
        if hasattr(self.raw_value, "__len__"):
            return len(self.raw_value)
        return 0

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, TaintedValue):
            return (
                self.raw_value == other.raw_value
                and self.is_untrusted == other.is_untrusted
                and self.source_type == other.source_type
                and self.originating_step_id == other.originating_step_id
                and self.source_urls == other.source_urls
            )
        return self.raw_value == other


def wrap_tainted(
    value: Any,
    is_untrusted: bool = True,
    source_type: str = "external_web",
    originating_step_id: str | None = None,
    source_urls: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> TaintedValue:
    """Wrap a value into an immutable TaintedValue envelope."""
    if isinstance(value, TaintedValue):
        merged_urls = list(value.source_urls)
        if source_urls:
            for u in source_urls:
                if u not in merged_urls:
                    merged_urls.append(u)
        meta = dict(value.metadata)
        if metadata:
            meta.update(metadata)
        return TaintedValue(
            raw_value=value.raw_value,
            is_untrusted=value.is_untrusted or is_untrusted,
            source_type=value.source_type or source_type,
            originating_step_id=value.originating_step_id or originating_step_id,
            source_urls=tuple(merged_urls),
            metadata=meta,
        )

    return TaintedValue(
        raw_value=value,
        is_untrusted=is_untrusted,
        source_type=source_type,
        originating_step_id=originating_step_id,
        source_urls=tuple(source_urls) if source_urls else (),
        metadata=dict(metadata) if metadata else {},
    )


def is_tainted(value: Any) -> bool:
    """Check whether a value or any nested element within it is marked untrusted."""
    if isinstance(value, TaintedValue):
        return value.is_untrusted
    if isinstance(value, dict):
        return any(is_tainted(v) for v in value.values())
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(is_tainted(v) for v in value)
    return False


def unwrap_tainted(value: Any) -> Any:
    """Recursively unwrap any TaintedValue instances to retrieve raw underlying data."""
    if isinstance(value, TaintedValue):
        return unwrap_tainted(value.raw_value)
    if isinstance(value, dict):
        return {k: unwrap_tainted(v) for k, v in value.items()}
    if isinstance(value, list):
        return [unwrap_tainted(v) for v in value]
    if isinstance(value, tuple):
        return tuple(unwrap_tainted(v) for v in value)
    return value


def extract_provenance(value: Any) -> dict[str, Any]:
    """Extract provenance metadata from a value if it contains a TaintedValue."""
    if isinstance(value, TaintedValue):
        return {
            "is_untrusted": value.is_untrusted,
            "source_type": value.source_type,
            "originating_step_id": value.originating_step_id,
            "source_urls": list(value.source_urls),
            "metadata": dict(value.metadata),
        }
    if isinstance(value, dict):
        for k, v in value.items():
            res = extract_provenance(v)
            if res:
                return res
    if isinstance(value, (list, tuple)):
        for item in value:
            res = extract_provenance(item)
            if res:
                return res
    return {}


def render_for_prompt(value: Any, wrap_untrusted: bool = True) -> str:
    """Deterministically render values into safe prompt text, wrapping untrusted text in isolation tags."""
    if isinstance(value, TaintedValue):
        return value.to_prompt_text(wrap_untrusted=wrap_untrusted)

    if isinstance(value, dict):
        if any(is_tainted(v) for v in value.values()):
            lines = []
            for k, v in sorted(value.items()):
                rendered_v = render_for_prompt(v, wrap_untrusted=wrap_untrusted)
                lines.append(f"{k}: {rendered_v}")
            return "\n".join(lines)
        return json.dumps(value, indent=2)

    if isinstance(value, (list, tuple)):
        if any(is_tainted(v) for v in value):
            return "\n".join(f"- {render_for_prompt(v, wrap_untrusted=wrap_untrusted)}" for v in value)
        return json.dumps(value, indent=2)

    return str(value)
