"""Testes do alcance inferior da ancora robusta.

Cobrem o taper assimetrico (landmarks de baixo mantem peso ate quase
sair do frame) e o gain reforcado descendo na zona critica. Modulo
puro — sem cv2/mediapipe.
"""

from __future__ import annotations

import pytest

from core.hand_anchor import RobustHandAnchor, _edge_factor


def _hand_at(y: float, x: float = 0.5):
    """21 landmarks coincidentes — ancora resultante = ponto exato."""
    return [(x, y, 0.0)] * 21


class TestAsymmetricEdgeTaper:
    def test_bottom_landmark_keeps_full_weight(self):
        # y=0.97: com taper simetrico de 6% o fator seria 0.5;
        # com margem inferior de 2%, (1-0.97)/0.02 = 1.5 -> clamp 1.0
        assert _edge_factor(0.5, 0.97, 0.06, bottom_margin=0.02) == 1.0

    def test_symmetric_behavior_preserved_without_bottom_margin(self):
        # Sem bottom_margin o comportamento antigo se mantem
        assert _edge_factor(0.5, 0.97, 0.06) == pytest.approx(0.5)

    def test_top_edge_still_tapers(self):
        # Margem inferior menor NAO afeta o topo do frame
        assert _edge_factor(0.5, 0.03, 0.06, bottom_margin=0.02) == pytest.approx(0.5)

    def test_fully_out_still_zero(self):
        assert _edge_factor(0.5, 1.0, 0.06, bottom_margin=0.02) == 0.0

    def test_anchor_tracks_hand_near_bottom(self):
        """Mao a y=0.96: a ancora deve seguir, nao ser puxada pra cima."""
        anchor = RobustHandAnchor()
        result = anchor.compute(_hand_at(0.96))
        assert result.used_landmarks == 21
        assert abs(result.y - 0.96) < 0.01


class TestDownwardCriticalGain:
    def _drive_down(self, ys):
        anchor = RobustHandAnchor()
        last = None
        for y in ys:
            last = anchor.compute(_hand_at(y))
        return last

    def test_downward_motion_extrapolates_past_raw(self):
        """Descendo na zona critica, a ancora avanca ALEM da posicao
        crua — compensa a fase de homing onde a mao para de descer."""
        result = self._drive_down([0.88, 0.91, 0.94])
        assert result.y > 0.94

    def test_upward_motion_not_boosted(self):
        """Subindo a partir do fundo nao ganha o gain reforcado — sem
        overshoot na volta."""
        result = self._drive_down([0.94, 0.91, 0.88])
        # Pode extrapolar um pouco (gain padrao), mas nunca com o
        # reforco de descida: fica perto do valor cru.
        assert result.y < 0.90
