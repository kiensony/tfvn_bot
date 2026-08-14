"""Pure five-card-draw Poker ranking, dealer policy, and round state."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable, Sequence

from cogs.minigames._playing_cards import Card, draw_card


class HandCategory(IntEnum):
    HIGH_CARD = 0
    ONE_PAIR = 1
    TWO_PAIR = 2
    THREE_OF_A_KIND = 3
    STRAIGHT = 4
    FLUSH = 5
    FULL_HOUSE = 6
    FOUR_OF_A_KIND = 7
    STRAIGHT_FLUSH = 8


@dataclass(frozen=True, order=True)
class HandRank:
    category: HandCategory
    tiebreakers: tuple[int, ...]


HAND_LABELS = {
    HandCategory.HIGH_CARD: "Mậu thầu",
    HandCategory.ONE_PAIR: "Một đôi",
    HandCategory.TWO_PAIR: "Hai đôi",
    HandCategory.THREE_OF_A_KIND: "Bộ ba",
    HandCategory.STRAIGHT: "Sảnh",
    HandCategory.FLUSH: "Thùng",
    HandCategory.FULL_HOUSE: "Cù lũ",
    HandCategory.FOUR_OF_A_KIND: "Tứ quý",
    HandCategory.STRAIGHT_FLUSH: "Thùng phá sảnh",
}


def evaluate_hand(cards: Sequence[Card]) -> HandRank:
    """Evaluate exactly five distinct cards using standard Poker tie-breakers."""

    hand = tuple(cards)
    if len(hand) != 5:
        raise ValueError("A Poker hand must contain exactly five cards")
    if len(set(hand)) != 5:
        raise ValueError("A Poker hand cannot contain duplicate cards")

    ranks = [card.rank for card in hand]
    rank_counts = Counter(ranks)
    descending = tuple(sorted(ranks, reverse=True))
    unique_ranks = sorted(rank_counts)
    flush = len({card.suit for card in hand}) == 1
    if unique_ranks == [2, 3, 4, 5, 14]:
        straight_high = 5
    elif len(unique_ranks) == 5 and unique_ranks[-1] - unique_ranks[0] == 4:
        straight_high = unique_ranks[-1]
    else:
        straight_high = None

    groups = sorted(
        ((count, rank) for rank, count in rank_counts.items()),
        reverse=True,
    )
    if flush and straight_high is not None:
        return HandRank(HandCategory.STRAIGHT_FLUSH, (straight_high,))
    if groups[0][0] == 4:
        quad_rank = groups[0][1]
        kicker = next(rank for rank in ranks if rank != quad_rank)
        return HandRank(HandCategory.FOUR_OF_A_KIND, (quad_rank, kicker))
    if [count for count, _ in groups] == [3, 2]:
        return HandRank(HandCategory.FULL_HOUSE, (groups[0][1], groups[1][1]))
    if flush:
        return HandRank(HandCategory.FLUSH, descending)
    if straight_high is not None:
        return HandRank(HandCategory.STRAIGHT, (straight_high,))
    if groups[0][0] == 3:
        trip_rank = groups[0][1]
        kickers = tuple(sorted((rank for rank in ranks if rank != trip_rank), reverse=True))
        return HandRank(HandCategory.THREE_OF_A_KIND, (trip_rank, *kickers))

    pair_ranks = sorted(
        (rank for rank, count in rank_counts.items() if count == 2),
        reverse=True,
    )
    if len(pair_ranks) == 2:
        kicker = next(rank for rank, count in rank_counts.items() if count == 1)
        return HandRank(HandCategory.TWO_PAIR, (*pair_ranks, kicker))
    if len(pair_ranks) == 1:
        pair_rank = pair_ranks[0]
        kickers = tuple(sorted((rank for rank in ranks if rank != pair_rank), reverse=True))
        return HandRank(HandCategory.ONE_PAIR, (pair_rank, *kickers))
    return HandRank(HandCategory.HIGH_CARD, descending)


def compare_hands(player: Sequence[Card], dealer: Sequence[Card]) -> int:
    """Return 1 for a player win, -1 for a dealer win, or 0 for a tie."""

    player_rank = evaluate_hand(player)
    dealer_rank = evaluate_hand(dealer)
    return (player_rank > dealer_rank) - (player_rank < dealer_rank)


def rank_label(rank: HandRank | HandCategory) -> str:
    """Return the Vietnamese name of a hand category."""

    category = rank.category if isinstance(rank, HandRank) else rank
    try:
        return HAND_LABELS[HandCategory(category)]
    except (KeyError, ValueError) as exc:
        raise ValueError("Unknown Poker hand category") from exc


def dealer_discard_indices(hand: Sequence[Card]) -> tuple[int, ...]:
    """Choose up to three cards using a simple, deterministic dealer policy."""

    rank = evaluate_hand(hand)
    counts = Counter(card.rank for card in hand)
    if rank.category in {
        HandCategory.STRAIGHT,
        HandCategory.FLUSH,
        HandCategory.FULL_HOUSE,
        HandCategory.STRAIGHT_FLUSH,
    }:
        return ()
    if rank.category is HandCategory.FOUR_OF_A_KIND:
        quad_rank = next(card_rank for card_rank, count in counts.items() if count == 4)
        return tuple(index for index, card in enumerate(hand) if card.rank != quad_rank)
    if rank.category is HandCategory.THREE_OF_A_KIND:
        trip_rank = next(card_rank for card_rank, count in counts.items() if count == 3)
        return tuple(index for index, card in enumerate(hand) if card.rank != trip_rank)
    if rank.category is HandCategory.TWO_PAIR:
        return tuple(index for index, card in enumerate(hand) if counts[card.rank] == 1)
    if rank.category is HandCategory.ONE_PAIR:
        pair_rank = next(card_rank for card_rank, count in counts.items() if count == 2)
        return tuple(index for index, card in enumerate(hand) if card.rank != pair_rank)

    kept = set(
        sorted(range(5), key=lambda index: hand[index].rank, reverse=True)[:2]
    )
    return tuple(index for index in range(5) if index not in kept)


def replace_cards(
    hand: Sequence[Card],
    indices: Iterable[int],
    deck: list[Card],
) -> list[Card]:
    """Return a hand with the selected zero-based positions replaced from ``deck``."""

    cards = list(hand)
    selected = tuple(indices)
    if len(cards) != 5 or len(set(cards)) != 5:
        raise ValueError("A Poker hand must contain five distinct cards")
    if len(selected) > 3:
        raise ValueError("At most three Poker cards may be replaced")
    if any(isinstance(index, bool) or not isinstance(index, int) for index in selected):
        raise ValueError("Poker card positions must be integers")
    if len(set(selected)) != len(selected):
        raise ValueError("Poker card positions must be unique")
    if any(index < 0 or index >= 5 for index in selected):
        raise ValueError("Poker card position is out of range")
    if len(deck) < len(selected):
        raise ValueError("The deck does not have enough replacement cards")
    if len(set(deck)) != len(deck) or set(cards).intersection(deck):
        raise ValueError("Poker deck contains duplicate or already-dealt cards")

    for index in sorted(selected):
        cards[index] = draw_card(deck)
    return cards


class PokerGame:
    """One round of heads-up five-card draw against a deterministic dealer."""

    def __init__(self, deck: Sequence[Card]) -> None:
        if len(deck) < 10:
            raise ValueError("Five-card draw needs at least ten cards")
        if len(set(deck)) != len(deck):
            raise ValueError("Poker deck contains duplicate cards")
        self.deck = list(deck)
        self.player_hand: list[Card] = []
        self.dealer_hand: list[Card] = []
        for _ in range(5):
            self.player_hand.append(draw_card(self.deck))
            self.dealer_hand.append(draw_card(self.deck))
        self.finished = False
        self.result: int | None = None

    def draw(self, indices: Iterable[int]) -> int:
        """Apply the player's sole draw, let the dealer draw, and resolve."""

        self._require_active()
        self.player_hand = replace_cards(self.player_hand, indices, self.deck)
        dealer_indices = dealer_discard_indices(self.dealer_hand)
        self.dealer_hand = replace_cards(self.dealer_hand, dealer_indices, self.deck)
        self.result = compare_hands(self.player_hand, self.dealer_hand)
        self.finished = True
        return self.result

    def stand(self) -> int:
        """Keep all player cards and proceed directly to the dealer draw."""

        return self.draw(())

    def _require_active(self) -> None:
        if self.finished:
            raise RuntimeError("Poker round is already finished")
