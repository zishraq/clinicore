# 0014 — Encounter photos are served through a view, and re-encoded on the way in

Status: accepted, 2026-08-14.

## Context

The clinic wants to keep photographs against a visit: the patient, and the
documents they arrive holding — a referral letter, a lab report, an old
prescription from somewhere else. Until now the application stored no files at
all. `MEDIA_ROOT` was unset, nothing inherited `FileField`, and WhiteNoise
served `STATIC_ROOT` and nothing else.

SPEC §5 anticipates an `Attachment` model on a patient *or* an encounter, with
an access level. SPEC §8 says attachments must not be served from a public URL.
SPEC §10 wants media on a mounted volume behind a storage abstraction so it can
move to S3.

## Decision

### One model, `clinical.EncounterPhoto`, and not `Attachment`

The field is an `ImageField` whose validation refuses anything Pillow cannot
decode, so a PDF is rejected. Calling the model `Attachment` would promise
lab-report PDFs it cannot store. It also has no `access_level`: the whole
clinical app is already PRACTITIONER/OWNER, so a per-row level would be a column
with one value in it.

There is deliberately **no photo/document distinction**. A clinic photographing
a referral letter is taking a photograph; modelling "document" separately would
mean two tables, two upload paths and a question at upload time ("is this a
document?") whose answer changes nothing. The user-facing word comes from the
`photo` / `photo_plural` terminology keys, so a practice that mostly captures
paperwork relabels the whole feature in settings.

The divergence from SPEC §5 to record: **this is encounter-only.** Photographs
on a *patient* — an ID card, a consent form — remain unbuilt. Adding them later
is a second nullable FK or a second model, not a redesign of this one.

### `MEDIA_URL` is routed by nothing, in every mode including `DEBUG`

`config/urls.py` has no `static(settings.MEDIA_URL, ...)` line and must never
gain one. The bytes are reachable only through `clinical.views.encounter_photo`,
which runs `login_required`, `clinical_access_required`, and a
`get_object_or_404` against the organization-scoped default manager — so another
clinic's pk is a 404 by construction rather than by a filter someone remembered
to write (ADR 0005).

The tempting version of this is `if settings.DEBUG: urlpatterns += static(...)`.
It is refused because of what it does to the *other* environment: development
would then never exercise the protected view, so a bug in it — a missing
decorator, a manager that is not the scoped one — would be invisible until
production, where the direct route is gone and the broken view is the only one.
Serving media identically in both places is what makes local testing mean
anything. `core/tests/test_media_not_served.py` asserts that no URL pattern
matches a `/media/` path, so re-adding the route fails the suite.

`MEDIA_URL` is still set, because `FileField.url` is built from it. Rendering
`photo.image.url` into a template is a bug; every template uses
`{% url 'clinical:photo' photo.pk %}`.

### The organization id is in the storage path, and is not access control

`encounters/<organization_id>/<encounter_id>/<uuid4>.jpg`. That exists for
operations — per-tenant disk usage, a targeted restore, a stray file that can be
attributed — and for nothing else. The uuid is not a secret and is not treated
as one; the view is the boundary.

The uploaded filename is discarded entirely. It is the part of an upload an
attacker fully controls, and a caption is where a human name for the picture
belongs.

### Every image is decoded and re-encoded, which is a security control

`clinical/images.py` opens each upload with Pillow, verifies it, applies EXIF
orientation, flattens any alpha channel, downscales to a 1600px longest edge and
saves it as JPEG. Nothing a user uploads is ever written through as-is.

The obvious reading is that this is about disk — a 4 MB phone photograph becomes
about 250 KB, which matters on a small VPS. The more important effect is that
the stored bytes are always ones Pillow produced. Uploaded files are served from
the same origin as the application, so an SVG or an HTML page named `.jpg` would
otherwise be stored XSS against a session that can read every patient record.
Neither survives a decode-and-re-encode. Combined with a fixed
`Content-Type: image/jpeg`, the existing `SECURE_CONTENT_TYPE_NOSNIFF`, and a
generated filename, there is no path by which a byte the user chose is
interpreted as anything but a picture.

Two details that are easy to drop and hard to notice:

- **`ImageOps.exif_transpose` is not optional.** Phone cameras record
  orientation in EXIF rather than rotating pixels. Without it every portrait
  photograph is stored sideways, and the bytes are perfectly valid either way —
  no status-code test would ever fail.
- **EXIF is dropped rather than copied**, which takes the GPS coordinates of the
  clinic, and of wherever else a photograph was taken, off every stored file.

### Size is capped before Pillow is involved

10 MB, checked against the reported size first and the read length second. The
refusal names the limit and the file's own size, so the practitioner can act on
it. `Image.DecompressionBombError` is caught alongside the ordinary decode
failures — Pillow's own pixel ceiling is the guard against a small file that
expands to gigabytes.

### Validation is on the form, never in the view

A rejected upload has to come back as a field error on a redisplayed form with
the consultation note still typed into it. A doctor losing a half-written visit
because one photograph was 12 MB is a worse failure than the one being
prevented. `MultipleImageField.clean` is therefore where `normalize_image` runs,
and `services.attach_photos` receives bytes that are already known-good.

### Delete is hard, and removes the file

SPEC §4 nominally puts clinical records on soft delete. A soft-deleted row
pointing at a file that has been erased claims to hold something it does not, and
the alternative — keeping the file — makes "delete" a lie in the other
direction. `services.delete_photo` removes the file and then the row. That order
is chosen so the surviving failure mode is a row whose image 404s (visible, one
broken thumbnail) rather than bytes on disk that nothing can ever name again.

`bootstrap_demo --reset` calls it row by row for the same reason. `Encounter`
CASCADEs its photographs, so a queryset delete there never raises — it silently
orphans the files.

## Consequences

### A `pg_dump` is no longer a complete backup

This is the consequence most likely to be forgotten, and the one that loses data.
Before this change the database was the entire state of the application, and
`pg_dump` captured all of it. It now captures rows that point at files living in
the `media_data` volume. **Restoring the database alone gives a clinic a set of
visits whose photographs are all missing**, with no error anywhere — the rows are
intact and every thumbnail is broken.

The backup set is `pg_dump` **and** the `media_data` volume, and the restore
procedure SPEC §8 asks for has to exercise both.

### `collectstatic` is unaffected, but the volume mount point is not

`docker-compose.prod.yml` mounts a named volume at `/app/media`. Docker seeds a
fresh named volume from the image's directory *and its ownership*, so the
Dockerfile creates `/app/media` before its `chown` — otherwise Docker creates the
mount point as root and the non-root `appuser` cannot write a single upload.
Silent until the first photograph.

### The storage abstraction is real, and one line protects it

The serving view uses `photo.image.open('rb')`, never `photo.image.path`. `.path`
raises on any storage that is not a local filesystem, so that one call is what
keeps SPEC §10's eventual move to an S3-compatible backend a `STORAGES` setting
rather than a rewrite.
