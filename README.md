# limpiador-descargas

Analizador local de archivos para Windows, desarrollado solo con la biblioteca
estándar de Python. No elimina, mueve, renombra ni envía archivos a la Papelera.
No utiliza servicios externos. El origen siempre se elige explícitamente.

## Iniciar la interfaz

Desde la terminal de VS Code, dentro del proyecto:

```powershell
python gui.py
```

Requiere una instalación de Python con Tkinter/ttk (habitual en Windows).
La ventana no analiza ninguna carpeta al abrirse.

1. Pulsa **Seleccionar carpeta** y elige el origen.
2. Pulsa **Analizar archivos** para iniciar el análisis de solo lectura.
3. Consulta los indicadores y las cuatro pestañas: los 20 archivos más pesados,
   los antiguos de más de 90 días, duplicados confirmados y totales por extensión.
4. Ordena pulsando los encabezados. Las fechas se muestran en hora local y los
   tamaños y fechas se ordenan por valores reales.
5. Filtra por parte del nombre o extensión exacta (`txt` o `.txt`). Ambos filtros
   se combinan sin distinguir mayúsculas; `(sin extensión)` busca archivos sin
   sufijo. En estadísticas, el filtro de nombre busca la extensión.
6. Usa las barras de desplazamiento y **Anterior/Siguiente** para navegar páginas
   de hasta 500 filas. Los indicadores generales representan todo el análisis.
7. **Generar CSV** exporta los cuatro informes completos a una carpeta externa
   al origen. Los filtros y las marcas de revisión no cambian los CSV.
   Cada exportación crea nombres únicos sin sobrescribir reportes existentes.

## Selección y vista previa (Fase 4A)

La selección para revisión es independiente del resaltado azul de Tkinter:

- Resalta una o varias filas con Ctrl o Mayús y pulsa **Marcar resaltados** o
  **Desmarcar resaltados**. También puedes pulsar la celda **Revisión** para
  alternar una marca, o usar la barra espaciadora sobre las filas resaltadas.
- **Marcado** y el fondo amarillo identifican archivos incluidos en la vista
  previa. **Conservar** y el fondo verde identifican una copia protegida.
- **Limpiar selección** quita todas las marcas y decisiones de conservación,
  incluso las ocultas por filtros o en otras páginas. No cambia el análisis.
- El indicador muestra cantidad de archivos únicos y suma de tamaños lógicos.
  Un archivo presente en varias pestañas se cuenta una sola vez.
- Las marcas y las copias conservadas persisten al filtrar, ordenar o cambiar
  de página. Las estadísticas por extensión no representan archivos individuales
  y no permiten marcarlos.
- En duplicados, resalta una única copia y pulsa **Conservar copia resaltada**.
  Esa copia se desmarca y queda protegida. Cambiar la copia conservada no marca
  automáticamente la anterior ni las demás copias.
- Nunca se permite marcar todos los miembros de un grupo. La misma protección
  se aplica desde pesados y antiguos y considera todo el grupo, incluidos los
  miembros ocultos por filtros o paginación. En una selección múltiple se aceptan
  las marcas permitidas y se informa de las bloqueadas; queda una copia sin
  marcar, pero no se elige automáticamente una copia explícita para conservar.
- **Vista previa de limpieza** muestra nombre, tamaño, ubicación y motivos de
  revisión. Selecciona una fila y pulsa **Quitar resaltados de la selección**
  para retirarla individualmente. La ventana y los indicadores se actualizan.
  La vista previa también tiene desplazamiento y paginación.
- Nunca se marca nada por defecto, por nombre o por antigüedad. Ser antiguo
  no significa que un archivo sea menos importante. Los motivos son informativos.
- Cambiar el origen o iniciar otro análisis invalida inmediatamente las marcas,
  las decisiones de conservación y la vista previa anterior, incluso si falla
  el nuevo análisis. La selección no se guarda entre sesiones.

Esta fase termina en la vista previa. No existe botón de eliminación ni envío
real a la Papelera. Las acciones de selección solo cambian estructuras en memoria;
no vuelven a leer ni escriben archivos del origen. No se revalida su contenido
al abrir la vista previa: si han cambiado, vuelve a analizar antes de revisarlos.
Cualquier operación futura requerirá una implementación y autorización separadas.

