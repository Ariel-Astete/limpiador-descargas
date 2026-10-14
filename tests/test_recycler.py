"""Pruebas de reciclaje SIMULADO, exclusivamente sobre temporales ficticios."""
import ast
from dataclasses import replace
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from analyzer import Analysis, FileRecord, analyze_directory
from hasher import signature
from recycler import (BackendDisabled, Consent, Receipt, Recycler, SafetyError,
                      SimulatedBackend, Status, WindowsRecycleBackend,
                      classify_receipt, origin_state)
from selection import Selection, path_key


class FakeBinBackend(SimulatedBackend):
    """Papelera ficticia: rename solo entre carpetas temporales de la prueba."""
    def __init__(self, source, bin_path):
        super().__init__()
        self.source = Path(source)
        self.bin = Path(bin_path)
        self.bin.mkdir()

    def recycle(self, record, before_operation):
        before_operation()
        path = Path(record.path)
        if path.parent != self.source:
            raise SafetyError('El backend de prueba no admite otro origen.')
        self.calls.append(record.path)
        destination = self.bin / path.name
        if destination.exists():
            raise SafetyError('No sobrescribir la papelera ficticia.')
        path.rename(destination)
        verified = signature(destination.stat()) == record.identity
        return Receipt(callback_hr=0, recycle_item=str(destination), destination_verified=verified)


