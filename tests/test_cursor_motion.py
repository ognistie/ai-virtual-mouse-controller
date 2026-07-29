"""Testes do pipeline de movimento adaptativo (core/cursor_motion.py).

Todos DETERMINISTAS: o modulo nao tem relogio proprio — o ``dt`` e'
injetado em cada chamada. Nenhum ``time.sleep`` aqui.

Cobrem os invariantes que a UX exige:

- ganho monotonico e INVERSO na escala aparente da palma;
- ganho limitado;
- ancora parada => deslocamento zero (mudar distancia/aim/perfil NAO
  pode mover o cursor);
- continuidade ao atravessar distancia neutra, aim assist e entrada da
  assistencia inferior;
- ausencia de degrau de velocidade no "joelho" da curva inferior;
- equivalencia por TEMPO FISICO entre 30 e 60 FPS;
- alcance e liberacao da borda inferior;
- re-ancoragem apos perda da mao, sem salto.
"""

from __future__ import annotations

import math
import random

import pytest

from core.cursor_motion import (
    BottomAssist,
    CursorMotion,
    Envelope,
    MotionConfig,
    distance_gain,
    estimate_palm_scale,
    smoothstep,
    velocity_curve_factor,
)


# ---------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------

SCREEN_W, SCREEN_H = 1920, 1080
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = 0.08, 0.04, 0.14

# Conversao ancora -> pixel, igual ao CursorController.
PX_PER_UNIT_X = (SCREEN_W - 1) / (1.0 - 2 * MARGIN_X)
PX_PER_UNIT_Y = (SCREEN_H - 1) / (1.0 - MARGIN_TOP - MARGIN_BOTTOM)

BOTTOM_EDGE = 1.0 - MARGIN_BOTTOM


def _cfg(**kwargs) -> MotionConfig:
    """MotionConfig com a borda inferior alinhada as margens de teste."""
    base = dict(bottom_edge=BOTTOM_EDGE)
    base.update(kwargs)
    return MotionConfig(**base)


def _hand(scale: float, cx: float = 0.5, cy: float = 0.5):
    """21 landmarks com a geometria de palma que o estimador espera.

    Apenas 0, 5, 9, 13 e 17 importam para ``estimate_palm_scale``; os
    demais existem so pra completar os 21. ``scale`` e' o comprimento da
    palma (pulso -> MCP do medio) em unidades normalizadas do frame —
    e' exatamente o que cresce quando a mao se aproxima da webcam.
    """
    s = scale
    pts = [(cx, cy, 0.0)] * 21
    top = cy - 0.5 * s
    pts[0] = (cx, cy + 0.5 * s, 0.0)          # wrist
    pts[5] = (cx - 0.24 * s, top, 0.0)        # index MCP
    pts[9] = (cx, top, 0.0)                   # middle MCP
    pts[13] = (cx + 0.22 * s, top, 0.0)       # ring MCP
    pts[17] = (cx + 0.48 * s, top, 0.0)       # pinky MCP
    return pts


def _run(motion: CursorMotion, points, dt, **kwargs):
    """Roda uma trajetoria e devolve a lista de saidas."""
    out = []
    for pt in points:
        out.append(motion.update(pt, dt, **kwargs))
    return out


# ---------------------------------------------------------------------
# Estimativa de escala
# ---------------------------------------------------------------------

class TestPalmScale:
    def test_recovers_the_palm_length(self):
        assert estimate_palm_scale(_hand(0.22)) == pytest.approx(0.22, rel=0.02)

    def test_is_linear_in_hand_size(self):
        small = estimate_palm_scale(_hand(0.10))
        big = estimate_palm_scale(_hand(0.30))
        assert small is not None and big is not None
        assert big / small == pytest.approx(3.0, rel=0.02)

    def test_is_translation_invariant(self):
        a = estimate_palm_scale(_hand(0.22, cx=0.2, cy=0.2))
        b = estimate_palm_scale(_hand(0.22, cx=0.8, cy=0.8))
        assert a == pytest.approx(b, rel=1e-6)

    def test_survives_one_corrupted_landmark(self):
        """Mediana + rejeicao de outlier: um landmark predito longe do
        lugar nao pode mudar o ganho."""
        good = estimate_palm_scale(_hand(0.22))
        broken = _hand(0.22)
        broken[13] = (0.99, 0.01, 0.0)  # anelar "explodido"
        assert good is not None
        assert estimate_palm_scale(broken) == pytest.approx(good, rel=0.05)

    def test_returns_none_without_enough_landmarks(self):
        assert estimate_palm_scale(None) is None
        assert estimate_palm_scale([(0.5, 0.5, 0.0)] * 10) is None

    def test_returns_none_for_degenerate_hand(self):
        assert estimate_palm_scale([(0.5, 0.5, 0.0)] * 21) is None


