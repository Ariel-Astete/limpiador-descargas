"""Adaptador COM en cuarentena: TODAS las entradas nativas están bloqueadas.

No hay flag de entorno/CLI para habilitarlo. No se ha ejecutado contra Windows.
Definiciones revisables basadas en Microsoft Learn y ShObjIdl_core.h.
"""
import ctypes as C
import os
import threading
import uuid

from hasher import is_reparse, signature, validate_path
from selection import path_key
from recycler import (Receipt, SafetyError, HResultError, normalize_hresult,
                      hresult_failed, require_real_backend_enabled)

HR = C.c_int32
DWORD = C.c_uint32
PTR = C.c_void_p
CALLBACK = getattr(C, 'WINFUNCTYPE', C.CFUNCTYPE)
E_ABORT = -2147467260
E_NOINTERFACE = -2147467262
FOFX_RECYCLEONDELETE = 0x00080000
FOFX_EARLYFAILURE = 0x00100000
FOFX_ADDUNDORECORD = 0x20000000
FOF_NOERRORUI = 0x0400
FOF_NORECURSION = 0x1000
FOF_NO_CONNECTED_ELEMENTS = 0x2000
FOF_WANTNUKEWARNING = 0x4000
# No FOF_NOCONFIRMATION: nunca responde automáticamente sí a un diálogo.
FLAGS = (FOFX_RECYCLEONDELETE | FOFX_EARLYFAILURE | FOFX_ADDUNDORECORD
         | FOF_NOERRORUI | FOF_NORECURSION | FOF_NO_CONNECTED_ELEMENTS
         | FOF_WANTNUKEWARNING)
TSF_DELETE_RECYCLE_IF_POSSIBLE = 0x80


class GUID(C.Structure):
    _fields_ = [('data1', C.c_uint32), ('data2', C.c_uint16),
                ('data3', C.c_uint16), ('data4', C.c_ubyte * 8)]

    @classmethod
    def parse(cls, text):
        return cls.from_buffer_copy(uuid.UUID(text).bytes_le)


CLSID_FILE_OPERATION = GUID.parse('3ad05575-8857-4850-9277-11b85bdb8e09')
IID_FILE_OPERATION = GUID.parse('947aab5f-0a5c-4c13-b4d6-4bf7836fc9f8')
IID_SHELL_ITEM = GUID.parse('43826d1e-e718-42ee-bc55-a1e261c37bfe')
IID_PROGRESS_SINK = GUID.parse('04b0f1a7-9490-44bc-96e1-4296a31252e2')
IID_UNKNOWN = GUID.parse('00000000-0000-0000-c000-000000000046')


def error_hresult(error):
    try:
        return normalize_hresult(getattr(error, 'hresult', E_ABORT))
    except BaseException:
        return normalize_hresult(E_ABORT)


def checked(hr, stage='unknown'):
    if hresult_failed(hr):
        raise HResultError(hr, stage)
    return hr


class ComApartment:
    """STA por hilo; S_OK/S_FALSE se equilibran, fallos no liberan COM."""
    def __init__(self, ole):
        self.ole = ole
        self.owner = None
        self.initialized = False

    def __enter__(self):
        if self.initialized:
            raise SafetyError('No se puede reutilizar una sesión COM activa.')
        self.owner = threading.get_ident()
        checked(self.ole.CoInitializeEx(None, 0x2 | 0x4), 'initialize')  # STA + DISABLE_OLE1DDE
        self.initialized = True
        return self

    def __exit__(self, *_):
        if self.owner != threading.get_ident():
            raise SafetyError('COM debe liberarse en el mismo hilo que lo inicializó.')
        if self.initialized:
            self.initialized = False
            self.ole.CoUninitialize()


def method(pointer, index, *argtypes, restype=HR):
    if not pointer:
        raise SafetyError('Puntero COM vacío.')
    vtable = C.cast(pointer, C.POINTER(C.POINTER(PTR))).contents
    return CALLBACK(restype, PTR, *argtypes)(vtable[index])


