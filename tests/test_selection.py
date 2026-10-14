"""Selección con archivos ficticios exclusivamente en un temporal del proyecto."""
from pathlib import Path
import tempfile
import time
import unittest

from analyzer import analyze_directory, Analysis, FileRecord
from presentation import filter_rows, make_rows, page_rows, sort_rows
from selection import Selection, path_key


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='seleccion_ficticia_', dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'origen'
        self.source.mkdir()
        self.paths = []
        for name, data in [('a.txt', b'same'), ('b.txt', b'same'), ('c.txt', b'same'),
                           ('solo.dat', b'x' * 30), ('empty_a.bin', b''), ('empty_b.bin', b'')]:
            path = self.source / name
            path.write_bytes(data)
            self.paths.append(str(path))
        self.result = analyze_directory(self.source, now=time.time() + 100 * 86400)
        self.selection = Selection(self.result, self.source)
        self.a, self.b, self.c, self.solo, self.empty_a, self.empty_b = self.paths

    def test_initially_nothing_selected_or_retained(self):
        self.assertEqual(self.selection.count, 0)
        self.assertEqual(self.selection.total_bytes, 0)
        self.assertEqual(self.selection.retained, {})

    def test_manual_mark_and_unmark(self):
        self.assertEqual(self.selection.mark([self.solo]), [])
        self.assertTrue(self.selection.is_selected(self.solo))
        self.selection.unmark([self.solo])
        self.assertEqual(self.selection.count, 0)

    def test_sizes_no_double_counting_across_tabs(self):
        self.selection.mark([self.solo, self.a, self.solo, self.a])
        self.assertEqual(self.selection.count, 2)
        self.assertEqual(self.selection.total_bytes, 34)

    def test_last_copy_blocked(self):
        self.selection.mark([self.a, self.b])
        blocked = self.selection.mark([self.c])
        self.assertEqual(len(blocked), 1)
        self.assertFalse(self.selection.is_selected(self.c))
        self.assertEqual(self.selection.count, 2)

    def test_bulk_mark_keeps_one_in_each_group(self):
        blocked = self.selection.mark(self.paths)
        self.assertEqual(len(blocked), 2)
        self.assertEqual(self.selection.count, 4)
        self.assertEqual(self.selection.total_bytes, 38)
        for members in self.selection.groups.values():
            self.assertFalse(members <= self.selection.selected)

    def test_same_guard_when_marking_old_or_largest(self):
        for tab in ('old', 'largest', 'duplicates'):
            with self.subTest(tab=tab):
                self.selection.clear()
                rows = make_rows(self.result)[tab]
                self.selection.mark([row.path for row in rows])
                for members in self.selection.groups.values():
                    self.assertFalse(members <= self.selection.selected)

    def test_explicit_keep_protects_copy_without_auto_marking(self):
        self.selection.keep(self.b)
        self.assertTrue(self.selection.is_retained(self.b))
        self.assertEqual(self.selection.count, 0)
        self.assertTrue(self.selection.mark([self.b]))
        self.assertEqual(self.selection.mark([self.a, self.c]), [])
        self.assertEqual(self.selection.count, 2)

    def test_keep_unmarks_previously_marked_file(self):
        self.selection.mark([self.a])
        self.selection.keep(self.a)
        self.assertFalse(self.selection.is_selected(self.a))
        self.assertTrue(self.selection.is_retained(self.a))

    def test_change_kept_copy_never_automatically_selects_previous(self):
        self.selection.keep(self.a)
        self.selection.mark([self.b, self.c])
        self.selection.keep(self.b)
        self.assertFalse(self.selection.is_retained(self.a))
        self.assertFalse(self.selection.is_selected(self.a))
        self.assertFalse(self.selection.is_selected(self.b))
        self.assertTrue(self.selection.is_selected(self.c))
        self.assertEqual(self.selection.mark([self.a]), [])

    def test_cannot_keep_non_duplicate_or_unknown(self):
        for path in [self.solo, str(self.root / 'missing')]:
            with self.assertRaises(ValueError):
                self.selection.keep(path)

    def test_preview_reasons_and_sizes(self):
        self.selection.mark([self.a])
        item = self.selection.preview()[0]
        self.assertEqual((item.path, item.size), (self.a, 4))
        self.assertTrue(any('Grupo' in reason for reason in item.reasons))
        self.assertTrue(any('90 días' in reason for reason in item.reasons))
        self.assertTrue(any('pesados' in reason for reason in item.reasons))

    def test_remove_from_preview_updates_total(self):
        self.selection.mark([self.a, self.solo])
        self.selection.unmark([self.selection.preview()[0].path])
        self.assertEqual(self.selection.count, 1)
        self.assertEqual(self.selection.total_bytes, 30)

    def test_filters_do_not_change_selection(self):
        self.selection.mark([self.a, self.solo])
        rows = make_rows(self.result)['largest']
        self.assertEqual(filter_rows(rows, 'no existe'), [])
        visible = filter_rows(rows, extension='TXT')
        self.assertEqual(len(visible), 3)
        self.assertEqual(self.selection.count, 2)
        self.assertTrue(self.selection.is_selected(self.solo))

    def test_sort_and_page_preserve_selection(self):
        self.selection.mark([self.a, self.solo])
        rows = sort_rows(make_rows(self.result)['old'], 'size', True)
        for index in range(len(rows)):
            page, _, _ = page_rows(rows, index, 1)
            for row in page:
                self.assertEqual(self.selection.is_selected(row.path), row.path in [self.a, self.solo])
        self.assertEqual(self.selection.total_bytes, 34)

    def test_normalized_path_identity(self):
        alternate = str(self.source / 'sub' / '..' / 'a.txt')
        self.selection.mark([self.a, alternate])
        self.assertEqual(self.selection.count, 1)
        if __import__('os').name == 'nt':
            self.assertTrue(self.selection.is_selected(self.a.upper()))

    def test_unknown_and_outside_paths_rejected(self):
        outside = self.root / 'origen_extra' / 'ficticio.txt'
        outside.parent.mkdir()
        outside.write_bytes(b'ficticio')
        fake = FileRecord(str(outside), 8, 0, '.txt', ())
        result = Analysis(files=self.result.files + [fake])
        model = Selection(result, self.source)
        self.assertTrue(model.mark([str(outside), str(self.source / 'missing')]))
        self.assertEqual(model.count, 0)

    def test_clear_leaves_analysis_usable(self):
        self.selection.mark([self.a])
        self.selection.keep(self.b)
        self.selection.clear()
        self.assertEqual(self.selection.count, 0)
        self.assertEqual(self.selection.retained, {})
        self.assertEqual(self.selection.mark([self.a]), [])

    def test_reset_invalidates_old_inventory(self):
        self.selection.mark([self.a])
        self.selection.keep(self.b)
        self.selection.reset()
        self.assertEqual(self.selection.preview(), [])
        self.assertEqual(self.selection.groups, {})
        self.assertTrue(self.selection.mark([self.a]))

    def test_new_analysis_model_starts_empty(self):
        self.selection.mark([self.a, self.solo])
        replacement = Selection(analyze_directory(self.source), self.source)
        self.assertEqual(replacement.count, 0)
        self.assertEqual(replacement.retained, {})

    def test_zero_byte_duplicates_still_guarded(self):
        self.assertEqual(self.selection.mark([self.empty_a]), [])
        self.assertTrue(self.selection.mark([self.empty_b]))
        self.assertEqual(self.selection.count, 1)
        self.assertEqual(self.selection.total_bytes, 0)

    def test_incomplete_duplicate_group_blocked(self):
        record = self.result.files[0]
        ghost = FileRecord(str(self.root / 'fuera.txt'), record.size, 0, '.txt', ())
        model = Selection(Analysis(files=[record], duplicates=[('ficticio', [record, ghost])]), self.source)
        self.assertTrue(model.mark([record.path]))
        self.assertEqual(model.count, 0)

    def test_empty_analysis(self):
        model = Selection(Analysis(), self.source)
        self.assertEqual(model.preview(), [])
        self.assertEqual(model.total_bytes, 0)

    def test_selection_never_reads_or_writes_source(self):
        before = {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in self.paths}
        self.selection.mark(self.paths)
        self.selection.keep(self.b)
        self.selection.preview()
        self.selection.unmark([self.a])
        self.selection.clear()
        after = {path: (Path(path).read_bytes(), Path(path).stat().st_mtime_ns) for path in self.paths}
        self.assertEqual(before, after)


if __name__ == '__main__':
    unittest.main()
