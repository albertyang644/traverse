"""Session state management for Traverse file manager."""

import json
import os
from pathlib import Path


class SessionManager:
    """Manages session persistence (panes, directories, settings)."""

    def __init__(self):
        self.data = {
            'left_pane_dir': os.path.expanduser('~'),
            'right_pane_dir': os.path.expanduser('~'),
            'quick_launch_items': {},
            'filter_state': {},  # pane_id -> filter_text
            'column_visibility': {}  # pane_id -> list of visible column indices
        }

    def load(self, filepath='state/session.json'):
        """Load session data from JSON file."""
        try:
            with open(filepath) as f:
                self.data = json.load(f)
        except FileNotFoundError:
            pass  # Use defaults
        except json.JSONDecodeError as e:
            print(f"Failed to parse session.json: {e}")

    def save(self, filepath='state/session.json'):
        """Save session data to JSON file."""
        try:
            with open(filepath, 'w') as f:
                # Save relative paths from home directory for directories
                clean_data = {}
                for key, value in self.data.items():
                    if isinstance(value, dict):
                        cleaned_value = {}
                        for k, v in value.items():
                            if isinstance(v, str) and v.startswith(os.path.expanduser('~')):
                                rel_path = os.path.relpath(v, os.path.expanduser('~'))
                                cleaned_value[k] = rel_path
                            else:
                                cleaned_value[k] = v
                        clean_data[key] = cleaned_value
                    elif isinstance(value, str) and value.startswith(os.path.expanduser('~')):
                        clean_data[key] = os.path.relpath(value, os.path.expanduser('~'))
                    else:
                        clean_data[key] = value

            with open(filepath, 'w') as f:
                json.dump(clean_data, f, indent=2)
        except Exception as e:
            print(f"Failed to save session: {e}")

    def set_pane_dir(self, pane_id, directory):
        """Set the current directory for a specific pane."""
        self.data[pane_id + '_dir'] = os.path.expanduser(directory)

    def get_pane_dir(self, pane_id):
        """Get the saved directory for a pane."""
        dir_key = pane_id + '_dir'
        if dir_key in self.data:
            path = self.data[dir_key]
            # Convert relative back to absolute if needed
            home = os.path.expanduser('~')
            if path and not os.path.isabs(path) and path.startswith(home):
                pass  # Already handled when saving
            return path

        # Return default from data dict or expanduser
        return self.data.get('left_pane_dir', '~') or os.path.expanduser('~')

    def set_quick_launch(self, name, path):
        """Add/update a quick-launch item."""
        self.data['quick_launch_items'][name] = path

    def get_quick_launch(self, name):
        """Get a quick-launch item by name."""
        return self.data.get('quick_launch_items', {}).get(name)

    def set_filter_state(self, pane_id, filter_text):
        """Save filter state for a pane."""
        self.data['filter_state'][pane_id] = filter_text

    def get_filter_state(self, pane_id):
        """Get saved filter text for a pane."""
        return self.data.get('filter_state', {}).get(pane_id)


def _format_mtime(timestamp):
    """Format mtime as readable string."""
    from PyQt6.QtCore import Qt
    return Qt.QDateTime.fromSecsSinceEpoch(timestamp).toString('yyyy-MM-dd hh:mm:ss')