class RecyclerTests(unittest.TestCase):
    def setUp(self):
        drive = patch('recycler.query_drive_type', return_value=3)
        drive.start()
        self.addCleanup(drive.stop)
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix='reciclaje_ficticio_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'origen'
        self.source.mkdir()
        self.a = self.source / 'a.txt'
        self.b = self.source / 'b.txt'
        self.c = self.source / 'c.txt'
        self.solo = self.source / 'solo.dat'
        for path in (self.a, self.b, self.c):
            path.write_bytes(b'duplicado ficticio')
        self.solo.write_bytes(b'x' * 37)
        self.analysis = analyze_directory(self.source)
        self.selection = Selection(self.analysis, self.source)
        self.backend = SimulatedBackend()
        self.recycler = Recycler(self.backend)
        self.recycler.bind(self.analysis, self.source)

    def plan(self, paths=None):
        self.selection.mark(paths or [str(self.solo)])
        return self.recycler.prepare(self.selection)

    def execute(self, plan=None):
        plan = plan or self.plan()
        consent = self.recycler.confirm(plan, self.selection, approved=True)
        return self.recycler.execute(plan, consent, self.selection)

    def fake_bin(self):
        self.backend = FakeBinBackend(self.source, self.root / 'papelera_ficticia')
        self.recycler = Recycler(self.backend)
        self.recycler.bind(self.analysis, self.source)

    def test_no_confirmation_no_backend_calls(self):
        plan = self.plan()
        report = self.recycler.execute(plan, None, self.selection)
        self.assertTrue(report.cancelled)
        self.assertEqual(self.backend.calls, [])

    def test_cancel_confirmation(self):
        plan = self.plan()
        consent = self.recycler.confirm(plan, self.selection, approved=False)
        self.assertIsNone(consent)
        self.assertTrue(self.recycler.execute(plan, consent, self.selection).cancelled)
        self.assertEqual(self.backend.calls, [])

    def test_truthy_string_not_explicit_confirmation(self):
        plan = self.plan()
        self.assertIsNone(self.recycler.confirm(plan, self.selection, approved='sí'))

    def test_forged_consent_rejected(self):
        plan = self.plan()
        self.assertTrue(self.recycler.execute(plan, Consent('inventado'), self.selection).cancelled)

    def test_plan_amounts_and_frozen_records(self):
        plan = self.plan()
        self.assertEqual(plan.total_bytes, 37)
        self.assertEqual(len(plan.records), 1)
        with self.assertRaises(AttributeError):
            plan.source = 'otra carpeta'

    def test_default_simulator_does_not_change_files(self):
        before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.source.iterdir()}
        report = self.execute()
        self.assertEqual(report.count(Status.SIMULATED), 1)
        self.assertEqual(report.count(Status.CONFIRMED), 0)
        after = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.source.iterdir()}
        self.assertEqual(before, after)

    def test_consent_single_use(self):
        plan = self.plan()
        consent = self.recycler.confirm(plan, self.selection, approved=True)
        self.recycler.execute(plan, consent, self.selection)
        self.assertTrue(self.recycler.execute(plan, consent, self.selection).cancelled)
        self.assertEqual(len(self.backend.calls), 1)

    def test_changed_selection_cancels_confirmation(self):
        plan = self.plan()
        self.selection.mark([str(self.a)])
        with self.assertRaises(SafetyError):
            self.recycler.confirm(plan, self.selection, approved=True)
        self.assertEqual(self.backend.calls, [])

    def test_change_then_restore_selection_still_invalidates(self):
        plan = self.plan()
        self.selection.unmark([str(self.solo)])
        self.selection.mark([str(self.solo)])
        with self.assertRaises(SafetyError):
            self.recycler.confirm(plan, self.selection, approved=True)

    def test_changed_keep_choice_invalidates_consent(self):
        plan = self.plan([str(self.a)])
        consent = self.recycler.confirm(plan, self.selection, approved=True)
        self.selection.keep(str(self.b))
        self.assertTrue(self.recycler.execute(plan, consent, self.selection).cancelled)
        self.assertEqual(self.backend.calls, [])

    def test_new_analysis_invalidates_confirmation(self):
        plan = self.plan()
        consent = self.recycler.confirm(plan, self.selection, approved=True)
        self.recycler.bind(self.analysis, self.source)
        self.assertTrue(self.recycler.execute(plan, consent, self.selection).cancelled)

    def test_changed_source_invalidates_confirmation(self):
        plan = self.plan()
        consent = self.recycler.confirm(plan, self.selection, approved=True)
        other = self.root / 'otra'
        other.mkdir()
        self.recycler.bind(Analysis(), other)
        self.assertTrue(self.recycler.execute(plan, consent, self.selection).cancelled)

    def test_new_confirmation_plan_invalidates_old(self):
        plan = self.plan()
        consent = self.recycler.confirm(plan, self.selection, approved=True)
        self.recycler.prepare(self.selection)
        self.assertTrue(self.recycler.execute(plan, consent, self.selection).cancelled)

    def test_forged_plan_rejected(self):
        plan = self.plan()
        with self.assertRaises(SafetyError):
            self.recycler.confirm(replace(plan, source=str(self.root)), self.selection, approved=True)

    def test_modified_file_skipped(self):
        plan = self.plan()
        self.solo.write_bytes(b'contenido modificado')
        report = self.execute(plan)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertEqual(self.backend.calls, [])

    def test_same_size_changed_timestamp_skipped(self):
        plan = self.plan()
        info = self.solo.stat()
        os.utime(self.solo, ns=(info.st_atime_ns, info.st_mtime_ns + 10_000_000))
        self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)

    def test_replaced_file_skipped_even_with_original_size_and_time(self):
        plan = self.plan()
        info = self.solo.stat()
        self.solo.rename(self.root / 'original_ficticio')
        self.solo.write_bytes(b'x' * 37)
        os.utime(self.solo, ns=(info.st_atime_ns, info.st_mtime_ns))
        self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)
        self.assertEqual(self.backend.calls, [])

    def test_missing_file_skipped(self):
        plan = self.plan()
        self.solo.rename(self.root / 'movido_ficticio')
        self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)

    def test_directory_replacing_file_skipped(self):
        plan = self.plan()
        self.solo.rename(self.root / 'guardado_ficticio')
        self.solo.mkdir()
        self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)

    def test_permission_error_skipped(self):
        plan = self.plan()
        with patch('recycler.validate_record', side_effect=PermissionError('ruta privada ficticia')):
            report = self.execute(plan)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertNotIn('ruta privada', report.summary())
        self.assertEqual(self.backend.calls, [])

    def test_reparse_candidate_skipped(self):
        plan = self.plan()
        original = os.lstat
        def inspect(path, *args, **kwargs):
            if os.fspath(path) == str(self.solo):
                return SimpleNamespace(st_mode=0o100644, st_file_attributes=0x400)
            return original(path, *args, **kwargs)
        with patch('os.lstat', side_effect=inspect):
            self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)
        self.assertEqual(self.backend.calls, [])

    def test_reparse_parent_skipped(self):
        plan = self.plan()
        with patch('recycler.validate_path', side_effect=ValueError('redirección ficticia')):
            self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)
        self.assertEqual(self.backend.calls, [])

    def test_real_junction_replacing_source_skipped(self):
        if os.name != 'nt':
            self.skipTest('Windows')
        import subprocess
        plan = self.plan()
        target = self.root / 'origen_guardado'
        self.source.rename(target)
        result = subprocess.run(['cmd', '/c', 'mklink', '/J', str(self.source), str(target)], capture_output=True)
        self.assertEqual(result.returncode, 0)
        try:
            self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)
            self.assertEqual(self.backend.calls, [])
        finally:
            os.rmdir(self.source)  # Solo junction ficticia; no elimina el destino.

    def test_outside_inventory_file_skipped(self):
        outside = self.root / 'externo_ficticio'
        outside.write_bytes(b'fuera')
        info = outside.stat()
        record = FileRecord(str(outside), info.st_size, info.st_mtime, '', signature(info))
        self.recycler.bind(Analysis(files=[record]), self.source)
        self.selection._selected.add(path_key(outside))  # Simula selección corrupta.
        self.selection._files[path_key(outside)] = record
        plan = self.recycler.prepare(self.selection)
        self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)
        self.assertEqual(outside.read_bytes(), b'fuera')

    def test_relative_inventory_path_skipped(self):
        original = next(r for r in self.analysis.files if r.path == str(self.solo))
        record = replace(original, path=os.path.relpath(self.solo))
        self.recycler.bind(Analysis(files=[record]), self.source)
        plan = self.plan()
        self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)

    def test_hardlink_skipped(self):
        plan = self.plan()
        os.link(self.solo, self.root / 'enlace_duro_ficticio')
        self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)
        self.assertEqual(self.backend.calls, [])

    def test_root_identity_change_skipped(self):
        plan = self.plan()
        with patch('recycler._root_identity', return_value=('otra', 'identidad')):
            self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)

    def test_duplicate_content_revalidated_even_with_same_metadata(self):
        plan = self.plan([str(self.a)])
        info = self.a.stat()
        self.a.write_bytes(b'z' * info.st_size)
        os.utime(self.a, ns=(info.st_atime_ns, info.st_mtime_ns))
        report = self.execute(plan)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertEqual(self.backend.calls, [])

    def test_explicit_keeper_changed_skips_candidate(self):
        self.selection.keep(str(self.b))
        plan = self.plan([str(self.a)])
        self.b.write_bytes(b'cambiado')
        self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)
        self.assertTrue(self.a.exists())

    def test_all_unselected_copies_missing_skips_candidate(self):
        plan = self.plan([str(self.a)])
        self.b.rename(self.root / 'b_guardado')
        self.c.rename(self.root / 'c_guardado')
        self.assertEqual(self.execute(plan).count(Status.SKIPPED), 1)

    def test_last_copy_guard_independent_of_selection_model(self):
        self.selection._selected.update(path_key(p) for p in (self.a, self.b, self.c))
        with self.assertRaises(SafetyError):
            self.recycler.prepare(self.selection)

    def test_fake_bin_preserves_one_copy_and_all_contents(self):
        self.fake_bin()
        report = self.execute(self.plan([str(self.a), str(self.b)]))
        self.assertEqual(report.count(Status.SIMULATED), 2)
        self.assertEqual(report.count(Status.CONFIRMED), 0)
        self.assertEqual(self.c.read_bytes(), b'duplicado ficticio')
        for name in ('a.txt', 'b.txt'):
            self.assertEqual((self.backend.bin / name).read_bytes(), b'duplicado ficticio')

    def test_keeper_disappears_between_candidates(self):
        self.fake_bin()
        recycle = self.backend.recycle
        def move(record, validate):
            receipt = recycle(record, validate)
            self.c.rename(self.root / 'keeper_guardado')
            return receipt
        self.backend.recycle = move
        report = self.execute(self.plan([str(self.a), str(self.b)]))
        self.assertEqual(report.count(Status.SIMULATED), 1)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertTrue(self.b.exists())

    def test_final_validation_blocks_changed_file_before_backend_action(self):
        plan = self.plan()
        def altered(record, before):
            self.solo.write_bytes(b'nuevo')
            before()
        self.backend.recycle = altered
        report = self.execute(plan)
        self.assertEqual(report.count(Status.UNVERIFIABLE), 1)
        self.assertEqual(self.solo.read_bytes(), b'nuevo')

    def test_backend_error_no_retry(self):
        def error(*args):
            self.backend.calls.append('intento')
            raise OSError('ruta personal ficticia')
        self.backend.recycle = error
        report = self.execute()
        self.assertEqual(report.count(Status.FAILED), 1)
        self.assertEqual(self.backend.calls, ['intento'])
        self.assertNotIn('personal', report.summary())
        self.assertTrue(self.solo.exists())

    def test_missing_origin_without_receipt_is_not_success_and_stops_batch(self):
        plan = self.plan([str(self.a), str(self.solo)])
        def no_evidence(record, validate):
            validate()
            self.backend.calls.append(record.path)
            Path(record.path).rename(self.root / 'guardado_sin_evidencia')
            return Receipt(callback_hr=0)
        self.backend.recycle = no_evidence
        report = self.execute(plan)
        self.assertEqual(report.count(Status.UNVERIFIABLE), 1)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        self.assertEqual(len(self.backend.calls), 1)
        self.assertTrue(self.solo.exists())

    def test_unverified_destination_is_not_success(self):
        self.fake_bin()
        recycle = self.backend.recycle
        def unverified(record, validate):
            return replace(recycle(record, validate), destination_verified=False)
        self.backend.recycle = unverified
        self.assertEqual(self.execute().count(Status.UNVERIFIABLE), 1)

    def test_malformed_receipt_is_unverifiable(self):
        self.backend.recycle = lambda record, validate: Receipt(operation_hr='inválido')
        self.assertEqual(self.execute().count(Status.UNVERIFIABLE), 1)

    def test_unavailable_bin_cancels_before_call(self):
        self.backend.available = False
        report = self.execute()
        self.assertTrue(report.cancelled)
        self.assertEqual(self.backend.calls, [])
        self.assertTrue(self.solo.exists())

    def test_no_recycle_only_guarantee_cancels(self):
        self.backend.recycle_only = False
        self.assertTrue(self.execute().cancelled)
        self.assertEqual(self.backend.calls, [])

    def test_real_backend_blocked_even_when_available_is_tampered(self):
        backend = WindowsRecycleBackend()
        backend.available = True
        self.recycler = Recycler(backend)
        self.recycler.bind(self.analysis, self.source)
        with patch.object(backend, 'recycle') as native:
            self.assertTrue(self.execute().cancelled)
        native.assert_not_called()

    def test_real_backend_direct_call_blocked(self):
        with self.assertRaises(BackendDisabled):
            WindowsRecycleBackend().recycle(self.analysis.files[0], lambda: None)

    def test_missing_root_is_unknown_not_missing(self):
        plan = self.plan()
        self.source.rename(self.root / 'origen_guardado')
        self.assertEqual(origin_state(plan.records[0], plan), 'unknown')

    def test_new_analysis_required_after_attempt(self):
        self.execute()
        with self.assertRaises(SafetyError):
            self.recycler.prepare(self.selection)

    def test_no_permanent_delete_calls_in_production_modules(self):
        for name in ('recycler.py', 'windows_recycler.py', 'gui.py'):
            tree = ast.parse((Path(__file__).resolve().parents[1] / name).read_text(encoding='utf-8-sig'))
            forbidden = [node.func.attr for node in ast.walk(tree)
                         if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                         and node.func.attr in ('remove', 'unlink', 'rmtree')]
            self.assertEqual(forbidden, [], name)


