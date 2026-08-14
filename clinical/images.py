"""Decode, check and re-encode an uploaded photograph.

Nothing a user uploads is ever written through as-is: every stored image is
bytes Pillow produced, from a file Pillow was able to open. That is a security
control before it is a disk-space one — an SVG or an HTML page named ``.jpg``
cannot survive a decode-and-re-encode, so the file the serving view hands back
is always the image it claims to be. Rationale in
docs/adr/0014-encounter-photos-served-through-a-view.md.
"""

import io

from PIL import Image, ImageOps, UnidentifiedImageError

__all__ = ['MAX_EDGE', 'MAX_UPLOAD_BYTES', 'ImageRejected', 'normalize_image']

#: Refused before Pillow is handed the bytes. Phone photographs run 3-8 MB, so
#: this clears them with room while keeping a single request bounded.
MAX_UPLOAD_BYTES = 10 * 1024 * 1024

#: Longest edge of the stored image. A 4 MB phone photograph lands around
#: 250 KB here, and 1600px is legible for handwriting and for a photographed
#: lab report read on a phone with pinch-zoom — which is how these are read,
#: rather than printed. Chosen over 2000px deliberately: that is ~1.5x the
#: storage on every photograph to serve a case that may never arise. If the
#: clinic reports a report they cannot read, this constant is the whole fix.
MAX_EDGE = 1600

#: Visually indistinguishable from 90 at roughly two thirds the bytes.
JPEG_QUALITY = 82


class ImageRejected(ValueError):
    """The upload is too large, or is not an image Pillow can open.

    Carries a message meant for the practitioner, not a log: it names the file
    and what to do about it.
    """


def _megabytes(count: int) -> str:
    return f'{count / (1024 * 1024):.1f}'


def _flatten(image: Image.Image) -> Image.Image:
    """Drop any alpha channel onto white, so JPEG has something to encode.

    A screenshot of a document is commonly a transparent PNG, and JPEG has no
    alpha: without this the transparent regions encode as black and the page
    comes out unreadable.
    """
    if image.mode == 'RGB':
        return image
    if image.mode in {'RGBA', 'LA', 'P'}:
        with_alpha = image.convert('RGBA')
        canvas = Image.new('RGB', with_alpha.size, (255, 255, 255))
        canvas.paste(with_alpha, mask=with_alpha.getchannel('A'))
        return canvas
    return image.convert('RGB')


def normalize_image(uploaded) -> bytes:
    """Return JPEG bytes for ``uploaded``, or raise :class:`ImageRejected`.

    Ordered so the cheap refusal happens first and Pillow only ever sees a
    bounded amount of data.
    """
    size = getattr(uploaded, 'size', None) or 0
    if size > MAX_UPLOAD_BYTES:
        raise ImageRejected(
            f'“{uploaded.name}” is {_megabytes(size)} MB, and the limit is '
            f'{_megabytes(MAX_UPLOAD_BYTES)} MB per photo.'
        )

    uploaded.seek(0)
    raw = uploaded.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        # Reached when the reported size was absent or wrong; the check above
        # is the one that produces a good message, this one is the guarantee.
        raise ImageRejected(
            f'“{uploaded.name}” is larger than the '
            f'{_megabytes(MAX_UPLOAD_BYTES)} MB limit per photo.'
        )

    # Rejection is on content, never on the extension. verify() reads the
    # container and then leaves the instance unusable, which is why the real
    # work below opens the bytes a second time.
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            probe.verify()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError):
        # Short on purpose. These surface in a daisyUI toast, which sets
        # `white-space: nowrap` on a full-width fixed container — a long
        # sentence runs off the left edge and its beginning is unreadable,
        # worst on a phone. Naming the file is what the practitioner needs;
        # listing accepted formats is what pushed it over.
        raise ImageRejected(
            f'“{uploaded.name}” is not a picture the app can read.'
        ) from None

    try:
        with Image.open(io.BytesIO(raw)) as opened:
            # Phone cameras record orientation in EXIF rather than rotating the
            # pixels, so without this every portrait photograph is stored
            # sideways — and the tests would not notice, because the bytes are
            # perfectly valid either way.
            image = ImageOps.exif_transpose(opened) or opened
            image = _flatten(image)
            # thumbnail() only ever shrinks, so a small photograph is left at
            # its own resolution rather than being blown up and re-encoded.
            image.thumbnail((MAX_EDGE, MAX_EDGE), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            # No exif= argument, so the original EXIF block is dropped rather
            # than copied. That is intentional: it takes the GPS coordinates of
            # the clinic, and of the patient's home, off every photograph.
            image.save(buffer, format='JPEG', quality=JPEG_QUALITY, optimize=True)
    except (Image.DecompressionBombError, OSError, ValueError):
        raise ImageRejected(
            f'“{uploaded.name}” could not be processed — it may be damaged.'
        ) from None

    return buffer.getvalue()
