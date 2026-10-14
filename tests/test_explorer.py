"""Localización con mocks: nunca se abre Explorer ni se cargan DLL reales."""
import ctypes
from dataclasses import replace
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from analyzer import analyze_directory, capture_analysis_content
from explorer import LocationRequest, LocationError, WindowsExplorerBackend, locate_file, locate_for_gui, validate_source
from gui import Application, refresh_for_gui
from selection import Selection


class ExplorerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix='ubicacion_ficticia_')
        self.addCleanup(self.temp.cleanup)
        self.source = Path(self.temp.name) / 'origen'
        self.source.mkdir()
        self.file = self.source / 'dato, ficticio & español.txt'
        self.file.write_bytes(b'ficticio')
        self.result = analyze_directory(self.source)
        capture_analysis_content(self.result, self.source)
        self.request = LocationRequest(str(self.source), self.result.source_identity, self.result.files[0], self.result.content_hashes[str(self.file)])
        self.backend = Mock()

    def test_highlight(self):
        self.assertIn('resaltado', locate_file(self.request, self.backend))
        self.backend.select_file.assert_called_once_with(str(self.file))
        self.backend.open_folder.assert_not_called()

    def test_fallback(self):
        self.backend.select_file.side_effect = OSError()
        self.assertIn('contenedora', locate_file(self.request, self.backend))
        self.backend.open_folder.assert_called_once_with(str(self.source))

    def test_missing(self):
        missing = replace(self.request.record, path=str(self.source / 'no_existe'))
        with self.assertRaisesRegex(LocationError, 'no existe'):
            locate_file(replace(self.request, record=missing), self.backend)
        self.assertFalse(self.backend.mock_calls)

    def test_modified(self):
        self.file.write_bytes(b'cambio')
        with self.assertRaises(LocationError):
            locate_file(self.request, self.backend)
        self.assertFalse(self.backend.mock_calls)

    def test_digest_mismatch(self):
        with self.assertRaises(LocationError):
            locate_file(replace(self.request, digest='0'*64), self.backend)
        self.assertFalse(self.backend.mock_calls)

    def test_no_digest(self):
        with self.assertRaises(LocationError):
            locate_file(replace(self.request, digest=None), self.backend)

    def test_outside(self):
        record = replace(self.request.record, path=str(Path(self.temp.name) / 'externo.txt'))
        with self.assertRaises(LocationError):
            locate_file(replace(self.request, record=record), self.backend)
        self.assertFalse(self.backend.mock_calls)

    def test_invalid_root_identity(self):
        with self.assertRaises(LocationError):
            locate_file(replace(self.request, source_identity=(0,0,None)), self.backend)

    def test_reparse_rejected(self):
        with patch('explorer.validate_path', side_effect=ValueError()):
            with self.assertRaises(LocationError):
                locate_file(self.request, self.backend)
        self.assertFalse(self.backend.mock_calls)

    def test_no_fallback_after_change(self):
        def change(path):
            self.file.write_bytes(b'modificado')
            raise OSError()
        self.backend.select_file.side_effect = change
        with self.assertRaises(LocationError):
            locate_file(self.request, self.backend)
        self.backend.open_folder.assert_not_called()

    def test_both_launches_fail(self):
        self.backend.select_file.side_effect = OSError()
        self.backend.open_folder.side_effect = OSError()
        with self.assertRaises(LocationError):
            locate_file(self.request, self.backend)
        self.assertEqual(self.backend.select_file.call_count,1)
        self.assertEqual(self.backend.open_folder.call_count,1)

    def test_safe_gui_error(self):
        self.file.write_bytes(b'cambio')
        success,text=locate_for_gui(self.request,self.backend)
        self.assertFalse(success)
        self.assertNotIn(str(self.source),text)

    def test_content_unchanged(self):
        before=(self.file.read_bytes(),self.file.stat().st_mtime_ns)
        locate_file(self.request,self.backend)
        self.assertEqual(before,(self.file.read_bytes(),self.file.stat().st_mtime_ns))

    def test_refresh_invalid_source_no_scan(self):
        with patch('gui.analyze_for_gui') as scan:
            with self.assertRaises(LocationError):
                refresh_for_gui(self.source,(0,0,None))
        scan.assert_not_called()

    def test_refresh_changed_inventory(self):
        (self.source/'nuevo.txt').write_bytes(b'otro')
        result,rows=refresh_for_gui(self.source,self.result.source_identity)
        self.assertEqual(len(result.files),2)

    def test_gui_single_highlight_keeps_marks(self):
        app=self.make_app()
        app.selection.mark([str(self.file)])
        app.open_location([str(self.file)])
        self.assertEqual(app.selection.count,1)
        self.assertEqual(app.selection.total_bytes,len(b'ficticio'))
        self.assertEqual(app._launch.call_args.args[0],'location')

    def make_app(self):
        app=Application.__new__(Application)
        app.root=Mock();app.busy=False;app.result=self.result
        app.result_source=str(self.source);app.selection=Selection(self.result,self.source)
        app.explorer_backend=self.backend;app._set_busy=Mock();app._launch=Mock()
        return app

    def test_gui_multiple_highlights_no_launch(self):
        app=self.make_app()
        with patch('gui.messagebox.showinfo'):
            app.open_location([str(self.file),str(self.file)])
        app._launch.assert_not_called()

    def test_gui_unknown_row_no_launch(self):
        app=self.make_app()
        with patch('gui.messagebox.showwarning'):
            app.open_location(['unknown'])
        app._launch.assert_not_called()

    def test_gui_busy_no_launch(self):
        app=self.make_app();app.busy=True
        app.open_location([str(self.file)])
        app._launch.assert_not_called()

    def test_preview_highlight(self):
        app=self.make_app();app.preview_tree=Mock();app.preview_tree.selection.return_value=('row',)
        app.preview_paths={'row':str(self.file)}
        app.open_preview_location()
        app._launch.assert_called_once()

    def test_refresh_invalidates_selection(self):
        app=self.make_app();app.selection.mark([str(self.file)])
        app.invalidate_selection=Mock(side_effect=app.selection.clear)
        app.refresh_analysis()
        self.assertEqual(app.selection.count,0)
        self.assertIsNone(app.result)
        self.assertIs(app._launch.call_args.args[2],refresh_for_gui)

    def test_no_network_source(self):
        with self.assertRaises(LocationError):
            validate_source(r'\\server\share',self.result.source_identity)


