"""File search functionality using os.walk with wildcard support."""

import os
import fnmatch


def recursive_search(root_dir, search_pattern, recursive: bool = True):
    """
    Recursively search for files/directories matching a pattern.

    Args:
        root_dir: Starting directory path
        search_pattern: Search pattern (supports * and ? wildcards via fnmatch)

    Returns:
        List of matching file paths
    """
    matches = []

    # Convert glob pattern to regex if needed, or use fnmatch directly
    def match_path(path):
        basename = os.path.basename(path)
        return fnmatch.fnmatch(basename.lower(), search_pattern.lower())

    for dirpath, dirnames, filenames in os.walk(root_dir):
        # Check directories
        dirname = os.path.basename(dirpath)
        if dirpath != root_dir and match_path(dirname):
            matches.append(dirpath)

        # Check files
        for filename in filenames:
            if match_path(filename):
                filepath = os.path.join(dirpath, filename)
                matches.append(filepath)

        if not recursive:
            dirnames[:] = []

    return sorted(matches)


def search_in_current_dir(current_dir, pattern, recursive: bool = True):
    """Search current directory, optionally traversing subdirectories."""
    import sys

    expanded_dir = os.path.expanduser(current_dir)
    if not os.path.exists(expanded_dir):
        print(f"Error: Directory does not exist: {expanded_dir}")
        return []

    try:
        results = recursive_search(expanded_dir, pattern, recursive=recursive)

        # If no results in current dir itself (not subdirs), search just this directory
        if not results and os.path.exists(os.path.join(current_dir, pattern)):
            matches = [os.path.join(current_dir, pattern)]
            return sorted(matches)

        return results

    except PermissionError as e:
        print(f"Permission denied: {e}")
        return []


def search_files(root_dir, pattern, recursive: bool = True):
    """Search root_dir for files matching pattern (supports * and ? wildcards)."""
    if not pattern:
        return []
    return search_in_current_dir(root_dir, pattern, recursive=recursive)


if __name__ == "__main__":
    # Test the search function
    import sys

    if len(sys.argv) > 1:
        pattern = sys.argv[1]
        results = search_files(pattern)

        print(f"Found {len(results)} matches for '{pattern}':")
        for path in results[:20]:  # Show first 20
            rel_path = os.path.relpath(path, '~')
            print(rel_path)

        if len(results) > 20:
            print(f"... and {len(results) - 20} more matches")
