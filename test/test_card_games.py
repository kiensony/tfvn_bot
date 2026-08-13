import random
import unittest

from cogs.funny_things._blackjack_helpers import (
    BlackjackGame,
    BlackjackOutcome,
    dealer_should_hit,
    payout_return,
    resolve_blackjack,
    score_hand,
)
from cogs.funny_things._playing_cards import (
    Card,
    create_deck,
    draw_card,
    format_card,
    format_hand,
)
from cogs.funny_things._poker_helpers import (
    HandCategory,
    PokerGame,
    compare_hands,
    dealer_discard_indices,
    evaluate_hand,
    rank_label,
    replace_cards,
)


RANKS = {"T": 10, "J": 11, "Q": 12, "K": 13, "A": 14}


def cards(spec: str) -> list[Card]:
    result = []
    for token in spec.split():
        rank_text, suit = token[:-1], token[-1]
        rank = RANKS.get(rank_text, int(rank_text) if rank_text.isdigit() else 0)
        result.append(Card(rank, suit))
    return result


class TestPlayingCards(unittest.TestCase):
    def test_deck_is_complete_unique_and_seedable(self) -> None:
        first = create_deck(random.Random(42))
        second = create_deck(random.Random(42))
        self.assertEqual(first, second)
        self.assertEqual(len(first), 52)
        self.assertEqual(len(set(first)), 52)

    def test_card_validation_draw_and_formatting(self) -> None:
        with self.assertRaises(ValueError):
            Card(1, "♠")
        with self.assertRaises(ValueError):
            Card(14, "x")
        deck = cards("2♠ A♥")
        self.assertEqual(draw_card(deck), Card(14, "♥"))
        self.assertEqual(format_card(Card(13, "♦")), "K♦")
        self.assertEqual(format_hand(cards("10♣ J♠")), "10♣ J♠")
        with self.assertRaises(ValueError):
            draw_card([])


class TestBlackjackRules(unittest.TestCase):
    def test_aces_and_natural_scoring(self) -> None:
        self.assertEqual(score_hand(cards("A♠ A♥ 9♦")).total, 21)
        self.assertEqual(score_hand(cards("A♠ A♥ A♦ 8♣")).total, 21)
        self.assertTrue(score_hand(cards("A♠ K♥")).blackjack)
        self.assertFalse(score_hand(cards("A♠ 5♥ 5♦")).blackjack)
        self.assertTrue(score_hand(cards("A♠ 6♥")).soft)

    def test_dealer_stands_on_soft_and_hard_seventeen(self) -> None:
        self.assertFalse(dealer_should_hit(cards("A♠ 6♥")))
        self.assertFalse(dealer_should_hit(cards("10♠ 7♥")))
        self.assertTrue(dealer_should_hit(cards("A♠ 5♥")))

    def test_resolution_prioritizes_naturals_and_handles_busts(self) -> None:
        self.assertIs(
            resolve_blackjack(cards("A♠ K♥"), cards("A♦ Q♣")),
            BlackjackOutcome.PUSH,
        )
        self.assertIs(
            resolve_blackjack(cards("A♠ K♥"), cards("7♦ 7♣ 7♥")),
            BlackjackOutcome.PLAYER_BLACKJACK,
        )
        self.assertIs(
            resolve_blackjack(cards("10♠ 8♥"), cards("10♦ 8♣")),
            BlackjackOutcome.PUSH,
        )
        self.assertIs(
            resolve_blackjack(cards("K♠ Q♥ 2♦"), cards("10♦ 8♣")),
            BlackjackOutcome.DEALER_WIN,
        )
        self.assertIs(
            resolve_blackjack(cards("10♠ 9♥"), cards("K♦ 8♣ 5♥")),
            BlackjackOutcome.PLAYER_WIN,
        )

    def test_returns_include_original_stake(self) -> None:
        self.assertEqual(payout_return(5, BlackjackOutcome.PLAYER_BLACKJACK), 12)
        self.assertEqual(payout_return(10, BlackjackOutcome.PLAYER_WIN), 20)
        self.assertEqual(payout_return(10, BlackjackOutcome.PUSH), 10)
        self.assertEqual(payout_return(10, BlackjackOutcome.DEALER_WIN), 0)

    def test_game_deals_player_dealer_player_dealer_and_auto_finishes(self) -> None:
        natural_deck = cards("7♣ K♥ 9♦ A♠")
        natural = BlackjackGame(natural_deck)
        self.assertTrue(natural.finished)
        self.assertIs(natural.outcome, BlackjackOutcome.PLAYER_BLACKJACK)

        hit_deck = cards("2♣ 5♣ 7♣ 6♥ 9♦ 10♠")
        game = BlackjackGame(hit_deck)
        drawn = game.hit()
        self.assertEqual(drawn, Card(5, "♣"))
        self.assertTrue(game.finished)
        self.assertIs(game.outcome, BlackjackOutcome.PLAYER_WIN)
        with self.assertRaises(RuntimeError):
            game.stand()


