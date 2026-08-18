"""Path-based file operations for CRUD."""

import os


def create_directory(path):
    try:
        os.makedirs(path, exist_ok=True)
        return path
    except OSError as e:
        if os.path.isdir(path):
            pass
        else:
            raise


def create_file(path):
    if os.path.exists(path) and os.path.isfile(path):
        pass # don't overwrite existing file
    else:
        with open(path, "wb") as f:
            pass


def _safe_rename(src, dst):
    import shutil
    if os.path.isdir(src):
        shutil.move(str(src), str(dst))  # noqa: S605
    else:
        _move_file(src, dst)


def rename_path(src, dst):
    import shutil
    if not os.path.exists(src):
        raise FileNotFoundError(f"Path does not exist: {src}")
    shutil.move(str(src), str(dst))


def delete_path(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Path does not exist: {path}")
    if os.path.isdir(path):
        import shutil
        shutil.rmtree(path)
    else:
        os.remove(path)


class Actions:
    """Namespace for all file operations; instantiated by MainWindow."""
    create_directory = staticmethod(create_directory)
    create_file = staticmethod(create_file)
    rename_path = staticmethod(rename_path)
    delete_path = staticmethod(delete_path)
