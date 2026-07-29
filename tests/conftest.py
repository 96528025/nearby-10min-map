"""Shared fixtures.

The whole suite is offline. No test may open a network connection: every
external service (Valhalla, Nominatim, Photon, Overpass, Overture) is either
mocked or simply never reached. `no_network` is autouse, so a regression that
introduces a real request fails loudly instead of silently depending on a
public API being up.
"""
import socket
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "map" / "server"))
sys.path.insert(0, str(ROOT / "map" / "scripts"))


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Make any attempt to open a socket a test failure."""
    def blocked(*args, **kwargs):
        raise AssertionError(
            "test attempted a real network connection; mock it instead")

    monkeypatch.setattr(socket, "socket", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)


@pytest.fixture
def square():
    """4 km-ish axis-aligned square around (0, 0) as a GeoJSON Polygon."""
    return {"type": "Polygon", "coordinates": [
        [[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]]}


@pytest.fixture
def square_with_hole():
    """Same square with a central hole from -0.5 to 0.5."""
    return {"type": "Polygon", "coordinates": [
        [[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]],
        [[-0.5, -0.5], [0.5, -0.5], [0.5, 0.5], [-0.5, 0.5], [-0.5, -0.5]]]}


@pytest.fixture
def multipolygon_with_hole():
    """Two disjoint squares; the second one has a hole."""
    return {"type": "MultiPolygon", "coordinates": [
        [[[-1, -1], [1, -1], [1, 1], [-1, 1], [-1, -1]]],
        [[[10, 10], [12, 10], [12, 12], [10, 12], [10, 10]],
         [[10.5, 10.5], [11.5, 10.5], [11.5, 11.5], [10.5, 11.5],
          [10.5, 10.5]]]]}
