"""AgentRunner protocol — the narrow interface every agent backend implements.

The runner takes a natural-language prompt, runs the underlying agent until
it either finishes, hits `max_turns`, or times out, and returns the final
text response. All tool orchestration (browser, file I/O, etc.) happens
inside the backend; callers only see the text.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


class AgentRunError(RuntimeError):
    """Raised when the underlying agent backend fails in a non-timeout way."""


@runtime_checkable
class AgentRunner(Protocol):
    """Narrow interface for executing an agent turn loop.

    Implementations must be safe to call concurrently — each call runs
    an independent agent session.
    """

    async def run(
        self,
        prompt: str,
        *,
        max_turns: int = 30,
        timeout: float = 600.0,
    ) -> str:
        """Execute the agent and return the final text response.

        Args:
            prompt: User-facing prompt for the agent.
            max_turns: Hard cap on assistant turns.
            timeout: Wall-clock timeout in seconds. Callers should expect
                `asyncio.TimeoutError` when exceeded.

        Returns:
            Final text from the agent (last assistant text block or
            result message, depending on backend).
        """
        ...
