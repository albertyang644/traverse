"""Filter bar widget for filtering file list contents."""

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QPushButton, QFrame, QLabel
)
from PyQt6.QtGui import QAction as QtAction
from PyQt6.QtCore import Qt


class FilterBar(QWidget):
    """A filter bar that allows filtering the file list contents."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(2)

        # Top row: filter input + clear button
        top_row = QHBoxLayout()

        self.filter_input = QLineEdit()
        self.filter_input.setPlaceholderText("Filter files...")
        self.filter_input.returnPressed.connect(self._on_filter_changed)

        # Clear filter button
        self.clear_btn = QPushButton('Clear')
        self.clear_btn.clicked.connect(self._clear_filter)

        top_row.addWidget(QLabel("Show:"))
        top_row.addWidget(self.filter_input, 1)
        top_row.addWidget(self.clear_btn)

        layout.addLayout(top_row)

    def _on_filter_changed(self):
        """Handle filter text change - emit signal to parent."""
        if hasattr(self.parent(), 'filter_text'):
            self.parent().filter_text = self.filter_input.text()

    def _clear_filter(self):
        """Clear the filter and refresh."""
        self.filter_input.clear()
        if hasattr(self, '_on_clear'):
            self._on_clear()


class FilteredListPane(QWidget):
    """File list pane with integrated filter bar using QSortFilterProxyModel."""

    def __init__(self, parent=None):
        super().__init__(parent)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # Filter bar at top
        self.filter_bar = FilterBar(parent=self)
        self.filter_input = self.filter_bar.filter_input
        self.clear_btn = self.filter_bar.clear_btn

        # Connect filter changes to model filtering
        self.filter_input.textChanged.connect(self._on_filter_text_changed)

        layout.addWidget(self.filter_bar, 0)  # Non-stretching header row

        # Table view for displaying files
        self.table_view = None  # Will be set by parent
        self.model = []  # Raw model data
        self.proxy_model = None

    def _on_filter_text_changed(self, text):
        """Handle filter text change - update proxy model."""
        if not hasattr(self, 'proxy_model'):
            return

        filtered_items = [item for item in self.model]

        if text.strip():
            pattern = text.lower()
            filtered_items = [
                item for item in filtered_items
                if pattern in item['name'].lower() or pattern in item['type'].lower()
            ]

        # Update proxy model source data
        self.proxy_model.setSourceData(filtered_items)

    def _setup_model(self):
        """Set up the model using os.listdir."""
        from PyQt6.QtWidgets import QSortFilterProxyModel

        if not hasattr(self, 'current_dir'):
            return

        # Set up proxy model for filtering and sorting
        self.proxy_model = QSortFilterProxyModel()
        self.proxy_model.setSourceModel(None)  # Will be set via setData with dict list
        self.proxy_model.setDynamicSortFilter(True)

        def filter_func(proxy, source):
            text = self.filter_input.text().lower() or ''
            for row in range(source.rowCount()):
                name = source.data(source.index(row, 0))
                type_str = source.data(source.index(row, 1))
                if not text or (text in str(name).lower() and text in str(type_str).lower()):
                    return True
            return False

        self.proxy_model.setFilterFunction(filter_func)

    def refresh(self):
        """Refresh the pane content."""
        # Reload model data from current directory
        import os
        items = sorted(os.listdir(self.current_dir)) if hasattr(self, 'current_dir') else []

        for item in items:
            try:
                full_path = os.path.join(self.current_dir, item)
                stat_info = os.stat(full_path)

                size = stat_info.st_size
                mtime = self._format_mtime(stat_info.st_mtime)

                if os.path.isdir(full_path):
                    type_str = 'Dir'
                else:
                    ext = __import__('os').path.splitext(item)[1]
                    type_str = ext[1:] or '(no extension)'

                item_data = {
                    'name': item,
                    'size': size,
                    'modified': mtime,
                    'type': type_str,
                    'path': full_path,
                    'is_dir': os.path.isdir(full_path)
                }
                self.model.append(item_data)

            except OSError:
                pass

        # Apply current filter
        if hasattr(self, 'filter_input'):
            self._on_filter_text_changed(self.filter_input.text())


def _format_mtime(timestamp):
    """Format mtime as readable string."""
    from PyQt6.QtCore import Qt
    return Qt.QDateTime.fromSecsSinceEpoch(timestamp).toString('yyyy-MM-dd hh:mm:ss')
