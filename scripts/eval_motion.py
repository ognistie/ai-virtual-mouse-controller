"""
scripts/eval_motion.py
======================

Harness de avaliacao do "feel" do cursor — roda o pipeline REAL
(GestureDetector -> smoother -> CursorController) com maos sinteticas,
relogio simulado e PyAutoGUI falso. Nao abre webcam nem mexe no mouse.

Serve pra comparar baseline vs candidato de forma reproduzivel antes de
mexer em parametros de suavizacao/gestos. Cada cenario roda com varias
seeds de ruido (jitter de landmark tipico do MediaPipe) e reporta media
e desvio padrao.

Uso:
    python scripts/eval_motion.py                 # tabela no terminal
    python scripts/eval_motion.py --json out.json # salva metricas
    python scripts/eval_motion.py --seeds 20

Metricas (todas em pixels de uma tela 1920x1080 simulada):
    rest_jitter_px          desvio padrao do cursor com a mao parada
    rest_path_px_s          quanto o cursor "passeia" por segundo parado
    move_lag_px             atraso medio do smoother durante um movimento
    move_settle_ms          tempo pro cursor assentar apos a mao parar
    move_overshoot_px       quanto o cursor passa do ponto final
    slow_max_step_px        maior salto num movimento lento (escadinha)
    click_drift_px          distancia entre o clique e onde a mao mirava
    flicker_clicks          cliques gerados por UMA pinca com ruido na borda
    flicker_drag_drops      drags interrompidos por ruido na pinca
    repeat_clicks           2 pincas intencionais seguidas (esperado: 2)
    click_move_max_step_px  maior salto ao destravar apos clicar andando
    tilt_path_px            cursor andando so por inclinar o pulso (mao parada)
    edge_overshoot_px       cursor passando da mao na borda lateral e voltando
    edge_reach_px           distancia que faltou pra borda lateral da tela
    bottom_reach_px         distancia que faltou pro fundo da tela
    bottom_overshoot_px     recuo do cursor apos descer ate o fundo

Gate automatizado com os mesmos cenarios: tests/test_motion_quality.py.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from contextlib import ExitStack
from itertools import pairwise
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from core import cursor_controller as cc_mod  # noqa: E402
from core import gesture_detector as gd_mod  # noqa: E402
from core import smoothing as sm_mod  # noqa: E402
from core.cursor_controller import CursorController  # noqa: E402
from core.gesture_detector import Gesture  # noqa: E402
from core.hand_tracker import HandLandmarks  # noqa: E402
from core.runtime_settings import RuntimeSettings  # noqa: E402
from core.smoothing import make_smoother  # noqa: E402
from tests.synthetic_hand import FakeClock, make_hand, min_jerk  # noqa: E402

SCREEN_W, SCREEN_H = 1920, 1080
CAMERA_FPS = 30.0


# ---------------------------------------------------------------------
# Pipeline com relogio e PyAutoGUI falsos
# ---------------------------------------------------------------------


class _FakePyAutoGUI:
    FAILSAFE = False
    PAUSE = 0.0

    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock
        self.x = SCREEN_W // 2
        self.y = SCREEN_H // 2
        self.clicks: List[Tuple[str, int, int]] = []

    def size(self):
        return SCREEN_W, SCREEN_H

    def position(self):
        return self.x, self.y

    def moveTo(self, x, y, *a, **k):
        self.x, self.y = int(x), int(y)

    def _at(self, kind, x=None, y=None):
        if x is not None and y is not None:
            self.x, self.y = int(x), int(y)
        self.clicks.append((kind, self.x, self.y))

    def click(self, x=None, y=None, *a, **k):
        self._at("click", x, y)

    def rightClick(self, x=None, y=None, *a, **k):
        self._at("right", x, y)

    def mouseDown(self, x=None, y=None, *a, **k):
        self._at("down", x, y)

    def mouseUp(self, x=None, y=None, *a, **k):
        self._at("up", x, y)

    def press(self, *a, **k):
        pass

    def scroll(self, *a, **k):
        pass


class Pipeline:
    """Replica o caminho do VirtualMouseService._tick sem camera/UI."""

    def __init__(self, ticks_per_frame: int = 1) -> None:
        self.clock = FakeClock()
        self.gui = _FakePyAutoGUI(self.clock)
        self._stack = ExitStack()
        for mod in (gd_mod, sm_mod, cc_mod):
            self._stack.enter_context(mock.patch.object(mod, "time", self.clock))
        self._stack.enter_context(mock.patch.object(cc_mod, "pyautogui", self.gui))

        # Mesma construcao do service (detector/smoother/cursor).
        from services.virtual_mouse_service import VirtualMouseService

        self.runtime = RuntimeSettings(initial_profile=getattr(config, "PROFILE", "smooth"))
        self.detector = VirtualMouseService._build_detector(self.runtime)
        self.smoother = make_smoother(
            config.SMOOTHING_STRATEGY,
            alpha=config.SMOOTHING_FACTOR,
            freq=config.ONE_EURO_FREQ,
            min_cutoff=self.runtime.get("one_euro_min_cutoff"),
            beta=self.runtime.get("one_euro_beta"),
            d_cutoff=config.ONE_EURO_D_CUTOFF,
        )
        self.cursor = self._build_cursor()
        self.ticks_per_frame = ticks_per_frame
        self.trace: List[dict] = []
        self.events: List[Tuple[float, Gesture]] = []

    def _build_cursor(self) -> CursorController:
        return CursorController(
            screen_margin_percentage=config.SCREEN_MARGIN_PERCENTAGE,
            screen_margin_x=config.SCREEN_MARGIN_X,
            screen_margin_top=config.SCREEN_MARGIN_TOP,
            screen_margin_bottom=config.SCREEN_MARGIN_BOTTOM,
            dead_zone_pixels=config.DEAD_ZONE_PIXELS,
            click_freeze_seconds=(self.runtime.get("anchor_freeze_duration_ms") / 1000.0),
            drag_precision_factor=config.DRAG_PRECISION_FACTOR,
            y_bottom_boost_enabled=config.CURSOR_Y_BOTTOM_BOOST_ENABLED,
            y_bottom_boost_knee=config.CURSOR_Y_BOTTOM_BOOST_KNEE,
            y_bottom_boost_power=config.CURSOR_Y_BOTTOM_BOOST_POWER,
            bottom_reach_enabled=config.CURSOR_BOTTOM_REACH_ENABLED,
            bottom_reach_band_px=config.CURSOR_BOTTOM_REACH_BAND_PX,
            bottom_reach_max_gain=config.CURSOR_BOTTOM_REACH_MAX_GAIN,
            bottom_reach_max_extra_px=config.CURSOR_BOTTOM_REACH_MAX_EXTRA_PX,
        )

    def close(self) -> None:
        self._stack.close()

    def frame(self, hand: Optional[HandLandmarks]) -> None:
        """Um frame de camera (repetido ticks_per_frame vezes, como o
        loop antigo fazia quando rodava mais rapido que a webcam)."""
        dt = 1.0 / CAMERA_FPS / self.ticks_per_frame
        for _ in range(self.ticks_per_frame):
            self.clock.t += dt
            events = self.detector.update(hand)
            smooth_in = smooth_out = None
            for ev in events:
                if ev.gesture == Gesture.MOVE and ev.position is not None:
                    smooth_in = ev.position
                    smooth_out = self.smoother(*ev.position)
                    self.cursor.move(*smooth_out)
            for ev in events:
                self.events.append((self.clock.t, ev.gesture))
                g = ev.gesture
                if g == Gesture.CLICK:
                    self.cursor.click()
                elif g == Gesture.DOUBLE_CLICK:
                    self.cursor.double_click()
                elif g == Gesture.RIGHT_CLICK:
                    self.cursor.right_click()
                elif g == Gesture.DRAG_START:
                    self.cursor.drag_start()
                elif g == Gesture.DRAG_END:
                    self.cursor.drag_end()
            self.trace.append(
                {
                    "t": self.clock.t,
                    "x": self.gui.x,
                    "y": self.gui.y,
                    "sin": smooth_in,
                    "sout": smooth_out,
                }
            )


def _px(p: Tuple[float, float]) -> Tuple[float, float]:
    return p[0] * SCREEN_W, p[1] * SCREEN_H


# ---------------------------------------------------------------------
# Cenarios
# ---------------------------------------------------------------------


def scenario_rest(seed: int, tpf: int) -> Dict[str, float]:
    rng = random.Random(seed)
    p = Pipeline(tpf)
    try:
        n = int(3.0 * CAMERA_FPS)
        for _ in range(n):
            p.frame(make_hand(0.5, 0.5, rng=rng))
        tail = p.trace[int(len(p.trace) * 0.3) :]
        xs = [s["x"] for s in tail]
        ys = [s["y"] for s in tail]
        jitter = math.sqrt(statistics.pvariance(xs) + statistics.pvariance(ys))
        path = sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for a, b in pairwise(tail))
        dur = tail[-1]["t"] - tail[0]["t"]
        return {"rest_jitter_px": jitter, "rest_path_px_s": path / max(dur, 1e-6)}
    finally:
        p.close()


def scenario_move(seed: int, tpf: int) -> Dict[str, float]:
    """Alcance rapido: 25% do frame em 450 ms, depois segura 1 s."""
    rng = random.Random(seed)
    p = Pipeline(tpf)
    try:
        for _ in range(int(0.6 * CAMERA_FPS)):
            p.frame(make_hand(0.35, 0.5, rng=rng))
        start = len(p.trace)
        n_move = int(0.45 * CAMERA_FPS)
        for i in range(1, n_move + 1):
            x = 0.35 + 0.25 * min_jerk(i / n_move)
            p.frame(make_hand(x, 0.5, rng=rng))
        move_end_t = p.clock.t
        move_end = len(p.trace)
        for _ in range(int(1.0 * CAMERA_FPS)):
            p.frame(make_hand(0.60, 0.5, rng=rng))

        moving = [s for s in p.trace[start:move_end] if s["sin"] is not None]
        lag = (
            statistics.fmean(math.dist(_px(s["sin"]), _px(s["sout"])) for s in moving)
            if moving
            else 0.0
        )

        final_x = statistics.fmean(s["x"] for s in p.trace[-10:])
        settle_t = move_end_t
        for s in reversed(p.trace[move_end:]):
            if abs(s["x"] - final_x) > 3.0:
                settle_t = s["t"]
                break
        overshoot = max(0.0, max(s["x"] for s in p.trace[start:]) - final_x)
        return {
            "move_lag_px": lag,
            "move_settle_ms": (settle_t - move_end_t) * 1000.0,
            "move_overshoot_px": overshoot,
        }
    finally:
        p.close()


def scenario_slow(seed: int, tpf: int) -> Dict[str, float]:
    """Ajuste fino: 4% do frame por segundo durante 2 s."""
    rng = random.Random(seed)
    p = Pipeline(tpf)
    try:
        for _ in range(int(0.6 * CAMERA_FPS)):
            p.frame(make_hand(0.45, 0.5, rng=rng))
        start = len(p.trace)
        n = int(2.0 * CAMERA_FPS)
        for i in range(n):
            p.frame(make_hand(0.45 + 0.04 * (i / CAMERA_FPS), 0.5, rng=rng))
        seg = p.trace[start:]
        steps = [abs(b["x"] - a["x"]) for a, b in pairwise(seg)]
        travel = seg[-1]["x"] - seg[0]["x"]
        return {"slow_max_step_px": max(steps), "slow_travel_px": travel}
    finally:
        p.close()


def scenario_click(seed: int, tpf: int) -> Dict[str, float]:
    """Mira parada, fecha a pinca em ~130 ms (o curl desloca a ancora)."""
    rng = random.Random(seed)
    p = Pipeline(tpf)
    try:
        for _ in range(int(1.0 * CAMERA_FPS)):
            p.frame(make_hand(0.5, 0.5, rng=rng))
        aim = (p.gui.x, p.gui.y)
        n_close = 4
        for i in range(1, n_close + 1):
            p.frame(make_hand(0.5, 0.5, pinch=i / n_close, rng=rng))
        for _ in range(int(0.2 * CAMERA_FPS)):
            p.frame(make_hand(0.5, 0.5, pinch=1.0, rng=rng))
        clicks = [c for c in p.gui.clicks if c[0] == "click"]
        if not clicks:
            return {"click_drift_px": float("nan"), "click_count": 0.0}
        _, cx, cy = clicks[0]
        return {
            "click_drift_px": math.dist(aim, (cx, cy)),
            "click_count": float(len(clicks)),
        }
    finally:
        p.close()


def scenario_flicker(seed: int, tpf: int) -> Dict[str, float]:
    """Pinca segurada 2.5 s com a distancia oscilando PERTO do limiar.

    Esperado: 1 clique + 1 drag continuo. Cada clique extra ou drag
    interrompido e' um erro que o usuario sente.
    """
    rng = random.Random(seed)
    p = Pipeline(tpf)
    try:
        for _ in range(int(0.8 * CAMERA_FPS)):
            p.frame(make_hand(0.5, 0.5, rng=rng))
        # pinch=0.80 deixa polegar-indicador ~ no limiar adaptativo.
        n = int(2.5 * CAMERA_FPS)
        for _ in range(n):
            amount = 0.86 + rng.gauss(0.0, 0.035)
            p.frame(make_hand(0.5, 0.5, pinch=min(1.0, amount), rng=rng))
        for _ in range(int(0.5 * CAMERA_FPS)):
            p.frame(make_hand(0.5, 0.5, rng=rng))
        clicks = sum(1 for _, g in p.events if g == Gesture.CLICK)
        drag_ends = sum(1 for _, g in p.events if g == Gesture.DRAG_END)
        drag_starts = sum(1 for _, g in p.events if g == Gesture.DRAG_START)
        return {
            "flicker_clicks": float(clicks),
            "flicker_drag_drops": float(max(0, drag_ends - 1) + max(0, drag_starts - 1)),
        }
    finally:
        p.close()


def scenario_repeat(seed: int, tpf: int) -> Dict[str, float]:
    """Dois cliques intencionais seguidos (abre ~metade entre eles).

    Caminho valido que NAO pode regredir com a histerese da pinca:
    esperado exatamente 2 cliques.
    """
    rng = random.Random(seed)
    p = Pipeline(tpf)
    try:
        for _ in range(int(0.8 * CAMERA_FPS)):
            p.frame(make_hand(0.5, 0.5, rng=rng))
        for _ in range(2):
            for amount in (0.5, 0.8, 1.0, 1.0, 1.0, 1.0, 0.8, 0.5, 0.5, 0.5):
                p.frame(make_hand(0.5, 0.5, pinch=amount, rng=rng))
        for _ in range(int(0.3 * CAMERA_FPS)):
            p.frame(make_hand(0.5, 0.5, rng=rng))
        clicks = sum(1 for _, g in p.events if g == Gesture.CLICK)
        return {"repeat_clicks": float(clicks)}
    finally:
        p.close()


def scenario_click_move(seed: int, tpf: int) -> Dict[str, float]:
    """Clica e ja sai andando (caso comum: clicar num menu e seguir).

    Durante a trava pos-clique a mao continua andando; ao destravar o
    cursor nao deveria saltar. Mede o maior salto por frame depois do
    clique comparado ao passo tipico do proprio movimento.
    """
    rng = random.Random(seed)
    p = Pipeline(tpf)
    try:
        for _ in range(int(0.8 * CAMERA_FPS)):
            p.frame(make_hand(0.45, 0.5, rng=rng))
        for amount in (0.4, 0.8, 1.0):
            p.frame(make_hand(0.45, 0.5, pinch=amount, rng=rng))
        start = len(p.trace)
        x = 0.45
        for i in range(int(0.5 * CAMERA_FPS)):
            amount = max(0.0, 1.0 - i / 3)
            x += 0.006  # ~0.18 frame/s: deslocamento moderado
            p.frame(make_hand(x, 0.5, pinch=amount, rng=rng))
        seg = p.trace[start - 1 :]
        steps = [abs(b["x"] - a["x"]) for a, b in pairwise(seg)]
        typical = statistics.median(s for s in steps if s > 0) if any(steps) else 0.0
        return {
            "click_move_max_step_px": max(steps),
            "click_move_typical_step_px": typical,
        }
    finally:
        p.close()


def scenario_tilt(seed: int, tpf: int) -> Dict[str, float]:
    """Mao PARADA fora do centro, so inclinando/girando o pulso.

    Inclinar encurta pulso->MCP no frame (tamanho aparente da mao muda
    ~15%) sem a mao sair do lugar. O cursor deveria ficar parado; quanto
    ele anda aqui e' movimento que o usuario nao fez.
    """
    rng = random.Random(seed)
    p = Pipeline(tpf)
    try:
        cx, cy = 0.74, 0.40
        for _ in range(int(0.8 * CAMERA_FPS)):
            p.frame(make_hand(cx, cy, rng=rng))
        start = (p.gui.x, p.gui.y)
        mark = len(p.trace)
        n = int(2.0 * CAMERA_FPS)
        for i in range(n):
            # inclina e volta (~0.6 s por ciclo)
            size = 0.22 * (1.0 - 0.075 * (1 - math.cos(2 * math.pi * i / 18)))
            size *= 1.0 + rng.gauss(0.0, 0.02)  # ruido do tamanho aparente
            p.frame(make_hand(cx, cy, rng=rng, size=size))
        seg = p.trace[mark:]
        path = sum(math.hypot(b["x"] - a["x"], b["y"] - a["y"]) for a, b in pairwise(seg))
        worst = max(math.dist(start, (s["x"], s["y"])) for s in seg)
        return {"tilt_path_px": path, "tilt_max_px": worst}
    finally:
        p.close()


def scenario_side_edge(seed: int, tpf: int) -> Dict[str, float]:
    """Leva a mao ate a borda lateral do frame (dedos saindo do quadro).

    O cursor deve chegar na borda da tela SEM passar da mao e voltar
    (overshoot = cursor "mais rapido que a mao").
    """
    rng = random.Random(seed)
    p = Pipeline(tpf)
    try:
        for _ in range(int(0.6 * CAMERA_FPS)):
            p.frame(make_hand(0.55, 0.5, rng=rng))
        start = len(p.trace)
        n = int(0.4 * CAMERA_FPS)
        for i in range(1, n + 1):
            p.frame(make_hand(0.55 + 0.39 * min_jerk(i / n), 0.5, rng=rng))
        for _ in range(int(1.0 * CAMERA_FPS)):
            p.frame(make_hand(0.94, 0.5, rng=rng))
        xs = [s["x"] for s in p.trace[start:]]
        final = statistics.fmean(xs[-8:])
        return {
            "edge_overshoot_px": max(xs) - final,
            "edge_reach_px": (SCREEN_W - 1) - final,
        }
    finally:
        p.close()


def scenario_bottom(seed: int, tpf: int) -> Dict[str, float]:
    """Desce a mao ate a borda inferior do frame (caminho da taskbar)."""
    rng = random.Random(seed)
    p = Pipeline(tpf)
    try:
        for _ in range(int(0.6 * CAMERA_FPS)):
            p.frame(make_hand(0.5, 0.55, rng=rng))
        n = int(0.8 * CAMERA_FPS)
        for i in range(1, n + 1):
            y = 0.55 + 0.33 * min_jerk(i / n)
            p.frame(make_hand(0.5, y, rng=rng))
        start_hold = len(p.trace)
        for _ in range(int(1.0 * CAMERA_FPS)):
            p.frame(make_hand(0.5, 0.88, rng=rng))
        ys = [s["y"] for s in p.trace]
        peak = max(ys)
        final = statistics.fmean(s["y"] for s in p.trace[-8:])
        return {
            "bottom_reach_px": (SCREEN_H - 1) - peak,
            "bottom_overshoot_px": max(0.0, peak - final),
            "bottom_hold_jitter_px": statistics.pstdev(s["y"] for s in p.trace[start_hold + 10 :]),
        }
    finally:
        p.close()


SCENARIOS: Dict[str, Callable[[int, int], Dict[str, float]]] = {
    "rest": scenario_rest,
    "move": scenario_move,
    "slow": scenario_slow,
    "click": scenario_click,
    "flicker": scenario_flicker,
    "repeat": scenario_repeat,
    "click_move": scenario_click_move,
    "tilt": scenario_tilt,
    "side_edge": scenario_side_edge,
    "bottom": scenario_bottom,
}


def run(seeds: int, tpf: int) -> Dict[str, Dict[str, float]]:
    agg: Dict[str, List[float]] = {}
    for fn in SCENARIOS.values():
        for seed in range(seeds):
            for k, v in fn(seed, tpf).items():
                agg.setdefault(k, []).append(v)
    out: Dict[str, Dict[str, float]] = {}
    for k, vals in agg.items():
        clean = [v for v in vals if not math.isnan(v)]
        out[k] = {
            "mean": statistics.fmean(clean) if clean else float("nan"),
            "std": statistics.pstdev(clean) if len(clean) > 1 else 0.0,
            "n": float(len(clean)),
        }
    return out


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument(
        "--ticks-per-frame",
        type=int,
        default=1,
        help="2 simula o loop rodando 2x mais rapido que a webcam " "(frames duplicados).",
    )
    ap.add_argument("--json", type=Path, default=None)
    args = ap.parse_args(argv)

    import logging

    logging.disable(logging.CRITICAL)

    res = run(args.seeds, args.ticks_per_frame)
    width = max(len(k) for k in res)
    for k, v in res.items():
        print(f"{k:<{width}}  {v['mean']:9.2f}  ± {v['std']:.2f}")
    if args.json:
        args.json.write_text(json.dumps(res, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
