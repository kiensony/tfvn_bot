"""Pure Blackjack scoring and round state."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Sequence

from cogs.minigames._playing_cards import Card, draw_card


class BlackjackOutcome(str, Enum):
    PLAYER_BLACKJACK = "player_blackjack"
    PLAYER_WIN = "player_win"
    DEALER_WIN = "dealer_win"
    PUSH = "push"


@dataclass(frozen=True)
class BlackjackHandValue:
    total: int
    soft: bool
    blackjack: bool
    bust: bool


def score_hand(cards: Sequence[Card]) -> BlackjackHandValue:
    """Score a Blackjack hand while downgrading aces from 11 to 1 as needed."""

    total = sum(11 if card.rank == 14 else min(card.rank, 10) for card in cards)
    high_aces = sum(card.rank == 14 for card in cards)
    while total > 21 and high_aces:
        total -= 10
        high_aces -= 1
    return BlackjackHandValue(
        total=total,
        soft=high_aces > 0,
        blackjack=len(cards) == 2 and total == 21,
        bust=total > 21,
    )


def dealer_should_hit(cards: Sequence[Card]) -> bool:
    """Use the house rule that the dealer stands on every 17, including soft 17."""

    value = score_hand(cards)
    return not value.bust and value.total < 17


def resolve_blackjack(
    player_hand: Sequence[Card],
    dealer_hand: Sequence[Card],
) -> BlackjackOutcome:
    """Resolve two final Blackjack hands, giving two-card naturals priority."""

    player = score_hand(player_hand)
    dealer = score_hand(dealer_hand)
    if player.blackjack and dealer.blackjack:
        return BlackjackOutcome.PUSH
    if player.blackjack:
        return BlackjackOutcome.PLAYER_BLACKJACK
    if dealer.blackjack or player.bust:
        return BlackjackOutcome.DEALER_WIN
    if dealer.bust or player.total > dealer.total:
        return BlackjackOutcome.PLAYER_WIN
    if player.total < dealer.total:
        return BlackjackOutcome.DEALER_WIN
    return BlackjackOutcome.PUSH


def payout_return(bet: int, outcome: BlackjackOutcome) -> int:
    """Return the total credit due after a stake was already deducted."""

    if isinstance(bet, bool) or not isinstance(bet, int) or bet <= 0:
        raise ValueError("Blackjack bet must be a positive integer")
    if outcome is BlackjackOutcome.PLAYER_BLACKJACK:
        return bet + (3 * bet) // 2
    if outcome is BlackjackOutcome.PLAYER_WIN:
        return bet * 2
    if outcome is BlackjackOutcome.PUSH:
        return bet
    return 0


class BlackjackGame:
    """A single player-versus-dealer Blackjack round."""

    def __init__(self, deck: Sequence[Card]) -> None:
        if len(deck) < 4:
            raise ValueError("Blackjack needs at least four cards")
        if len(set(deck)) != len(deck):
            raise ValueError("Blackjack deck contains duplicate cards")
        self.deck = list(deck)
        self.player_hand = [draw_card(self.deck)]
        self.dealer_hand = [draw_card(self.deck)]
        self.player_hand.append(draw_card(self.deck))
        self.dealer_hand.append(draw_card(self.deck))
        self.finished = False
        self.outcome: BlackjackOutcome | None = None

        player = score_hand(self.player_hand)
        dealer = score_hand(self.dealer_hand)
        if player.blackjack or dealer.blackjack:
            self.outcome = resolve_blackjack(self.player_hand, self.dealer_hand)
            self.finished = True

    def hit(self) -> Card:
        """Draw for the player and automatically finish on a bust or 21."""

        self._require_active()
        card = draw_card(self.deck)
        self.player_hand.append(card)
        value = score_hand(self.player_hand)
        if value.bust:
            self.outcome = BlackjackOutcome.DEALER_WIN
            self.finished = True
        elif value.total == 21:
            self.stand()
        return card

    def stand(self) -> BlackjackOutcome:
        """Finish the dealer hand and resolve the round."""

        self._require_active()
        while dealer_should_hit(self.dealer_hand):
            self.dealer_hand.append(draw_card(self.deck))
        self.outcome = resolve_blackjack(self.player_hand, self.dealer_hand)
        self.finished = True
        return self.outcome

    def _require_active(self) -> None:
        if self.finished:
            raise RuntimeError("Blackjack round is already finished")
