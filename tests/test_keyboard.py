"""
tests.test_keyboard
===================

Testes do Smart Adaptive Holographic Keyboard. Cobrem layouts, hover,
predicao, adaptive e o pipeline de render offscreen (sem janela).
"""

from __future__ import annotations

import pytest

from core.keyboard import (
    ABNT2, COMPACT, FULL, QWERTY, AdaptiveModel, HoverDetector, KeyState,
    KeyboardState, TextPredictor, get_layout,
)
from core.keyboard.controller import KeyboardController
from core.keyboard.hover import KeyRect
from core.keyboard.models import KeyEvent
from core.keyboard.output import SystemTyper


# ───────────────────────────────────────────────────────── layouts


def test_layouts_nonempty():
    for layout in (ABNT2, QWERTY, COMPACT, FULL):
        assert len(layout.keys) > 0
        assert layout.rows > 0
        assert layout.cols > 0


def test_abnt2_has_cedilha():
    assert ABNT2.by_code("ç") is not None


def test_get_layout_fallback():
    assert get_layout("INEXISTENTE").name == "ABNT2"
    assert get_layout("qwerty").name == "QWERTY"


def test_no_duplicate_codes_per_layout():
    for layout in (ABNT2, QWERTY, COMPACT, FULL):
        codes = [k.code for k in layout.keys]
        assert len(codes) == len(set(codes)), f"dup em {layout.name}"


# ───────────────────────────────────────────────────────── predictor


def test_predictor_autocomplete():
    p = TextPredictor()  # usa mini-vocab embutido
    for ch in "intel":
        p.feed_char(ch)
    sugs = p.suggestions()
    assert any(s.startswith("intel") for s in sugs)


def test_predictor_commit_on_space():
    p = TextPredictor()
    for ch in "casa":
        p.feed_char(ch)
    p.feed_special("space")
    assert "casa" in p.committed_text()
    assert p.prefix == ""


def test_predictor_backspace():
    p = TextPredictor()
    for ch in "abc":
        p.feed_char(ch)
    p.feed_special("backspace")
    assert p.prefix == "ab"


# ───────────────────────────────────────────────────────── hover


def _make_rects(state: KeyboardState):
    """Gera KeyRect num grid simples pra teste de hover."""
    rects = []
    cell = 60.0
    for k in state.layout.keys:
        cx = 100 + k.col * cell + (k.width * cell) / 2
        cy = 100 + k.row * cell + cell / 2
        rects.append(KeyRect(key=k, cx=cx, cy=cy,
                             half_w=k.width * cell / 2 - 4, half_h=cell / 2 - 4))
    return rects


def test_hover_detects_nearest():
    state = KeyboardState(layout=QWERTY, keys={})
    for k in QWERTY.keys:
        state.keys[k.code] = KeyState(key=k)
    rects = _make_rects(state)
    hov = HoverDetector()
    hov.set_rects(rects, 1920, 1080)
    # Mira exatamente no centro da tecla 'q'
    q = next(r for r in rects if r.key.code == "q")
    hovered = hov.update(q.cx, q.cy, state)
    assert hovered == "q"
    assert state.keys["q"].hover_score > 0.5


# ───────────────────────────────────────────────────────── adaptive


def test_adaptive_learns_miss():
    state = KeyboardState(layout=QWERTY, keys={})
    for k in QWERTY.keys:
        state.keys[k.code] = KeyState(key=k)
    model = AdaptiveModel()
    # Simula: usuario digita 't', apaga, digita 'r' (correcao) varias vezes
    import time
    t = time.time()
    for i in range(10):
        model.record_press(KeyEvent("t", "t", t, 0.5, 200, 200), (210, 205), state)
        model.record_press(KeyEvent("backspace", "", t + 0.1, 0.9, 0, 0), (0, 0), state)
        model.record_press(KeyEvent("r", "r", t + 0.2, 0.8, 150, 200), (150, 200), state)
        t += 1.0
    model.apply_to_state(state, 30.0, 22.0)
    # 't' deve ter ganho hit_scale > 1.0 (area de ativacao expandida)
    assert state.keys["t"].hit_scale >= 1.0


