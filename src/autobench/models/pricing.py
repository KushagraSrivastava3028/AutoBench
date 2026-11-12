"""Token -> USD, versioned.

The table is versioned and the version is recorded on every run, because
provider prices change and a cost figure that silently reprices history is
worse than no cost figure. G6 asks for reported USD within 2% of the provider's
invoice; the gap is almost always cache accounting, so cached input tokens are
priced as their own line rather than folded into input.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bump on every edit. Stored on the run so old numbers stay explainable.
PRICING_VERSION = "2026-09-01"


@dataclass(frozen=True, slots=True)
class Price:
    """USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float
    #: Cache *reads*. Usually a large discount on input.
    cached_input_per_mtok: float | None = None
    #: Cache *writes*. Usually a premium on input; ignored when a provider
    #: does not bill for them separately.
    cache_write_per_mtok: float | None = None
    context_window: int = 200_000

    def cost(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        cache_write_tokens: int = 0,
    ) -> float:
        """USD for one call.

        ``cached_tokens`` are *not* also counted in ``input_tokens``: providers
        report them as a separate bucket, and double-counting them is the most
        common way a self-reported cost drifts from the invoice.
        """
        cached_rate = (
            self.cached_input_per_mtok
            if self.cached_input_per_mtok is not None
            else self.input_per_mtok
        )
        write_rate = (
            self.cache_write_per_mtok
            if self.cache_write_per_mtok is not None
            else self.input_per_mtok
        )
        return (
            input_tokens * self.input_per_mtok
            + output_tokens * self.output_per_mtok
            + cached_tokens * cached_rate
            + cache_write_tokens * write_rate
        ) / 1_000_000


#: Keyed by ``provider/model_id``. Unknown models cost 0.0 and say so loudly.
PRICES: dict[str, Price] = {
    # --- anthropic ---------------------------------------------------------
    "anthropic/claude-opus-5": Price(5.00, 25.00, 0.50, 6.25, 200_000),
    "anthropic/claude-sonnet-5": Price(3.00, 15.00, 0.30, 3.75, 200_000),
    "anthropic/claude-fable-5-1": Price(3.00, 15.00, 0.30, 3.75, 200_000),
    "anthropic/claude-haiku-4-5-20251001": Price(1.00, 5.00, 0.10, 1.25, 200_000),
    # --- openai ------------------------------------------------------------
    "openai/gpt-4o": Price(2.50, 10.00, 1.25, None, 128_000),
    "openai/gpt-4o-mini": Price(0.15, 0.60, 0.075, None, 128_000),
    "openai/o1-preview": Price(15.00, 60.00, 7.50, None, 128_000),
    # --- google ------------------------------------------------------------
    "google/gemini-2.0-flash": Price(0.10, 0.40, 0.025, None, 1_048_576),
    "google/gemini-2.5-pro": Price(1.25, 10.00, 0.31, None, 1_048_576),
    # --- self-hosted -------------------------------------------------------
    # Not free — electricity and GPU-hours are real — but not billed per token,
    # so the honest entry is zero with a note rather than an invented number.
    "openai_compat/*": Price(0.0, 0.0, 0.0, None, 128_000),
    "mock/*": Price(0.0, 0.0, 0.0, None, 128_000),
}


def lookup(provider: str, model_id: str) -> Price | None:
    """Exact match, then the provider's wildcard, then nothing."""
    return PRICES.get(f"{provider}/{model_id}") or PRICES.get(f"{provider}/*")


def estimate_cost(
    provider: str,
    model_id: str,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> tuple[float, bool]:
    """Return ``(usd, known)``.

    ``known=False`` means the model is not in the table: the caller reports the
    run's cost as "unpriced" rather than as "$0.00", which would be a lie of a
    much more expensive kind.
    """
    price = lookup(provider, model_id)
    if price is None:
        return 0.0, False
    return price.cost(input_tokens, output_tokens, cached_tokens, cache_write_tokens), True
