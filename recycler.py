"""Limpieza supervisada. Esta entrega solo permite backends simulados.

No contiene borrado permanente, reintentos, ni habilitación por entorno/CLI.
Los mensajes son genéricos; las rutas solo pertenecen a resultados locales.
"""
from dataclasses import dataclass
from enum import Enum
import os
import ntpath
import stat
import threading
import uuid

from hasher import calculate_sha256, is_reparse, signature, validate_path
from selection import path_key, SelectionSnapshot, SelectionLockedError


class SafetyError(ValueError):
    def __init__(self, message, *, fatal=False):
        super().__init__(message)
        self.fatal = fatal


def normalize_hresult(value):
    if isinstance(value, bool) or not isinstance(value, int) or not -(1 << 31) <= value < (1 << 32):
        raise ValueError('HRESULT inválido.')
    return value & 0xFFFFFFFF


class HResultError(SafetyError):
    def __init__(self, hresult, stage='unknown'):
        self.hresult = normalize_hresult(hresult)
        self.stage = stage if stage in {
            'initialize', 'create', 'flags', 'item', 'queue', 'perform',
            'abort_query', 'get_path', 'release', 'callback', 'unknown'} else 'unknown'
        super().__init__(f'Error COM en {self.stage}: HRESULT 0x{self.hresult:08X}.', fatal=True)



class BackendDisabled(RuntimeError):
    pass


def require_real_backend_enabled():
    """Bloqueo deliberado: debe revisarse en una entrega posterior autorizada."""
    raise BackendDisabled('Backend real bloqueado; pendiente de revisión e integración autorizada.')


class Status(str, Enum):
    CONFIRMED = 'reciclaje_confirmado'
    FAILED = 'fallo'
    UNVERIFIABLE = 'no_verificable'
    SKIPPED = 'omitido'
    SIMULATED = 'simulado'


@dataclass(frozen=True)
class Receipt:
    operation_hr: int = 0
    callback_hr: int | None = None
    aborted: bool = False
    recycle_item: str | None = None
    destination_verified: bool = False
    dry_run: bool = False
    diagnostic_hr: int | None = None
    stage: str | None = None
    fatal: bool = False


@dataclass(frozen=True)
class ItemResult:
    path: str
    status: Status
    message: str
    attempted: bool = False
    hresult: int | None = None
    stage: str | None = None
    operation_hr: int | None = None
    callback_hr: int | None = None
    cleanup_hresults: tuple = ()


@dataclass(frozen=True)
class Report:
    items: tuple = ()
    cancelled: bool = False
    message: str = ''

    def count(self, status):
        return sum(item.status == status for item in self.items)

    @property
    def processed(self):
        return sum(item.attempted for item in self.items)

    def summary(self):
        return (f'Procesados: {self.processed} · Reciclaje confirmado: {self.count(Status.CONFIRMED)} · '
                f'Simulados: {self.count(Status.SIMULATED)} · Fallidos: {self.count(Status.FAILED)} · '
                f'No verificables: {self.count(Status.UNVERIFIABLE)} · Omitidos: {self.count(Status.SKIPPED)}')


@dataclass(frozen=True)
class Protection:
    digest: str
    keepers: tuple


@dataclass(frozen=True)
class Plan:
    token: str
    session: str
    source: str
    root_identity: tuple
    records: tuple
    protections: tuple
    selection_stamp: tuple
    content_hashes: tuple = ()

    @property
    def total_bytes(self):
        return sum(record.size for record in self.records)


@dataclass(frozen=True)
class Consent:
    token: str


def selection_stamp(selection):
    return selection.stamp if isinstance(selection, SelectionSnapshot) else selection.snapshot().stamp


def hresult_failed(value):
    return bool(normalize_hresult(value) & 0x80000000)


