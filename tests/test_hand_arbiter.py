"""Duas maos em dois canais: identidade estavel e quem e' dono do cursor."""

from __future__ import annotations

from core.hand_arbiter import LEFT, RIGHT, HandArbiter
from core.hand_tracker import HandLandmarks

DT = 1.0 / 30.0


def _hand(x: float, y: float = 0.5, size: float = 0.15, label: str = "Right",
          score: float = 0.9) -> HandLandmarks:
    # So punho (0) e base do medio (9) importam pro arbitro.
    lm = [(x, y + size, 0.0)] * 21
    lm[9] = (x, y, 0.0)
    return HandLandmarks(landmarks=tuple(lm), handedness=label, score=score)


class _Run:
    """Alimenta o arbitro frame a frame com relogio proprio."""

    def __init__(self) -> None:
        self.arb = HandArbiter()
        self.t = 0.0

    def step(self, *hands, locked: bool = False):
        self.t += DT
        return self.arb.assign(hands, self.t, locked=locked)


def _both(run: _Run, left, right):
    a = run.step(left, right)
    assert a.left is left and a.right is right
    return a


def test_label_chooses_channel_when_hand_appears():
    run = _Run()
    left = _hand(0.8, label="Left")
    assert run.step(left).left is left


def test_same_label_on_two_new_hands_splits_by_position():
    run = _Run()
    a, b = _hand(0.8, label="Right"), _hand(0.2, label="Right")
    result = run.step(a, b)
    assert result.left is b and result.right is a


def test_list_order_swap_keeps_channels():
    run = _Run()
    left, right = _hand(0.2, label="Left"), _hand(0.8, label="Right")
    _both(run, left, right)
    for _ in range(10):
        result = run.step(right, left)
        assert result.left is left and result.right is right


def test_flickering_label_keeps_channels():
    run = _Run()
    _both(run, _hand(0.2, label="Left"), _hand(0.8, label="Right"))
    for i in range(30):
        wrong = "Right" if i % 2 else "Left"
        left, right = _hand(0.2, label=wrong), _hand(0.8, label=wrong)
        result = run.step(right, left)
        assert result.left is left and result.right is right


def test_duplicate_detection_is_one_hand():
    run = _Run()
    a = _hand(0.5, score=0.95)
    result = run.step(a, _hand(0.51, score=0.5))
    assert result.right is a and result.left is None


def test_first_hand_becomes_owner():
    run = _Run()
    assert run.step(_hand(0.2, label="Left")).owner == LEFT


def test_still_second_hand_never_takes_over():
    run = _Run()
    run.step(_hand(0.8, label="Right"))
    for _ in range(60):
        assert run.step(_hand(0.2, label="Left"), _hand(0.8)).owner == RIGHT


def test_moving_idle_hand_takes_over_when_owner_is_still():
    run = _Run()
    run.step(_hand(0.8, label="Right"))
    owners = [
        run.step(_hand(0.1 + i * 0.015, label="Left"), _hand(0.8)).owner
        for i in range(30)
    ]
    assert owners[0] == RIGHT and owners[-1] == LEFT


def test_brief_twitch_does_not_steal():
    run = _Run()
    run.step(_hand(0.8))
    run.step(_hand(0.2, label="Left"), _hand(0.8))
    run.step(_hand(0.25, label="Left"), _hand(0.8))
    for _ in range(30):
        assert run.step(_hand(0.25, label="Left"), _hand(0.8)).owner == RIGHT


def test_moving_owner_keeps_control():
    run = _Run()
    run.step(_hand(0.8))
    for i in range(30):
        result = run.step(_hand(0.1 + i * 0.015, label="Left"), _hand(0.8 - i * 0.01))
        assert result.owner == RIGHT


def test_locked_gesture_blocks_takeover():
    run = _Run()
    run.step(_hand(0.8))
    for i in range(30):
        result = run.step(
            _hand(0.1 + i * 0.015, label="Left"), _hand(0.8), locked=True,
        )
        assert result.owner == RIGHT


def test_owner_leaving_hands_over_after_grace():
    run = _Run()
    left, right = _hand(0.2, label="Left"), _hand(0.8)
    run.step(right)
    run.step(left, right)
    # Piscada: canal reservado, dona nao muda.
    assert run.step(left).owner == RIGHT
    assert [run.step(left).owner for _ in range(10)][-1] == LEFT


def test_blink_returns_to_same_channel():
    run = _Run()
    left, right = _hand(0.2, label="Left"), _hand(0.8)
    _both(run, left, right)
    run.step(left)
    assert run.step(left, right).right is right


def test_owner_kept_while_locked_even_if_gone():
    run = _Run()
    left, right = _hand(0.2, label="Left"), _hand(0.8)
    run.step(right)
    run.step(left, right)
    for _ in range(15):
        assert run.step(left, locked=True).owner == RIGHT
    assert run.step(left).owner == LEFT


def test_no_hands_clears_owner_and_any_hand_restarts():
    run = _Run()
    run.step(_hand(0.2, label="Left"))
    for _ in range(10):
        assert run.step().left is None
    assert run.step().owner is None
    assert run.step(_hand(0.8, label="Right")).owner == RIGHT
