"""Regresiones 4B.1: datos ficticios, callbacks en memoria y APIs simuladas."""
from dataclasses import replace
import ctypes as C
import gc
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch
import weakref

from analyzer import Analysis, FileRecord, analyze_directory, capture_analysis_content
from hasher import signature
import recycler
from recycler import (BackendDisabled, HResultError, Receipt, Recycler,
                      SafetyError, SimulatedBackend, Status, normalize_hresult)
from selection import Selection, SelectionLockedError, path_key
import windows_recycler as win


class CallbackHardeningTests(unittest.TestCase):
    def setUp(self):
        self.before_registry = set(win.ProgressSink._live)

    def tearDown(self):
        # Solo referencias ficticias de estas pruebas, nunca una Release nativa.
        with patch.object(win, 'release'):
            for address, sink in tuple(win.ProgressSink._live.items()):
                if address not in self.before_registry:
                    sink.close()
                    sink.new_item = None
                    sink.release_uncertain = False
                    while sink.refs:
                        sink.callbacks[2](sink.pointer)
            win.ProgressSink.drain_owner_references()

    def test_keyboard_interrupt_system_exit_and_base_exception_do_not_escape(self):
        for error in (KeyboardInterrupt('ficticio'), SystemExit(1), BaseException('ficticio')):
            with self.subTest(error=type(error).__name__):
                sink = win.ProgressSink(Mock(side_effect=error))
                result = sink.callbacks[11](sink.pointer, 0x80, 123)
                self.assertEqual(result, win.E_ABORT)
                self.assertTrue(sink.callback_error)
                self.assertFalse(sink.pre_validated)
                self.assertEqual(sink.exception_type, type(error).__name__)
                self.assertEqual(sink.diagnostic_hr, 0x80004004)
                sink.close()

    def test_unsigned_hresult_return_is_normalized(self):
        sink = win.ProgressSink(Mock())
        call = sink._catch(lambda *args: 0x80004005)
        self.assertEqual(call(None), -2147467259)

    def test_invalid_callback_return_aborts(self):
        sink = win.ProgressSink(Mock())
        for value in ('invalid', None, 2**40, True):
            with self.subTest(value=value):
                self.assertEqual(sink._catch(lambda *args: value)(None), win.E_ABORT)
        self.assertTrue(sink.callback_error)

    def test_ulong_exception_returns_zero_not_hresult(self):
        sink = win.ProgressSink(Mock())
        def interrupted(*args):
            raise KeyboardInterrupt()
        call = sink._catch(interrupted, win.DWORD)
        self.assertEqual(call(None), 0)
        self.assertTrue(sink.callback_error)

    def test_hresult_from_callback_error_is_preserved(self):
        sink = win.ProgressSink(Mock(side_effect=HResultError(0x80270037, 'perform')))
        self.assertEqual(sink.callbacks[11](sink.pointer, 0x80, 123), win.E_ABORT)
        self.assertEqual(sink.diagnostic_hr, 0x80270037)

    def test_external_reference_keeps_python_sink_alive_after_close(self):
        sink = win.ProgressSink(Mock())
        address = sink.pointer.value
        self.assertEqual(sink.callbacks[1](sink.pointer), 2)
        reference = weakref.ref(sink)
        sink.close()  # Referencia propia liberada; queda la externa.
        self.assertEqual(sink.refs, 1)
        del sink
        gc.collect()
        self.assertIsNotNone(reference())
        held = win.ProgressSink._live[address]
        self.assertEqual(held.callbacks[2](held.pointer), 0)
        # Retención hasta haber vuelto de la llamada C/ctypes.
        self.assertIn(address, win.ProgressSink._live)
        win.ProgressSink.drain_owner_references()
        self.assertNotIn(address, win.ProgressSink._live)

    def test_close_is_idempotent_and_releases_own_reference(self):
        sink = win.ProgressSink(Mock())
        address = sink.pointer.value
        with patch.object(win, 'release') as release:
            sink.close()
            sink.close()
        self.assertEqual(sink.refs, 0)
        release.assert_called_once_with(None)
        self.assertNotIn(address, win.ProgressSink._live)

    def test_closed_sink_rejects_operations_even_if_external_ref_exists(self):
        before = Mock()
        sink = win.ProgressSink(before)
        sink.callbacks[1](sink.pointer)
        sink.close()
        self.assertEqual(sink.callbacks[11](sink.pointer, 0x80, 123), win.E_ABORT)
        before.assert_not_called()

    def test_wrong_thread_close_keeps_references_for_owner(self):
        sink = win.ProgressSink(Mock())
        errors = []
        def worker():
            try:
                sink.close()
            except SafetyError:
                errors.append(True)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        self.assertEqual(errors, [True])
        self.assertFalse(sink.closed)
        self.assertEqual(sink.refs, 1)
        sink.close()

    def test_wrong_thread_ulong_callback_returns_zero_and_keeps_count(self):
        sink = win.ProgressSink(Mock())
        results = []
        thread = threading.Thread(target=lambda: results.append(sink.callbacks[2](sink.pointer)))
        thread.start()
        thread.join(timeout=2)
        self.assertEqual(results, [0])
        self.assertEqual(sink.refs, 1)
        self.assertTrue(sink.callback_error)

    def test_release_underflow_and_addref_after_zero_are_rejected(self):
        sink = win.ProgressSink(Mock())
        self.assertEqual(sink.callbacks[2](sink.pointer), 0)
        self.assertEqual(sink.callbacks[2](sink.pointer), 0)
        self.assertTrue(sink.callback_error)
        output = win.PTR()
        result = sink.callbacks[0](sink.pointer, C.byref(win.IID_UNKNOWN), C.byref(output))
        self.assertEqual(result, win.E_ABORT)
        self.assertIsNone(output.value)

    def test_uncertain_release_retains_memory_without_retry(self):
        sink = win.ProgressSink(Mock())
        sink.new_item = 999  # Puntero ficticio: todos los Release están sustituidos.
        with patch.object(win, 'release', side_effect=HResultError(0x80004005, 'release')) as release:
            with self.assertRaises(HResultError):
                sink.close()
            sink.close()
        self.assertEqual(release.call_count, 1)
        self.assertTrue(sink.release_uncertain)
        self.assertIn(sink.pointer.value, win.ProgressSink._live)

    def test_checked_keeps_exact_hresult_and_stage(self):
        with self.assertRaises(HResultError) as caught:
            win.checked(-2147024891, 'flags')
        self.assertEqual(caught.exception.hresult, 0x80070005)
        self.assertEqual(caught.exception.stage, 'flags')
        self.assertIn('0x80070005', str(caught.exception))

    def test_invalid_hresult_and_unsafe_stage_are_not_accepted(self):
        for value in (True, 'path', None, 2**32, -(2**31)-1):
            with self.assertRaises(ValueError):
                normalize_hresult(value)
        self.assertEqual(HResultError(0x80004005, 'ruta-personal-ficticia').stage, 'unknown')

    def test_real_entrypoints_remain_blocked_before_any_dll(self):
        with patch.object(win.C, 'WinDLL', create=True) as loader:
            for function, args in ((win._load_apis, ()), (win.recycle_one, (None, Mock())),
                                   (win._perform_native, (None, Mock()))):
                with self.assertRaises(BackendDisabled):
                    function(*args)
        loader.assert_not_called()

    def test_cleanup_error_does_not_replace_primary_hresult(self):
        from tests.test_windows_recycler import FakeOle, FakeShell
        from analyzer import FileRecord
        events = []
        record = FileRecord('ruta-ficticia-no-accedida', 0, 0, '', ())
        def failing_method(pointer, index, *types, **kwargs):
            return lambda *args: 0x80070005
        def failing_release(pointer):
            if pointer:
                raise HResultError(0x80004005, 'release')
        with patch.object(win, 'require_real_backend_enabled'), \
             patch.object(win, '_load_apis', return_value=(FakeOle(events), FakeShell())), \
             patch.object(win, 'method', side_effect=failing_method), \
             patch.object(win, 'release', side_effect=failing_release):
            with self.assertRaises(HResultError) as caught:
                win._perform_native(record, Mock())
        self.assertEqual(caught.exception.hresult, 0x80070005)
        self.assertEqual(caught.exception.stage, 'flags')
        self.assertEqual(caught.exception.cleanup_diagnostics, (0x80004005,))
        self.assertEqual(events[-1][0], 'uninit')

    def test_invalid_cleanup_diagnostic_falls_back_without_exception(self):
        error = RuntimeError('ficticio')
        error.hresult = 'invalid'
        self.assertEqual(win.error_hresult(error), 0x80004004)

    def test_close_inside_pre_callback_cannot_authorize_operation(self):
        sink = win.ProgressSink(Mock())
        sink.before_operation = sink.close
        self.assertEqual(sink.callbacks[11](sink.pointer, 0x80, 123), win.E_ABORT)
        self.assertFalse(sink.pre_validated)
        self.assertIn(sink.pointer.value, win.ProgressSink._live)
        win.ProgressSink.drain_owner_references()
        self.assertNotIn(sink.pointer.value, win.ProgressSink._live)

    def test_rejected_callback_does_not_decode_untrusted_string_pointer(self):
        sink = win.ProgressSink(Mock())
        # Solo pasa el número: el callback no lee memoria en esa dirección.
        self.assertEqual(sink.callbacks[5](sink.pointer, 0, 123, 1), win.E_ABORT)
        self.assertEqual(sink.callbacks[7](sink.pointer, 0, 123, 456, 1), win.E_ABORT)


