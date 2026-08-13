"""Validated playing cards shared by the interactive casino games."""

from __future__ import annotations

import random
from dataclasses import dataclass


SUITS = ("♠", "♥", "♦", "♣")
MIN_RANK = 2
MAX_RANK = 14
RANK_LABELS = {
    11: "J",
    12: "Q",
    13: "K",
    14: "A",
}


@dataclass(frozen=True)
class Card:
    """One standard playing card; ranks 11–14 are J, Q, K, and A."""

    rank: int
    suit: str

    def __post_init__(self) -> None:
        if (
            isinstance(self.rank, bool)
            or not isinstance(self.rank, int)
            or not MIN_RANK <= self.rank <= MAX_RANK
        ):
            raise ValueError("Card rank must be an integer from 2 through 14")
        if self.suit not in SUITS:
            raise ValueError(f"Card suit must be one of: {' '.join(SUITS)}")


def create_deck(rng: random.Random | None = None) -> list[Card]:
    """Return a shuffled, complete 52-card deck."""

    deck = [Card(rank, suit) for suit in SUITS for rank in range(2, 15)]
    if rng is None:
        random.shuffle(deck)
    else:
        rng.shuffle(deck)
    return deck


def draw_card(deck: list[Card]) -> Card:
    """Remove and return the next card from ``deck``."""

    if not deck:
        raise ValueError("The deck has no cards left")
    return deck.pop()


def format_card(card: Card) -> str:
    """Format a card compactly for a Discord message."""

    return f"{RANK_LABELS.get(card.rank, str(card.rank))}{card.suit}"


def format_hand(cards: list[Card] | tuple[Card, ...]) -> str:
    """Format a sequence of cards on one readable line."""

    return " ".join(format_card(card) for card in cards)