# ---------------------------------------------------------------------
# 1 + 2 — Ganho monotonico e limitado
# ---------------------------------------------------------------------

class TestDistanceGain:
    def test_far_beats_neutral_beats_near(self):
        cfg = _cfg()
        ref = cfg.scale_reference
        g_far = distance_gain(ref * 0.5, cfg)
        g_neutral = distance_gain(ref, cfg)
        g_near = distance_gain(ref * 1.8, cfg)
        assert g_far > g_neutral > g_near

    def test_is_strictly_monotonic_decreasing(self):
        cfg = _cfg()
        scales = [0.02 + i * 0.01 for i in range(60)]
        gains = [distance_gain(s, cfg) for s in scales]
        for a, b in zip(gains, gains[1:]):
            assert b <= a + 1e-12

    def test_stays_inside_configured_bounds(self):
        cfg = _cfg()
        for s in [0.0, 1e-6, 0.05, 0.22, 0.5, 5.0, 1e6]:
            g = distance_gain(s, cfg)
            assert cfg.gain_near - 1e-9 <= g <= cfg.gain_far + 1e-9

    def test_reference_scale_is_exactly_neutral(self):
        cfg = _cfg()
        assert distance_gain(cfg.scale_reference, cfg) == pytest.approx(1.0)

    def test_curve_is_c1_at_the_neutral_point(self):
        """Derivada continua na emenda: atravessar a distancia neutra nao
        muda a velocidade aparente em degrau.

        Comparamos as derivadas laterais na referencia com a inclinacao
        MAXIMA da curva — perto da emenda elas tem que ser desprezveis.
        """
        cfg = _cfg()
        ref = cfg.scale_reference
        h = 1e-4
        left = abs(distance_gain(ref, cfg) - distance_gain(ref - h, cfg)) / h
        right = abs(distance_gain(ref + h, cfg) - distance_gain(ref, cfg)) / h

        peak = max(
            abs(distance_gain(0.05 + i * 0.002 + h, cfg)
                - distance_gain(0.05 + i * 0.002, cfg)) / h
            for i in range(200)
        )
        assert peak > 1.0                      # a curva realmente sobe
        assert left < peak / 100.0
        assert right < peak / 100.0
        assert abs(left - right) < peak / 100.0

    def test_disabled_returns_neutral(self):
        cfg = _cfg(distance_gain_enabled=False)
        assert distance_gain(0.05, cfg) == 1.0
        assert distance_gain(0.90, cfg) == 1.0


class TestSmoothstep:
    def test_endpoints_and_clamp(self):
        assert smoothstep(-1.0) == 0.0
        assert smoothstep(0.0) == 0.0
        assert smoothstep(1.0) == 1.0
        assert smoothstep(2.0) == 1.0
        assert smoothstep(0.5) == pytest.approx(0.5)

    def test_zero_derivative_at_both_ends(self):
        h = 1e-5
        assert smoothstep(h) / h < 1e-3
        assert (1.0 - smoothstep(1.0 - h)) / h < 1e-3


class TestVelocityCurve:
    def test_tremor_is_killed(self):
        cfg = _cfg()
        assert velocity_curve_factor(0.0, cfg) == 0.0

    def test_neutral_zone_is_unity(self):
        cfg = _cfg()
        mid = (cfg.velocity_precision_zone + cfg.velocity_fast_threshold) * 0.5
        assert velocity_curve_factor(mid * cfg.velocity_reference_fps, cfg) == 1.0

    def test_fast_zone_amplifies(self):
        cfg = _cfg()
        fast = cfg.velocity_fast_threshold * cfg.velocity_reference_fps * 4
        assert velocity_curve_factor(fast, cfg) == pytest.approx(
            cfg.velocity_fast_factor
        )


