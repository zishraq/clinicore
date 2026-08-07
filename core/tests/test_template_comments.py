"""Every ``{# … #}`` must open and close on one line.

Django's ``{# #}`` is a **single-line** comment. Spread it over two lines and
the template engine does not parse it at all: the text renders to the page, in
front of whoever is using the screen. There is no error, no warning, and no
failing test — which is why this trap has now been sprung four separate times in
this project, most recently under the Save buttons on the visit form.

It is cheap to detect and expensive to notice, so it is detected. Multi-line
commentary belongs in ``{% comment %}…{% endcomment %}``, which is a real tag
and is parsed as one.
"""

from pathlib import Path

import pytest
from django.conf import settings

#: Every directory templates are loaded from, plus each app's own.
TEMPLATE_ROOTS = [Path(directory) for directory in settings.TEMPLATES[0]['DIRS']] + [
    path for path in Path(settings.BASE_DIR).glob('*/templates') if path.is_dir()
]


def _template_files() -> list[Path]:
    found: list[Path] = []
    for root in TEMPLATE_ROOTS:
        found.extend(sorted(root.rglob('*.html')))
    return found


def _unclosed_comment_lines(text: str) -> list[tuple[int, str]]:
    """Line numbers where a ``{#`` has no ``#}`` after it on the same line."""
    offenders = []
    for number, line in enumerate(text.splitlines(), start=1):
        cursor = 0
        while True:
            opened = line.find('{#', cursor)
            if opened == -1:
                break
            if '#}' not in line[opened + 2 :]:
                offenders.append((number, line.strip()))
                break
            cursor = opened + 2
    return offenders


def test_there_are_templates_to_check():
    """A glob that silently matches nothing would make this suite a no-op."""
    assert len(_template_files()) > 20


@pytest.mark.parametrize('template', _template_files(), ids=lambda path: str(path.name))
def test_no_template_has_a_multi_line_comment(template: Path):
    offenders = _unclosed_comment_lines(template.read_text(encoding='utf-8'))
    assert not offenders, (
        f'{template} opens a {{# … #}} that does not close on the same line:\n'
        + '\n'.join(f'  line {number}: {text}' for number, text in offenders)
        + '\n\nDjango only parses single-line {# #}. A multi-line one is not a '
        'comment at all — it renders to the page. Use {% comment %} … '
        '{% endcomment %} instead.'
    )


def test_the_check_catches_a_multi_line_comment():
    """The detector itself, so a broken regex cannot pass this file silently."""
    bad = '<p>ok</p>\n{# this comment\n   runs on #}\n'
    assert _unclosed_comment_lines(bad) == [(2, '{# this comment')]


def test_the_check_allows_the_legitimate_forms():
    good = (
        '{# a normal single-line comment #}\n'
        '<div class="x">{# trailing #}</div>\n'
        '{% comment %}\n  a real multi-line comment\n{% endcomment %}\n'
        '{# two #} on {# one line #}\n'
    )
    assert _unclosed_comment_lines(good) == []
