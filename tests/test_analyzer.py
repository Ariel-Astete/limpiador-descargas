"""Todas las pruebas usan archivos ficticios dentro de un temporal del proyecto."""
import contextlib
import csv
import hashlib
import io
import os
from pathlib import Path
import stat
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import analyzer
from analyzer import analyze_directory, validate_directories
from hasher import BLOCK_SIZE, calculate_sha256, is_reparse, validate_path
from main import main
from reporter import safe_cell, write_reports


class AnalyzerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix='prueba_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'origen_ficticio'
        self.source.mkdir()
        self.output = self.root / 'salida_ficticia'
        self.now = 1_800_000_000

    def create(self, name, content=b'x', age=0):
        path = self.source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        timestamp = self.now - age * 86400
        os.utime(path, (timestamp, timestamp))
        return path

    def test_empty(self):
        result = analyze_directory(self.source)
        self.assertEqual(result.files, [])
        self.assertEqual(result.total_bytes, 0)

    def test_recursive(self):
        self.create('nested/data.txt', b'abc')
        result = analyze_directory(self.source)
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.total_bytes, 3)

    def test_twenty_largest(self):
        for index in range(25):
            self.create(f'{index}.bin', b'x' * index)
        result = analyze_directory(self.source)
        self.assertEqual([item.size for item in result.largest], list(range(24, 4, -1)))

    def test_extension_totals(self):
        self.create('a.TXT', b'aaa')
        self.create('b.txt', b'bb')
        self.create('no_extension', b'x')
        result = analyze_directory(self.source)
        self.assertEqual(result.extensions['.txt'], {'count': 2, 'bytes': 5})
        self.assertEqual(result.extensions['(sin extensión)']['bytes'], 1)

    def test_age_boundary(self):
        self.create('old', age=91)
        self.create('boundary', age=90)
        self.create('new', age=89)
        result = analyze_directory(self.source, now=self.now)
        self.assertEqual([Path(item.path).name for item in result.old], ['old'])

    def test_duplicates_confirmed_by_content(self):
        self.create('a', b'abc')
        self.create('b', b'abc')
        self.create('c', b'xyz')
        result = analyze_directory(self.source)
        self.assertEqual(len(result.duplicates), 1)
        digest, group = result.duplicates[0]
        self.assertEqual(digest, hashlib.sha256(b'abc').hexdigest())
        self.assertEqual(len(group), 2)

    def test_unique_sizes_not_hashed(self):
        self.create('a', b'x')
        self.create('b', b'xx')
        with patch('analyzer.calculate_sha256') as hashing:
            analyze_directory(self.source)
        hashing.assert_not_called()

    def test_empty_files_duplicate(self):
        self.create('a', b'')
        self.create('b', b'')
        self.assertEqual(len(analyze_directory(self.source).duplicates), 1)

    def test_hash_failure_does_not_stop(self):
        self.create('a')
        self.create('b')
        with patch('analyzer.calculate_sha256', return_value=None):
            result = analyze_directory(self.source)
        self.assertEqual(result.errors, 2)
        self.assertEqual(result.duplicates, [])

    def test_scandir_permission_error(self):
        self.create('good')
        (self.source / 'blocked').mkdir()
        original = os.scandir
        def scan(path):
            if Path(path).name == 'blocked':
                raise PermissionError('ficticio')
            return original(path)
        with patch('analyzer.os.scandir', side_effect=scan):
            result = analyze_directory(self.source)
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.errors, 1)

    def test_lstat_permission_error(self):
        denied = self.create('denied')
        self.create('good', b'xx')
        original = os.lstat
        def inspect(path, *args, **kwargs):
            if os.fspath(path) == str(denied):
                raise PermissionError('ficticio')
            return original(path, *args, **kwargs)
        with patch('analyzer.os.lstat', side_effect=inspect):
            result = analyze_directory(self.source)
        self.assertEqual(len(result.files), 1)
        self.assertEqual(result.errors, 1)

    def test_reparse_attribute(self):
        info = SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        self.assertTrue(is_reparse(info))
        self.assertTrue(is_reparse(SimpleNamespace(st_mode=stat.S_IFLNK)))

    def test_reparse_entry_ignored(self):
        redirected = self.create('redirected')
        original = os.lstat
        def inspect(path, *args, **kwargs):
            if os.fspath(path) == str(redirected):
                return SimpleNamespace(st_mode=stat.S_IFREG, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch('analyzer.os.lstat', side_effect=inspect):
            result = analyze_directory(self.source)
        self.assertEqual(result.files, [])
        self.assertEqual(result.skipped, 1)

    @unittest.skipUnless(os.name == 'nt', 'Prueba específica de Windows')
    def test_real_junction(self):
        target = self.root / 'destino_ficticio'
        target.mkdir()
        (target / 'secreto_ficticio').write_bytes(b'ficticio')
        link = self.source / 'junction'
        process = subprocess.run(['cmd', '/c', 'mklink', '/J', str(link), str(target)],
                                 capture_output=True, check=False)
        self.assertEqual(process.returncode, 0, 'No se pudo crear junction ficticia')
        try:
            result = analyze_directory(self.source)
            self.assertEqual(result.files, [])
            self.assertEqual(result.skipped, 1)
            self.assertIsNone(calculate_sha256(link / 'secreto_ficticio'))
            with self.assertRaises(ValueError):
                validate_directories(self.source, link / 'salida')
        finally:
            os.rmdir(link)  # Quita únicamente el enlace ficticio, no el destino.

    def test_hasher_blocks(self):
        content = b'z' * (BLOCK_SIZE + 37)
        path = self.create('large', content)
        self.assertEqual(calculate_sha256(path), hashlib.sha256(content).hexdigest())

    def test_hasher_missing_or_directory(self):
        self.assertIsNone(calculate_sha256(self.source / 'missing'))
        self.assertIsNone(calculate_sha256(self.source))

    def test_hasher_open_denied(self):
        path = self.create('a')
        with patch('hasher.os.open', side_effect=PermissionError):
            self.assertIsNone(calculate_sha256(path))

    def test_changed_file_rejected(self):
        path = self.create('a', b'abc')
        original = os.fstat
        def changed(fd):
            data = original(fd)
            return SimpleNamespace(st_dev=data.st_dev, st_ino=data.st_ino,
                st_size=data.st_size + 1, st_mtime_ns=data.st_mtime_ns,
                st_ctime_ns=data.st_ctime_ns)
        with patch('hasher.os.fstat', side_effect=changed):
            self.assertIsNone(calculate_sha256(path))

    def test_output_inside_rejected(self):
        for output in [self.source, self.source / 'reportes', self.source / 'a' / '..']:
            with self.assertRaises(ValueError):
                validate_directories(self.source, output)

    def test_sibling_output_allowed(self):
        sibling = self.root / 'origen_ficticio_reportes'
        self.assertEqual(validate_directories(self.source, sibling)[1], str(sibling))

    def test_invalid_arguments(self):
        for kwargs in [{'days': -1}, {'top': 0}]:
            with self.assertRaises(ValueError):
                analyze_directory(self.source, **kwargs)

    def test_reports_and_no_overwrite(self):
        self.create('a.txt', b'abc', age=100)
        self.create('b.txt', b'abc', age=100)
        result = analyze_directory(self.source, now=self.now)
        paths = write_reports(result, self.source, self.output)
        second = write_reports(result, self.source, self.output)
        self.assertEqual(len(paths), 4)
        self.assertTrue(set(paths).isdisjoint(second))
        expected = {'pesados': 2, 'antiguos': 2, 'duplicados': 2, 'extensiones': 1}
        for path in paths:
            with open(path, encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(len(rows), expected[Path(path).stem.split('_')[0]])

    def test_empty_reports_have_headers(self):
        paths = write_reports(analyze_directory(self.source), self.source, self.output)
        for path in paths:
            with open(path, encoding='utf-8-sig', newline='') as handle:
                rows = list(csv.reader(handle))
            self.assertEqual(len(rows), 1)

    def test_report_inside_rejected_without_creation(self):
        destination = self.source / 'reports'
        with self.assertRaises(ValueError):
            write_reports(analyze_directory(self.source), self.source, destination)
        self.assertFalse(destination.exists())

    def test_spreadsheet_formula_escaped(self):
        self.assertEqual(safe_cell('=SUM(1,2)'), "'=SUM(1,2)")
        self.assertEqual(safe_cell('@name'), "'@name")
        self.assertEqual(safe_cell(42), 42)

    def test_cli_summary_private_and_read_only(self):
        path = self.create('nombre_ficticio.txt', b'contenido ficticio')
        before = (path.read_bytes(), path.stat().st_mtime_ns)
        console = io.StringIO()
        with contextlib.redirect_stdout(console):
            status = main([str(self.source), '--output', str(self.output)])
        self.assertEqual(status, 0)
        self.assertNotIn(str(self.root), console.getvalue())
        self.assertNotIn(path.name, console.getvalue())
        self.assertEqual(before, (path.read_bytes(), path.stat().st_mtime_ns))
        self.assertEqual(list(self.source.iterdir()), [path])

    def test_cli_error_private(self):
        console = io.StringIO()
        with contextlib.redirect_stdout(console):
            status = main([str(self.source / 'inexistente'), '--output', str(self.output)])
        self.assertEqual(status, 1)
        self.assertNotIn(str(self.root), console.getvalue())


if __name__ == '__main__':
    unittest.main()