# ---------------------------------------------------------------------
# 3 — Invariancia: ancora parada => deslocamento zero
# ---------------------------------------------------------------------

class TestInvariance:
    def test_static_anchor_with_changing_distance_does_not_move(self):
        motion = CursorMotion(_cfg())
        anchor = (0.30, 0.65)
        motion.update(anchor, 1 / 60, landmarks=_hand(0.22))
        start = motion.position

        # Mao "se aproxima" bruscamente: a escala triplica, o ganho muda
        # inteiro — a ancora nao. O cursor NAO pode se mexer.
        for _ in range(120):
            motion.update(anchor, 1 / 60, landmarks=_hand(0.40))
        assert motion.position == start

    def test_static_anchor_with_aim_assist_toggling_does_not_move(self):
        motion = CursorMotion(_cfg())
        anchor = (0.5, 0.5)
        motion.update(anchor, 1 / 60, landmarks=_hand(0.22))
        start = motion.position
        for i in range(120):
            motion.update(
                anchor, 1 / 60, landmarks=_hand(0.22),
                aim_target=1.0 if i % 2 == 0 else 0.0,
            )
        assert motion.position == start

    def test_static_anchor_with_profile_change_does_not_move(self):
        motion = CursorMotion(_cfg())
        anchor = (0.5, 0.5)
        motion.update(anchor, 1 / 60, landmarks=_hand(0.22))
        start = motion.position
        motion.set_config(_cfg(gain_far=2.0, gain_near=0.3, aim_slowdown_factor=0.1))
        motion.update(anchor, 1 / 60, landmarks=_hand(0.22), base_sensitivity=1.5)
        assert motion.position == start

    def test_static_anchor_inside_bottom_band_does_not_creep(self):
        """Mao parada dentro da faixa inferior, sem intencao recente de
        descer: o cursor NAO pode fugir sozinho (defeito do border creep)."""
        motion = CursorMotion(_cfg())
        anchor = (0.5, BOTTOM_EDGE - 0.02)
        motion.update(anchor, 1 / 60, landmarks=_hand(0.22))
        start = motion.position
        for _ in range(180):  # 3 segundos parado
            motion.update(anchor, 1 / 60, landmarks=_hand(0.22))
        assert motion.position == start

    def test_gain_change_only_affects_future_motion(self):
        far = CursorMotion(_cfg())
        near = CursorMotion(_cfg())
        for m, scale in ((far, 0.12), (near, 0.36)):
            m.update((0.5, 0.5), 1 / 60, landmarks=_hand(scale))
        # Mesmo primeiro frame => mesma posicao, independente do ganho.
        assert far.position == near.position


# ---------------------------------------------------------------------
# 4 — Continuidade nas transicoes
# ---------------------------------------------------------------------

def _step_sizes_px(outputs):
    """Deslocamento por frame, em pixels de 1920x1080."""
    steps = []
    for a, b in zip(outputs, outputs[1:]):
        dx = (b[0] - a[0]) * PX_PER_UNIT_X
        dy = (b[1] - a[1]) * PX_PER_UNIT_Y
        steps.append(math.hypot(dx, dy))
    return steps


