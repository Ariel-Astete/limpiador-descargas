"""COM/ABI con objetos Python ficticios. NUNCA se cargan DLL de Windows."""
import ctypes as C
from pathlib import Path
import tempfile
import threading
from unittest.mock import Mock, patch
import unittest

from analyzer import analyze_directory
from recycler import BackendDisabled, SafetyError
import windows_recycler as win


class FakeOle:
    def __init__(self, events, initialize_hr=0):
        self.events = events
        self.initialize_hr = initialize_hr

    def CoInitializeEx(self, reserved, flags):
        self.events.append(('init', threading.get_ident(), flags))
        return self.initialize_hr

    def CoUninitialize(self):
        self.events.append(('uninit', threading.get_ident()))

    def CoCreateInstance(self, clsid, outer, context, iid, output):
        C.cast(output, C.POINTER(win.PTR))[0] = 101
        self.events.append(('create',))
        return 0


class FakeShell:
    def SHCreateItemFromParsingName(self, path, context, iid, output):
        C.cast(output, C.POINTER(win.PTR))[0] = 102
        return 0


class ComTests(unittest.TestCase):
    def test_success_initialization_balanced(self):
        events = []
        with win.ComApartment(FakeOle(events)):
            events.append(('work',))
        self.assertEqual([event[0] for event in events], ['init', 'work', 'uninit'])
        self.assertEqual(events[0][1], events[-1][1])
        self.assertEqual(events[0][2], 6)

    def test_s_false_also_balanced(self):
        events = []
        with win.ComApartment(FakeOle(events, 1)):
            pass
        self.assertEqual([event[0] for event in events], ['init', 'uninit'])

    def test_changed_mode_does_not_uninitialize(self):
        events = []
        with self.assertRaises(SafetyError):
            with win.ComApartment(FakeOle(events, 0x80010106)):
                self.fail('No debe entrar')
        self.assertEqual([event[0] for event in events], ['init'])

    def test_exception_still_uninitializes(self):
        events = []
        with self.assertRaises(RuntimeError):
            with win.ComApartment(FakeOle(events)):
                raise RuntimeError('ficticio')
        self.assertEqual(events[-1][0], 'uninit')

    def test_com_owner_is_background_thread(self):
        events = []
        def worker():
            with win.ComApartment(FakeOle(events)):
                pass
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        self.assertEqual(events[0][1], events[-1][1])
        self.assertNotEqual(events[0][1], threading.get_ident())

    def test_cannot_release_com_on_another_thread(self):
        events = []
        session = win.ComApartment(FakeOle(events))
        session.__enter__()
        errors = []
        def worker():
            try:
                session.__exit__(None, None, None)
            except SafetyError:
                errors.append(True)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=2)
        self.assertEqual(errors, [True])
        self.assertEqual([event[0] for event in events], ['init'])
        session.__exit__(None, None, None)

    def test_double_exit_does_not_double_uninitialize(self):
        events = []
        session = win.ComApartment(FakeOle(events))
        with session:
            pass
        session.__exit__(None, None, None)
        self.assertEqual(sum(event[0] == 'uninit' for event in events), 1)

    def test_all_real_entrypoints_are_blocked_before_api_load(self):
        with patch.object(win, '_load_apis') as load:
            for entry in (win.recycle_one, win._perform_native):
                with self.assertRaises(BackendDisabled):
                    entry(None, Mock())
            load.assert_not_called()
        with self.assertRaises(BackendDisabled):
            win._load_apis()

    def test_flags_require_recycle_and_never_yes_to_all(self):
        self.assertTrue(win.FLAGS & win.FOFX_RECYCLEONDELETE)
        self.assertTrue(win.FLAGS & win.FOFX_EARLYFAILURE)
        self.assertTrue(win.FLAGS & win.FOF_NO_CONNECTED_ELEMENTS)
        self.assertFalse(win.FLAGS & 0x10)  # FOF_NOCONFIRMATION

    def test_guid_abi(self):
        self.assertEqual(C.sizeof(win.GUID), 16)
        self.assertEqual(C.sizeof(win.HR), 4)
        self.assertEqual(C.sizeof(win.DWORD), 4)


