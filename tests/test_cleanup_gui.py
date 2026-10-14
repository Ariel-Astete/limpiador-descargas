"""Pruebas de conexión GUI sin abrir ventanas; widgets y COM simulados."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch
import unittest

from analyzer import analyze_directory
from gui import Application, cleanup_for_gui
from recycler import Recycler, SafetyError, SimulatedBackend, Status, WindowsRecycleBackend
from selection import Selection
from tests.test_recycler import FakeBinBackend


class CleanupGuiTests(unittest.TestCase):
    def setUp(self):
        drive = patch('recycler.query_drive_type', return_value=3)
        drive.start()
        self.addCleanup(drive.stop)
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix='gui_simulada_')
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'origen'
        self.source.mkdir()
        self.path = self.source / 'ficticio.txt'
        self.path.write_bytes(b'archivo ficticio')
        self.result = analyze_directory(self.source)
        self.app = Application.__new__(Application)
        self.app.root = Mock()
        self.app.busy = False
        self.app.result = self.result
        self.app.result_source = str(self.source)
        self.app.selection = Selection(self.result, self.source)
        self.app.recycler = Recycler(SimulatedBackend())
        self.app.recycler.bind(self.result, self.source)
        self.app.selection_buttons = [Mock()]
        self.app.preview_button = Mock()
        self.app.recycle_button = Mock()
        self.app.preview_window = None

    def test_no_selection_button_disabled(self):
        self.app.update_selection_controls()
        self.app.recycle_button.configure.assert_called_with(state='disabled')

    def test_simulator_button_enabled_only_with_selection(self):
        self.app.selection.mark([str(self.path)])
        self.app.update_selection_controls()
        self.app.recycle_button.configure.assert_called_with(state='normal')

    def test_real_backend_button_always_disabled(self):
        self.app.recycler = Recycler()
        self.app.selection.mark([str(self.path)])
        self.app.update_selection_controls()
        self.app.recycle_button.configure.assert_called_with(state='disabled')

    def test_real_backend_attribute_changes_do_not_enable_gui(self):
        backend = WindowsRecycleBackend()
        backend.available = backend.simulated = True
        self.app.recycler = Recycler(backend)
        self.app.selection.mark([str(self.path)])
        self.app.update_selection_controls()
        self.app.recycle_button.configure.assert_called_with(state='disabled')

    def test_busy_button_disabled(self):
        self.app.selection.mark([str(self.path)])
        self.app.busy = True
        self.app.update_selection_controls()
        self.app.recycle_button.configure.assert_called_with(state='disabled')

    def test_direct_real_confirmation_callback_never_opens_execution_window(self):
        self.app.recycler = Recycler()
        self.app.selection.mark([str(self.path)])
        with patch('gui.messagebox.showinfo') as notice, patch('gui.tk.Toplevel') as window:
            self.app.open_cleanup_confirmation()
        window.assert_not_called()
        notice.assert_called_once()

    def test_invalidate_closes_preview_and_discards_plan(self):
        self.app.selection.mark([str(self.path)])
        plan = self.app.recycler.prepare(self.app.selection)
        self.app.recycler.confirm(plan, self.app.selection, approved=True)
        preview = Mock()
        self.app.preview_window = preview
        self.app.sync_selection = Mock()
        self.app.invalidate_selection()
        self.assertEqual(self.app.selection.count, 0)
        self.assertIsNone(self.app.preview_window)
        preview.destroy.assert_called_once()
        with self.assertRaises(SafetyError):
            self.app.recycler.prepare(self.app.selection)

    def test_worker_refreshes_actual_inventory_after_fake_bin_operation(self):
        backend = FakeBinBackend(self.source, Path(self.temp.name) / 'papelera_ficticia')
        controller = Recycler(backend)
        controller.bind(self.result, self.source)
        self.app.selection.mark([str(self.path)])
        plan = controller.prepare(self.app.selection)
        consent = controller.confirm(plan, self.app.selection, approved=True)
        report, updated, rows = cleanup_for_gui(controller, plan, consent, self.app.selection)
        self.assertEqual(report.count(Status.SIMULATED), 1)
        self.assertEqual(report.count(Status.CONFIRMED), 0)
        self.assertEqual(updated.files, [])
        self.assertEqual(rows['largest'], ())
        self.assertEqual((backend.bin / self.path.name).read_bytes(), b'archivo ficticio')

    def test_refresh_failure_preserves_operation_report(self):
        self.app.selection.mark([str(self.path)])
        plan = self.app.recycler.prepare(self.app.selection)
        consent = self.app.recycler.confirm(plan, self.app.selection, approved=True)
        with patch('gui.analyze_for_gui', side_effect=PermissionError('ficticio')):
            report, updated, rows = cleanup_for_gui(self.app.recycler, plan, consent, self.app.selection)
        self.assertEqual(report.count(Status.SIMULATED), 1)
        self.assertIsNone(updated)
        self.assertIsNone(rows)

    def test_inventory_cleared_on_operation_error(self):
        self.app.selection.mark([str(self.path)])
        self.app.metrics = [Mock() for _ in range(6)]
        self.app.export_button = Mock()
        self.app.sync_selection = Mock()
        self.app.render_table = Mock()
        self.app.tokens = {key: 1 for key in self.app.SPECS}
        self.app.filtered = {key: [object()] for key in self.app.SPECS}
        self.app.pages = {key: 3 for key in self.app.SPECS}
        self.app.clear_cleanup_inventory()
        self.assertIsNone(self.app.result)
        self.assertIsNone(self.app.result_source)
        self.assertEqual(self.app.selection.count, 0)
        self.assertTrue(all(not rows for rows in self.app.filtered.values()))
        self.assertEqual(self.app.render_table.call_count, 4)

    def test_cancelled_operation_does_not_scan_old_origin(self):
        self.app.selection.mark([str(self.path)])
        plan = self.app.recycler.prepare(self.app.selection)
        with patch('gui.analyze_for_gui') as scan:
            report, updated, rows = cleanup_for_gui(self.app.recycler, plan, None, self.app.selection)
        self.assertTrue(report.cancelled)
        scan.assert_not_called()
        self.assertIsNone(updated)

    def test_replaced_root_prevents_automatic_inventory_refresh(self):
        self.app.selection.mark([str(self.path)])
        plan = self.app.recycler.prepare(self.app.selection)
        consent = self.app.recycler.confirm(plan, self.app.selection, approved=True)
        self.source.rename(Path(self.temp.name) / 'original_guardado')
        self.source.mkdir()
        with patch('gui.analyze_for_gui') as scan:
            report, updated, rows = cleanup_for_gui(self.app.recycler, plan, consent, self.app.selection)
        self.assertEqual(report.count(Status.SKIPPED), 1)
        scan.assert_not_called()
        self.assertIsNone(updated)


    def confirmation_mocks(self, stack, choice):
        self.app.selection.mark([str(self.path)])
        self.app._launch = Mock()
        self.app._set_busy = Mock()
        self.app.status = Mock()
        buttons = {}
        def button(*args, **kwargs):
            widget = Mock()
            buttons[kwargs['text']] = kwargs.get('command')
            return widget
        for name in ('Frame', 'Label', 'Treeview', 'Scrollbar'):
            stack.enter_context(patch('gui.ttk.' + name))
        stack.enter_context(patch('gui.ttk.Button', side_effect=button))
        stack.enter_context(patch('gui.tk.Toplevel'))
        pages = stack.enter_context(patch('gui.paged_tree'))
        warning = stack.enter_context(patch('gui.messagebox.showwarning'))
        def wait(window):
            if choice == 'stale':
                self.app.selection.unmark([str(self.path)])
            if choice in ('accept', 'stale'):
                buttons['Confirmar simulación']()
        self.app.root.wait_window.side_effect = wait
        return pages, warning

    def test_confirmation_window_cancel_does_not_launch_worker(self):
        with ExitStack() as stack:
            pages, warning = self.confirmation_mocks(stack, 'cancel')
            self.app.open_cleanup_confirmation()
        self.app._launch.assert_not_called()
        self.assertEqual(self.app.recycler.backend.calls, [])
        self.assertEqual(len(pages.call_args.args[2]), 1)

    def test_confirmation_window_explicit_accept_launches_only_simulation(self):
        with ExitStack() as stack:
            pages, warning = self.confirmation_mocks(stack, 'accept')
            self.app.open_cleanup_confirmation()
        self.app._launch.assert_called_once()
        args = self.app._launch.call_args.args
        self.assertEqual(args[0], 'cleanup')
        self.assertEqual(pages.call_args.args[2][0].path, str(self.path))
        self.assertEqual(self.app.recycler.backend.calls, [])  # El hilo aún no se accionó.
        report, result, rows = args[2](*args[3:])  # Solo motor simulado sobre temporal.
        self.assertEqual(report.count(Status.SIMULATED), 1)
        self.assertEqual(report.count(Status.CONFIRMED), 0)
        self.assertTrue(self.path.exists())

    def test_confirmation_window_stale_selection_rejected(self):
        with ExitStack() as stack:
            pages, warning = self.confirmation_mocks(stack, 'stale')
            self.app.open_cleanup_confirmation()
        self.app._launch.assert_not_called()
        warning.assert_called_once()
        self.assertEqual(self.app.recycler.backend.calls, [])


if __name__ == '__main__':
    unittest.main()