class TestContinuity:
    def test_crossing_the_neutral_distance_has_no_jump(self):
        """Velocidade da mao constante, escala varrendo LONGE -> PERTO:
        o deslocamento por frame varia suavemente."""
        motion = CursorMotion(_cfg())
        dt = 1 / 60
        outputs = []
        for i in range(240):
            scale = 0.12 + (0.36 - 0.12) * (i / 239.0)
            anchor = (0.20 + 0.002 * i, 0.5)
            outputs.append(
                motion.update(anchor, dt, landmarks=_hand(scale, cx=anchor[0]))
            )
        steps = _step_sizes_px(outputs[2:])
        jumps = [abs(b - a) for a, b in zip(steps, steps[1:])]
        assert max(jumps) < 1.0

    def test_aim_assist_entry_and_exit_are_gradual(self):
        motion = CursorMotion(_cfg())
        dt = 1 / 60
        outputs = []
        for i in range(240):
            anchor = (0.20 + 0.002 * i, 0.5)
            aim = 1.0 if 80 <= i < 160 else 0.0
            outputs.append(
                motion.update(
                    anchor, dt, landmarks=_hand(0.22, cx=anchor[0]),
                    aim_target=aim,
                )
            )
        steps = _step_sizes_px(outputs[2:])
        jumps = [abs(b - a) for a, b in zip(steps, steps[1:])]
        # Sem envelope, a troca booleana daria ~60% do passo de uma vez.
        assert max(jumps) < 2.0

    def test_bottom_band_entry_has_no_velocity_step(self):
        """Caso 5 (knee): diferencas finitas antes/depois da entrada da
        curva nao revelam degrau de velocidade."""
        motion = CursorMotion(_cfg())
        dt = 1 / 60
        outputs = []
        y = 0.45
        for _ in range(200):
            y += 0.0025
            outputs.append(motion.update((0.5, y), dt, landmarks=_hand(0.22, cy=y)))
        steps = _step_sizes_px(outputs[2:])
        jumps = [abs(b - a) for a, b in zip(steps, steps[1:])]
        assert max(jumps) < 1.5

    def test_bottom_assist_starts_at_unity_gain(self):
        """A assistencia NASCE em ganho 1.0 e com derivada zero
        (smoothstep na entrada da faixa) — sem degrau de velocidade."""
        cfg = _cfg()
        assist = BottomAssist(cfg)
        entry = cfg.bottom_edge - cfg.bottom_band
        assert assist.gain(entry, 0.20) == pytest.approx(1.0)
        assert assist.gain(entry + 0.001, 0.20) == pytest.approx(1.0, abs=1e-3)


# ---------------------------------------------------------------------
# 7 — Tres distancias, mesma trajetoria
# ---------------------------------------------------------------------

class TestThreeDistances:
    def _travel(self, scale: float, step: float = 0.003, n: int = 120) -> float:
        motion = CursorMotion(_cfg())
        dt = 1 / 60
        start = None
        pos = None
        for i in range(n):
            anchor = (0.15 + step * i, 0.5)
            pos = motion.update(anchor, dt, landmarks=_hand(scale, cx=anchor[0]))
            if start is None:
                start = pos
        assert start is not None and pos is not None
        return pos[0] - start[0]

    def test_far_travels_more_than_neutral_than_near(self):
        far = self._travel(0.12)
        neutral = self._travel(0.22)
        near = self._travel(0.36)
        assert far > neutral > near

    def test_neutral_distance_is_one_to_one(self):
        """Na distancia de referencia E na zona neutra da curva
        balistica, o ganho total e' exatamente 1.0."""
        assert self._travel(0.22, step=0.03, n=20) == pytest.approx(
            0.03 * 19, rel=0.02
        )

    def test_measured_gains_match_the_configuration(self):
        cfg = _cfg()
        neutral = self._travel(0.22, step=0.03, n=20)
        far = self._travel(0.10, step=0.03, n=20)
        near = self._travel(0.40, step=0.03, n=20)
        assert far / neutral == pytest.approx(cfg.gain_far, rel=0.03)
        assert near / neutral == pytest.approx(cfg.gain_near, rel=0.03)


# ---------------------------------------------------------------------
# 8 — Equivalencia 30 / 60 FPS
# ---------------------------------------------------------------------