class NativeExplorerMockTests(unittest.TestCase):
    def api(self, initialize=0, parse=0, opening=0):
        ole=Mock();shell=Mock()
        ole.CoInitializeEx.return_value=initialize
        def parsing(path,context,out,attributes,returned):
            ctypes.cast(out,ctypes.POINTER(ctypes.c_void_p))[0]=ctypes.c_void_p(1234)
            return parse
        shell.SHParseDisplayName.side_effect=parsing
        shell.SHOpenFolderAndSelectItems.return_value=opening
        return ole,shell

    def run_select(self,ole,shell):
        with patch('explorer.ctypes.WinDLL',side_effect=lambda name,**kw: ole if name=='ole32.dll' else shell):
            WindowsExplorerBackend().select_file(r'C:\ficticio.txt')

    def test_com_success_lifetime(self):
        ole,shell=self.api();self.run_select(ole,shell)
        ole.CoTaskMemFree.assert_called_once();ole.CoUninitialize.assert_called_once()
        self.assertEqual(shell.SHOpenFolderAndSelectItems.call_args.args[1:],(0,None,0))

    def test_com_s_false_balanced(self):
        ole,shell=self.api(initialize=1);self.run_select(ole,shell)
        ole.CoUninitialize.assert_called_once()

    def test_com_failure_not_uninitialized(self):
        ole,shell=self.api(initialize=-1)
        with self.assertRaises(OSError):self.run_select(ole,shell)
        ole.CoUninitialize.assert_not_called();shell.SHParseDisplayName.assert_not_called()

    def test_parse_failure_free_and_uninitialize(self):
        ole,shell=self.api(parse=-1)
        with self.assertRaises(OSError):self.run_select(ole,shell)
        ole.CoTaskMemFree.assert_called_once();ole.CoUninitialize.assert_called_once()
        shell.SHOpenFolderAndSelectItems.assert_not_called()

    def test_open_failure_free_and_uninitialize(self):
        ole,shell=self.api(opening=-1)
        with self.assertRaises(OSError):self.run_select(ole,shell)
        ole.CoTaskMemFree.assert_called_once();ole.CoUninitialize.assert_called_once()

    def test_folder_shell_false_system_executable(self):
        kernel=Mock()
        def directory(buffer,size):buffer.value=r'C:\Windows';return len(buffer.value)
        kernel.GetWindowsDirectoryW.side_effect=directory
        with patch('explorer.ctypes.WinDLL',return_value=kernel),patch('explorer.subprocess.Popen') as launch:
            WindowsExplorerBackend().open_folder(r'C:\ficticio & datos')
        launch.assert_called_once_with([r'C:\Windows\explorer.exe','/e,',r'C:\ficticio & datos'],shell=False)

    def test_folder_windows_directory_failure_no_process(self):
        kernel=Mock();kernel.GetWindowsDirectoryW.return_value=0
        with patch('explorer.ctypes.WinDLL',return_value=kernel),patch('explorer.subprocess.Popen') as launch:
            with self.assertRaises(OSError):WindowsExplorerBackend().open_folder(r'C:\ficticio')
        launch.assert_not_called()