class ReceiptTests(unittest.TestCase):
    def test_real_confirmation_requires_callback_and_destination_proof(self):
        receipt = Receipt(callback_hr=0, recycle_item='referencia-ficticia', destination_verified=True)
        self.assertEqual(classify_receipt(receipt, 'missing', False), Status.CONFIRMED)
        for incomplete in (replace(receipt, callback_hr=None), replace(receipt, recycle_item=None),
                           replace(receipt, destination_verified=False)):
            self.assertEqual(classify_receipt(incomplete, 'missing', False), Status.UNVERIFIABLE)

    def test_origin_disappearance_only_is_not_proof(self):
        self.assertEqual(classify_receipt(Receipt(callback_hr=0), 'missing', False), Status.UNVERIFIABLE)

    def test_failure_and_unknown_distinguished(self):
        receipt = Receipt(callback_hr=0x80004005)
        self.assertEqual(classify_receipt(receipt, 'unchanged', False), Status.FAILED)
        self.assertEqual(classify_receipt(receipt, 'missing', False), Status.UNVERIFIABLE)

    def test_aborted_operation_never_success(self):
        receipt = Receipt(aborted=True, callback_hr=0, recycle_item='ficticio', destination_verified=True)
        self.assertEqual(classify_receipt(receipt, 'missing', False), Status.UNVERIFIABLE)

    def test_origin_present_never_confirms_recycling(self):
        receipt = Receipt(callback_hr=0, recycle_item='ficticio', destination_verified=True)
        self.assertEqual(classify_receipt(receipt, 'unchanged', False), Status.UNVERIFIABLE)

    def test_dry_run_never_claims_real_recycling(self):
        self.assertEqual(classify_receipt(Receipt(dry_run=True), 'unchanged', False), Status.UNVERIFIABLE)
        self.assertEqual(classify_receipt(Receipt(dry_run=True), 'unchanged', True), Status.SIMULATED)


if __name__ == '__main__':
    unittest.main()