class TestFrameRateEquivalence:
    def _drive(self, fps: float, seconds: float, scale: float = 0.22):
        motion = CursorMotion(_cfg())
        dt = 1.0 / fps
        frames = int(seconds * fps)
        pos = None
        for i in range(frames + 1):
            t = i * dt
            # Mesma trajetoria FISICA: 0.25 unidades/s na horizontal,
            # 0.18 unidades/s na vertical.
            anchor = (0.20 + 0.25 * t, 0.30 + 0.18 * t)
            pos = motion.update(
                anchor, dt, landmarks=_hand(scale, cx=anchor[0], cy=anchor[1]),
            )
        assert pos is not None
        return pos

    def test_same_trajectory_ends_within_two_pixels(self):
        a = self._drive(30.0, 2.0)
        b = self._drive(60.0, 2.0)
        dx = (a[0] - b[0]) * PX_PER_UNIT_X
        dy = (a[1] - b[1]) * PX_PER_UNIT_Y
        assert math.hypot(dx, dy) <= 2.0

    def test_holds_at_a_different_distance_too(self):
        a = self._drive(30.0, 2.0, scale=0.13)
        b = self._drive(60.0, 2.0, scale=0.13)
        dx = (a[0] - b[0]) * PX_PER_UNIT_X
        dy = (a[1] - b[1]) * PX_PER_UNIT_Y
        assert math.hypot(dx, dy) <= 2.0


# ---------------------------------------------------------------------
# 9 + 10 — Borda inferior: alcance e liberacao
# ---------------------------------------------------------------------

def _to_screen_y(ny: float) -> int:
    t = (ny - MARGIN_TOP) / (1.0 - MARGIN_TOP - MARGIN_BOTTOM)
    t = min(1.0, max(0.0, t))
    return int(t * (SCREEN_H - 1))


class TestBottomReach:
    # 0.015 unidades/frame a 60 FPS = 0.9 unidades/s: um movimento
    # deliberado normal (a fase balistica de Fitts). Um arrasto
    # milimetrico cai POR DESIGN na zona de precisao da curva balistica
    # e nao deve varrer a tela inteira numa passada so.
    DESCENT_STEP = 0.015

    def _descend(self, motion: CursorMotion, scale: float, dt: float = 1 / 60):
        """Descida suave da mao, do meio do frame ate perto do fundo."""
        outputs = []
        y = 0.50
        while y < 0.96:
            y += self.DESCENT_STEP
            outputs.append(motion.update((0.5, y), dt, landmarks=_hand(scale, cy=y)))
        return outputs

    def test_reaches_the_last_pixels_with_hand_near(self):
        """Mao PERTO (ganho 0.75) e' o pior caso: sem assistencia o
        movimento relativo nao cobre a tela inteira."""
        motion = CursorMotion(_cfg())
        outputs = self._descend(motion, 0.36)
        assert _to_screen_y(outputs[-1][1]) >= SCREEN_H - 2

    def test_reaches_the_last_pixels_with_hand_far(self):
        motion = CursorMotion(_cfg())
        outputs = self._descend(motion, 0.13)
        assert _to_screen_y(outputs[-1][1]) >= SCREEN_H - 2

    def test_descent_never_goes_backwards(self):
        motion = CursorMotion(_cfg())
        outputs = self._descend(motion, 0.36)
        ys = [o[1] for o in outputs]
        for a, b in zip(ys, ys[1:]):
            assert b >= a - 1e-12

    def test_descent_has_no_teleport(self):
        """Nenhum frame pode saltar: sem edge snap, o maior passo fica
        muito abaixo dos 48px que o snap antigo dava de uma vez."""
        motion = CursorMotion(_cfg())
        outputs = self._descend(motion, 0.36)
        steps = [
            (b[1] - a[1]) * PX_PER_UNIT_Y for a, b in zip(outputs, outputs[1:])
        ]
        # O movimento do proprio usuario ja vale ~12px/frame aqui; a
        # assistencia multiplica no maximo por bottom_max_gain. Longe
        # dos 48px que o edge snap entregava de uma vez.
        assert max(steps) < 30.0

    def test_assist_never_pushes_past_the_edge(self):
        motion = CursorMotion(_cfg())
        outputs = self._descend(motion, 0.36)
        assert all(o[1] <= 1.0 for o in outputs)

    def test_baseline_without_assist_falls_short(self):
        """Justifica a existencia da assistencia: so a margem NAO basta
        com a mao perto da webcam (ganho < 1)."""
        motion = CursorMotion(_cfg(bottom_assist_enabled=False))
        outputs = self._descend(motion, 0.36)
        assert _to_screen_y(outputs[-1][1]) < SCREEN_H - 2

    def test_release_moves_away_within_100ms(self):
        """Caso 10: apos alcancar a borda, subir tem que afastar o cursor
        em ate 100ms — sem ficar preso."""
        motion = CursorMotion(_cfg())
        outputs = self._descend(motion, 0.36)
        at_edge = outputs[-1][1]
        y = 0.96
        dt = 1 / 60
        moved_away_after = None
        for i in range(1, 13):  # 12 frames = 200ms
            y -= self.DESCENT_STEP
            pos = motion.update((0.5, y), dt, landmarks=_hand(0.36, cy=y))
            if pos[1] < at_edge - 1e-9:
                moved_away_after = i * dt
                break
        assert moved_away_after is not None
        assert moved_away_after <= 0.100

    def test_upward_motion_cancels_assist_immediately(self):
        cfg = _cfg()
        assist = BottomAssist(cfg)
        y = cfg.bottom_edge - 0.02
        assert assist.gain(y, 0.20) > 1.0
        # Mesma posicao, sentido invertido: ganho volta a 1.0 no MESMO
        # frame — sem rampa de saida, sem cursor preso.
        assert assist.gain(y, -0.20) == 1.0

    def test_static_hand_gets_no_assist(self):
        cfg = _cfg()
        assist = BottomAssist(cfg)
        assert assist.gain(cfg.bottom_edge - 0.005, 0.0) == 1.0

    def test_gain_is_monotonic_in_proximity(self):
        cfg = _cfg()
        assist = BottomAssist(cfg)
        gains = [
            assist.gain(cfg.bottom_edge - cfg.bottom_band + i * 0.005, 0.20)
            for i in range(40)
        ]
        for a, b in zip(gains, gains[1:]):
            assert b >= a - 1e-12
        assert gains[0] == pytest.approx(1.0)
        assert gains[-1] > 1.0

    def test_gain_never_exceeds_max_gain(self):
        cfg = _cfg()
        assist = BottomAssist(cfg)
        for vy in (0.05, 0.2, 1.0, 50.0):
            for i in range(40):
                y = cfg.bottom_edge - cfg.bottom_band + i * 0.006
                assert assist.gain(y, vy) <= cfg.bottom_max_gain + 1e-9

    def test_extra_speed_is_capped(self):
        """Teto absoluto da velocidade ADICIONADA, mesmo com o usuario
        descendo absurdamente rapido."""
        cfg = _cfg()
        assist = BottomAssist(cfg)
        y = cfg.bottom_edge - 0.002
        for vy in (0.5, 2.0, 10.0, 100.0):
            extra = (assist.gain(y, vy) - 1.0) * vy
            assert extra <= cfg.bottom_max_extra_rate + 1e-9

    def test_assist_is_inert_outside_the_band(self):
        cfg = _cfg()
        assist = BottomAssist(cfg)
        outside = cfg.bottom_edge - cfg.bottom_band - 0.01
        assert assist.gain(outside, 0.5) == 1.0

    def test_disabled_assist_is_inert(self):
        cfg = _cfg(bottom_assist_enabled=False)
        assist = BottomAssist(cfg)
        assert assist.gain(cfg.bottom_edge - 0.01, 0.5) == 1.0


