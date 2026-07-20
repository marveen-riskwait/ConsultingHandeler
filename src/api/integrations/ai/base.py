"""Abstract LLM provider contract."""
from dataclasses import dataclass, field


@dataclass
class LLMResult:
    """A single assistant completion + bookkeeping for the audit trail."""
    text: str
    model: str = ""
    usage: dict = field(default_factory=dict)


class LLMProvider:
    """Adapters implement `complete()`.

    `system` is the persona/guardrails string. `messages` is a list of
    {"role": "user"|"assistant", "content": str} in chronological order.
    """

    name = "base"
    available = False

    def complete(self, system, messages):  # pragma: no cover - interface
        raise NotImplementedError
