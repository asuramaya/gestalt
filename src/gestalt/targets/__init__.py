# SPDX-License-Identifier: GPL-3.0-or-later
"""gestalt.targets — provider subprocess registry + merge."""
from .registry import PROVIDERS_DIR, Registry

__all__ = ["Registry", "PROVIDERS_DIR"]
