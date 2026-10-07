"""Paged preview window: review differences and optionally export all rows."""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from comparison_runtime import COPY, UPDATE, TRASH, FOLDER, export_csv


def size_label(size):
    if size is None:
        return '—'
    value = float(size)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f'{value:.1f} {unit}' if unit != 'B' else f'{size} B'
        value /= 1024


def show_comparison(application, result, on_sync):
    old = application.comparison_window
    if old and old.winfo_exists():
        old.destroy()
    window = application.comparison_window = tk.Toplevel(application.root)
    window.title('Sincronizador de Rede - comparação')
    window.geometry('980x570')
    window.minsize(800, 480)
    ttk.Label(window, text='PRÉVIA DA SINCRONIZAÇÃO', font=('Segoe UI', 14, 'bold')).pack(
        anchor='w', padx=16, pady=(14, 6))
    summaries = ttk.Frame(window)
    summaries.pack(fill='x', padx=16)
    wrapping = []
    for key, values in result.summaries.items():
        text = (f'Destino {key.removeprefix("DESTINO")}: copiar {values[COPY]}  |  atualizar {values[UPDATE]}'
                f'  |  lixeira {values[TRASH]}  |  iguais {values["Iguais"]}  |  criar pastas {values[FOLDER]}'
                f'  |  transferir {size_label(values["copy_bytes"])}  |  lixeira {size_label(values["trash_bytes"])}')
        if values['Ignorados']:
            text += f'  |  fora da análise {values["Ignorados"]}'
        label = ttk.Label(summaries, text=text, wraplength=930)
        label.pack(anchor='w', pady=2)
        wrapping.append(label)
    note = ttk.Label(window, text='Comparação por tamanho e data. Arquivos podem mudar até a execução. Links e junções não são analisados.\n'
                     'Filtros afetam somente a lista; a sincronização processa todos os destinos salvos.', wraplength=930)
    note.pack(anchor='w', padx=16, pady=(6, 8))
    wrapping.append(note)
    def resize(event):
        if event.widget == window:
            for label in wrapping:
                label.configure(wraplength=max(100, event.width - 32))
    window.bind('<Configure>', resize)
    filters = ttk.Frame(window)
    filters.pack(fill='x', padx=16, pady=(0, 8))
    destination = tk.StringVar(value='Todos os destinos')
    action = tk.StringVar(value='Todas as ações')
    destination_box = ttk.Combobox(filters, textvariable=destination, state='readonly', width=22,
        values=['Todos os destinos', *result.summaries])
    destination_box.pack(side='left', padx=(0, 8))
    action_box = ttk.Combobox(filters, textvariable=action, state='readonly', width=22,
        values=['Todas as ações', COPY, UPDATE, TRASH, FOLDER])
    action_box.pack(side='left')
    table_frame = ttk.Frame(window)
    table_frame.pack(fill='both', expand=True, padx=16)
    table = ttk.Treeview(table_frame, columns=('destino', 'acao', 'arquivo', 'tamanho'), show='headings')
    for key, heading, width in [('destino', 'Destino', 100), ('acao', 'Ação', 145),
                                ('arquivo', 'Arquivo / pasta (caminho relativo)', 570), ('tamanho', 'Tamanho', 95)]:
        table.heading(key, text=heading)
        table.column(key, width=width, minwidth=75, stretch=key == 'arquivo')
    vertical = ttk.Scrollbar(table_frame, orient='vertical', command=table.yview)
    horizontal = ttk.Scrollbar(table_frame, orient='horizontal', command=table.xview)
    table.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    table.grid(row=0, column=0, sticky='nsew')
    vertical.grid(row=0, column=1, sticky='ns')
    horizontal.grid(row=1, column=0, sticky='ew')
    table_frame.rowconfigure(0, weight=1)
    table_frame.columnconfigure(0, weight=1)
    table.tag_configure(COPY, foreground='#23683a')
    table.tag_configure(UPDATE, foreground='#194e73')
    table.tag_configure(TRASH, foreground='#9a4c00')
    page_info = tk.StringVar()
    footer = ttk.Frame(window)
    footer.pack(fill='x', padx=16, pady=8)
    ttk.Label(footer, textvariable=page_info).pack(side='left')
    state = {'page': 0, 'rows': result.changes}
    page_size = 250

    def render():
        table.delete(*table.get_children())
        start = state['page'] * page_size
        rows = state['rows']
        for item in rows[start:start + page_size]:
            table.insert('', 'end', values=(item.destination, item.action, item.relative, size_label(item.size)),
                         tags=(item.action,))
        end = min(len(rows), start + page_size)
        page_info.set(f'{start + 1 if rows else 0}–{end} de {len(rows)} alterações  |  {result.finished_at}')
        previous.configure(state='normal' if state['page'] else 'disabled')
        following.configure(state='normal' if end < len(rows) else 'disabled')

    def turn(direction):
        state['page'] += direction
        render()

    previous = ttk.Button(footer, text='Anterior', command=lambda: turn(-1))
    previous.pack(side='right', padx=4)
    following = ttk.Button(footer, text='Próxima', command=lambda: turn(1))
    following.pack(side='right')

    def filter_rows(*_):
        state['rows'] = [item for item in result.changes
                        if (destination.get() == 'Todos os destinos' or item.destination == destination.get())
                        and (action.get() == 'Todas as ações' or item.action == action.get())]
        state['page'] = 0
        render()
    destination_box.bind('<<ComboboxSelected>>', filter_rows)
    action_box.bind('<<ComboboxSelected>>', filter_rows)

    def export():
        path = filedialog.asksaveasfilename(parent=window, title='Exportar comparação completa',
            defaultextension='.csv', filetypes=[('Arquivo CSV', '*.csv')], initialfile='comparacao.csv')
        if not path:
            return
        try:
            export_csv(result, path)
        except OSError as exc:
            messagebox.showerror('Sincronizador de Rede', str(exc), parent=window)
        else:
            messagebox.showinfo('Sincronizador de Rede', 'Comparação completa exportada.', parent=window)

    buttons = ttk.Frame(window)
    buttons.pack(fill='x', padx=16, pady=(0, 14))
    ttk.Button(buttons, text='Exportar CSV completo', command=export).pack(side='left')
    ttk.Button(buttons, text='Sincronizar agora', command=on_sync).pack(side='right')
    ttk.Button(buttons, text='Fechar', command=window.destroy).pack(side='right', padx=8)
    render()
    return window