# ---------------------------------------------------------------------
# 11 — Perda da mao
# ---------------------------------------------------------------------

class TestHandLoss:
    def test_short_blink_keeps_position_and_continues(self):
        motion = CursorMotion(_cfg())
        dt = 1 / 60
        for i in range(30):
            motion.update((0.30 + 0.002 * i, 0.5), dt, landmarks=_hand(0.22))
        before = motion.position
        for _ in range(3):  # 50ms sem mao
            motion.notify_hand_lost(dt)
        assert motion.position == before

        # Mao volta no MESMO ponto: continua de onde parou.
        after = motion.update((0.30 + 0.002 * 29, 0.5), dt, landmarks=_hand(0.22))
        assert after == before

    def test_long_loss_reanchors_without_jump(self):
        motion = CursorMotion(_cfg())
        dt = 1 / 60
        for i in range(30):
            motion.update((0.30 + 0.002 * i, 0.5), dt, landmarks=_hand(0.22))
        before = motion.position

        for _ in range(60):  # 1 segundo sem mao
            motion.notify_hand_lost(dt)

        # Mao reaparece do OUTRO LADO do frame: a saida nao pode saltar.
        after = motion.update((0.90, 0.10), dt, landmarks=_hand(0.22))
        assert after == before

        # E o movimento seguinte volta a funcionar normalmente.
        nxt = motion.update((0.91, 0.10), dt, landmarks=_hand(0.22))
        assert nxt[0] > after[0]

    def test_soft_reset_preserves_output(self):
        motion = CursorMotion(_cfg())
        motion.update((0.3, 0.7), 1 / 60, landmarks=_hand(0.22))
        before = motion.position
        motion.soft_reset()
        assert motion.position == before

    def test_hold_freezes_output_and_realigns_input(self):
        """Trocar de shape (OPEN_HAND -> FIST -> OPEN_HAND) preserva
        continuidade: o cursor nao salta quando o gesto volta."""
        motion = CursorMotion(_cfg())
        dt = 1 / 60
        for i in range(20):
            motion.update((0.30 + 0.002 * i, 0.5), dt, landmarks=_hand(0.22))
        frozen = motion.position

        # Mao continua andando durante o congelamento.
        for i in range(30):
            assert motion.hold((0.50 + 0.004 * i, 0.62), dt) == frozen

        # Volta a mover: delta e' medido do ULTIMO ponto de hold.
        nxt = motion.update((0.50 + 0.004 * 29 + 0.01, 0.62), dt,
                            landmarks=_hand(0.22))
        assert frozen is not None
        # Anda apenas o delta DESTE frame (0.01, atenuado pela curva
        # balistica) — nao os 0.116 que a mao percorreu congelada.
        moved = nxt[0] - frozen[0]
        assert 0.0 < moved <= 0.01 + 1e-9


