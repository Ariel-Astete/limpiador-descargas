"""Regresiones de exportación congelada, solo con directorios ficticios."""
from contextlib import ExitStack
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from analyzer import analyze_directory
from reporter import validate_report_destination, write_reports


class FrozenReportsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1],prefix='paquete_ficticio_')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.package=self.root/'paquete';self.package.mkdir()
        self.internal=self.package/'_internal';self.internal.mkdir()
        self.source=self.root/'origen';self.source.mkdir()
        (self.source/'ficticio.txt').write_bytes(b'ficticio')
        self.result=analyze_directory(self.source)
        stack=ExitStack();self.addCleanup(stack.close)
        stack.enter_context(patch.object(sys,'frozen',True,create=True))
        stack.enter_context(patch.object(sys,'executable',str(self.package/'app.exe')))
        stack.enter_context(patch.object(sys,'_MEIPASS',str(self.internal),create=True))

    def test_package_root_rejected(self):
        with self.assertRaises(ValueError):write_reports(self.result,self.source,self.package)
        self.assertFalse(list(self.package.glob('*.csv')))

    def test_internal_rejected(self):
        with self.assertRaises(ValueError):write_reports(self.result,self.source,self.internal)

    def test_new_internal_subdirectory_not_created(self):
        output=self.internal/'reportes'
        with self.assertRaises(ValueError):write_reports(self.result,self.source,output)
        self.assertFalse(output.exists())

    def test_external_export(self):
        output=self.root/'reportes'
        reports=write_reports(self.result,self.source,output)
        self.assertEqual(len(reports),4)
        self.assertTrue(all(Path(path).parent==output for path in reports))

    def test_sibling_prefix_is_allowed(self):
        validate_report_destination(self.root/'paquete_otro')

    def test_separate_internal_directory_rejected(self):
        other=self.root/'runtime'
        with patch.object(sys,'_MEIPASS',str(other)):
            with self.assertRaises(ValueError):validate_report_destination(other/'datos')

    def test_unfrozen_preserves_behavior(self):
        with patch.object(sys,'frozen',False):
            validate_report_destination(self.package)