class RecyclerHardeningTests(unittest.TestCase):
    def setUp(self):
        drive = patch('recycler.query_drive_type', return_value=3)
        drive.start()
        self.addCleanup(drive.stop)
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix='endurecimiento_ficticio_')
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'origen'
        self.source.mkdir()
        self.a = self.source / 'a.dat'
        self.b = self.source / 'b.txt'
        self.c = self.source / 'c.txt'
        self.a.write_bytes(b'original ficticio unico')
        self.b.write_bytes(b'duplicado ficticio')
        self.c.write_bytes(b'duplicado ficticio')
        self.analysis = analyze_directory(self.source)
        self.selection = Selection(self.analysis, self.source)
        self.backend = SimulatedBackend()
        self.controller = Recycler(self.backend)
        self.controller.bind(self.analysis, str(self.source))

    def plan(self, paths=None):
        self.selection.mark(paths or [str(self.a)])
        return self.controller.prepare(self.selection)

    def execute(self, plan):
        consent = self.controller.confirm(plan, self.selection, approved=True)
        return self.controller.execute(plan, consent, self.selection)

    def test_unique_file_modified_with_same_metadata_is_skipped(self):
        plan = self.plan()
        info = self.a.stat()
        self.a.write_bytes(b'z' * info.st_size)
        os.utime(self.a, ns=(info.st_atime_ns, info.st_mtime_ns))
        report = self.execute(plan)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertEqual(self.backend.calls, [])

    def test_missing_content_baseline_prevents_attempt(self):
        with patch('recycler.calculate_sha256', return_value=None):
            self.controller.bind(self.analysis, str(self.source))
        report = self.execute(self.plan())
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertEqual(self.backend.calls, [])

    def test_baseline_is_immutable_after_confirmation(self):
        capture_analysis_content(self.analysis, self.source)
        self.controller.bind(self.analysis, str(self.source))
        plan = self.plan()
        hashes = dict(plan.content_hashes)
        self.analysis.content_hashes[str(self.a)] = '0' * 64
        self.assertEqual(dict(plan.content_hashes), hashes)
        self.assertEqual(self.execute(plan).count(Status.SIMULATED), 1)

    def test_worker_capture_avoids_rehash_on_bind(self):
        capture_analysis_content(self.analysis, self.source)
        with patch('recycler.calculate_sha256') as hashing:
            self.controller.bind(self.analysis, str(self.source))
        hashing.assert_not_called()

    def test_failed_capture_not_automatically_retried_on_bind(self):
        self.analysis.content_hashes = {record.path: None for record in self.analysis.files}
        with patch('recycler.calculate_sha256') as hashing:
            self.controller.bind(self.analysis, str(self.source))
        hashing.assert_not_called()
        self.assertEqual(self.execute(self.plan()).count(Status.SKIPPED), 1)

    def test_selection_mutations_blocked_during_backend_call_but_reads_work(self):
        plan = self.plan()
        def operation(record, validate):
            self.assertEqual(self.selection.count, 1)
            self.assertTrue(self.selection.is_selected(record.path))
            for function, args in ((self.selection.unmark, [record.path]),
                                   (self.selection.mark, [str(self.b)])):
                with self.assertRaises(SelectionLockedError):
                    function(args)
            for function in (self.selection.clear, self.selection.reset):
                with self.assertRaises(SelectionLockedError):
                    function()
            with self.assertRaises(SelectionLockedError):
                self.selection.keep(str(self.c))
            validate()
            return Receipt(dry_run=True)
        self.backend.recycle = operation
        self.assertEqual(self.execute(plan).count(Status.SIMULATED), 1)
        self.selection.clear()  # Desbloqueada tras finalizar.
        self.assertEqual(self.selection.count, 0)

    def test_snapshot_does_not_change_when_live_selection_changes(self):
        self.selection.mark([str(self.a)])
        snapshot = self.selection.snapshot()
        self.selection.mark([str(self.b)])
        self.assertEqual(snapshot.selected, frozenset({path_key(self.a)}))
        self.assertNotEqual(snapshot.stamp, self.selection.snapshot().stamp)
        with self.assertRaises(AttributeError):
            snapshot.revision = 0

    def test_retained_mapping_cannot_be_mutated_without_revision(self):
        self.selection.keep(str(self.c))
        with self.assertRaises(TypeError):
            self.selection.retained[1] = path_key(self.b)

    def test_second_operation_on_frozen_selection_cancels(self):
        plan = self.plan()
        consent = self.controller.confirm(plan, self.selection, approved=True)
        with self.selection.freeze():
            report = self.controller.execute(plan, consent, self.selection)
        self.assertTrue(report.cancelled)
        self.assertEqual(self.backend.calls, [])

    def test_selection_thaws_after_base_exception_from_backend(self):
        self.backend.recycle = Mock(side_effect=SystemExit('ficticio'))
        report = self.execute(self.plan())
        self.assertTrue(report.cancelled)
        self.assertEqual(report.count(Status.FAILED), 1)
        self.selection.clear()

    def test_receipt_hresult_failure_cancels_remaining_batch_and_preserves_code(self):
        plan = self.plan([str(self.a), str(self.b)])
        self.backend.recycle = Mock(return_value=Receipt(operation_hr=0x8027003A, stage='perform'))
        report = self.execute(plan)
        self.assertEqual(self.backend.recycle.call_count, 1)
        self.assertTrue(report.cancelled)
        self.assertEqual(report.count(Status.FAILED), 1)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertEqual(report.items[0].hresult, 0x8027003A)
        self.assertIn('0x8027003A', report.items[0].message)

    def test_exception_hresult_cancels_remaining_batch_and_preserves_stage(self):
        plan = self.plan([str(self.a), str(self.b)])
        self.backend.recycle = Mock(side_effect=HResultError(0x80270037, 'flags'))
        report = self.execute(plan)
        self.assertEqual(self.backend.recycle.call_count, 1)
        self.assertTrue(report.cancelled)
        self.assertEqual(report.items[0].hresult, 0x80270037)
        self.assertEqual(report.items[0].stage, 'flags')

    def test_unexpected_backend_error_stops_batch_without_retry(self):
        plan = self.plan([str(self.a), str(self.b)])
        self.backend.recycle = Mock(side_effect=OSError('error con ruta ficticia'))
        report = self.execute(plan)
        self.assertEqual(self.backend.recycle.call_count, 1)
        self.assertTrue(report.cancelled)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertNotIn('ruta ficticia', report.items[0].message)

    def test_copies_with_same_physical_identity_not_accepted_as_keeper(self):
        plan = self.plan([str(self.b)])
        original = recycler.validate_record
        def validate(record, plan):
            info = original(record, plan)
            if record.path == str(self.c):
                return self.b.stat()  # Simula un alias sin crear enlaces.
            return info
        with patch('recycler.validate_record', side_effect=validate):
            report = self.execute(plan)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertTrue(report.cancelled)
        self.assertEqual(self.backend.calls, [])

    def test_analysis_bound_to_other_root_is_rejected(self):
        wrong = replace(self.analysis, source_path=str(self.source.parent))
        with self.assertRaises(SafetyError):
            self.controller.bind(wrong, str(self.source))

    def test_analysis_root_identity_mismatch_is_rejected(self):
        wrong = replace(self.analysis, source_identity=(1, 2, 3))
        with self.assertRaises(SafetyError):
            self.controller.bind(wrong, str(self.source))

    def test_birthtime_strengthens_signature(self):
        from types import SimpleNamespace
        base = dict(st_dev=1, st_ino=2, st_size=3, st_mtime_ns=4, st_ctime_ns=5)
        first = SimpleNamespace(**base, st_birthtime_ns=6)
        second = SimpleNamespace(**base, st_birthtime_ns=7)
        self.assertNotEqual(signature(first), signature(second))

    def test_complete_simulation_preserves_source_bytes_and_metadata(self):
        before = {path.name: (path.read_bytes(), signature(path.stat())) for path in self.source.iterdir()}
        report = self.execute(self.plan([str(self.a), str(self.b)]))
        after = {path.name: (path.read_bytes(), signature(path.stat())) for path in self.source.iterdir()}
        self.assertEqual(before, after)
        self.assertEqual(report.count(Status.CONFIRMED), 0)
        self.assertEqual(report.count(Status.SIMULATED), 2)

    def test_incomplete_group_with_two_authorized_members_is_blocked(self):
        outsider = FileRecord(str(self.source.parent / 'fuera_ficticio'), 1, 0, '', ())
        members = [record for record in self.analysis.files if record.path != str(self.a)]
        result = Analysis(files=members, duplicates=[('ficticio', members + [outsider])])
        model = Selection(result, self.source)
        self.assertTrue(model.mark([str(self.b)]))
        self.assertEqual(model.count, 0)

    def test_both_global_and_callback_hresult_are_kept(self):
        plan = self.plan()
        self.backend.recycle = Mock(return_value=Receipt(operation_hr=0x80004005, callback_hr=0x80270037))
        report = self.execute(plan)
        self.assertEqual(report.items[0].operation_hr, 0x80004005)
        self.assertEqual(report.items[0].callback_hr, 0x80270037)
        self.assertEqual(report.items[0].hresult, 0x80270037)

    def test_failed_reference_cleanup_hresult_is_kept_in_report(self):
        error = HResultError(0x80070005, 'flags')
        error.cleanup_diagnostics = (0x80004005,)
        self.backend.recycle = Mock(side_effect=error)
        report = self.execute(self.plan())
        self.assertEqual(report.items[0].hresult, 0x80070005)
        self.assertEqual(report.items[0].cleanup_hresults, (0x80004005,))

    def test_selected_file_volume_mismatch_is_rejected(self):
        plan = self.plan()
        original = os.lstat
        from types import SimpleNamespace
        def inspect(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if os.fspath(path) == str(self.a):
                return SimpleNamespace(st_mode=info.st_mode, st_ino=info.st_ino,
                    st_dev=info.st_dev + 1, st_nlink=1, st_file_attributes=0)
            return info
        with patch('os.lstat', side_effect=inspect):
            report = self.execute(plan)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertTrue(report.cancelled)
        self.assertEqual(self.backend.calls, [])

    def test_new_junction_attribute_in_final_lstat_is_rejected(self):
        plan = self.plan()
        original = os.lstat
        from types import SimpleNamespace
        def inspect(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if os.fspath(path) == str(self.a):
                return SimpleNamespace(st_mode=info.st_mode, st_ino=info.st_ino,
                    st_dev=info.st_dev, st_nlink=1, st_file_attributes=0x400)
            return info
        # Simula que el cambio llega después del control de los componentes.
        with patch('recycler.validate_path', side_effect=lambda path: os.fspath(path)), \
             patch('os.lstat', side_effect=inspect):
            report = self.execute(plan)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertTrue(report.cancelled)
        self.assertEqual(self.backend.calls, [])

    def test_selection_getters_remain_responsive_from_another_thread(self):
        self.selection.mark([str(self.a)])
        values = []
        with self.selection.freeze():
            thread = threading.Thread(target=lambda: values.append(self.selection.snapshot().stamp))
            thread.start()
            thread.join(timeout=1)
            self.assertFalse(thread.is_alive())
            self.assertEqual(len(values), 1)

    def test_frozen_selection_rejects_mutation_from_another_thread(self):
        self.selection.mark([str(self.a)])
        errors = []
        def change():
            try:
                self.selection.clear()
            except SelectionLockedError:
                errors.append(True)
        with self.selection.freeze():
            thread = threading.Thread(target=change)
            thread.start()
            thread.join(timeout=1)
        self.assertEqual(errors, [True])
        self.assertEqual(self.selection.count, 1)

    def test_cancelled_frozen_attempt_consumes_consent(self):
        plan = self.plan()
        consent = self.controller.confirm(plan, self.selection, approved=True)
        with self.selection.freeze():
            self.assertTrue(self.controller.execute(plan, consent, self.selection).cancelled)
        self.assertTrue(self.controller.execute(plan, consent, self.selection).cancelled)
        self.assertEqual(self.backend.calls, [])

    def test_bind_from_worker_baselines_does_not_walk_each_file_in_gui(self):
        capture_analysis_content(self.analysis, self.source)
        with patch('recycler.validate_record', side_effect=AssertionError('No recorrer inventario en Tk')):
            self.controller.bind(self.analysis, str(self.source))
        self.assertEqual(self.execute(self.plan()).count(Status.SIMULATED), 1)

    def test_diagnostic_hresult_error_cancels_even_without_fatal_flag(self):
        plan = self.plan([str(self.a), str(self.b)])
        self.backend.recycle = Mock(return_value=Receipt(dry_run=True, diagnostic_hr=0x8027003A))
        report = self.execute(plan)
        self.assertTrue(report.cancelled)
        self.assertEqual(self.backend.recycle.call_count, 1)
        self.assertEqual(report.count(Status.FAILED), 1)
        self.assertEqual(report.count(Status.SKIPPED), 1)


@unittest.skipUnless(os.name == 'nt', 'Validaciones específicas de Windows')
class WindowsPathHardeningTests(unittest.TestCase):
    def test_network_mapped_drive_rejected_before_lstat(self):
        with patch('recycler.query_drive_type', return_value=4), patch('recycler.validate_path') as validate:
            with self.assertRaises(SafetyError):
                recycler._safe_local_path('Z:\\ficticio\\archivo.txt')
        validate.assert_not_called()

    def test_only_fixed_local_drive_is_accepted(self):
        for kind in (0, 1, 2, 4, 5, 6):
            with self.subTest(kind=kind), patch('recycler.query_drive_type', return_value=kind):
                with self.assertRaises(SafetyError):
                    recycler._safe_local_path('C:\\ficticio\\archivo.txt')
        with patch('recycler.query_drive_type', return_value=3), patch('recycler.validate_path', side_effect=lambda path: path):
            self.assertEqual(recycler._safe_local_path('C:\\ficticio\\archivo.txt'), 'C:\\ficticio\\archivo.txt')

    def test_unc_device_ads_reserved_and_ambiguous_paths_rejected(self):
        paths = ['\\\\servidor\\carpeta\\ficticio', '\\\\?\\C:\\ficticio',
                 '\\\\.\\C:\\ficticio', 'C:\\ficticio.txt:datos', 'C:\\NUL.txt',
                 'C:\\COM1', 'C:\\nombre.\\ficticio', 'C:\\nombre \\ficticio',
                 'C:\\*.txt']
        with patch('recycler.query_drive_type') as query, patch('recycler.validate_path') as validate:
            for path in paths:
                with self.subTest(path=path), self.assertRaises(SafetyError):
                    recycler._safe_local_path(path)
        query.assert_not_called()
        validate.assert_not_called()

    def test_drive_query_failure_cancels_validation(self):
        with patch('recycler.query_drive_type', side_effect=OSError('ficticio')):
            with self.assertRaises(SafetyError) as caught:
                recycler._safe_local_path('C:\\ficticio.txt')
        self.assertTrue(caught.exception.fatal)

    def test_drive_type_api_uses_only_injected_readonly_double(self):
        kernel = Mock()
        kernel.GetDriveTypeW.return_value = 4
        with patch('ctypes.WinDLL', return_value=kernel) as loader:
            self.assertEqual(recycler.query_drive_type('Z:\\'), 4)
        loader.assert_called_once_with('kernel32', winmode=0x00000800)
        kernel.GetDriveTypeW.assert_called_once_with('Z:\\')
        self.assertEqual(kernel.GetDriveTypeW.argtypes, [C.c_wchar_p])
        self.assertIs(kernel.GetDriveTypeW.restype, C.c_uint32)


if __name__ == '__main__':
    unittest.main()