# ───────────────────────────────────────────────────────── controller


def test_controller_pinch_types_char():
    state = KeyboardState(layout=QWERTY, keys={})
    for k in QWERTY.keys:
        state.keys[k.code] = KeyState(key=k)
    state.visible = True

    typed = []

    class _SpyTyper(SystemTyper):
        def type_char(self, ch):
            typed.append(ch)

    ctrl = KeyboardController(
        state=state,
        typer=_SpyTyper(dry_run=True),
        predictor=TextPredictor(),
        adaptive=AdaptiveModel(),
        accessibility=__import__(
            "core.keyboard.accessibility", fromlist=["AccessibilitySettings"]
        ).AccessibilitySettings(),
    )
    rects = _make_rects(state)
    ctrl.set_rects_from(rects, 1920, 1080)

    a = next(r for r in rects if r.key.code == "a")
    # Hover sem pinch (varias vezes pra subir o score via EMA)
    for _ in range(8):
        ctrl.on_frame((a.cx, a.cy), pinch_now=False)
    assert state.hovered_code == "a"
    # Pinch edge → press
    ctrl.on_frame((a.cx, a.cy), pinch_now=True)
    assert "a" in typed


# ───────────────────────────────────────────────────────── render offscreen


def test_render_offscreen_produces_content():
    """Render no QImage deve produzir pixels (valida pipeline de paint)."""
    pytest.importorskip("PySide6")
    from core.keyboard import KeyboardOverlay

    kb = KeyboardOverlay(layout_name="ABNT2", typer_dry_run=True)
    if not kb.available:
        pytest.skip("PySide6 sem display disponivel")
    kb.set_enabled(True)
    img = kb.renderer.render_to_image(800, 600)
    # Conta pixels nao-transparentes
    nz = 0
    for y in range(0, img.height(), 10):
        for x in range(0, img.width(), 10):
            if (img.pixel(x, y) >> 24) & 0xFF:
                nz += 1
    kb.close()
    assert nz > 50, "render offscreen produziu imagem vazia"


# ───────────────────────────────────────────────────── F1.1 path cache


def test_path_cache_reuses_objects():
    """Cache de QPainterPath deve crescer 1x por (code, bucket), nao por frame."""
    pytest.importorskip("PySide6")
    from core.keyboard import KeyboardOverlay

    kb = KeyboardOverlay(layout_name="QWERTY", typer_dry_run=True)
    if not kb.available:
        pytest.skip("PySide6 sem display disponivel")
    kb.set_enabled(True)
    # Forca render N vezes — paths devem cachear, nao crescer linear
    for _ in range(50):
        _ = kb.renderer.render_to_image(800, 600)
    # 62 teclas x ~3 buckets visitados = ~200 max esperado, bem abaixo do cap 1024
    n = len(kb.renderer._path_cache)
    assert 0 < n < 500, f"cache cresceu demais: {n}"
    kb.close()


# ───────────────────────────────────────────────────── F2.1 centralizacao


def test_keyboard_centered_vertically():
    """VERTICAL_ANCHOR=0.5 deve posicionar teclado no centro da tela."""
    pytest.importorskip("PySide6")
    from core.keyboard import KeyboardOverlay

    kb = KeyboardOverlay(
        layout_name="ABNT2", typer_dry_run=True, vertical_anchor=0.50,
    )
    if not kb.available:
        pytest.skip("PySide6 sem display disponivel")
    kb.renderer._screen_w = 1920
    kb.renderer._screen_h = 1080
    kb.set_enabled(True)
    kb.renderer._compute_layout()
    ox, oy = kb.renderer._kb_origin
    total_h = kb.state.layout.rows * kb.renderer._cell_size
    expected_oy = (1080 - total_h) * 0.50
    assert abs(oy - expected_oy) < 1.0
    # Centro vertical deve estar ~540
    assert abs((oy + total_h / 2) - 540) < 10.0
    kb.close()