def classify_receipt(receipt, origin_state, simulated):
    """La desaparición nunca prueba reciclaje. Evidencia positiva por archivo."""
    if not isinstance(receipt, Receipt):
        return Status.UNVERIFIABLE
    failed = (hresult_failed(receipt.operation_hr) or receipt.aborted or receipt.fatal
              or (receipt.diagnostic_hr is not None and hresult_failed(receipt.diagnostic_hr))
              or (receipt.callback_hr is not None and hresult_failed(receipt.callback_hr)))
    if failed:
        return Status.FAILED if origin_state == 'unchanged' else Status.UNVERIFIABLE
    if receipt.dry_run:
        return Status.SIMULATED if simulated and origin_state == 'unchanged' else Status.UNVERIFIABLE
    if (receipt.operation_hr == 0 and receipt.callback_hr == 0 and receipt.recycle_item
            and receipt.destination_verified and origin_state == 'missing'):
        return Status.SIMULATED if simulated else Status.CONFIRMED
    return Status.UNVERIFIABLE


class SimulatedBackend:
    """Simulación inerte: valida, registra llamadas y NO mueve archivos."""
    simulated = True
    available = True
    recycle_only = True

    def __init__(self):
        self.calls = []

    def recycle(self, record, before_operation):
        before_operation()
        self.calls.append(record.path)
        return Receipt(dry_run=True)


class WindowsRecycleBackend:
    """Fachada real bloqueada tanto para GUI como para llamadas directas."""
    simulated = False
    available = False
    recycle_only = False  # No se afirma una garantía nativa todavía no verificada.

    def recycle(self, record, before_operation):
        require_real_backend_enabled()  # Antes de importar/cargar DLL o COM.
        from windows_recycler import recycle_one
        return recycle_one(record, before_operation)


def _root_identity(source):
    source = validate_path(source)
    info = os.lstat(source)
    if is_reparse(info) or not stat.S_ISDIR(info.st_mode) or not info.st_ino:
        raise SafetyError('No se puede validar la identidad del origen.', fatal=True)
    return (info.st_dev, info.st_ino, getattr(info, 'st_birthtime_ns', None))


DRIVE_FIXED = 3


def query_drive_type(root):
    """Consulta de capacidad de solo lectura. Las pruebas inyectan un doble.

    No inicializa COM ni llama a Shell/IFileOperation. No admite volúmenes
    desconocidos, remotos, extraíbles o de memoria como candidatos a limpieza.
    """
    if os.name != 'nt':
        raise SafetyError('No es una consulta de Windows.', fatal=True)
    import ctypes
    kernel = ctypes.WinDLL('kernel32', winmode=0x00000800)
    function = kernel.GetDriveTypeW
    function.argtypes = [ctypes.c_wchar_p]
    function.restype = ctypes.c_uint32
    return function(root)


def _safe_local_path(path):
    if not isinstance(path, str) or not os.path.isabs(path) or '\x00' in path:
        raise SafetyError('Se requiere una ruta absoluta válida.', fatal=True)
    if os.name == 'nt':
        drive, tail = ntpath.splitdrive(path)
        if len(drive) != 2 or drive[1] != ':' or drive[0] not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ' or ':' in tail:
            raise SafetyError('No se admiten rutas de red, dispositivos ni flujos alternativos.', fatal=True)
        parts = [part for part in tail.replace('/', '\\').split('\\') if part]
        reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
        reserved.update(f'{prefix}{digit}' for prefix in ('COM', 'LPT') for digit in '123456789¹²³')
        if any(part.endswith((' ', '.')) or any(char in part for char in '*?<>|"')
               or part.split('.')[0].upper() in reserved for part in parts):
            raise SafetyError('Ruta ambigua o reservada de Windows.', fatal=True)
        try:
            kind = query_drive_type(drive + '\\')
        except (OSError, ValueError):
            raise SafetyError('No se pudo validar la unidad.', fatal=True) from None
        if kind != DRIVE_FIXED:
            raise SafetyError('La unidad no es local fija o su capacidad es desconocida.', fatal=True)
    return validate_path(path)