## Consola y reportes

La interfaz de consola sigue disponible:

```text
python main.py <carpeta-origen> --output <carpeta-externa> [--days 90] [--top 20]
```

Los informes son `pesados_<id>.csv`, `antiguos_<id>.csv`,
`duplicados_<id>.csv` y `extensiones_<id>.csv`, en UTF-8 con BOM y fechas UTC.
Contienen rutas locales privadas; no se transmiten a servicios externos. La
consola muestra estadísticas sin nombres ni rutas. El origen y la salida deben
estar libres de enlaces simbólicos y puntos de redirección en sus componentes.
La salida no puede estar dentro del origen. Si se analiza el proyecto completo,
`reportes/` dentro de él no es una salida válida. Un fallo de exportación puede
 dejar CSV parciales; no se borran automáticamente.

## Estructura

- `main.py`: argparse y resumen de consola.
- `analyzer.py`: inventario iterativo, antigüedad, extensiones y duplicados.
- `hasher.py`: SHA-256 en bloques de 1 MiB y comprobaciones de lectura segura.
- `reporter.py`: CSV locales, nombres únicos y protección frente a fórmulas.
- `gui.py`: Tkinter/ttk, tablas, acciones, hilos, cola y ventana de vista previa.
- `presentation.py`: modelos de filas, formatos, filtros y ordenación puros.
- `selection.py`: selección en memoria, inventario autorizado, protección de
  duplicados, copias conservadas, estimación lógica y motivos de revisión.
- `demo_gui.py`: demostración con archivos ficticios en un temporal del proyecto.
- `tests/test_analyzer.py`: 27 pruebas originales con archivos ficticios.
- `tests/test_presentation.py`: 20 pruebas de presentación y comunicación.
- `tests/test_selection.py`: 23 pruebas de selección y vista previa.
- `reportes/`: carpeta local de informes; nunca se borran sus reportes anteriores.

## Pruebas y demostración segura

```powershell
python -m unittest discover -v
python demo_gui.py
```

La demostración crea 513 archivos ficticios y deja su carpeta seleccionada;
pulsa **Analizar archivos** para iniciar. Puedes probar las marcas, conservar
copias y abrir la vista previa sin acceder a Descargas. Al cerrar se retira solo
el temporal ficticio. Los CSV exportados fuera del temporal permanecen en la
carpeta elegida. No cambies a carpetas personales durante esta demostración.

Verificación automática de widgets reales, solo con archivos ficticios:

```powershell
python demo_gui.py --verify
```

Acciona automáticamente la interfaz sobre el temporal ficticio: comprueba
indicadores, tablas, filtros, ordenación, páginas, marcas, protección de copias,
vista previa, retirada individual, invalidación al volver a analizar y dos
exportaciones sin sobrescrituras. Comprueba que el contenido y las fechas de
modificación de los archivos ficticios sigan intactos. Cierra la ventana al
terminar. Requiere una sesión de escritorio con Tk disponible.

Las pruebas usan solo temporales dentro del proyecto. La prueba de junction
real es específica de Windows y se omite en otros sistemas. No requieren paquetes.
Se conservan las 47 pruebas anteriores y se añaden 23: total 70.

## Límites de seguridad y estimación

- El espacio potencial es una estimación lógica, no garantía de espacio físico
  recuperable. Enlaces duros, compresión y archivos dispersos afectan ese cálculo.
- El análisis no es una instantánea. No analizar carpetas que otros procesos
  estén modificando. Las comprobaciones lstat/fstat detectan muchos cambios,
  pero queda una carrera si otro proceso sustituye componentes entre la
  comprobación y la apertura en Windows.
- Se omiten enlaces simbólicos, junctions y todos los puntos de redirección.
  Los errores de acceso o lectura no detienen el análisis; su contador también
  incluye archivos que cambiaron durante el cálculo de hashes.
- Leer puede actualizar la fecha de último acceso según Windows; el programa
  no escribe intencionalmente metadatos del origen.
- Se mantiene en memoria el inventario y la selección, no contenidos completos.
  Calcular hashes de candidatos grandes consume tiempo e I/O.
