"""
core/perf_telemetry.py
======================

Telemetria de performance por estagio do tick loop.

Design:
- Opt-in via config flag (default ON em diagnostico, pode desligar)
- Custo desprezivel quando habilitado (~100ns por stage entry/exit)
- Custo ZERO quando desligado (early-return no contextmanager)
- Rolling window de samples + log periodico de p50/p99
- Sem deps externas

Uso pelo service:
    from core.perf_telemetry import get_profiler

    prof = get_profiler()
    with prof.stage("inference"):
        hands = tracker.process(rgb)
    prof.tick()  # marca fim do frame

Reports automaticos via logger.info a cada N ticks.
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from typing import Deque, Dict, Optional


logger = logging.getLogger(__name__)


class TickProfiler:
    """
    Agregador de timing por estagio. Rolling window com p50/p99.

    Thread-safe? Nao. Usar do thread do main loop.
    """

    def __init__(
        self,
        *,
        window_size: int = 120,
        report_every: int = 120,
    ) -> None:
        if window_size < 1:
            raise ValueError("window_size deve ser >= 1")
        if report_every < 1:
            raise ValueError("report_every deve ser >= 1")
        self._window = window_size
        self._report_every = report_every
        self._stages: Dict[str, Deque[float]] = defaultdict(
            lambda: deque(maxlen=window_size)
        )
        self._tick_count = 0
        self._enabled = True

    @property
    def enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, on: bool) -> None:
        """Liga/desliga em runtime. Quando off, stage() vira no-op puro."""
        self._enabled = bool(on)

    @contextmanager
    def stage(self, name: str):
        """
        Context manager pra timar um estagio nomeado.

        with prof.stage("inference"):
            heavy_work()
        """
        if not self._enabled:
            yield
            return
        start = time.perf_counter()
        try:
            yield
        finally:
            dt_ms = (time.perf_counter() - start) * 1000.0
            self._stages[name].append(dt_ms)

    def tick(self) -> None:
        """Marca fim de um frame. Reports periodicos sao disparados aqui."""
        if not self._enabled:
            return
        self._tick_count += 1
        if self._tick_count % self._report_every == 0:
            self._log_report()

    def snapshot(self) -> Dict[str, Dict[str, float]]:
        """
        Retorna {stage_name: {"p50": ms, "p99": ms, "n": samples}}
        do window atual. Util pra inspecao programatica.
        """
        out: Dict[str, Dict[str, float]] = {}
        for name, samples in self._stages.items():
            if not samples:
                continue
            sorted_s = sorted(samples)
            n = len(sorted_s)
            p50 = sorted_s[n // 2]
            p99 = sorted_s[min(n - 1, int(n * 0.99))]
            out[name] = {"p50": p50, "p99": p99, "n": float(n)}
        return out

    def reset(self) -> None:
        """Limpa todas as samples e zera tick counter."""
        self._stages.clear()
        self._tick_count = 0

    @property
    def tick_count(self) -> int:
        return self._tick_count

    def _log_report(self) -> None:
        snap = self.snapshot()
        if not snap:
            return
        parts = [
            f"{name}={data['p50']:.1f}/{data['p99']:.1f}"
            for name, data in sorted(snap.items())
        ]
        logger.info("PERF p50/p99 ms: %s", " | ".join(parts))


# Singleton global — uma instancia por processo
_global: Optional[TickProfiler] = None


def get_profiler() -> TickProfiler:
    """
    Retorna o profiler global. Lazy-create na primeira chamada.

    Para reconfigurar (ex: no startup do service), use configure().
    """
    global _global
    if _global is None:
        _global = TickProfiler()
    return _global


def configure(
    *,
    window_size: int = 120,
    report_every: int = 120,
    enabled: bool = True,
) -> TickProfiler:
    """
    Reconfigura o profiler global. Substitui a instancia atual.

    Chamar uma vez no startup. Defaults sao razoaveis pra 30+ FPS
    (1 report a cada ~4s).
    """
    global _global
    _global = TickProfiler(
        window_size=window_size,
        report_every=report_every,
    )
    _global.set_enabled(enabled)
    return _global