def validate_record(record, plan):
    """Revalida cada componente, contención, tipo e identidad/metadatos."""
    if _root_identity(plan.source) != plan.root_identity:
        raise SafetyError('El directorio autorizado fue sustituido.', fatal=True)
    path = _safe_local_path(record.path)
    key, root = path_key(path), path_key(plan.source)
    try:
        inside = os.path.commonpath([root, key]) == root and key != root
    except ValueError:
        inside = False
    if not inside:
        raise SafetyError('Archivo fuera del directorio autorizado.')
    info = os.lstat(path)
    if is_reparse(info) or not stat.S_ISREG(info.st_mode) or not info.st_ino:
        raise SafetyError('No es un archivo regular validable.', fatal=True)
    if info.st_dev != plan.root_identity[0]:
        raise SafetyError('El archivo no pertenece al volumen autorizado.', fatal=True)
    if info.st_nlink > 1:
        raise SafetyError('Enlace duro omitido por seguridad en esta fase.')
    if (signature(info) != record.identity or info.st_size != record.size
            or info.st_mtime != record.modified):
        raise SafetyError('El archivo cambió o fue reemplazado desde el análisis.')
    return info


def validate_refresh_source(plan):
    """No reconstruir el inventario sobre un origen sustituido o redirigido."""
    _safe_local_path(plan.source)
    if _root_identity(plan.source) != plan.root_identity:
        raise SafetyError('El origen autorizado ya no tiene la misma identidad.')


def origin_state(record, plan):
    try:
        validate_record(record, plan)
        return 'unchanged'
    except FileNotFoundError:
        try:
            if _root_identity(plan.source) != plan.root_identity:
                return 'unknown'
            validate_path(record.path)
            parent = os.lstat(os.path.dirname(record.path))
            if not stat.S_ISDIR(parent.st_mode):
                return 'unknown'
        except (OSError, ValueError):
            return 'unknown'
        try:
            os.lstat(record.path)
        except FileNotFoundError:
            return 'missing'
        except OSError:
            return 'unknown'
        return 'unknown'
    except (OSError, ValueError):
        return 'unknown'


