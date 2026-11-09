"""
app/builtin — Preset integrations wrapping existing converters.

Calling register_all() registers every builtin integration's source,
destination, and custom-transform handlers with the core integration
framework.  Called once at app startup.

Phase 2 additions:
  - "webhook" source: passthrough handler (data arrives in-memory)
  - "api_poll" source: passthrough handler (data arrives in-memory)
  - "api" destination: sends data to external REST endpoints
"""


def _register_phase2_handlers():
    """Register Phase 2 source/destination types with the framework."""
    from ..core.integration import register_source, register_destination

    # Webhook source: data arrives in-memory via webhook_routes.py,
    # so the source handler is a passthrough that returns data as-is.
    def webhook_source_handler(data, source_config):
        return data

    # API poll source: data arrives in-memory via ApiPollManager,
    # so the source handler is also a passthrough.
    def api_poll_source_handler(data, source_config):
        return data

    register_source("webhook", webhook_source_handler)
    register_source("api_poll", api_poll_source_handler)

    # API destination: sends data to an external REST endpoint
    from ..core.api_destination import write_api_destination
    register_destination("api", write_api_destination)


def register_all():
    """Register all builtin integrations."""
    from .tecom_integration import register as register_tecom
    from .ordrsp_integration import register as register_ordrsp

    register_tecom()
    register_ordrsp()
    _register_phase2_handlers()
