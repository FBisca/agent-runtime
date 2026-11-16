"""The one place a store row is given an id, so every family names its rows the same way."""

import uuid


def uid():
    return uuid.uuid4().hex