class Recycler:
    def __init__(self, backend=None):
        self.backend = WindowsRecycleBackend() if backend is None else backend
        self._lock = threading.RLock()
        self._session = None
        self._plans = {}
        self._consents = {}
        self._busy = False

    def invalidate(self):
        with self._lock:
            if self._busy:
                raise SafetyError('Operación en curso; no se puede cambiar el análisis.')
            self._session = None
            self._plans.clear()
            self._consents.clear()

    def bind(self, result, source):
        """Captura inmutable del inventario. No selecciona ni limpia archivos."""
        with self._lock:
            self.invalidate()
            source = _safe_local_path(os.path.abspath(source))
            identity = _root_identity(source)
            records = tuple(result.files)
            if result.source_path is not None and path_key(result.source_path) != path_key(source):
                raise SafetyError('El análisis pertenece a otro origen.', fatal=True)
            if result.source_identity is not None and result.source_identity != identity:
                raise SafetyError('El origen cambió desde el análisis.', fatal=True)
            groups = tuple((digest, tuple(members)) for digest, members in result.duplicates)
            session = uuid.uuid4().hex
            baseline = {}
            provisional = Plan('', session, source, identity, records, (), ())
            for record in records:
                key = path_key(record.path)
                try:
                    if record.path in result.content_hashes:
                        # Captura ya realizada en el worker. Se verificará otra vez
                        # antes de cada intento; bind no recorre archivos en Tk.
                        digest = result.content_hashes[record.path]
                    else:
                        validate_record(record, provisional)
                        # Compatibilidad con resultados sin captura de contenido.
                        # GUI ya captura en su worker para no hacer hashes en Tk.
                        digest = calculate_sha256(record.path)
                        validate_record(record, provisional)
                    if (isinstance(digest, str) and len(digest) == 64
                            and all(char in '0123456789abcdef' for char in digest)):
                        baseline[key] = digest
                except (OSError, ValueError):
                    continue
            self._session = (session, source, identity, records, groups, tuple(baseline.items()))

    def prepare(self, selection):
        with self._lock:
            if self._busy or self._session is None or not selection.count:
                raise SafetyError('No hay una selección y análisis vigentes.')
            session, source, identity, inventory, groups, baseline = self._session
            snapshot = selection.snapshot()
            mapping = {path_key(record.path): record for record in inventory}
            selected = snapshot.selected
            if not selected <= mapping.keys():
                raise SafetyError('Selección fuera del inventario autorizado.')
            records = tuple(mapping[key] for key in sorted(selected))
            protections = {}
            for index, (digest, members) in enumerate(groups, 1):
                member_map = {path_key(record.path): record for record in members}
                targets = selected & member_map.keys()
                if not targets:
                    continue
                if not member_map.keys() <= mapping.keys():
                    raise SafetyError('Grupo duplicado incompleto en el inventario.')
                keep_keys = member_map.keys() - selected
                explicit = dict(snapshot.retained).get(index)
                if explicit is not None:
                    if explicit not in keep_keys:
                        raise SafetyError('La copia conservada no es válida.')
                    keep_keys = {explicit}
                if not keep_keys:
                    raise SafetyError('Debe conservarse al menos una copia de cada grupo.')
                keepers = tuple(member_map[key] for key in sorted(keep_keys))
                for target in targets:
                    protections[target] = Protection(digest, keepers)
            # Preparación sin acceso a archivos: las lecturas se hacen en el hilo de trabajo.
            plan = Plan(uuid.uuid4().hex, session, source, identity, records,
                        tuple(protections.items()), snapshot.stamp, baseline)
            self._plans.clear()
            self._consents.clear()
            self._plans[plan.token] = plan
            return plan

    def _current(self, plan, selection):
        return (self._session is not None and self._session[0] == plan.session
                and self._plans.get(plan.token) is plan
                and selection_stamp(selection) == plan.selection_stamp)

    def confirm(self, plan, selection, approved=False):
        with self._lock:
            if approved is not True:
                self._plans.pop(plan.token, None)
                self._consents.clear()
                return None
            if self._busy or not self._current(plan, selection):
                raise SafetyError('La confirmación ya no corresponde al análisis y selección actuales.')
            self._consents.clear()
            consent = Consent(uuid.uuid4().hex)
            self._consents[consent.token] = plan.token
            return consent

    def _validate(self, record, plan, selection):
        if not self._current(plan, selection):
            raise SafetyError('Selección o análisis desactualizado.', fatal=True)
        candidate_info = validate_record(record, plan)
        baseline = dict(plan.content_hashes).get(path_key(record.path))
        if baseline is None:
            raise SafetyError('No existe un hash de referencia validado para este archivo.')
        digest = calculate_sha256(record.path)
        validate_record(record, plan)
        if digest is None or digest != baseline:
            raise SafetyError('El contenido cambió respecto a la referencia autorizada.')
        protection = dict(plan.protections).get(path_key(record.path))
        if protection is not None:
            if digest != protection.digest:
                raise SafetyError('El contenido duplicado no coincide con el análisis.', fatal=True)
            keeper_valid = False
            for keeper in protection.keepers:
                try:
                    info = validate_record(keeper, plan)
                    if (info.st_dev, info.st_ino) == (candidate_info.st_dev, candidate_info.st_ino):
                        continue  # Un alias al mismo objeto no es otra copia.
                    keeper_digest = calculate_sha256(keeper.path)
                    validate_record(keeper, plan)
                    if keeper_digest == protection.digest:
                        keeper_valid = True
                        break
                except (OSError, ValueError):
                    continue
            if not keeper_valid:
                raise SafetyError('No se puede validar una copia que permanecerá en el origen.', fatal=True)
        validate_record(record, plan)
        if not self._current(plan, selection):
            raise SafetyError('La versión autorizada dejó de estar vigente.', fatal=True)

    def execute(self, plan, consent, selection):
        # La selección queda inmutable sin bloquear sus getters ni Tkinter.
        try:
            with selection.freeze() as snapshot:
                return self._execute_frozen(plan, consent, snapshot)
        except SelectionLockedError:
            with self._lock:
                if isinstance(consent, Consent):
                    self._consents.pop(consent.token, None)
            return Report(cancelled=True, message='La selección ya está siendo utilizada.')

    def _execute_frozen(self, plan, consent, selection):
        """Una llamada por archivo, una autorización por lote; nunca reintenta."""
        with self._lock:
            authorized = (isinstance(consent, Consent)
                          and self._consents.pop(consent.token, None) == plan.token)
            if not authorized or self._busy or not self._current(plan, selection):
                return Report(cancelled=True, message='Operación cancelada: falta confirmación vigente de un solo uso.')
            self._busy = True
        items = []
        try:
            # No hay ajuste/flag para permitir el backend real en esta entrega.
            if isinstance(self.backend, WindowsRecycleBackend) or self.backend.simulated is not True:
                require_real_backend_enabled()
            if not self.backend.available or not self.backend.recycle_only:
                return Report(cancelled=True, message='Backend de reciclaje no disponible o sin garantía de reciclaje exclusivo.')
            stop = False
            cancelled = False
            for record in plan.records:
                if stop:
                    items.append(ItemResult(record.path, Status.SKIPPED, 'Lote detenido por una condición incompatible con la seguridad.'))
                    continue
                try:
                    self._validate(record, plan, selection)
                except (OSError, ValueError) as error:
                    message = str(error) if isinstance(error, SafetyError) else 'No se pudo validar el archivo, su origen o una copia conservada.'
                    items.append(ItemResult(record.path, Status.SKIPPED, message,
                                            hresult=getattr(error, 'hresult', None), stage=getattr(error, 'stage', None)))
                    stop = bool(getattr(error, 'fatal', False))
                    cancelled = cancelled or stop
                    continue
                try:
                    receipt = self.backend.recycle(record, lambda r=record: self._validate(r, plan, selection))
                except SafetyError as error:
                    state = origin_state(record, plan)
                    status = Status.SKIPPED if state == 'unchanged' else Status.UNVERIFIABLE
                    message = str(error) if isinstance(error, HResultError) else 'Cambio detectado por el backend; no se reintentará.'
                    items.append(ItemResult(record.path, status, message, status == Status.UNVERIFIABLE,
                                            getattr(error, 'hresult', None), getattr(error, 'stage', None),
                                            cleanup_hresults=getattr(error, 'cleanup_diagnostics', ())))
                    stop = True
                    cancelled = True
                    continue
                except BaseException:
                    state = origin_state(record, plan)
                    status = Status.FAILED if state == 'unchanged' else Status.UNVERIFIABLE
                    items.append(ItemResult(record.path, status, 'El backend informó un error; no se reintentará.', True))
                    stop = True
                    cancelled = True
                    continue
                try:
                    status = classify_receipt(receipt, origin_state(record, plan), self.backend.simulated)
                except (TypeError, ValueError, AttributeError):
                    status = Status.UNVERIFIABLE
                messages = {
                    Status.SIMULATED: 'Operación simulada; no confirma un envío real a la Papelera.',
                    Status.CONFIRMED: 'Reciclaje individual confirmado por el backend y comprobación del origen.',
                    Status.FAILED: 'Falló la operación; el archivo original sigue sin cambios.',
                    Status.UNVERIFIABLE: 'No hay evidencia suficiente de reciclaje; no se reintentará.',
                }
                diagnostic = None
                stage = receipt.stage if isinstance(receipt, Receipt) else None
                if isinstance(receipt, Receipt):
                    for value in (receipt.diagnostic_hr, receipt.callback_hr, receipt.operation_hr):
                        if value is None:
                            continue
                        try:
                            if hresult_failed(value):
                                diagnostic = normalize_hresult(value)
                                break
                        except ValueError:
                            continue
                message = messages[status]
                if diagnostic is not None:
                    message += f' HRESULT 0x{diagnostic:08X}.'
                def optional_hr(value):
                    try:
                        return normalize_hresult(value) if value is not None else None
                    except ValueError:
                        return None
                items.append(ItemResult(record.path, status, message, True, diagnostic, stage,
                                        optional_hr(receipt.operation_hr) if isinstance(receipt, Receipt) else None,
                                        optional_hr(receipt.callback_hr) if isinstance(receipt, Receipt) else None))
                stop = status in (Status.UNVERIFIABLE, Status.FAILED) or (isinstance(receipt, Receipt) and receipt.fatal)
                cancelled = cancelled or stop
            return Report(tuple(items), cancelled=cancelled,
                          message='Lote cancelado por una condición incompatible con la seguridad.' if cancelled else '')
        except BackendDisabled:
            return Report(cancelled=True, message='Backend real bloqueado; no se inició ninguna operación de Windows.')
        finally:
            with self._lock:
                self._busy = False
                self._plans.clear()
                self._consents.clear()
                self._session = None  # Requiere inventario nuevo incluso después de error.