def release(pointer):
    if pointer:
        method(pointer, 2, restype=DWORD)(pointer)


class _SinkObject(C.Structure):
    _fields_ = [('vtable', C.POINTER(PTR))]


class ProgressSink:
    """Vtable IFileOperationProgressSink; pruebas solo con punteros simulados."""
    _live = {}
    _registry_lock = threading.RLock()

    def __init__(self, before_operation, item_matches=None):
        self.before_operation = before_operation
        self.item_matches = item_matches or bool
        self.pre_count = 0
        self.owner = threading.get_ident()
        self._state_lock = threading.RLock()
        self.refs = 1
        self.closed = False
        self._owner_released = False
        self._active_callbacks = 0
        self.release_uncertain = False
        self.diagnostic_hr = None
        self.diagnostic_stage = None
        self.exception_type = None
        self.pre_validated = False
        self.callback_hr = None
        self.new_item = None
        self.callback_error = False
        self.post_count = 0
        # Los nombres de callbacks rechazados permanecen como punteros opacos:
        # no convertir LPCWSTR antes de entrar en la barrera de excepciones.
        prototypes = [
            (HR, (C.POINTER(GUID), C.POINTER(PTR)), self._query),
            (DWORD, (), self._addref), (DWORD, (), self._release),
            (HR, (), self._ok), (HR, (HR,), self._ok),
            (HR, (DWORD, PTR, PTR), self._deny),
            (HR, (DWORD, PTR, PTR, HR, PTR), self._deny),
            (HR, (DWORD, PTR, PTR, PTR), self._deny),
            (HR, (DWORD, PTR, PTR, PTR, HR, PTR), self._deny),
            (HR, (DWORD, PTR, PTR, PTR), self._deny),
            (HR, (DWORD, PTR, PTR, PTR, HR, PTR), self._deny),
            (HR, (DWORD, PTR), self._pre_delete),
            (HR, (DWORD, PTR, HR, PTR), self._post_delete),
            (HR, (DWORD, PTR, PTR), self._deny),
            (HR, (DWORD, PTR, PTR, PTR, DWORD, HR, PTR), self._deny),
            (HR, (DWORD, DWORD), self._ok),
            (HR, (), self._ok), (HR, (), self._ok), (HR, (), self._ok),
        ]
        self.callbacks = [CALLBACK(result, PTR, *args)(self._catch(function, result))
                          for result, args, function in prototypes]
        self.vtable = (PTR * len(self.callbacks))(*[C.cast(fn, PTR).value for fn in self.callbacks])
        self.object = _SinkObject(self.vtable)
        self.pointer = C.cast(C.pointer(self.object), PTR)
        # La referencia COM mantiene vivo el objeto Python, no solo su contador.
        with self._registry_lock:
            self._live[self.pointer.value] = self

    def _record_error(self, error, stage='callback'):
        self.callback_error = True
        self.pre_validated = False
        self.exception_type = type(error).__name__
        try:
            self.diagnostic_hr = normalize_hresult(getattr(error, 'hresult', E_ABORT))
        except BaseException:
            self.diagnostic_hr = normalize_hresult(E_ABORT)
        self.diagnostic_stage = stage

    def _catch(self, function, restype=HR):
        fallback = 0 if restype is DWORD else E_ABORT
        def call(*args):
            active = False
            result = fallback
            try:
                with self._state_lock:
                    self._active_callbacks += 1
                    active = True
                if threading.get_ident() != self.owner:
                    raise SafetyError('Callback recibido en otro hilo.', fatal=True)
                result = function(*args)
                if restype is DWORD:
                    if isinstance(result, bool) or not isinstance(result, int) or not 0 <= result < (1 << 32):
                        raise SafetyError('Resultado ULONG inválido.', fatal=True)
                else:
                    value = normalize_hresult(result)
                    result = value if value < (1 << 31) else value - (1 << 32)
            except BaseException as error:
                # La frontera ctypes nunca propaga ni KeyboardInterrupt/SystemExit.
                try:
                    self._record_error(error)
                except BaseException:
                    pass
                result = fallback
            finally:
                if active:
                    try:
                        with self._state_lock:
                            self._active_callbacks -= 1
                    except BaseException:
                        result = fallback
            return result
        return call

    @classmethod
    def drain_owner_references(cls):
        """Después de volver de C, nunca desde una devolución de Release."""
        owner = threading.get_ident()
        with cls._registry_lock:
            for address, sink in tuple(cls._live.items()):
                with sink._state_lock:
                    if (sink.owner == owner and sink.closed and sink.refs == 0
                            and sink._active_callbacks == 0 and sink.new_item is None
                            and not sink.release_uncertain):
                        del cls._live[address]

    def _query(self, this, iid, output):
        if not output:
            return E_NOINTERFACE
        output[0] = None
        if iid and bytes(iid.contents) in (bytes(IID_PROGRESS_SINK), bytes(IID_UNKNOWN)):
            self._addref()
            output[0] = self.pointer.value
            return 0
        return E_NOINTERFACE

    def _addref(self, *_):
        with self._state_lock:
            if self.refs == 0 or self.refs == 0xFFFFFFFF:
                raise SafetyError('AddRef sobre objeto liberado o contador agotado.', fatal=True)
            self.refs += 1
            return self.refs

    def _release(self, *_):
        with self._state_lock:
            if self.refs == 0:
                raise SafetyError('Release sin referencia vigente.', fatal=True)
            self.refs -= 1
            # No retirar la vtable mientras el trampoline ctypes está ejecutándose.
            return self.refs

    def _ok(self, *_):
        return 0

    def _deny(self, *_):
        return E_ABORT

    def _pre_delete(self, this, flags, item):
        self.pre_validated = False
        if self.closed or self.callback_error:
            return E_ABORT
        self.pre_count += 1
        if (self.pre_count != 1 or not flags & TSF_DELETE_RECYCLE_IF_POSSIBLE
                or not item or not self.item_matches(item)):
            return E_ABORT
        self.before_operation()
        if self.closed or self.callback_error or self.refs == 0:
            return E_ABORT
        self.pre_validated = True
        return 0

    def _post_delete(self, this, flags, item, hr, new_item):
        if self.closed or self.callback_error:
            return E_ABORT
        self.post_count += 1
        if self.post_count != 1 or not self.pre_validated or not flags & TSF_DELETE_RECYCLE_IF_POSSIBLE:
            self.callback_error = True
            return E_ABORT
        self.callback_hr = hr
        if hr == 0 and new_item:
            method(new_item, 1, restype=DWORD)(new_item)
            self.new_item = new_item  # Referencia propia hasta terminar las comprobaciones.
        return 0

    def close(self):
        if threading.get_ident() != self.owner:
            raise SafetyError('Las referencias COM deben liberarse en su hilo propietario.', fatal=True)
        if self.closed:
            self.drain_owner_references()
            return
        self.closed = True
        try:
            release(self.new_item)
        except BaseException as error:
            self.release_uncertain = True
            self._record_error(error, 'release')
            # Se retiene la memoria; no se vuelve a liberar un puntero incierto.
            raise HResultError(self.diagnostic_hr, 'release') from None
        else:
            self.new_item = None
        finally:
            if not self._owner_released:
                self._owner_released = True
                if self.refs > 0:
                    self._release()
                else:
                    self.callback_error = True
            self.drain_owner_references()


