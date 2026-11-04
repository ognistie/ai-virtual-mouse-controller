"""
main.py
=======

Entry point do AI Virtual Mouse Controller.

Uso:
    avmc            (comando instalado pelo pip)
    py -m avmc      (Windows, funciona mesmo sem a pasta Scripts no PATH)
    python main.py  (a partir do repositorio)

Opcoes:
    --list-cameras  lista as webcams encontradas e sai
    --camera N      usa a webcam N e lembra a escolha nas proximas vezes

Sai com codigo:
    0 = ok
    1 = falha de webcam
    2 = erro inesperado
"""

from __future__ import annotations

import argparse
import logging
import sys
from typing import Optional, Sequence


def _check_environment() -> None:
    """Verifica o mediapipe ANTES de qualquer import do projeto.

    O projeto usa a Tasks API (HandLandmarker). Se o import falhar,
    mostramos mensagem clara com comando de fix em vez de erro obscuro.
    """
    try:
        import mediapipe as mp
    except ImportError:
        sys.stderr.write(
            "\nERRO: mediapipe nao esta instalado.\n"
            "Solucao: pip install ai-virtual-mouse-controller\n\n"
        )
        sys.exit(1)

    try:
        from mediapipe.tasks.python.vision import HandLandmarker  # noqa: F401
    except ImportError as e:
        version = getattr(mp, "__version__", "desconhecida")
        sys.stderr.write(
            "\n" + "=" * 70 + "\n"
            f"ERRO: mediapipe {version} sem HandLandmarker ({e}).\n"
            "Esse projeto requer mediapipe 0.10.18 ou mais novo.\n\n"
            "Solucao rapida (copia e cola no terminal):\n\n"
            '  pip install --upgrade "mediapipe>=0.10.18"\n\n'
            "Depois roda 'avmc' de novo.\n"
            + "=" * 70 + "\n\n"
        )
        sys.exit(1)


def _setup_windows_path() -> None:
    """No Windows, poe a pasta do avmc.exe no PATH do usuario (1a execucao).

    Nunca impede o programa de abrir: qualquer falha aqui e' ignorada.
    """
    try:
        from core.windows_path import OPT_OUT_ENV, ensure_scripts_on_path

        added = ensure_scripts_on_path()
    except Exception:
        return
    if added:
        print(
            f"[AVM] Pasta '{added}' adicionada ao PATH do seu usuario.\n"
            "[AVM] Nas proximas vezes, abra um terminal NOVO e digite: avmc\n"
            f"[AVM] (pra nao mexer no PATH, defina {OPT_OUT_ENV}=1)",
            flush=True,
        )


def _setup_logging() -> None:
    import config
    level = getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )


def _parse_args(argv: Optional[Sequence[str]]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="avmc",
        description="AI Virtual Mouse Controller: controle o mouse com a mao.",
    )
    parser.add_argument(
        "--camera", type=int, metavar="N",
        help="usa a webcam N (veja --list-cameras) e salva como padrao",
    )
    parser.add_argument(
        "--list-cameras", action="store_true",
        help="lista as webcams encontradas e sai",
    )
    args = parser.parse_args(argv)
    if args.camera is not None and args.camera < 0:
        parser.error("--camera precisa ser 0 ou maior")
    return args


def _list_cameras() -> int:
    import config
    from core.camera_select import list_cameras, resolve_camera_index

    print("Procurando webcams...", flush=True)
    cameras = list_cameras()
    if not cameras:
        print("Nenhuma webcam encontrada (ou todas ocupadas por outro app).")
        return 1
    current = resolve_camera_index(None, config.CAMERA_INDEX)
    for cam in cameras:
        mark = "  <- em uso" if cam.index == current else ""
        print(f"  {cam.index}: {cam.width}x{cam.height}{mark}")
    print("\nPra trocar: avmc --camera N  (a escolha fica salva)")
    return 0


def _choose_camera(cli_index: Optional[int]) -> int:
    """Indice da webcam; --camera N tambem salva N como padrao."""
    import config
    from core.camera_select import resolve_camera_index, save_camera

    if cli_index is not None:
        try:
            path = save_camera(cli_index)
            print(f"[AVM] Camera {cli_index} salva como padrao ({path})", flush=True)
        except OSError as e:
            print(f"[AVM] Nao consegui salvar a camera padrao: {e}", flush=True)
    return resolve_camera_index(cli_index, config.CAMERA_INDEX)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = _parse_args(argv)
    if args.list_cameras:
        return _list_cameras()

    _setup_windows_path()

    # Check ANTES de importar service (que ja tocaria em mediapipe)
    _check_environment()

    _setup_logging()
    logger = logging.getLogger("main")
    logger.info("AI Virtual Mouse Controller — iniciando...")

    from core.camera import CameraOpenError
    from services.virtual_mouse_service import VirtualMouseService

    camera_index = _choose_camera(args.camera)
    logger.info("Webcam: indice %d", camera_index)
    try:
        service = VirtualMouseService.from_config(camera_index=camera_index)
    except CameraOpenError as e:
        sys.stderr.write(
            f"\nERRO: {e}\n"
            "Dicas: feche Zoom/Teams/OBS ou o app de Camera; confira se a\n"
            "webcam esta conectada; com mais de uma camera, rode\n"
            "'avmc --list-cameras' e depois 'avmc --camera N'.\n\n"
        )
        return 1
    except Exception:
        logger.exception("Falha ao iniciar")
        return 2
    return service.run()


if __name__ == "__main__":
    sys.exit(main())
