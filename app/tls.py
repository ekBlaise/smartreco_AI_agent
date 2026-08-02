"""Use the operating system's certificate store for outbound HTTPS.

Python ships its own CA bundle (certifi), which does not include the private
root certificates installed by corporate TLS-inspecting proxies. On such a
network every call to Mesh API fails with::

    [SSL: CERTIFICATE_VERIFY_FAILED] unable to get local issuer certificate

even though the browser on the same machine is perfectly happy. ``truststore``
points Python at the OS trust store instead, which already trusts whatever the
machine is configured to trust.

This only ever *widens* trust to match the host — it never disables
verification.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_enabled = False


def enable_system_trust_store() -> bool:
    """Route Python's TLS verification through the OS trust store.

    Returns True if it took effect. Safe to call more than once, and a no-op if
    ``truststore`` is not installed.
    """
    global _enabled
    if _enabled:
        return True
    try:
        import truststore

        truststore.inject_into_ssl()
    except ImportError:
        logger.debug("truststore not installed; using the bundled CA certificates")
        return False
    except Exception as exc:  # pragma: no cover - platform dependent
        logger.warning("Could not enable the system trust store: %s", exc)
        return False

    _enabled = True
    logger.debug("TLS verification now uses the system certificate store")
    return True