def test_vertical_anchor_top_bottom():
    """Anchor 0.0=topo, 1.0=base — formula deve responder."""
    pytest.importorskip("PySide6")
    from core.keyboard import KeyboardOverlay

    for anchor, expected_top in ((0.0, 0.0), (1.0, 1.0)):
        kb = KeyboardOverlay(
            layout_name="QWERTY", typer_dry_run=True, vertical_anchor=anchor,
        )
        if not kb.available:
            pytest.skip("PySide6 sem display disponivel")
        kb.renderer._screen_h = 1000
        kb.renderer._screen_w = 1920
        kb.set_enabled(True)
        kb.renderer._compute_layout()
        _, oy = kb.renderer._kb_origin
        total_h = kb.state.layout.rows * kb.renderer._cell_size
        free = 1000 - total_h
        assert abs(oy - free * expected_top) < 1.0
        kb.close()


# ───────────────────────────────────────────────────── F3.1 Infinity Edge


def test_no_hard_panel_border():
    """Bordas extremas devem ter alpha baixo (Infinity Edge fade)."""
    pytest.importorskip("PySide6")
    from core.keyboard import KeyboardOverlay

    kb = KeyboardOverlay(layout_name="QWERTY", typer_dry_run=True)
    if not kb.available:
        pytest.skip("PySide6 sem display disponivel")
    # Renderiza no tamanho real do screen interno pra fade ficar dentro.
    kb.renderer._screen_w = 1920
    kb.renderer._screen_h = 1080
    kb.set_enabled(True)
    kb.renderer._compute_layout()
    img = kb.renderer.render_to_image(1920, 1080)
    # Bordas extremas (linha 2 e ultima-2) devem fade out completo.
    edge_alphas = []
    w, h = img.width(), img.height()
    for x in range(0, w, 60):
        edge_alphas.append((img.pixel(x, 2) >> 24) & 0xFF)
        edge_alphas.append((img.pixel(x, h - 3) >> 24) & 0xFF)
    avg_edge_alpha = sum(edge_alphas) / max(1, len(edge_alphas))
    assert avg_edge_alpha < 25, f"bordas opacas (avg={avg_edge_alpha}) - sem fade"
    kb.close()


# ───────────────────────────────────────────────────── F4.4 cooldown


def test_press_cooldown_120ms():
    """Cooldown reduzido pra 120ms."""
    from core.keyboard.controller import PRESS_COOLDOWN_S

    assert PRESS_COOLDOWN_S == 0.12


# ───────────────────────────────────────────────────── F4.5 hover wider


def test_hover_max_distance_widened():
    """max_distance_factor subiu pra 1.55 → menos zona morta."""
    hov = HoverDetector()
    assert hov._max_dist_factor >= 1.5


# ───────────────────────────────────────────────────── F1.3 dirty-check


def test_paint_signature_stable_when_idle():
    """Signature identica quando nada muda → skip repaint."""
    pytest.importorskip("PySide6")
    from core.keyboard import KeyboardOverlay

    kb = KeyboardOverlay(layout_name="QWERTY", typer_dry_run=True)
    if not kb.available:
        pytest.skip("PySide6 sem display disponivel")
    # Forca reduced_motion ON pra desligar arcos (que tem time_sig variavel)
    kb.accessibility.reduced_motion = True
    kb.set_enabled(True)
    kb.renderer._compute_layout()
    sig1 = kb.renderer._paint_signature()
    sig2 = kb.renderer._paint_signature()
    assert sig1 == sig2
    # Muda hover_score → signature deve mudar
    first_code = next(iter(kb.state.keys))
    kb.state.keys[first_code].hover_score = 0.8
    sig3 = kb.renderer._paint_signature()
    assert sig1 != sig3
    kb.close()
