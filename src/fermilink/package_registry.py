from __future__ import annotations

import sys

from fermilink.packages import package_registry as _impl

# Compatibility shim: preserve legacy import path.
sys.modules[__name__] = _impl
