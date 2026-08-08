"""The two silences that having no LOGGING block produced.

Both are asserted by emitting through the real code path and reading what the
*configured* handlers write. That distinction is the whole test. Inspecting the
config would have passed before the fix — `django.request` did have handlers,
they just dropped every record — and `caplog` would also have passed, because it
attaches a handler of its own and so measures pytest rather than the settings.

So these swap the configured handlers' streams for a buffer and put them back,
adding nothing to the hierarchy. A logger with no handler anywhere falls through
to `logging.lastResort`, which writes nowhere near these buffers, and the
assertion fails as it should.
"""

import io
import logging
from contextlib import contextmanager
from types import SimpleNamespace

from core.context import organization_timezone


def _handlers_that_would_run(logger: logging.Logger) -> list[logging.Handler]:
    """Walk propagation exactly as ``logging.Logger.callHandlers`` does."""
    handlers: list[logging.Handler] = []
    current: logging.Logger | None = logger
    while current:
        handlers.extend(current.handlers)
        current = current.parent if current.propagate else None
    return handlers


@contextmanager
def capture_configured_output(logger_name: str):
    """Yield a buffer holding what the settings' own handlers emit."""
    logger = logging.getLogger(logger_name)
    buffer = io.StringIO()
    streams = [
        handler
        for handler in _handlers_that_would_run(logger)
        if isinstance(handler, logging.StreamHandler)
    ]
    assert streams, (
        f'{logger_name} resolves to no stream handler; logging.lastResort would '
        'be used and the message would carry no level, name, or timestamp'
    )

    originals = [handler.setStream(buffer) for handler in streams]
    try:
        yield buffer
    finally:
        for handler, original in zip(streams, originals, strict=True):
            handler.setStream(original)


def test_a_tenants_unusable_timezone_is_reported_attributably():
    """The warning ADR 0011 relies on has to be findable in a production log.

    It always reached stderr, even unconfigured, via `logging.lastResort` — which
    writes the bare message. The level and the logger name were what was
    missing, and they are what makes it greppable rather than an unexplained
    sentence sitting among request lines.
    """
    organization = SimpleNamespace(pk=7, timezone='Mars/Olympus_Mons')

    with (
        capture_configured_output('core.context') as buffer,
        organization_timezone(organization),
    ):
        pass

    written = buffer.getvalue()
    assert 'unusable timezone' in written
    assert 'WARNING' in written
    assert 'core.context' in written


def test_an_unhandled_500_is_written_to_the_log():
    """This one produced no output at all before the fix.

    `django.request` propagates to `django`, which Django's defaults give a
    console handler filtered by require_debug_true plus an AdminEmailHandler
    with no ADMINS. Handlers existed, so lastResort never fired; both dropped
    the record, so a 500 was written nowhere.
    """
    with capture_configured_output('django.request') as buffer:
        logging.getLogger('django.request').error('Internal Server Error: /billing/3/')

    written = buffer.getvalue()
    assert 'Internal Server Error: /billing/3/' in written
    assert 'ERROR' in written
    assert 'django.request' in written


def test_query_logging_stays_off():
    """SQL carries patient data and must not follow the root level down."""
    assert logging.getLogger('django.db.backends').getEffectiveLevel() >= logging.INFO