def _load_apis():
    require_real_backend_enabled()  # Incluso llamadas directas no cargan DLL.
    if os.name != 'nt':
        raise SafetyError('Solo disponible en Windows.')
    ole = C.WinDLL('ole32', use_last_error=True)
    shell = C.WinDLL('shell32', use_last_error=True)
    ole.CoInitializeEx.argtypes, ole.CoInitializeEx.restype = [PTR, DWORD], HR
    ole.CoUninitialize.argtypes, ole.CoUninitialize.restype = [], None
    ole.CoCreateInstance.argtypes = [C.POINTER(GUID), PTR, DWORD, C.POINTER(GUID), C.POINTER(PTR)]
    ole.CoCreateInstance.restype = HR
    ole.CoTaskMemFree.argtypes, ole.CoTaskMemFree.restype = [PTR], None
    shell.SHCreateItemFromParsingName.argtypes = [C.c_wchar_p, PTR, C.POINTER(GUID), C.POINTER(PTR)]
    shell.SHCreateItemFromParsingName.restype = HR
    return ole, shell


def _shell_path(item, ole):
    text = PTR()
    try:
        checked(method(item, 5, DWORD, C.POINTER(PTR))(item, 0x80058000, C.byref(text)), 'get_path')  # SIGDN_FILESYSPATH
        return C.wstring_at(text) if text.value else None
    finally:
        if text.value:
            ole.CoTaskMemFree(text)