class TestPokerRules(unittest.TestCase):
    def test_every_hand_category(self) -> None:
        examples = {
            "2♠ 5♥ 7♦ 9♣ J♠": HandCategory.HIGH_CARD,
            "2♠ 2♥ 7♦ 9♣ J♠": HandCategory.ONE_PAIR,
            "2♠ 2♥ 7♦ 7♣ J♠": HandCategory.TWO_PAIR,
            "2♠ 2♥ 2♦ 9♣ J♠": HandCategory.THREE_OF_A_KIND,
            "5♠ 6♥ 7♦ 8♣ 9♠": HandCategory.STRAIGHT,
            "2♠ 5♠ 7♠ 9♠ J♠": HandCategory.FLUSH,
            "2♠ 2♥ 2♦ 9♣ 9♠": HandCategory.FULL_HOUSE,
            "2♠ 2♥ 2♦ 2♣ J♠": HandCategory.FOUR_OF_A_KIND,
            "5♠ 6♠ 7♠ 8♠ 9♠": HandCategory.STRAIGHT_FLUSH,
        }
        for hand, category in examples.items():
            with self.subTest(category=category):
                self.assertIs(evaluate_hand(cards(hand)).category, category)

    def test_wheel_and_tie_breakers(self) -> None:
        wheel = evaluate_hand(cards("A♠ 2♥ 3♦ 4♣ 5♠"))
        six_high = evaluate_hand(cards("2♠ 3♥ 4♦ 5♣ 6♠"))
        self.assertEqual(wheel.tiebreakers, (5,))
        self.assertGreater(six_high, wheel)
        self.assertGreater(
            evaluate_hand(cards("K♠ K♥ A♦ 9♣ 3♠")),
            evaluate_hand(cards("K♦ K♣ Q♥ J♣ 10♠")),
        )
        self.assertEqual(
            compare_hands(cards("A♠ K♠ 9♠ 5♠ 2♠"), cards("A♥ K♥ 9♥ 5♥ 2♥")),
            0,
        )

    def test_invalid_hands_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            evaluate_hand(cards("A♠ K♥ Q♦ J♣"))
        with self.assertRaises(ValueError):
            evaluate_hand(cards("A♠ A♠ Q♦ J♣ 10♥"))

    def test_dealer_draw_policy(self) -> None:
        self.assertEqual(dealer_discard_indices(cards("5♠ 6♥ 7♦ 8♣ 9♠")), ())
        self.assertEqual(dealer_discard_indices(cards("2♠ 2♥ 2♦ 2♣ J♠")), (4,))
        self.assertEqual(dealer_discard_indices(cards("2♠ 2♥ 2♦ 9♣ J♠")), (3, 4))
        self.assertEqual(dealer_discard_indices(cards("2♠ 2♥ 7♦ 7♣ J♠")), (4,))
        self.assertEqual(dealer_discard_indices(cards("2♠ 2♥ 7♦ 9♣ J♠")), (2, 3, 4))
        self.assertEqual(len(dealer_discard_indices(cards("2♠ 5♥ 7♦ 9♣ J♠"))), 3)

    def test_replacement_validates_positions_and_deck(self) -> None:
        hand = cards("2♠ 3♥ 4♦ 5♣ 7♠")
        deck = cards("8♠ 9♥ 10♦")
        replaced = replace_cards(hand, (1, 3), deck)
        self.assertEqual(replaced[0], hand[0])
        self.assertNotEqual(replaced[1], hand[1])
        with self.assertRaises(ValueError):
            replace_cards(hand, (0, 1, 2, 3), cards("8♠ 9♥ 10♦ J♣"))
        with self.assertRaises(ValueError):
            replace_cards(hand, (1, 1), cards("8♠ 9♥"))

    def test_poker_game_allows_one_player_decision(self) -> None:
        game = PokerGame(create_deck(random.Random(7)))
        result = game.stand()
        self.assertIn(result, (-1, 0, 1))
        self.assertTrue(game.finished)
        self.assertIsNotNone(game.result)
        self.assertTrue(rank_label(evaluate_hand(game.player_hand)))
        with self.assertRaises(RuntimeError):
            game.draw((0,))


if __name__ == "__main__":
    unittest.main()
