"""Localización de archivos; no contiene operaciones de limpieza."""
import ctypes
import os
import stat
import subprocess
from dataclasses import dataclass

from hasher import validate_path, signature, calculate_sha256


class LocationError(ValueError):
    """Mensaje local seguro, sin incluir rutas ni excepciones del sistema."""


@dataclass(frozen=True)
class LocationRequest:
    source: str
    source_identity: tuple
    record: object
    digest: str | None


def validate_source(source, identity):
    try:
        root = os.path.abspath(source)
        if root.startswith(("\\\\", "//")):
            raise ValueError
        validate_path(root)
        info = os.lstat(root)
        current = (info.st_dev, info.st_ino, getattr(info, 'st_birthtime_ns', None))
        if not stat.S_ISDIR(info.st_mode) or current != identity:
            raise ValueError
        return root
    except (OSError, ValueError, TypeError):
        raise LocationError('La carpeta autorizada cambió o no puede validarse. Selecciónala de nuevo.') from None


def validate_location(request):
    root = validate_source(request.source, request.source_identity)
    try:
        path = os.path.abspath(request.record.path)
        if '\x00' in path or os.path.normcase(os.path.commonpath((root, path))) != os.path.normcase(root):
            raise ValueError
        validate_path(path)
        info = os.lstat(path)
        if not stat.S_ISREG(info.st_mode) or signature(info) != request.record.identity:
            raise ValueError
        if not request.digest or calculate_sha256(path) != request.digest:
            raise ValueError
        validate_path(path)
        if signature(os.lstat(path)) != request.record.identity:
            raise ValueError
        validate_source(root, request.source_identity)
        return path
    except FileNotFoundError:
        raise LocationError('El archivo ya no existe. Pulsa Actualizar análisis.') from None
    except (OSError, ValueError, TypeError):
        raise LocationError('El archivo cambió, está redirigido o no puede validarse. Pulsa Actualizar análisis.') from None


class WindowsExplorerBackend:
    """Solo Shell de localización; nunca IFileOperation ni apertura del contenido."""
    def select_file(self, path):
        if os.name != 'nt':
            raise OSError('Windows requerido')
        shell = ctypes.WinDLL('shell32.dll', winmode=0x800)
        ole = ctypes.WinDLL('ole32.dll', winmode=0x800)
        ole.CoInitializeEx.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        ole.CoInitializeEx.restype = ctypes.c_int32
        ole.CoUninitialize.argtypes = []
        ole.CoUninitialize.restype = None
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        ole.CoTaskMemFree.restype = None
        shell.SHParseDisplayName.argtypes = [ctypes.c_wchar_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32, ctypes.POINTER(ctypes.c_uint32)]
        shell.SHParseDisplayName.restype = ctypes.c_int32
        shell.SHOpenFolderAndSelectItems.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.c_uint32]
        shell.SHOpenFolderAndSelectItems.restype = ctypes.c_int32
        hr = ole.CoInitializeEx(None, 2)
        if hr not in (0, 1):
            raise OSError('COM no disponible')
        pidl = ctypes.c_void_p()
        try:
            hr = shell.SHParseDisplayName(path, None, ctypes.byref(pidl), 0, None)
            if hr != 0 or not pidl.value:
                raise OSError('No se pudo localizar')
            # cidl=0: PIDL absoluto del archivo; abre su padre y lo resalta.
            if shell.SHOpenFolderAndSelectItems(pidl, 0, None, 0) != 0:
                raise OSError('No se pudo resaltar')
        finally:
            if pidl.value:
                ole.CoTaskMemFree(pidl)
            ole.CoUninitialize()

    def open_folder(self, parent):
        if os.name != 'nt':
            raise OSError('Windows requerido')
        kernel = ctypes.WinDLL('kernel32.dll', winmode=0x800)
        kernel.GetWindowsDirectoryW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
        kernel.GetWindowsDirectoryW.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.GetWindowsDirectoryW(buffer, len(buffer))
        if not 0 < length < len(buffer):
            raise OSError('Explorador no disponible')
        executable = os.path.join(buffer.value, 'explorer.exe')
        # Ejecutable del sistema y argumentos separados: ninguna interpretación de shell.
        subprocess.Popen([executable, '/e,', parent], shell=False)


def locate_file(request, backend=None):
    path = validate_location(request)
    backend = backend if backend is not None else WindowsExplorerBackend()
    try:
        backend.select_file(path)
        return 'Se solicitó al Explorador mostrar el archivo resaltado. Las marcas no cambiaron.'
    except Exception:
        # No abrir siquiera el padre si el archivo cambió durante el intento.
        path = validate_location(request)
        parent = os.path.dirname(path)
        try:
            validate_path(parent)
            if not stat.S_ISDIR(os.lstat(parent).st_mode):
                raise ValueError
            backend.open_folder(parent)
        except Exception:
            raise LocationError('No se pudo abrir el Explorador. Las marcas no cambiaron.') from None
        return 'No fue posible resaltar el archivo; se solicitó abrir su carpeta contenedora. Las marcas no cambiaron.'


def locate_for_gui(request, backend=None):
    try:
        return True, locate_file(request, backend)
    except LocationError as error:
        return False, str(error)
