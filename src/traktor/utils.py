"""Shared utility functions for traktor."""


def normalize_tmdb_id(tmdb_id):
    """Normalize TMDb ID to string or None.

    TMDb IDs must be stored as strings for consistent lookup across the codebase.
    This function handles conversion from various input types, treating falsy
    values (0, empty string) as None.

    Args:
        tmdb_id: TMDb ID as string, int, or None

    Returns:
        String representation of TMDb ID, or None if input is None/falsy
    """
    if tmdb_id is None:
        return None
    return str(tmdb_id) if tmdb_id else None