- El análisis, la exportación y la preparación de tablas se ejecutan fuera del
  hilo de Tkinter. Solo el hilo principal actualiza widgets; una cola comunica
  los resultados. El indicador de actividad no estima un porcentaje.
- Durante análisis o exportación se bloquean las acciones de selección y el
  cambio de origen. Hay que esperar a que termine para cerrar; no hay cancelación.
- Esta fase no analizó Descargas reales ni implementó operaciones de limpieza.

## Fase 4B — implementación para revisión, solo simulaciones

**El backend real de Windows permanece bloqueado.** El botón **Enviar
seleccionados a la Papelera** aparece deshabilitado en `python gui.py`, incluso
con archivos marcados. No hay opción de entorno, ajuste ni argumento de consola
que permita activarlo. Tampoco se habilita mediante el módulo ni llamando
 directamente al adaptador nativo. No se cambiaron las aprobaciones de Codex.

### Arquitectura y confirmación

- `recycler.py` contiene `Recycler`, planes inmutables, confirmaciones de un
  solo uso, validación del origen/archivos/duplicados y resultados individuales.
- `bind()` captura una versión del inventario. `prepare()` captura exactamente
  las marcas y las decisiones sobre copias conservadas. `confirm()` solo acepta
  `approved=True` explícito; cancelar no genera autorización.
- Una revisión incremental en `Selection` invalida una confirmación si las
  marcas o las decisiones cambian, incluso si después se restablecen. Cambiar
  carpeta, iniciar otro análisis o preparar otro plan también invalida el anterior.
- `execute()` requiere ese plan registrado y una autorización vigente de un solo
  uso. Revalida antes de cada intento y mediante un callback inmediatamente antes
  de actuar. Cada archivo tiene como máximo un intento; no hay reintentos automáticos.
- Se comprueban contención en el origen, identidad del directorio, todos los
  componentes sin reparse, archivo regular, identidad, tamaño y modificación.
  Se rechazan rutas de red/dispositivos, flujos alternativos y alias ambiguos
  de Windows. Los enlaces duros se omiten por seguridad en esta entrega.
- Los duplicados requieren SHA-256 actualizado del candidato y de una copia
  no seleccionada que permanecerá en el origen. La protección se verifica de
  nuevo para cada archivo, no solo al preparar el lote. Si una copia elegida
  para conservar cambia o desaparece, se omite el candidato.

### Backends y estados

`SimulatedBackend` es inerte: valida y registra intentos, pero no mueve ni
elimina archivos. Las pruebas tienen además una papelera ficticia que mueve
únicamente archivos temporales a otra carpeta del mismo temporal y verifica
que todos sus contenidos sigan presentes. No usa la Papelera de Windows.

Los estados son **simulado**, **reciclaje confirmado**, **fallo**,
**no verificable** y **omitido**. Los simulados nunca se cuentan como reciclajes
reales confirmados. El resumen separa intentos de omisiones anteriores al intento.
La desaparición del origen no es evidencia suficiente: la clasificación exige
resultado satisfactorio por archivo, referencia al elemento en la Papelera,
verificación del destino, ausencia comprobada del archivo original y ausencia
 de cancelación. Un resultado no verificable detiene el resto del lote.

`windows_recycler.py` contiene el adaptador `ctypes`/`IFileOperation` en
cuarentena para revisión. Solicita `FOFX_RECYCLEONDELETE`, falla pronto ante
 errores y no utiliza `FOF_NOCONFIRMATION`, que respondería afirmativamente a
 diálogos de Windows. No hay borrado permanente alternativo. Usa operaciones
 individuales y evita elementos conectados; el callback previo comprueba el
 elemento y aborta si no se indica reciclaje o si falla la revalidación.

El adaptador no ha ejecutado ninguna llamada real de reciclaje ni se ha cargado
su API nativa durante las pruebas. Las entradas y el cargador están bloqueados
antes de inicializar COM. Las secuencias nativas se prueban con dobles Python.

COM se inicializa como STA en el hilo que lo utilizará. Tanto `S_OK` como
`S_FALSE` se equilibran con `CoUninitialize()` en ese mismo hilo; un fallo de
inicialización, incluido `RPC_E_CHANGED_MODE`, no provoca `CoUninitialize()`.
Los punteros y buffers tienen liberación explícita, y las referencias COM se
liberan antes de finalizar el apartamento, también ante excepciones.

