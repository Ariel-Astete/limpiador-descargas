"""Interfaz local. Solo el hilo principal puede acceder a Tkinter."""
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from analyzer import analyze_directory, capture_analysis_content
from presentation import filter_rows, format_date, format_size, make_rows, page_rows, sort_rows
from reporter import write_reports
from selection import Selection
from explorer import LocationRequest, LocationError, locate_for_gui, validate_source
from recycler import Recycler, SafetyError, WindowsRecycleBackend, validate_refresh_source


ERRORS = {
    'analysis': 'No se pudo analizar la carpeta. Comprueba que exista, que tengas permisos y que no sea una redirección.',
    'export': 'No se pudieron completar los reportes. Elige una carpeta fuera del origen, sin redirecciones y con permisos de escritura. Pueden quedar CSV parciales.',
    'cleanup': 'No se pudo completar la simulación. El inventario debe volver a analizarse; no se reintentará automáticamente.',
    'location': 'No se pudo localizar el archivo. Actualiza el análisis y vuelve a intentarlo.',
    'view': 'No se pudo actualizar la tabla. Intenta cambiar o limpiar los filtros.',
}


def run_job(events, kind, token, function, *args):
    """No recibe widgets. No publica excepciones con rutas personales."""
    try:
        value = function(*args)
    except Exception:
        events.put(('error', kind, token, ERRORS[kind]))
    else:
        events.put(('ok', kind, token, value))


def analyze_for_gui(source):
    result = analyze_directory(source)
    capture_analysis_content(result, source)
    return result, make_rows(result)


def refresh_for_gui(source, identity):
    validate_source(source, identity)
    return analyze_for_gui(source)


def prepare_view(rows, name, extension, column, descending):
    return sort_rows(filter_rows(rows, name, extension), column, descending)


def cleanup_for_gui(recycler, plan, consent, selection):
    report = recycler.execute(plan, consent, selection)
    if report.cancelled and not report.processed:
        return report, None, None
    try:
        validate_refresh_source(plan)
        result, rows = analyze_for_gui(plan.source)
    except Exception:
        result, rows = None, None
    return report, result, rows


def paged_tree(tree, parent, rows, values_for):
    """Máximo 500 inserciones Tk por página, también en confirmación/resumen."""
    footer = ttk.Frame(parent)
    footer.pack(fill='x', pady=8)
    label = tk.StringVar()
    ttk.Label(footer, textvariable=label).pack(side='left')
    state = [0]
    def render(direction=0):
        children = tree.get_children()
        if children:
            tree.delete(*children)
        visible, page, pages = page_rows(rows, state[0] + direction, 500)
        state[0] = page
        for row in visible:
            tree.insert('', 'end', values=values_for(row))
        label.set(f'{len(rows)} archivos · Página {page + 1}/{pages}')
        previous.configure(state='normal' if page else 'disabled')
        following.configure(state='normal' if page + 1 < pages else 'disabled')
    previous = ttk.Button(footer, text='Anterior', command=lambda: render(-1))
    previous.pack(side='right', padx=6)
    following = ttk.Button(footer, text='Siguiente', command=lambda: render(1))
    following.pack(side='right', padx=6)
    render()