# ---------------------------------------------------------------------
# 6 — Jitter (unidade; a versao com OneEuro esta no teste de integracao)
# ---------------------------------------------------------------------

class TestJitter:
    def test_stationary_noise_does_not_drift(self):
        """Ruido estacionario nao pode virar deriva acumulada.

        O integrador soma DELTAS, que telescopam: com ancora oscilando em
        torno de um ponto fixo, a saida fica presa a vizinhanca dele.
        """
        rng = random.Random(20260729)
        motion = CursorMotion(_cfg())
        dt = 1 / 60
        base = (0.5, 0.5)
        start = motion.update(base, dt, landmarks=_hand(0.22))
        for _ in range(600):  # 10 segundos
            anchor = (
                base[0] + rng.uniform(-0.0015, 0.0015),
                base[1] + rng.uniform(-0.0015, 0.0015),
            )
            motion.update(anchor, dt, landmarks=_hand(0.22))
        pos = motion.position
        assert pos is not None and start is not None
        drift = math.hypot(
            (pos[0] - start[0]) * PX_PER_UNIT_X,
            (pos[1] - start[1]) * PX_PER_UNIT_Y,
        )
        assert drift <= 6.0


# ---------------------------------------------------------------------
# Envelope
# ---------------------------------------------------------------------

class TestEnvelope:
    def test_attack_is_gradual_and_bounded(self):
        env = Envelope(0.10, 0.22)
        dt = 1 / 60
        values = [env.update(1.0, dt) for _ in range(60)]
        assert values[0] < 0.25
        assert values[-1] > 0.99
        for a, b in zip(values, values[1:]):
            assert b >= a

    def test_release_is_slower_than_attack(self):
        up = Envelope(0.10, 0.22)
        for _ in range(120):
            up.update(1.0, 1 / 60)
        frames_down = 0
        while up.value > 0.5 and frames_down < 1000:
            up.update(0.0, 1 / 60)
            frames_down += 1

        down = Envelope(0.10, 0.22)
        frames_up = 0
        while down.value < 0.5 and frames_up < 1000:
            down.update(1.0, 1 / 60)
            frames_up += 1

        assert frames_down > frames_up

    def test_is_frame_rate_independent(self):
        a = Envelope(0.10, 0.22)
        b = Envelope(0.10, 0.22)
        for _ in range(30):
            a.update(1.0, 1 / 30)
        for _ in range(60):
            b.update(1.0, 1 / 60)
        assert a.value == pytest.approx(b.value, abs=0.01)