def recycle_one(record, before_operation):
    require_real_backend_enabled()
    return _perform_native(record, before_operation)


def _perform_native(record, before_operation):
    require_real_backend_enabled()  # No se puede eludir usando esta entrada privada.
    ole, shell = _load_apis()
    with ComApartment(ole):
        operation, item = PTR(), PTR()
        sink = ProgressSink(before_operation,
                            lambda callback_item: path_key(_shell_path(callback_item, ole)) == path_key(record.path))
        primary_error = None
        try:
            before_operation()
            checked(ole.CoCreateInstance(C.byref(CLSID_FILE_OPERATION), None, 1,
                                         C.byref(IID_FILE_OPERATION), C.byref(operation)), 'create')
            checked(method(operation, 5, DWORD)(operation, FLAGS), 'flags')
            checked(shell.SHCreateItemFromParsingName(record.path, None, C.byref(IID_SHELL_ITEM), C.byref(item)), 'item')
            # Solo DeleteItem individual; no DeleteItems, carpetas ni elementos conectados.
            checked(method(operation, 18, PTR, PTR)(operation, item, sink.pointer), 'queue')
            operation_hr = method(operation, 21)(operation)
            aborted = C.c_int32(1)
            checked(method(operation, 22, C.POINTER(C.c_int32))(operation, C.byref(aborted)), 'abort_query')
            reference = None
            verified = False
            if sink.new_item and not sink.callback_error and sink.post_count == 1:
                reference = _shell_path(sink.new_item, ole)
                if reference:
                    # Evidencia adicional, no la mera desaparición del origen.
                    validate_path(reference)
                    info = os.lstat(reference)
                    verified = not is_reparse(info) and signature(info) == record.identity
            return Receipt(operation_hr, sink.callback_hr,
                           bool(aborted.value) or sink.callback_error, reference, verified,
                           diagnostic_hr=sink.diagnostic_hr, stage=sink.diagnostic_stage,
                           fatal=sink.callback_error)
        except BaseException as error:
            primary_error = error
            raise
        finally:
            # Se intenta liberar todo, preservando el HRESULT principal si falla
            # además la limpieza. Nunca salir de STA con un Release omitido.
            cleanup_errors = []
            for action in (sink.close, lambda: release(item), lambda: release(operation)):
                try:
                    action()
                except BaseException as error:
                    cleanup_errors.append(error)
            sink.drain_owner_references()
            if cleanup_errors:
                diagnostics = tuple(error_hresult(error) for error in cleanup_errors)
                if isinstance(primary_error, HResultError):
                    primary_error.cleanup_diagnostics = diagnostics
                elif primary_error is None:
                    raise HResultError(diagnostics[0], 'release') from None
