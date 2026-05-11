# v7 Implementation — Peace as Primary Click Gesture

## 🎯 Summary

**Inverted gesture semantics** for better ergonomics and fewer false positives:

- **Old (v6)**: PINCH 🤏 (polegar+indicador) = Click | PEACE ✌️ (dois dedos) = Double-click
- **New (v7)**: PEACE ✌️ (dois dedos) = Click | PEACE 2x rápido = Double-click | PINCH = DPI only

---

## 🔧 Changes Made

### 1. **config.py**

Renamed and added new parameters:
```python
# REMOVED:
# - CLICK_COOLDOWN_SECONDS
# - DOUBLE_CLICK_COOLDOWN_SECONDS (2.5s)
# - DOUBLE_CLICK_WINDOW_SECONDS

# ADDED:
PEACE_CLICK_COOLDOWN_SECONDS: float = 0.4
"""Tempo minimo entre cliques simples (anti spam) para o gesto PEACE."""

DOUBLE_CLICK_TIMEOUT_SECONDS: float = 0.35
"""
Janela de tempo para reconhecer dois cliques PEACE rapidos como duplo clique.
Se o usuario faz ✌️ (clique), solta, e faz ✌️ novamente em ate 0.35s,
isso dispara um DOUBLE_CLICK de verdade (dois cliques do sistema).
"""
```

### 2. **core/gesture_detector.py**

#### Updated `__init__` signature:
```python
def __init__(
    self,
    # ... existing params ...
    peace_click_cooldown: float = 0.4,
    double_click_timeout: float = 0.35,
    # ... removed: click_cooldown, double_click_cooldown, double_click_window
)
```

#### Removed state variables:
```python
# REMOVED:
self._pinch_started_at: Optional[float] = None
self._drag_active: bool = False
self._last_double_click_time: float = 0.0

# KEPT (renamed):
self._last_peace_click_time: float = 0.0  # was _last_double_click_time
self._last_click_time: float = 0.0        # for CLICK cooldown (was for pinch)
```

#### Completely rewrote `_process_action()`:

**Old logic (v6):**
1. SAIU de PEACE → emit DOUBLE_CLICK
2. SAIU de PINCH → emit CLICK or DRAG_END
3. ENTRA em PEACE → mark time
4. ENTRA em PINCH → mark time, maybe DRAG_START

**New logic (v7):**
1. SAIU de PEACE → emit CLICK or DOUBLE_CLICK (based on time since last click)
2. ENTRA em PEACE → mark time
3. PINCH → return None (no action, only DPI)

Key implementation:
```python
if previous_shape == HandShape.PEACE and shape != HandShape.PEACE:
    # Check if this is a double-click (second click within timeout)
    time_since_last_peace_click = now - self._last_peace_click_time
    
    if time_since_last_peace_click < self.double_click_timeout:
        # DOUBLE_CLICK! (two clicks within 0.35s)
        event = GestureEvent(Gesture.DOUBLE_CLICK, anchor, now)
    else:
        # Simple CLICK
        event = GestureEvent(Gesture.CLICK, anchor, now)
```

#### Updated property:
```python
# REMOVED:
@property
def double_click_cooldown_remaining(self) -> float:

# ADDED:
@property
def double_click_timeout_remaining(self) -> float:
    """Remaining time window to trigger double-click."""
```

### 3. **services/virtual_mouse_service.py**

#### Updated factory method:
```python
gesture_detector = GestureDetector(
    # ...
    peace_click_cooldown=config.PEACE_CLICK_COOLDOWN_SECONDS,
    double_click_timeout=config.DOUBLE_CLICK_TIMEOUT_SECONDS,
    # REMOVED:
    # - click_cooldown
    # - double_click_cooldown
    # - double_click_window
)
```

#### Updated overlay:
```python
# OLD:
dbl_cd = self.gesture_detector.double_click_cooldown_remaining
if dbl_cd > 0:
    cv2.putText(frame, f"DBL-CD: {dbl_cd:.1f}s", ...)

# NEW:
dbl_timeout = self.gesture_detector.double_click_timeout_remaining
if dbl_timeout > 0:
    cv2.putText(frame, f"DBL-TIMEOUT: {dbl_timeout:.1f}s", ...)
```

#### Updated footer help text:
```python
# OLD:
"ESC=quit | Open=move | Pinch=click | Pinch 2s=drag | Peace=2click | Fist=freeze"

# NEW:
"ESC=quit | Open=move | Peace=click | Peace 2x=dblclick | Fist=freeze"
```

### 4. **README.md**

- Updated version: v3 → v7
- Updated gesture table with new semantics
- Updated "Novidades" section
- Updated "A grande sacada" explanation
- Updated "Por que Peace e não Pinch?" comparison table
- Updated decision table in architecture section

---

## 🧪 Testing Checklist

- [ ] PEACE rápido = CLICK simples
- [ ] PEACE 2x (< 0.35s) = DOUBLE_CLICK (dois cliques do sistema)
- [ ] PEACE após PINCH = sem falsos positivos
- [ ] PINCH = apenas DPI adaptativo (sem clique)
- [ ] OPEN_HAND = mover cursor (sem mudanças)
- [ ] FIST = freeze cursor
- [ ] Overlay mostra "DBL-TIMEOUT" corretamente
- [ ] Footer help text está atualizado

---

## 🚀 Why v7 is Better

### Problem (v6)
- **Pinch ambiguity**: Natural thumb movement when using two fingers causes false positives
- **Click delays**: Having to wait for gesture timeout was slow
- **Drag removed**: Couldn't do intuitive drag gestures

### Solution (v7)
- **Peace is unique**: ✌️ gesture is clear, intentional, low false-positive
- **Double-click is natural**: Two quick Peace gestures mimics real mouse behavior
- **Pinch is safe**: Now only used for DPI, no false clicks during transitions
- **Faster responses**: No waiting for timeouts on standard clicks

### Ergonomics
| Aspect | v6 (Pinch) | v7 (Peace) |
|--------|-----------|-----------|
| False positives | Medium-High | Very Low |
| Gesture naturalness | Awkward | Natural (👍→✌️) |
| Double-click UX | Complex (👍→✊→👍) | Simple (✌️✌️) |
| Hand fatigue | Medium | Low |
| Learning curve | Steep | Gentle |

---

## 📝 Notes

- No breaking changes to the cursor controller or hand tracker
- DPI adaptive feature preserved (works with PINCH detection)
- All smoothing and camera parameters unchanged
- Tests may need updating to reflect new gesture semantics
