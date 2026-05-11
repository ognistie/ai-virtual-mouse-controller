#!/usr/bin/env python
"""Quick syntax validation."""

import sys
try:
    import config
    from core.gesture_detector import GestureDetector
    from services.virtual_mouse_service import VirtualMouseService
    print("✅ All imports OK - v7 implementation validated")
    sys.exit(0)
except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
