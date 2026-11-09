"""
app/builtin — Preset integrations wrapping existing converters.

Calling register_all() registers every builtin integration's source,
destination, and custom-transform handlers with the core integration
framework.  Called once at app startup.
"""


def register_all():
    """Register all builtin integrations."""
    from .tecom_integration import register as register_tecom
    from .ordrsp_integration import register as register_ordrsp

    register_tecom()
    register_ordrsp()