class SinkTests(unittest.TestCase):
    def setUp(self):
        before = set(win.ProgressSink._live)
        def cleanup():
            for address, sink in tuple(win.ProgressSink._live.items()):
                if address not in before:
                    sink.close()
                    while sink.refs:
                        sink.callbacks[2](sink.pointer)
            win.ProgressSink.drain_owner_references()
        self.addCleanup(cleanup)

    def test_pre_delete_aborts_if_recycle_flag_absent(self):
        before = Mock()
        sink = win.ProgressSink(before)
        self.assertEqual(sink.callbacks[11](sink.pointer, 0, 123), win.E_ABORT)
        before.assert_not_called()
        self.assertFalse(sink.pre_validated)

    def test_pre_delete_requires_matching_item(self):
        before = Mock()
        sink = win.ProgressSink(before, item_matches=lambda item: False)
        self.assertEqual(sink.callbacks[11](sink.pointer, 0x80, 123), win.E_ABORT)
        before.assert_not_called()

    def test_pre_delete_validation_error_aborts_without_exception_escape(self):
        before = Mock(side_effect=SafetyError('ficticio'))
        sink = win.ProgressSink(before)
        self.assertEqual(sink.callbacks[11](sink.pointer, 0x80, 123), win.E_ABORT)
        self.assertTrue(sink.callback_error)
        self.assertFalse(sink.pre_validated)

    def test_pre_delete_duplicate_callback_rejected(self):
        before = Mock()
        sink = win.ProgressSink(before)
        self.assertEqual(sink.callbacks[11](sink.pointer, 0x80, 123), 0)
        self.assertEqual(sink.callbacks[11](sink.pointer, 0x80, 123), win.E_ABORT)
        self.assertEqual(before.call_count, 1)

    def test_missing_recycle_item_never_produces_destination_reference(self):
        sink = win.ProgressSink(Mock())
        sink.callbacks[11](sink.pointer, 0x80, 123)
        self.assertEqual(sink.callbacks[12](sink.pointer, 0x80, 123, 0, None), 0)
        self.assertEqual(sink.callback_hr, 0)
        self.assertIsNone(sink.new_item)

    def test_post_without_pre_is_rejected(self):
        sink = win.ProgressSink(Mock())
        self.assertEqual(sink.callbacks[12](sink.pointer, 0x80, 123, 0, None), win.E_ABORT)
        self.assertTrue(sink.callback_error)

    def test_positive_post_reference_addref_and_release_once(self):
        sink = win.ProgressSink(Mock())
        sink.callbacks[11](sink.pointer, 0x80, 123)
        addref = Mock(return_value=2)
        with patch.object(win, 'method', return_value=addref), patch.object(win, 'release') as release:
            self.assertEqual(sink.callbacks[12](sink.pointer, 0x80, 123, 0, 456), 0)
            addref.assert_called_once_with(456)
            sink.close()
            release.assert_called_once_with(456)
            self.assertIsNone(sink.new_item)

    def test_failure_does_not_take_reference(self):
        sink = win.ProgressSink(Mock())
        sink.callbacks[11](sink.pointer, 0x80, 123)
        with patch.object(win, 'method') as method:
            sink.callbacks[12](sink.pointer, 0x80, 123, -1, 456)
        method.assert_not_called()
        self.assertIsNone(sink.new_item)

    def test_other_operation_callbacks_denied(self):
        sink = win.ProgressSink(Mock())
        name = C.cast(C.create_unicode_buffer('ficticio'), win.PTR)
        self.assertEqual(sink.callbacks[5](sink.pointer, 0, 123, name), win.E_ABORT)
        self.assertEqual(sink.callbacks[7](sink.pointer, 0, 123, 456, name), win.E_ABORT)
        self.assertEqual(sink.callbacks[9](sink.pointer, 0, 123, 456, name), win.E_ABORT)

    def test_query_interface_supports_only_unknown_and_sink(self):
        sink = win.ProgressSink(Mock())
        output = win.PTR()
        self.assertEqual(sink.callbacks[0](sink.pointer, C.byref(win.IID_PROGRESS_SINK), C.byref(output)), 0)
        self.assertEqual(output.value, sink.pointer.value)
        self.assertEqual(sink.callbacks[0](sink.pointer, C.byref(win.IID_SHELL_ITEM), C.byref(output)), win.E_NOINTERFACE)
        self.assertIsNone(output.value)


class NativeSequenceMockTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1], prefix='com_ficticio_')
        self.addCleanup(temp.cleanup)
        path = Path(temp.name) / 'ficticio.txt'
        path.write_bytes(b'ficticio')
        self.record = analyze_directory(temp.name).files[0]
        self.events = []
        self.ole = FakeOle(self.events)
        self.shell = FakeShell()

    def dispatch(self, pointer, index, *types, **kwargs):
        def call(this, *args):
            self.events.append(('method', index))
            if index == 22:
                C.cast(args[0], C.POINTER(C.c_int32))[0] = 0
            return 0
        return call

    def test_mock_native_sequence_releases_before_uninitialize(self):
        # Solo dobles Python. No se desactiva el bloqueo del controlador ni se cargan DLL.
        with patch.object(win, 'require_real_backend_enabled'), \
             patch.object(win, '_load_apis', return_value=(self.ole, self.shell)), \
             patch.object(win, 'method', side_effect=self.dispatch), \
             patch.object(win, 'release', side_effect=lambda pointer: self.events.append(('release', pointer))):
            receipt = win._perform_native(self.record, Mock())
        indices = [event[1] for event in self.events if event[0] == 'method']
        self.assertEqual(indices, [5, 18, 21, 22])
        self.assertIsNone(receipt.recycle_item)
        self.assertFalse(receipt.destination_verified)
        self.assertEqual(self.events[-1][0], 'uninit')
        released = [event[1].value for event in self.events if event[0] == 'release' and event[1]]
        self.assertEqual(released, [102, 101])

    def test_flags_failure_never_dispatches_operation_and_releases(self):
        def failure(pointer, index, *types, **kwargs):
            return lambda *args: 0x80004005
        with patch.object(win, 'require_real_backend_enabled'), \
             patch.object(win, '_load_apis', return_value=(self.ole, self.shell)), \
             patch.object(win, 'method', side_effect=failure) as method, \
             patch.object(win, 'release', side_effect=lambda pointer: self.events.append(('release', pointer))):
            with self.assertRaises(SafetyError):
                win._perform_native(self.record, Mock())
        self.assertEqual([call.args[1] for call in method.call_args_list], [5])
        self.assertEqual(self.events[-1][0], 'uninit')


if __name__ == '__main__':
    unittest.main()
