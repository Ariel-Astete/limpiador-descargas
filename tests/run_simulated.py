"""Ejecuta la suite con una barrera que prohíbe cargar DLL reales vía ctypes."""
from pathlib import Path
import sys
import unittest
from unittest.mock import patch


def main():
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    with patch('ctypes.WinDLL', create=True, side_effect=AssertionError('DLL real prohibida en la suite simulada')):
        suite = unittest.defaultTestLoader.discover(str(root / 'tests'), top_level_dir=str(root))
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
