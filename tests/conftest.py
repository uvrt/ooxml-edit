from __future__ import annotations

import pytest

from ooxml_edit.opc import OpcPackage
from ooxml_edit.xml import register_namespaces

import synthetic

# The test vocabulary, registered once the way a format layer registers its own.
register_namespaces({"tst": synthetic.NS})


@pytest.fixture
def data() -> bytes:
    """The synthetic package's bytes, as written by an ordinary zip writer."""
    return synthetic.outer_package()


@pytest.fixture
def package(data) -> OpcPackage:
    return OpcPackage.open(data)