class Application:
    PAGE_SIZE = 500
    SPECS = {
        'largest': ('Archivos más pesados', ('review', 'name', 'size', 'modified', 'extension', 'path')),
        'old': ('Archivos antiguos', ('review', 'name', 'size', 'modified', 'extension', 'path')),
        'duplicates': ('Archivos duplicados', ('review', 'group', 'name', 'size', 'modified', 'extension', 'path', 'digest')),
        'extensions': ('Estadísticas por extensión', ('extension', 'count', 'size')),
    }
    LABELS = {'review': 'Revisión', 'name': 'Nombre', 'size': 'Tamaño', 'modified': 'Modificado (hora local)',
              'extension': 'Extensión', 'path': 'Ruta local', 'group': 'Grupo',
              'count': 'Archivos', 'digest': 'SHA-256'}

    def __init__(self, root, recycle_backend=None, explorer_backend=None):
        self.root = root
        self.explorer_backend = explorer_backend
        self.events = queue.Queue()
        self.busy = False
        self.closed = False
        self.result = None
        self.result_source = None
        self.recycler = Recycler(recycle_backend)
        self.rows = {key: () for key in self.SPECS}
        self.filtered = {key: [] for key in self.SPECS}
        self.sorts = {key: ('size', True) for key in self.SPECS}
        self.sorts['duplicates'] = ('group', False)
        self.tokens = {key: 0 for key in self.SPECS}
        self.pages = {key: 0 for key in self.SPECS}
        self.tables = {}
        self.page_labels = {}
        self.page_buttons = {}
        self.visible_rows = {}
        self.selection = Selection()
        self.preview_window = None
        self.selection_buttons = []
        self.debounce = None
        root.title('Limpiador de Descargas · Analizador local')
        root.geometry('1280x880')
        root.minsize(1000, 720)
        root.protocol('WM_DELETE_WINDOW', self.close)
        self.source = tk.StringVar()
        self.name_filter = tk.StringVar()
        self.extension_filter = tk.StringVar()
        self.status = tk.StringVar(value='Selecciona una carpeta y pulsa Analizar archivos para comenzar.')
        self.metrics = [tk.StringVar(value='—') for _ in range(6)]
        self.selection_summary = tk.StringVar(value='0 archivos marcados · Estimación lógica: 0 B')
        self._build()
        self._poll_id = root.after(100, self._poll)

    def _build(self):
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('.', font=('Segoe UI', 10))
        style.configure('TFrame', background='#f3f6fa')
        style.configure('TLabel', background='#f3f6fa', foreground='#23324a')
        style.configure('Title.TLabel', font=('Segoe UI', 24, 'bold'))
        style.configure('Metric.TLabel', font=('Segoe UI', 18, 'bold'), foreground='#1664ad')
        style.configure('Accent.TButton', background='#1664ad', foreground='white', padding=(16, 10))
        style.map('Accent.TButton', background=[('active', '#124d85'), ('disabled', '#d4dce6')])
        style.configure('TButton', padding=(10, 7))
        style.configure('Treeview', rowheight=29, background='white', fieldbackground='white')
        style.configure('Treeview.Heading', font=('Segoe UI', 10, 'bold'), padding=8)
        body = ttk.Frame(self.root, padding=20)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Limpiador de Descargas', style='Title.TLabel').pack(anchor='w')
        ttk.Label(body, text='Analiza el espacio de tus archivos de forma local · Solo lectura').pack(anchor='w', pady=(3, 16))
        controls = ttk.Frame(body)
        controls.pack(fill='x')
        self.select_button = ttk.Button(controls, text='Seleccionar carpeta', command=self.select_source)
        self.select_button.pack(side='left')
        ttk.Entry(controls, textvariable=self.source, state='readonly').pack(side='left', fill='x', expand=True, padx=10)
        self.analyze_button = ttk.Button(controls, text='Analizar archivos', style='Accent.TButton', command=self.start_analysis, state='disabled')
        self.analyze_button.pack(side='left')
        self.refresh_button = ttk.Button(controls, text='Actualizar análisis', command=self.refresh_analysis, state='disabled')
        self.refresh_button.pack(side='left', padx=6)
        self.export_button = ttk.Button(controls, text='Generar CSV', command=self.select_export, state='disabled')
        self.export_button.pack(side='left', padx=(10, 0))
        cards = ttk.Frame(body)
        cards.pack(fill='x', pady=18)
        labels = ('Total de archivos', 'Tamaño analizado', 'Antiguos (>90 días)', 'Grupos duplicados', 'Errores de acceso/lectura', 'Omitidos')
        for index, label in enumerate(labels):
            cards.columnconfigure(index, weight=1)
            card = ttk.Frame(cards, padding=8, relief='solid', borderwidth=1)
            card.grid(row=0, column=index, sticky='nsew', padx=3)
            ttk.Label(card, text=label, wraplength=155).pack(anchor='w')
            ttk.Label(card, textvariable=self.metrics[index], style='Metric.TLabel').pack(anchor='w', pady=(6, 0))
        filters = ttk.Frame(body)
        filters.pack(fill='x', pady=(0, 8))
        ttk.Label(filters, text='Nombre contiene:').pack(side='left')
        self.name_entry = ttk.Entry(filters, textvariable=self.name_filter, width=25)
        self.name_entry.pack(side='left', padx=(6, 16))
        ttk.Label(filters, text='Extensión exacta:').pack(side='left')
        self.ext_entry = ttk.Entry(filters, textvariable=self.extension_filter, width=18)
        self.ext_entry.pack(side='left', padx=6)
        self.clear_button = ttk.Button(filters, text='Limpiar filtros', command=self.clear_filters)
        self.clear_button.pack(side='left', padx=6)
        self.name_filter.trace_add('write', self._schedule_filter)
        self.extension_filter.trace_add('write', self._schedule_filter)
        ttk.Label(body, text='Ordena pulsando los encabezados. En estadísticas, el filtro de nombre busca la extensión.').pack(anchor='w', pady=(0, 8))
        selection_bar = ttk.Frame(body)
        selection_bar.pack(fill='x', pady=(0, 8))
        ttk.Label(selection_bar, textvariable=self.selection_summary).pack(side='left')
        self.preview_button = ttk.Button(selection_bar, text='Vista previa de limpieza', command=self.open_preview, state='disabled')
        self.preview_button.pack(side='right')
        self.selection_buttons.append(self.preview_button)
        cleanup_bar = ttk.Frame(body)
        cleanup_bar.pack(fill='x', pady=(0, 8))
        self.recycle_button = ttk.Button(cleanup_bar, text='Enviar seleccionados a la Papelera', command=self.open_cleanup_confirmation, state='disabled')
        self.recycle_button.pack(side='right')
        ttk.Label(cleanup_bar, text='Reciclaje real bloqueado · Solo pruebas simuladas en esta etapa').pack(side='left')
        ttk.Label(body, text='Solo vista previa. El tamaño es una estimación lógica; la antigüedad no indica importancia.').pack(anchor='w', pady=(0, 8))
        notebook = ttk.Notebook(body)
        notebook.pack(fill='both', expand=True)
        for key, (title, columns) in self.SPECS.items():
            frame = ttk.Frame(notebook, padding=8)
            notebook.add(frame, text=title)
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            tree = ttk.Treeview(frame, columns=columns, show='headings', selectmode='extended')
            for column in columns:
                width = {'path': 350, 'name': 220, 'modified': 180, 'digest': 470}.get(column, 110)
                tree.column(column, width=width, minwidth=80, stretch=column in ('name', 'path'), anchor='e' if column in ('size', 'count', 'group') else 'w')
                tree.heading(column, text=self.LABELS[column], command=(lambda k=key, c=column: self.sort_table(k, c)) if column != 'review' else '')
            vertical = ttk.Scrollbar(frame, orient='vertical', command=tree.yview)
            horizontal = ttk.Scrollbar(frame, orient='horizontal', command=tree.xview)
            tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
            tree.grid(row=0, column=0, sticky='nsew')
            vertical.grid(row=0, column=1, sticky='ns')
            horizontal.grid(row=1, column=0, sticky='ew')
            footer = ttk.Frame(frame)
            footer.grid(row=2, column=0, columnspan=2, sticky='ew', pady=(8, 0))
            text = tk.StringVar(value='Sin resultados')
            ttk.Label(footer, textvariable=text).pack(side='left')
            previous = ttk.Button(footer, text='Anterior', command=lambda k=key: self.change_page(k, -1), state='disabled')
            previous.pack(side='right', padx=4)
            following = ttk.Button(footer, text='Siguiente', command=lambda k=key: self.change_page(k, 1), state='disabled')
            following.pack(side='right', padx=4)
            tree.tag_configure('marked', background='#fff0c2')
            tree.tag_configure('retained', background='#d4f3e7')
            self.visible_rows[key] = {}
            if key != 'extensions':
                actions = ttk.Frame(frame)
                actions.grid(row=3, column=0, columnspan=2, sticky='ew', pady=(8, 0))
                for label, action in (('Marcar resaltados', 'mark'), ('Desmarcar resaltados', 'unmark'), ('Limpiar selección', 'clear')):
                    button = ttk.Button(actions, text=label, command=lambda k=key, a=action: self.selection_action(k, a), state='disabled')
                    button.pack(side='left', padx=(0, 6))
                    self.selection_buttons.append(button)
                if key != 'extensions':
                    location = ttk.Button(actions, text='Abrir ubicación en Explorador', command=lambda k=key: self.open_table_location(k), state='disabled')
                    location.pack(side='left', padx=4)
                    self.selection_buttons.append(location)
                if key == 'duplicates':
                    button = ttk.Button(actions, text='Conservar copia resaltada', command=lambda: self.selection_action('duplicates', 'keep'), state='disabled')
                    button.pack(side='left', padx=6)
                    self.selection_buttons.append(button)
                tree.bind('<Button-1>', lambda event, k=key: self.toggle_review(event, k))
                tree.bind('<space>', lambda event, k=key: self.toggle_highlighted(k))
            self.tables[key] = tree
            self.page_labels[key] = text
            self.page_buttons[key] = (previous, following)
        self.progress = ttk.Progressbar(body, mode='indeterminate')
        self.progress.pack(fill='x', pady=(12, 6))
        ttk.Label(body, textvariable=self.status, wraplength=1100).pack(anchor='w')

    def select_source(self):
        if self.busy:
            return
        selected = filedialog.askdirectory(parent=self.root, title='Selecciona la carpeta a analizar', mustexist=True)
        if selected:
            self.set_source(selected)

    def set_source(self, selected):
        """Seleccionar no analiza: usado también por la demostración ficticia."""
        if self.busy:
            return
        self.source.set(selected)
        self.result = None
        self.result_source = None
        self.invalidate_selection()
        self.rows = {key: () for key in self.SPECS}
        for key in self.SPECS:
            self.tokens[key] += 1
            self.filtered[key] = []
            self.pages[key] = 0
            self.render_table(key)
        for metric in self.metrics:
            metric.set('—')
        self.analyze_button.configure(state='normal')
        self.export_button.configure(state='disabled')
        self.status.set('Carpeta seleccionada. Pulsa Analizar archivos; todavía no se ha leído su contenido.')

    def _launch(self, kind, token, function, *args):
        threading.Thread(target=run_job, args=(self.events, kind, token, function, *args), daemon=True).start()

    def _set_busy(self, busy, status=None):
        self.busy = busy
        for button in (self.select_button, self.analyze_button, self.export_button, self.clear_button):
            button.configure(state='disabled' if busy else 'normal')
        for entry in (self.name_entry, self.ext_entry):
            entry.configure(state='disabled' if busy else 'normal')
        if not busy:
            self.analyze_button.configure(state='normal' if self.source.get() else 'disabled')
            self.export_button.configure(state='normal' if self.result is not None else 'disabled')
            self.progress.stop()
        else:
            self.progress.start(12)
        if hasattr(self, 'refresh_button'):
            self.refresh_button.configure(state='normal' if self.result is not None and not self.busy else 'disabled')
        self.update_selection_controls()
        if self.preview_window is not None:
            self.render_preview()
        if status:
            self.status.set(status)

    def start_analysis(self):
        if self.busy or not self.source.get():
            return
        # Captura de origen: nunca se lee una StringVar desde un hilo.
        source = self.source.get()
        self.result = None
        self.result_source = source
        self.invalidate_selection()
        self.rows = {key: () for key in self.SPECS}
        for key in self.SPECS:
            self.tokens[key] += 1
            self.filtered[key] = []
            self.pages[key] = 0
            self.render_table(key)
        for metric in self.metrics:
            metric.set('—')
        self._set_busy(True, 'Analizando archivos… La duración depende del tamaño y de los candidatos a duplicados.')
        self._launch('analysis', source, analyze_for_gui, source)

    def refresh_analysis(self):
        if self.busy or self.result is None:
            return
        source, identity = self.result_source, self.result.source_identity
        # Invalidar de inmediato, incluso si la carpeta ya no puede validarse.
        self.invalidate_selection()
        self._set_busy(True, 'Actualizando análisis; selección anterior invalidada…')
        self.result = None
        self._launch('analysis', source, refresh_for_gui, source, identity)

    def open_table_location(self, key):
        self.open_location(self.highlighted_paths(key))

    def open_preview_location(self):
        paths = [self.preview_paths[iid] for iid in self.preview_tree.selection()
                 if iid in self.preview_paths]
        self.open_location(paths)

    def open_location(self, paths):
        if self.busy or self.result is None:
            return
        if len(paths) != 1:
            messagebox.showinfo('Localizar archivo', 'Resalta una única fila para abrir su ubicación.', parent=self.root)
            return
        record = next((item for item in self.result.files if item.path == paths[0]), None)
        if record is None:
            messagebox.showwarning('Archivo no localizado', 'La fila no pertenece al inventario actual. Actualiza el análisis.', parent=self.root)
            return
        request = LocationRequest(self.result_source, self.result.source_identity, record,
                                  self.result.content_hashes.get(record.path))
        self._set_busy(True, 'Validando archivo y solicitando su ubicación al Explorador…')
        self._launch('location', None, locate_for_gui, request, self.explorer_backend)

    def select_export(self):
        if self.busy or self.result is None:
            return
        output = filedialog.askdirectory(parent=self.root, title='Selecciona una carpeta de reportes fuera del origen', mustexist=True)
        if output:
            self.start_export(output)

    def start_export(self, output):
        if self.busy or self.result is None:
            return
        self._set_busy(True, 'Generando cuatro reportes CSV locales…')
        self._launch('export', None, write_reports, self.result, self.result_source, output)

    def _poll(self):
        if self.closed:
            return
        for _ in range(20):
            try:
                outcome, kind, token, value = self.events.get_nowait()
            except queue.Empty:
                break
            if kind == 'view':
                key, version = token
                if version != self.tokens[key]:
                    continue
                if outcome == 'ok':
                    self.filtered[key] = value
                    self.render_table(key)
                else:
                    self.status.set(value)
                continue
            self._set_busy(False)
            if outcome == 'error':
                if kind == 'cleanup':
                    self.clear_cleanup_inventory()
                if kind == 'analysis':
                    self.result_source = None
                self.status.set(value)
                messagebox.showerror('Operación incompleta', value, parent=self.root)
            elif kind == 'location':
                success, text = value
                self.status.set(text)
                if not success:
                    messagebox.showwarning('Archivo no localizado', text, parent=self.root)
            elif kind == 'analysis':
                self.result, self.rows = value
                result = self.result
                self.bind_recycler(result, self.result_source)
                self.selection = Selection(result, self.result_source)
                self.sync_selection()
                for variable, text in zip(self.metrics, (len(result.files), format_size(result.total_bytes), len(result.old), len(result.duplicates), result.errors, result.skipped)):
                    variable.set(str(text))
                self.export_button.configure(state='normal')
                self.refresh_views()
                text = f'Análisis terminado: {len(result.files)} archivos. Errores de acceso/lectura: {result.errors}; omitidos: {result.skipped}.'
                self.status.set(text)
                if result.errors:
                    messagebox.showwarning('Análisis terminado con incidencias', text, parent=self.root)
                else:
                    messagebox.showinfo('Análisis terminado', text, parent=self.root)
            elif kind == 'cleanup':
                report, result, rows = value
                source = token
                self.clear_cleanup_inventory()
                if result is not None:
                    self.result, self.rows, self.result_source = result, rows, source
                    self.bind_recycler(result, source)
                    self.selection = Selection(result, source)
                    for variable, text in zip(self.metrics, (len(result.files), format_size(result.total_bytes), len(result.old), len(result.duplicates), result.errors, result.skipped)):
                        variable.set(str(text))
                    self.sync_selection()
                    self.export_button.configure(state='normal')
                    self.refresh_views()
                text = report.summary() + (' · Inventario actualizado; selección vacía.' if result is not None else ' · No se pudo actualizar el inventario; vuelve a analizar.')
                if report.cancelled:
                    text = report.message + ' ' + text
                self.status.set(text)
                self.show_cleanup_report(report, text)
            elif kind == 'export':
                text = f'Se generaron {len(value)} CSV sin sobrescribir reportes anteriores. Los reportes incluyen todo el análisis, independientemente de los filtros.'
                self.status.set(text)
                messagebox.showinfo('Reportes generados', text, parent=self.root)
        self._poll_id = self.root.after(100, self._poll)

    def clear_filters(self):
        self.name_filter.set('')
        self.extension_filter.set('')

    def _schedule_filter(self, *_):
        if self.debounce:
            self.root.after_cancel(self.debounce)
        # Invalida inmediatamente vistas anteriores, incluso durante el debounce.
        for key in self.SPECS:
            self.tokens[key] += 1
        self.debounce = self.root.after(250, self.refresh_views)

    def refresh_views(self):
        self.debounce = None
        for key in self.SPECS:
            self.pages[key] = 0
            self.refresh_view(key)

    def refresh_view(self, key):
        self.tokens[key] += 1
        column, descending = self.sorts[key]
        self._launch('view', (key, self.tokens[key]), prepare_view,
                     self.rows[key], self.name_filter.get(), self.extension_filter.get(), column, descending)

    def sort_table(self, key, column):
        if column == 'review':
            return
        old_column, descending = self.sorts[key]
        self.sorts[key] = (column, not descending if old_column == column else column in ('size', 'count', 'modified'))
        self.pages[key] = 0
        self.refresh_view(key)

    def change_page(self, key, direction):
        self.pages[key] += direction
        self.render_table(key)

    def render_table(self, key):
        tree = self.tables[key]
        items = tree.get_children()
        if items:
            tree.delete(*items)
        rows, page, pages = page_rows(self.filtered[key], self.pages[key], self.PAGE_SIZE)
        self.pages[key] = page
        columns = self.SPECS[key][1]
        self.visible_rows[key] = {}
        for row in rows:
            values = [self.review_label(row.path) if column == 'review' else
                      format_size(row.size) if column == 'size' else
                      format_date(row.modified) if column == 'modified' else getattr(row, column)
                      for column in columns]
            iid = tree.insert('', 'end', values=values, tags=self.review_tags(row.path))
            self.visible_rows[key][iid] = row
        tree.yview_moveto(0)
        for column in columns:
            active, descending = self.sorts[key]
            marker = (' ▼' if descending else ' ▲') if column == active else ''
            tree.heading(column, text=self.LABELS[column] + marker)
        self.page_labels[key].set(f'{len(self.filtered[key])} resultados · Página {page + 1}/{pages} · Hasta {self.PAGE_SIZE} filas por página')
        previous, following = self.page_buttons[key]
        previous.configure(state='normal' if page > 0 else 'disabled')
        following.configure(state='normal' if page + 1 < pages else 'disabled')

    def review_label(self, path):
        if self.selection.is_retained(path):
            return 'Conservar'
        return 'Marcado' if self.selection.is_selected(path) else '—'

    def review_tags(self, path):
        if self.selection.is_retained(path):
            return ('retained',)
        return ('marked',) if self.selection.is_selected(path) else ()

    def update_selection_controls(self):
        if hasattr(self, "refresh_button"):
            self.refresh_button.configure(state="normal" if self.result is not None and not self.busy else "disabled")
        enabled = self.result is not None and not self.busy
        for button in self.selection_buttons:
            button.configure(state='normal' if enabled else 'disabled')
        self.preview_button.configure(state='normal' if enabled and self.selection.count else 'disabled')
        self.recycle_button.configure(state='normal' if enabled and self.selection.count and self.simulated_cleanup_available() else 'disabled')

    def invalidate_selection(self):
        self.recycler.invalidate()
        self.selection.reset()
        if self.preview_window is not None:
            self.preview_window.destroy()
            self.preview_window = None
        self.sync_selection()

    def sync_selection(self):
        self.selection_summary.set(f'{self.selection.count} archivos marcados · Estimación lógica: {format_size(self.selection.total_bytes)}')
        for key, tree in self.tables.items():
            if key == 'extensions':
                continue
            for iid, row in self.visible_rows.get(key, {}).items():
                tree.set(iid, 'review', self.review_label(row.path))
                tree.item(iid, tags=self.review_tags(row.path))
        self.update_selection_controls()
        if self.preview_window is not None:
            self.render_preview()

    def highlighted_paths(self, key):
        return [self.visible_rows[key][iid].path for iid in self.tables[key].selection()
                if iid in self.visible_rows[key]]

    def selection_action(self, key, action, paths=None):
        if self.busy or self.result is None or key == 'extensions':
            return
        paths = self.highlighted_paths(key) if paths is None else paths
        blocked = []
        if action == 'clear':
            self.selection.clear()
        elif action == 'mark':
            blocked = self.selection.mark(paths)
        elif action == 'unmark':
            self.selection.unmark(paths)
        elif action == 'keep':
            if len(paths) != 1:
                blocked = ['Resalta una única copia para conservar. No se marcarán las otras copias automáticamente.']
            else:
                try:
                    self.selection.keep(paths[0])
                except ValueError as error:
                    blocked = [str(error)]
        self.sync_selection()
        if blocked:
            messagebox.showwarning('Selección protegida', '\n'.join(dict.fromkeys(blocked)), parent=self.root)
        elif paths or action == 'clear':
            self.status.set('Selección de revisión actualizada. No se ha modificado ningún archivo.')
        else:
            self.status.set('Resalta una o más filas; usa Ctrl o Mayús para seleccionar varias.')

    def toggle_review(self, event, key):
        tree = self.tables[key]
        if tree.identify_region(event.x, event.y) == 'cell' and tree.identify_column(event.x) == '#1':
            iid = tree.identify_row(event.y)
            row = self.visible_rows[key].get(iid)
            if row:
                self.selection_action(key, 'unmark' if self.selection.is_selected(row.path) else 'mark', [row.path])
                return 'break'

    def toggle_highlighted(self, key):
        paths = self.highlighted_paths(key)
        action = 'unmark' if paths and all(self.selection.is_selected(path) for path in paths) else 'mark'
        self.selection_action(key, action, paths)
        return 'break'

    def open_preview(self):
        if self.busy or not self.selection.count:
            return
        if self.preview_window is not None:
            self.preview_window.lift()
            return
        window = tk.Toplevel(self.root)
        self.preview_window = window
        window.title('Vista previa de limpieza · No se eliminarán archivos')
        window.geometry('1080x620')
        window.minsize(800, 480)
        window.transient(self.root)
        window.protocol('WM_DELETE_WINDOW', self.close_preview)
        body = ttk.Frame(window, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text='Vista previa de revisión', style='Title.TLabel').pack(anchor='w')
        self.preview_summary = tk.StringVar()
        ttk.Label(body, textvariable=self.preview_summary).pack(anchor='w', pady=8)
        ttk.Label(body, text='Estimación lógica, no garantía de espacio físico. Esta ventana solo modifica la selección en memoria.').pack(anchor='w', pady=(0, 10))
        holder = ttk.Frame(body)
        holder.pack(fill='both', expand=True)
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        columns = ('name', 'size', 'location', 'reason')
        self.preview_tree = ttk.Treeview(holder, columns=columns, show='headings', selectmode='extended')
        for column, label, width in zip(columns, ('Nombre', 'Tamaño', 'Ubicación', 'Motivo de revisión'), (200, 120, 350, 500)):
            self.preview_tree.heading(column, text=label)
            self.preview_tree.column(column, width=width, minwidth=100, stretch=False)
        vertical = ttk.Scrollbar(holder, orient='vertical', command=self.preview_tree.yview)
        horizontal = ttk.Scrollbar(holder, orient='horizontal', command=self.preview_tree.xview)
        self.preview_tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        self.preview_tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        actions = ttk.Frame(body)
        actions.pack(fill='x', pady=(12, 0))
        self.preview_remove = ttk.Button(actions, text='Quitar resaltados de la selección', command=self.remove_preview)
        self.preview_remove.pack(side='left')
        self.preview_location = ttk.Button(actions, text='Abrir ubicación en Explorador', command=self.open_preview_location)
        self.preview_location.pack(side='left', padx=8)
        ttk.Button(actions, text='Cerrar vista previa', command=self.close_preview).pack(side='right')
        self.preview_previous = ttk.Button(actions, text='Anterior', command=lambda: self.change_preview_page(-1))
        self.preview_previous.pack(side='left', padx=8)
        self.preview_next = ttk.Button(actions, text='Siguiente', command=lambda: self.change_preview_page(1))
        self.preview_next.pack(side='left')
        self.preview_page = 0
        self.render_preview()

    def close_preview(self):
        if self.preview_window is not None:
            self.preview_window.destroy()
            self.preview_window = None

    def change_preview_page(self, direction):
        self.preview_page += direction
        self.render_preview()

    def render_preview(self):
        from pathlib import Path
        tree = self.preview_tree
        children = tree.get_children()
        if children:
            tree.delete(*children)
        items, self.preview_page, pages = page_rows(self.selection.preview(), self.preview_page, self.PAGE_SIZE)
        self.preview_paths = {}
        for item in items:
            path = Path(item.path)
            iid = tree.insert('', 'end', values=(path.name, format_size(item.size), str(path.parent), '; '.join(item.reasons)))
            self.preview_paths[iid] = item.path
        self.preview_summary.set(f'{self.selection.count} archivos · {format_size(self.selection.total_bytes)} lógicos · Página {self.preview_page + 1}/{pages}')
        self.preview_previous.configure(state='normal' if self.preview_page > 0 else 'disabled')
        self.preview_next.configure(state='normal' if self.preview_page + 1 < pages else 'disabled')
        if hasattr(self, 'preview_location'):
            self.preview_location.configure(state='disabled' if self.busy else 'normal')
        self.preview_remove.configure(state='normal' if not self.busy and self.selection.count else 'disabled')

    def remove_preview(self):
        if self.busy:
            return
        paths = [self.preview_paths[iid] for iid in self.preview_tree.selection() if iid in self.preview_paths]
        self.selection.unmark(paths)
        self.sync_selection()

    def simulated_cleanup_available(self):
        # No basta cambiar available: la GUI nunca habilita un backend real.
        backend = self.recycler.backend
        return (not isinstance(backend, WindowsRecycleBackend) and backend.simulated is True
                and backend.available and backend.recycle_only)

    def bind_recycler(self, result, source):
        try:
            self.recycler.bind(result, source)
        except (OSError, ValueError):
            self.recycler.invalidate()

    def clear_cleanup_inventory(self):
        self.result = None
        self.result_source = None
        self.invalidate_selection()
        self.rows = {key: () for key in self.SPECS}
        for key in self.SPECS:
            self.tokens[key] += 1
            self.filtered[key] = []
            self.pages[key] = 0
            self.render_table(key)
        for metric in self.metrics:
            metric.set('—')
        self.export_button.configure(state='disabled')

    def open_cleanup_confirmation(self):
        if self.busy or self.result is None or not self.selection.count:
            return
        if not self.simulated_cleanup_available():
            messagebox.showinfo('Reciclaje real bloqueado', 'El backend de Windows está deshabilitado. No se enviará ningún archivo a la Papelera en esta etapa.', parent=self.root)
            return
        try:
            plan = self.recycler.prepare(self.selection)
        except (OSError, ValueError):
            messagebox.showwarning('Selección no válida', 'Vuelve a analizar y selecciona archivos del inventario vigente.', parent=self.root)
            return
        window = tk.Toplevel(self.root)
        window.title('Confirmar operación simulada · Ningún envío real')
        window.geometry('1000x560')
        window.transient(self.root)
        body = ttk.Frame(window, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=f'{len(plan.records)} archivos · {format_size(plan.total_bytes)} estimados', style='Metric.TLabel').pack(anchor='w')
        ttk.Label(body, text='SIMULACIÓN: no se moverán archivos. La operación real futura los enviará a la Papelera, donde seguirán ocupando espacio.', wraplength=950).pack(anchor='w', pady=10)
        holder = ttk.Frame(body)
        holder.pack(fill='both', expand=True)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        tree = ttk.Treeview(holder, columns=('name', 'size', 'path'), show='headings')
        for key, title, width in [('name', 'Nombre', 230), ('size', 'Tamaño', 130), ('path', 'Ubicación completa', 600)]:
            tree.heading(key, text=title)
            tree.column(key, width=width, stretch=False)
        vertical = ttk.Scrollbar(holder, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(holder, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky='nsew')
        vertical.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        from pathlib import Path
        paged_tree(tree, body, plan.records,
                   lambda record: (Path(record.path).name, format_size(record.size), record.path))
        approved = [False]
        def accept():
            approved[0] = True
            window.destroy()
        buttons = ttk.Frame(body)
        buttons.pack(fill='x', pady=12)
        ttk.Button(buttons, text='Cancelar', command=window.destroy).pack(side='right')
        ttk.Button(buttons, text='Confirmar simulación', command=accept).pack(side='right', padx=12)
        window.protocol('WM_DELETE_WINDOW', window.destroy)
        window.grab_set()
        self.root.wait_window(window)
        try:
            consent = self.recycler.confirm(plan, self.selection, approved=approved[0])
        except SafetyError:
            messagebox.showwarning('Confirmación caducada', 'El análisis o la selección cambiaron. No se iniciará ninguna operación.', parent=self.root)
            return
        if consent is None:
            self.status.set('Operación cancelada; no se inició ninguna acción.')
            return
        self._set_busy(True, 'Validando y simulando operaciones… No se enviarán archivos a la Papelera.')
        self._launch('cleanup', plan.source, cleanup_for_gui, self.recycler, plan, consent, self.selection)

    def show_cleanup_report(self, report, text):
        window = tk.Toplevel(self.root)
        window.title('Resultado de la operación · Solo simulación')
        window.geometry('1050x560')
        body = ttk.Frame(window, padding=16)
        body.pack(fill='both', expand=True)
        ttk.Label(body, text=text, wraplength=1000).pack(anchor='w', pady=(0, 12))
        holder = ttk.Frame(body)
        holder.pack(fill='both', expand=True)
        holder.rowconfigure(0, weight=1)
        holder.columnconfigure(0, weight=1)
        tree = ttk.Treeview(holder, columns=('path', 'status', 'message'), show='headings')
        for key, title, width in [('path', 'Archivo local', 430), ('status', 'Estado individual', 180), ('message', 'Detalle', 550)]:
            tree.heading(key, text=title)
            tree.column(key, width=width, stretch=False)
        scroll = ttk.Scrollbar(holder, orient='vertical', command=tree.yview)
        horizontal = ttk.Scrollbar(holder, orient='horizontal', command=tree.xview)
        tree.configure(yscrollcommand=scroll.set, xscrollcommand=horizontal.set)
        tree.grid(row=0, column=0, sticky='nsew')
        scroll.grid(row=0, column=1, sticky='ns')
        horizontal.grid(row=1, column=0, sticky='ew')
        paged_tree(tree, body, report.items,
                   lambda item: (item.path, item.status.value.replace('_', ' '), item.message))
        ttk.Button(body, text='Cerrar', command=window.destroy).pack(anchor='e', pady=(10, 0))

    def close(self):
        if self.busy:
            messagebox.showinfo('Operación en curso', 'Espera a que termine el análisis o la exportación antes de cerrar.', parent=self.root)
            return
        self.closed = True
        self.root.after_cancel(self._poll_id)
        if self.debounce:
            self.root.after_cancel(self.debounce)
        self.root.destroy()


def main():
    root = tk.Tk()
    Application(root)
    root.mainloop()


if __name__ == '__main__':
    main()
