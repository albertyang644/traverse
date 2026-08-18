"""Quick launch toolbar with configurable icon buttons."""

from PyQt6.QtWidgets import (
    QToolBar, QWidget, QVBoxLayout, QLabel, QPushButton, QHBoxLayout,
    QMessageBox, QFileDialog, QLineEdit
)
from PyQt6.QtGui import QAction, QIcon


class QuickLaunchToolbar(QToolBar):
    """Customizable quick-launch toolbar with home/parent/back buttons."""

    def __init__(self, parent=None):
        super().__init__("Quick Launch", parent)
        self.setMovable(False)
        self.setIconSize(QIcon().effectiveIconSize())  # Default icon size

        # Create layout for toolbar buttons
        layout = QHBoxLayout(self)
        layout.addWidget(self)  # Self as widget in horizontal layout

        # Initialize quick launch items
        self.quick_launch_items = []
        self._add_standard_buttons()

    def _add_standard_buttons(self):
        """Add standard quick-launch buttons (Home, Parent, Back)."""
        # Home button - goes to user home directory
        self.home_btn = QPushButton('Home')
        self.home_btn.clicked.connect(lambda: self._navigate_to('~'))
        self.home_btn.setFixedWidth(80)

        # Parent directory button
        self.parent_btn = QPushButton('Parent')
        self.parent_btn.clicked.connect(self._go_parent)
        self.parent_btn.setFixedWidth(75)

        # Back button - remember last location
        self.back_btn = QPushButton('Back')
        self.back_btn.clicked.connect(self._navigate_back)
        self.back_btn.setFixedWidth(60)

        layout.addWidget(self.home_btn)
        layout.addWidget(self.parent_btn)
        layout.addWidget(self.back_btn)

    def _go_parent(self):
        """Navigate to parent directory if possible."""
        current_dir = getattr(self, 'current_dir', None) or '~'

        # Get absolute path and go up one level
        import os
        abs_path = os.path.expanduser(current_dir)
        parent_path = str(os.path.dirname(abs_path))

        if not parent_path.startswith('~'):  # Not at root
            self._navigate_to(parent_path)

    def _navigate_back(self):
        """Navigate back to previously visited directory."""
        last_location = getattr(self, 'last_location', None)

        if last_location:
            self._navigate_to(last_location)

    def _navigate_to(self, path):
        """Navigate to a specific path (emits signal for parent window)."""
        import os

        expanded_path = os.path.expanduser(path)

        # Emit navigation event with absolute path
        if hasattr(self.parent(), 'set_current_dir'):
            self.parent().set_current_dir(expanded_path)

    def set_last_location(self, path):
        """Remember this location for Back button."""
        import os
        expanded = os.path.expanduser(path)
        # Store parent directory as "back" target
        back_target = str(os.path.dirname(expanded))
        self.last_location = back_target

    def add_quick_launch(self, name, path):
        """Add a custom quick-launch button."""
        btn = QPushButton(name)
        btn.clicked.connect(lambda p=path: self._navigate_to(p))
        btn.setFixedWidth(80)
        self.addWidget(btn)
        self.quick_launch_items.append((name, path))

    def remove_quick_launch(self, name):
        """Remove a quick-launch button by name."""
        for i, (n, p) in enumerate(self.quick_launch_items):
            if n == name:
                # Find and remove the widget
                child = self.findChild(QPushButton, str(i + len([b for b in [self.home_btn, self.parent_btn, self.back_btn]])))
                try:
                    self.removeWidget(child)
                    child.deleteLater()
                except:
                    pass
                del self.quick_launch_items[i]
                break

    def load_from_session(self):
        """Load quick-launch items from session.json."""
        import json
        from pathlib import Path

        state_file = Path('state/session.json')
        if not state_file.exists():
            return

        try:
            with open(state_file) as f:
                data = json.load(f)

            # Load custom quick-launch items
            for name, path in data.get('quick_launch', {}).items():
                self.add_quick_launch(name, path)

        except Exception as e:
            print(f"Failed to load quick launch items: {e}")

