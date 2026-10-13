"""Testes do HandTracker (MediaPipe Tasks API / HandLandmarker)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("mediapipe")

from core.hand_tracker import MODEL_PATH, HandLandmarks, HandTracker


DEMO_VIDEO = Path(__file__).resolve().parent.parent / "docs" / "assets" / "video" / "avmc-demo.mp4"
# Quadro do video de demo com a mao direita aberta, bem visivel. Pela API
# antiga (mp.solutions.hands) esse quadro dava handedness "Right" e os
# dedos levantados.
DEMO_OPEN_HAND_FRAME = 2464


def _fake_result(label: str, score: float = 0.9):
    landmarks = [SimpleNamespace(x=i / 20, y=1 - i / 20, z=-i / 100) for i in range(21)]
    category = SimpleNamespace(category_name=label, display_name="", score=score)
    return SimpleNamespace(hand_landmarks=[landmarks], handedness=[[category]])


def _make_tracker(**kwargs) -> HandTracker:
    try:
        return HandTracker(**kwargs)
    except RuntimeError as e:
        if "libEGL" in str(e):
            pytest.skip("libEGL ausente (sudo apt install libegl1)")
        raise


# ---------------------------------------------------------------------
# Conversao do resultado (sem rodar o modelo)
# ---------------------------------------------------------------------

def test_result_vazio_retorna_lista_vazia():
    empty = SimpleNamespace(hand_landmarks=[], handedness=[])
    assert HandTracker._result_to_hands(empty) == []


def test_result_converte_landmarks_e_score():
    (hand,) = HandTracker._result_to_hands(_fake_result("Left", score=0.77))
    assert len(hand.landmarks) == 21
    assert hand.landmarks[4] == pytest.approx((0.2, 0.8, -0.04))
    assert hand.score == pytest.approx(0.77)


@pytest.mark.parametrize(("tasks_label", "expected"), [("Left", "Right"), ("Right", "Left")])
def test_handedness_destrocada_pra_convencao_antiga(tasks_label, expected):
    # A Tasks API nao assume imagem espelhada; mp.solutions assumia.
    # fingers_up() depende da convencao antiga.
    (hand,) = HandTracker._result_to_hands(_fake_result(tasks_label))
    assert hand.handedness == expected


def test_handedness_ausente_cai_no_default():
    result = _fake_result("Left")
    result.handedness = []
    (hand,) = HandTracker._result_to_hands(result)
    assert hand.handedness == "Right"
    assert hand.score == 0.0


# ---------------------------------------------------------------------
# Timestamps e desenho
# ---------------------------------------------------------------------

def test_timestamps_estritamente_crescentes():
    tracker = HandTracker.__new__(HandTracker)
    tracker._t0 = 1e12  # monotonic "no futuro": forca ts negativo/repetido
    tracker._last_ts_ms = -1
    stamps = [tracker._next_timestamp_ms() for _ in range(5)]
    assert all(b > a for a, b in zip(stamps, stamps[1:]))


def test_draw_desenha_no_frame_e_ignora_vazio():
    frame = np.zeros((100, 100, 3), np.uint8)
    tracker = HandTracker.__new__(HandTracker)
    tracker.draw(frame, [])
    tracker.draw(frame, None)
    assert not frame.any()

    hand = HandLandmarks(
        landmarks=tuple((0.2 + i * 0.03, 0.2 + i * 0.03, 0.0) for i in range(21)),
        handedness="Right",
        score=1.0,
    )
    tracker.draw(frame, [hand])
    assert frame.any()


# ---------------------------------------------------------------------
# Modelo real
# ---------------------------------------------------------------------

def test_modelo_empacotado_existe():
    assert MODEL_PATH.is_file()
    assert MODEL_PATH.stat().st_size > 1_000_000


def test_frame_sem_mao_nao_detecta():
    tracker = _make_tracker()
    try:
        hands, raw = tracker.process_with_raw(np.zeros((540, 960, 3), np.uint8))
        assert hands == []
        assert raw == []
    finally:
        tracker.close()


def test_mao_real_do_video_de_demo():
    cv2 = pytest.importorskip("cv2")
    if not DEMO_VIDEO.is_file():
        pytest.skip("video de demo ausente")
    cap = cv2.VideoCapture(str(DEMO_VIDEO))
    cap.set(cv2.CAP_PROP_POS_FRAMES, DEMO_OPEN_HAND_FRAME)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        pytest.skip("OpenCV sem backend pra decodificar o video")
    rgb = cv2.cvtColor(cv2.resize(frame, (960, 540)), cv2.COLOR_BGR2RGB)

    tracker = _make_tracker(min_detection_confidence=0.3, min_tracking_confidence=0.3)
    try:
        hands = tracker.process(rgb)
    finally:
        tracker.close()

    assert len(hands) == 1
    assert hands[0].handedness == "Right"
    # Polegar fica fora: nesse quadro ele esta no limite da heuristica de
    # fingers_up() e vira com diferencas minimas de decodificacao do video.
    assert hands[0].fingers_up()[1:] == [True, True, True, True]
