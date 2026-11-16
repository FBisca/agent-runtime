"""The store's errors: an unusable store, a refused terminal, and everything else the store refuses.

They live beside the families that raise them, so `dispatch_store` republishes the same three
classes it always did and a caller still catches `StoreError` from the module it imported."""


class StoreError(Exception):
    pass


class StorageError(StoreError):
    """The store itself is unusable (directory ownership, schema version); never an authorization verdict."""


class TerminalRefused(StoreError):
    """The terminal belongs to another session: this session does not own it and no team was registered."""