Referencias técnicas:

- [IFileOperation y requisito STA](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nn-shobjidl_core-ifileoperation)
- [Banderas de operación](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperation-setoperationflags)
- [PostDeleteItem y elemento reciclado](https://learn.microsoft.com/en-us/windows/win32/api/shobjidl_core/nf-shobjidl_core-ifileoperationprogresssink-postdeleteitem)
- [Inicialización y equilibrio de COM](https://learn.microsoft.com/en-us/windows/win32/api/combaseapi/nf-combaseapi-coinitializeex)

### Conexión con la interfaz

La GUI incluye la ventana de confirmación y un resumen individual paginado,
con cantidades, tamaños y rutas locales. El backend simulado puede inyectarse
por código para pruebas; la aplicación normal usa la fachada real bloqueada.
La confirmación simulada se identifica claramente y no promete un movimiento.

La ejecución y la reconstrucción del inventario usan el hilo de trabajo y la
cola existentes. Solo el hilo principal actualiza Tkinter. Durante la operación
se bloquean las acciones de selección, análisis y cambio de carpeta. Al terminar
se invalidan las marcas, planes y confirmaciones anteriores. Se vuelve a leer
el inventario del mismo origen autorizado si su identidad sigue siendo válida.
Una cancelación no provoca un análisis adicional. Si la actualización falla,
se descartan los datos anteriores y se pide volver a analizar.

### Verificación ejecutada

```powershell
python -m unittest discover -v
```

**158 pruebas aprobadas: 70 anteriores y 88 nuevas, sin fallos ni omisiones en
Windows.** Las nuevas pruebas cubren confirmaciones, caducidad, archivos
modificados/sustituidos/desaparecidos, errores de acceso, rutas externas,
redirecciones y junctions ficticias, duplicados, recibos incompletos, bloqueo
real, ausencia de reintentos, actualización de inventario y ciclo de vida COM.
Las pruebas de GUI usan widgets simulados sin abrir ventanas; las de COM usan
solo objetos Python y punteros ficticios. No se ejecutó una integración real.

### Limitaciones pendientes antes de habilitar reciclaje real

- Los mocks no certifican el comportamiento de Shell, sus políticas, una
  Papelera llena/deshabilitada ni las variantes de volúmenes y permisos.
  Antes de habilitar el adaptador debe revisarse la garantía de no recurrir
  jamás a borrado permanente, incluyendo estas condiciones. El indicador
  `TSF_DELETE_RECYCLE_IF_POSSIBLE` por sí solo no demuestra tal garantía.
  Por ello, todas las operaciones reales se cancelan incondicionalmente hoy.
- Las validaciones por ruta no son atómicas con la operación Shell. Otro proceso
  podría sustituir un componente, archivo o copia conservada después de la
  última comprobación. Todavía no se implementó un bloqueo nativo que elimine
  esa carrera; el backend no debe habilitarse asumiendo que los mocks la resuelven.
- Los archivos que no son duplicados se comparan por identidad y metadatos, no
  mediante un hash original de todo el inventario. Un cambio de contenido que
  preserve identidad, tamaño y fecha podría no detectarse en ellos.
- La ABI y los callbacks del adaptador necesitan revisión e integración nativa;
  una ejecución simulada correcta no equivale a una verificación de Windows.
- Enviar a la Papelera no libera necesariamente espacio físico de inmediato:
  los archivos continúan almacenados allí. No se implementó vaciar la Papelera.
- La revisión de código y la primera integración real con archivos ficticios
  requieren una nueva autorización. No se analizaron ni modificaron Descargas
  personales en esta etapa y no se instalaron dependencias ni se utilizó Git.

## Fase 4B.1 — endurecimiento, sin habilitación nativa

Esta entrega conserva todos los bloqueos de Fase 4B. No existe una opción para
habilitar reciclaje real en el módulo ni en la GUI. No se implementó cuarentena,
movimiento, eliminación, envío a la Papelera ni integración con el Explorador.
El adaptador nativo sigue en cuarentena de código, exclusivamente para revisión.

Cambios:

- La barrera de callbacks captura `BaseException`, incluidos `KeyboardInterrupt`
  y `SystemExit`, y valida los retornos antes de devolverlos a ctypes. Los
  callbacks HRESULT fallan con `E_ABORT`; AddRef/Release devuelven un ULONG
  válido (cero ante error), nunca un HRESULT negativo convertido a ULONG.
- Un registro de referencias mantiene vivos el sink, su vtable y los callbacks
  mientras existan referencias COM. La referencia inicial tiene liberación
  explícita, el cierre es idempotente y debe hacerse en el hilo propietario.
  Los objetos se retiran del registro después de volver de C, nunca mientras
  está activo el callback de Release. Los sinks cerrados rechazan operaciones.
- Si una liberación es incierta se retiene la memoria y no se reintenta usar ese
  puntero. Una referencia externa sin liberar también mantiene vivo el sink.
  Esto prioriza evitar punteros liberados; puede retener memoria indefinidamente.
- `HResultError` conserva HRESULT normalizado y etapa. Los resultados individuales
  conservan códigos de operación, callback y errores secundarios de liberación.
  Un error de limpieza no sustituye el HRESULT principal de la operación.
- Un error de backend, cancelación, HRESULT fallido o resultado no verificable
  detiene el resto del lote. Una omisión por un archivo cambiado puede omitir
  solo ese candidato; una condición global insegura cancela el lote. No hay
  reintentos de operaciones ni borrado permanente alternativo.
- Se exige una unidad local fija. Se rechazan unidades de red asignadas a letras,
  UNC, rutas de dispositivos, flujos alternativos, nombres reservados, comodines,
  alias ambiguos y unidades desconocidas/extraíbles. `GetDriveTypeW` es una
  consulta de solo lectura; en las pruebas se sustituye por dobles Python.
- Se vuelve a comprobar el atributo reparse en el lstat final de archivo y raíz,
  el volumen, identidad y metadatos. Se incluye la fecha de creación de Windows
  cuando está disponible. Un alias al mismo objeto no cuenta como otra copia.
- La selección dispone de snapshots atómicos e inmutables. Durante la ejecución
  se bloquean mutaciones mediante la API, sin bloquear las consultas de la GUI.
  Las copias conservadas se exponen como mapa de solo lectura. La confirmación
  sigue ligada al identificador del análisis y a una revisión concreta y se
  consume también al cancelar un intento por selección ya bloqueada.
- Los resultados del análisis llevan origen e identidad del directorio. El
  vínculo con el motor rechaza un análisis de otro origen o de una raíz sustituida.
- Se captura SHA-256 para todos los archivos del análisis gráfico en el hilo de
  trabajo, verificando metadatos antes y después. Los archivos no duplicados
  también se comparan por contenido antes de un intento. La consola mantiene su
  comportamiento anterior de hashes solo para candidatos a duplicados.

La captura de contenido de GUI aumenta el tiempo e I/O: leerá el inventario
completo, aunque el backend real siga bloqueado. La ventana muestra actividad
mientras ese trabajo ocurre en el worker. `bind()` reutiliza las referencias
capturadas sin recorrer nuevamente los archivos en Tkinter. Para resultados
antiguos sin hashes, el motor dispone de captura compatible; los llamadores
externos deben ejecutar ese vínculo fuera de su hilo de interfaz.

Un hash que no pueda capturarse deja el archivo sin referencia y no permite
ningún intento. No se reintenta automáticamente esa captura fallida. Los hashes
se copian a la sesión y al plan, de forma que cambios posteriores en el objeto
`Analysis` no cambian la referencia autorizada. La captura no permite saber qué
contenido existía antes de su primera lectura y no es una instantánea atómica.

### Pruebas de esta entrega

```powershell
python -B tests/run_simulated.py
```

El ejecutor mantiene una barrera que prohíbe cargar DLL reales mediante
`ctypes.WinDLL`; los casos que prueban el adaptador usan API y punteros ficticios.
Las pruebas anteriores que usan una papelera ficticia conservan sus aserciones
sobre archivos temporales; no usan la Papelera de Windows. Los fixtures de
volumen y de referencias COM se aislaron sin eliminar las 158 pruebas anteriores.

Se añadieron 52 regresiones: total **210 pruebas**. Cubren excepciones de todos
los tipos, retornos inválidos, retención de referencias, cierre en otro hilo,
liberación incierta, HRESULT principales/secundarios, cancelación del lote,
unidades y rutas incompatibles, reparse introducido después de la primera
validación, cambios de contenido con metadatos conservados y snapshots entre hilos.

### Riesgos que no se dan por resueltos

- La validación del sistema de archivos y la acción de Shell seguirían separadas.
  La raíz, componentes, candidato y copia conservada pueden cambiar después del
  último control. No se añadieron bloqueos nativos de archivos/directorios ni
  operaciones por handle. El bloqueo de selección protege objetos Python, no
  archivos frente a otros procesos.
- Las banderas de reciclaje y las advertencias no se presentan como una garantía
  probada de ausencia de borrado permanente en todas las condiciones de Windows.
  No se comprobó una Papelera deshabilitada/llena, políticas, permisos ni
  interacción real entre Shell y callbacks. Por ello el backend permanece bloqueado.
- Los mocks no certifican el comportamiento nativo, la ABI en todas las
  arquitecturas, la liberación de referencias externas ni recuperación física.
  Un fallo de proceso, puntero inválido o error fatal del intérprete no puede
  resolverse capturando excepciones Python.
- Los identificadores pueden reutilizarse y el inventario no es transaccional.
  La fecha de creación y los hashes aumentan los controles, sin constituir una
  garantía universal frente a cambios concurrentes o datos manipulados.
- Una consulta de unidad local fija no prueba capacidad de reciclaje, no reserva
  espacio en la Papelera y no certifica todos los alias o proveedores del sistema.
- No hay diario persistente de operaciones ni implementación de cuarentena.
  No se pretende habilitar limpieza automática ni liberar almacenamiento aquí.

No se ejecutó `IFileOperation`, ni siquiera sobre archivos ficticios. No se
analizaron Descargas personales, no se instalaron dependencias, no se utilizó
Git y no se cambiaron las aprobaciones manuales. La siguiente alternativa de
limpieza asistida mediante el Explorador debe evaluarse en una etapa separada.


En los callbacks de operaciones rechazadas, los nombres LPCWSTR se representan
como punteros opacos del mismo tamaño ABI. No se decodifican cadenas antes de
entrar en la barrera de excepciones. Las pruebas conservan las aserciones de
rechazo originales usando buffers ficticios con su puntero correspondiente.

## Fase 4B.2: limpieza asistida en el Explorador

Inicia la aplicación con `python gui.py`. Selecciona una carpeta y pulsa
**Analizar archivos**; no se accede automáticamente a Descargas.

En las pestañas de archivos, resalta una sola fila y pulsa **Abrir ubicación en
Explorador**. También puedes hacerlo desde **Vista previa**, resaltando una fila.
La fila resaltada es independiente de las marcas de revisión: abrir su ubicación
no cambia la cantidad marcada ni la estimación de tamaño.

La aplicación revalida carpeta autorizada, identidad, metadatos y SHA-256 del
archivo. Si desapareció, cambió, hay redirecciones o no existe una huella válida,
no intenta localizarlo y solicita actualizar el análisis. Las rutas de red y de
dispositivo se rechazan para esta función.

`explorer.py` utiliza `SHParseDisplayName` y `SHOpenFolderAndSelectItems` en un
hilo de trabajo, con inicialización/liberación de COM y de PIDL en ese hilo.
Si no puede resaltar el archivo, vuelve a validarlo e intenta abrir exclusivamente
la carpeta contenedora con el ejecutable del Explorador del directorio de Windows,
argumentos separados y `shell=False`. No se ejecuta el archivo seleccionado.
El mensaje informa de una solicitud aceptada, no garantiza qué ventana será visible.

Después de realizar cambios manuales en Windows, pulsa **Actualizar análisis**.
Se descartan las marcas, las copias conservadas y las confirmaciones anteriores,
y se vuelve a comprobar la identidad de la carpeta antes del análisis. Si la
carpeta fue reemplazada, debes seleccionarla de nuevo. El nuevo inventario requiere
marcas explícitas; nunca se realiza limpieza automática.

El reciclaje nativo sigue bloqueado. `recycler.py`, `windows_recycler.py` y sus
barreras no se modifican en esta fase. No se realiza eliminación, movimiento ni
renombrado de archivos desde la aplicación.

### Verificación y demostración ficticia

- `python -B tests/run_simulated.py`: suite con barrera de carga de DLL reales.
- `python -B demo_gui.py`: ventana con 513 archivos temporales ficticios y
  **Explorador simulado**, incluso al pulsar los botones de localización.
- `python -B demo_gui.py --verify`: verifica automáticamente tablas, vista previa,
  localización simulada y actualización de análisis con widgets Tk reales.

Las pruebas de localización usan mocks, sin abrir Explorer ni carpetas personales.
Los temporales de demostración se retiran al cerrar y se comprueba que su contenido
y fecha de modificación permanezcan intactos.

Limitaciones: la integración real de navegación en Explorer no se ha probado en
esta etapa. Validar y abrir no constituye una operación atómica: un proceso externo
puede cambiar rutas después de la última comprobación. Windows controla sus vistas,
miniaturas y extensiones del Shell. La comprobación SHA-256 puede tardar con archivos
grandes; se ejecuta fuera del hilo de Tkinter. Abrir una ubicación no recupera
espacio; la estimación corresponde únicamente a las marcas en memoria.

Referencias oficiales:
[SHOpenFolderAndSelectItems](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shopenfolderandselectitems)
y [SHParseDisplayName](https://learn.microsoft.com/en-us/windows/win32/api/shlobj_core/nf-shlobj_core-shparsedisplayname).

## Fase 5: distribución Windows 10 x64

La distribución verificada está en:

```text
dist/final/LimpiadorDeDescargas/LimpiadorDeDescargas.exe
```

Abre ese archivo con doble clic. Distribuye **toda la carpeta
LimpiadorDeDescargas**, incluyendo `_internal`; no basta con copiar el `.exe`.
El destinatario no necesita Python, VS Code ni el entorno `.venv`.
La interfaz comienza sin carpeta ni análisis automático. El reciclaje real sigue
bloqueado, también en el código empaquetado.

Los reportes se guardan en la carpeta que elijas mediante **Generar CSV**.
La aplicación rechaza tanto el directorio analizado como la carpeta del ejecutable
congelado y sus directorios internos. Elige una carpeta de datos separada.
No se empaquetan reportes, Descargas, pruebas, fixtures ni carpetas de demostración.

### Construcción

Requisitos del equipo de construcción: Windows, Python 3.13 de 64 bits con Tcl/Tk
funcional, al menos 2 GiB libres y autorización para descargar las dependencias.
Solo el equipo que construye necesita Python y PyInstaller.

Desde la carpeta del proyecto, en PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --no-cache-dir -r requirements-build.txt
.\build.ps1
```

`build.ps1` comprueba versión, arquitectura, espacio y disponibilidad de Tcl;
ejecuta la suite simulada antes de construir y rechaza una salida que ya exista.
No instala paquetes ni borra distribuciones anteriores. Para otra construcción:

```powershell
.\build.ps1 -OutputDirectory dist/otra-version -WorkDirectory build/otra-version
```

Si la política de Windows bloquea scripts PowerShell, **no es necesario cambiarla**.
Ejecuta los pasos directamente, cada uno por separado, y continúa solo si termina
correctamente. Usa directorios nuevos para conservar construcciones anteriores:

```powershell
.\.venv\Scripts\python.exe -c "import tkinter; print(tkinter.Tcl().eval('info patchlevel'))"
.\.venv\Scripts\python.exe -B tests/run_simulated.py
.\.venv\Scripts\python.exe -m PyInstaller --distpath dist/otra-version --workpath build/otra-version LimpiadorDeDescargas.spec
```

El `.spec` usa rutas derivadas de su ubicación, `onedir`, `console=False`, sin UPX,
sin privilegios administrativos y sin datos añadidos. Los hooks de PyInstaller
recogen Python y Tcl/Tk. La aplicación distribuida sigue usando biblioteca estándar;
las dependencias adicionales pertenecen exclusivamente a la construcción.

### Resultados y alcance de verificación

- Python 3.13.5 x64; PyInstaller 6.22.3; Tcl/Tk 8.6.15; Windows 10 19045.
- 239 pruebas originales aprobadas antes de construir. Con siete regresiones
  adicionales de exportación congelada: **246 aprobadas, 0 fallidas**.
- Paquete final: **25.65 MiB**, 991 archivos. PE x64, subsistema Windows GUI.
- Copia del ejecutable abierta en una carpeta temporal fuera del proyecto, desde
  otro directorio de trabajo y con `PATH` limitado a System32, sin variables de
  Python/venv/Tcl heredadas. Se comprobó la aparición de su ventana Tkinter.
- El controlador externo `tests/verify_bundle_code.py` carga los nueve módulos
  de la aplicación desde el archivo EXE, no desde los fuentes. Verifica con Tk real
  513 archivos ficticios, análisis, duplicados, selección, conservación de copias,
  filtros, páginas, vista previa, actualización y ocho CSV sin sobrescritura.
  Comprueba el botón de reciclaje deshabilitado al actualizar controles y la
  barrera que rechaza el backend real. No llama a IFileOperation.
- La inspección de los archivos y del archivo PYZ confirma que no se incluyeron
  pruebas, demostración ni CSV.
- Se verificó la solicitud nativa de localización mediante el código contenido
  en el paquete, exclusivamente para `copia_a.txt` de la carpeta ficticia.
  El retorno indica solicitud aceptada, sin garantizar visualmente el resaltado.
- Los archivos ficticios conservaron contenido y fecha de modificación.

**Límite de estas verificaciones:** la prueba funcional automatizada carga el código
empaquetado en un controlador Python externo. No equivale a automatizar todos los
botones dentro del proceso del EXE. Los clics externos no dieron resultados fiables;
la captura de ventana no se ejecutó porque su autorización fue rechazada. Queda
pendiente una revisión manual completa en el EXE y una prueba en otro equipo sin
Python instalado; el inicio con PATH aislado no sustituye esa última prueba.

Para revisar manualmente el EXE, usa solo una carpeta de archivos ficticios:
selecciónala, analiza, marca una copia de duplicado, abre la vista previa, genera CSV
en una carpeta externa y abre su ubicación. Verifica que el botón de reciclaje
permanezca deshabilitado y que actualizar el análisis descarte las marcas.

### Incidencias de construcción y protecciones de Windows

1. El sandbox bloqueó la red de pip (WinError 10013). La instalación autorizada
   se completó con acceso de red explícito, únicamente dentro de `.venv`.
2. Windows rechazó `build.ps1` por su política de ejecución. Se mantuvo esa política
   y se usaron los comandos directos.
3. La primera construcción bajo sandbox no pudo cargar `init.tcl` y excluyó Tkinter.
   **La salida `dist/LimpiadorDeDescargas` es diagnóstica y no debe distribuirse.**
   Se conservó sin borrar. La reconstrucción autorizada en `dist/final` incluyó
   `_tkinter`, Tcl y Tk; sus archivos `init.tcl` y `tk.tcl` están presentes.
4. El informe final `build/final/LimpiadorDeDescargas/warn-LimpiadorDeDescargas.txt`
   enumera imports condicionales de otras plataformas (`posix`, `pwd`, `grp`,
   `fcntl`, Java) y módulos especiales de importación. No vuelve a señalar Tkinter.
   El controlador de pruebas tuvo una consulta a un widget ya destruido, corregida;
   la repetición funcional pasó.
5. No se observó un bloqueo o aviso de Defender/SmartScreen en los lanzamientos
   comprobados. Esto no garantiza que otro equipo no lo muestre. El ejecutable
   no está firmado; no se desactivaron protecciones ni se añadieron exclusiones.

SHA-256 del EXE final:

```text
a64e99a50be9d563743eea3acd4ba4d7393ad114354d8b5647609d91e66fa5d7
```

Los controladores externos y las pruebas permanecen en `tests/` y no se distribuyen.
La copia temporal de prueba y sus tres fixtures se conservan para revisión; su
ubicación está registrada localmente en `build/verification-state.json`. No se
eliminaron ni movieron archivos personales, no se hizo reciclaje, no se cambió la
configuración de aprobaciones y no se utilizó Git.
