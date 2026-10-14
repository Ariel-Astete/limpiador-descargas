"""Pruebas puras de presentación y comunicación: ningún archivo personal."""
import queue
import threading
import unittest
from unittest.mock import patch

from analyzer import Analysis, FileRecord
from gui import ERRORS, analyze_for_gui, prepare_view, run_job
from presentation import Row, filter_rows, format_date, format_size, make_rows, page_rows, sort_rows


class PresentationTests(unittest.TestCase):
    def setUp(self):
        self.rows = [Row('Informe.TXT', '.txt', 100, 200),
                     Row('foto.jpg', '.jpg', 2000, 100),
                     Row('datos.txt', '.txt', 9, 300)]

    def test_size_units(self):
        for size, text in [(0, '0 B'), (1023, '1023 B'), (1024, '1.00 KiB'),
                           (1024**2, '1.00 MiB'), (1024**3, '1.00 GiB')]:
            self.assertEqual(format_size(size), text)

    def test_date_format(self):
        self.assertRegex(format_date(1_800_000_000), r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$')

    def test_invalid_date(self):
        self.assertEqual(format_date(float('inf')), 'Fecha fuera de rango')

    def test_filter_name_case_insensitive(self):
        self.assertEqual(filter_rows(self.rows, ' INFORME '), [self.rows[0]])

    def test_filter_extension_normalized(self):
        self.assertEqual(filter_rows(self.rows, extension='TXT'), [self.rows[0], self.rows[2]])
        self.assertEqual(filter_rows(self.rows, extension=' .JPG '), [self.rows[1]])

    def test_combined_filters(self):
        self.assertEqual(filter_rows(self.rows, 'datos', 'txt'), [self.rows[2]])
        self.assertEqual(filter_rows(self.rows, 'datos', 'jpg'), [])

    def test_no_filter_keeps_all(self):
        self.assertEqual(filter_rows(self.rows), self.rows)

    def test_no_extension_filter(self):
        row = Row('LEEME', '(sin extensión)', 0)
        self.assertEqual(filter_rows([row], extension='(sin extensión)'), [row])

    def test_numeric_size_sort(self):
        self.assertEqual([row.size for row in sort_rows(self.rows, 'size')], [9, 100, 2000])
        self.assertEqual([row.size for row in sort_rows(self.rows, 'size', True)], [2000, 100, 9])

    def test_date_sort(self):
        self.assertEqual([row.modified for row in sort_rows(self.rows, 'modified')], [100, 200, 300])

    def test_name_sort_and_immutability(self):
        before = list(self.rows)
        self.assertEqual([row.name for row in sort_rows(self.rows, 'name')], ['datos.txt', 'foto.jpg', 'Informe.TXT'])
        self.assertEqual(self.rows, before)

    def test_invalid_sort_column(self):
        with self.assertRaises(ValueError):
            sort_rows(self.rows, 'unknown')

    def test_page_boundaries(self):
        rows = list(range(1001))
        self.assertEqual(page_rows(rows, 2), ([1000], 2, 3))
        self.assertEqual(page_rows(rows, 9), ([1000], 2, 3))
        self.assertEqual(page_rows([], 7), ([], 0, 1))
        self.assertEqual(page_rows(rows, -1)[1], 0)

    def test_invalid_page_size(self):
        with self.assertRaises(ValueError):
            page_rows([], page_size=0)

    def test_analysis_mapping_preserves_groups(self):
        file = FileRecord('ficticio.txt', 3, 100, '.txt', ())
        result = Analysis(files=[file], largest=[file], old=[file],
                          duplicates=[('hash-ficticio', [file, file])],
                          extensions={'.txt': {'count': 1, 'bytes': 3}})
        rows = make_rows(result)
        self.assertEqual(set(rows), {'largest', 'old', 'duplicates', 'extensions'})
        self.assertEqual(rows['duplicates'][0].group, 1)
        self.assertEqual(rows['duplicates'][0].digest, 'hash-ficticio')
        self.assertEqual(rows['extensions'][0].count, 1)
        self.assertEqual(rows['largest'][0].path, file.path)

    def test_extension_view_filter(self):
        rows = [Row('.txt', '.txt', 9, count=2), Row('.jpg', '.jpg', 100, count=1)]
        self.assertEqual(filter_rows(rows, name='txt'), [rows[0]])

    def test_prepare_view_combines_operations(self):
        self.assertEqual([row.size for row in prepare_view(self.rows, '', 'txt', 'size', True)], [100, 9])

    def test_worker_publishes_result_from_background(self):
        events = queue.Queue()
        thread = threading.Thread(target=run_job, args=(events, 'view', ('old', 3), threading.get_ident))
        thread.start()
        thread.join(timeout=2)
        outcome, kind, token, identity = events.get_nowait()
        self.assertEqual((outcome, kind, token), ('ok', 'view', ('old', 3)))
        self.assertNotEqual(identity, threading.get_ident())

    def test_worker_error_does_not_leak_exception(self):
        events = queue.Queue()
        def failure():
            raise PermissionError('ruta-personal-ficticia')
        run_job(events, 'analysis', 1, failure)
        self.assertEqual(events.get_nowait(), ('error', 'analysis', 1, ERRORS['analysis']))

    def test_gui_analysis_reuses_analyzer(self):
        result = Analysis()
        with patch('gui.analyze_directory', return_value=result) as scan:
            analysis, rows = analyze_for_gui('origen-ficticio')
        scan.assert_called_once_with('origen-ficticio')
        self.assertIs(analysis, result)
        self.assertEqual(rows['largest'], ())


if __name__ == '__main__':
    unittest.main()
