"""
app/services/storage_service.py - Wrapper over Supabase Storage.

Provides a uniform interface for uploading matter-scoped documents
(pleadings, discovery PDFs) and generating signed download URLs.

Path convention:
    matters/{matter_id}/pleadings/{pleading_id}.pdf
    matters/{matter_id}/discovery/{document_id}.pdf

Bucket: matter-documents (private, signed URLs only)

── IMAGE TRANSFORMATION IS ON, AND IS HARMLESS ONLY WHILE WE STORE PDFs ──────

Supabase enables image transformation on a storage project automatically. It
cannot affect anything here today, for two independent reasons, and both have to
keep holding:

1. **PDF is not a transformable source.** The feature covers PNG, JPEG, WebP,
   AVIF, GIF, ICO, SVG, HEIC, BMP and TIFF. Every upload route refuses anything
   but ``application/pdf`` -- including the signed-URL path, which did not until
   somebody asked this question.
2. **It is never automatic.** A transformation happens only when a caller passes
   a ``transform`` option, which produces a ``/storage/v1/render/image/`` URL.
   Nothing here passes one, so every URL we mint is a plain ``/object/`` link to
   the bytes as stored. Verified against Supabase's documentation, Oct 2026:
   transformations are applied on request, never at upload, and a file nobody
   transforms is untouched.

**THE TRAP IS AHEAD OF US, NOT BEHIND.** Step 0 of the document pipeline
converts photographs and TIFFs to PDF, and the whole argument for the
as-produced record is that this conversion destroys what matters: a photograph's
EXIF capture time and GPS are routinely the evidence in a family-law matter. So
the as-produced original of such a file would be a JPEG, a HEIC or a TIFF --
**all three transformable sources.**

At that point serving one through a transform would return a re-encoded image,
in WebP by default, with its metadata stripped, and it would look like the
photograph. A derived image offered as the original is an evidentiary failure
that leaves no trace in the file it produces.

So, when images are ever stored here:

- The as-produced original is served through ``get_signed_url`` and nothing
  else. No ``transform``, not even ``format: 'origin'`` -- that still goes
  through the render path.
- A thumbnail of an exhibit photograph is a DERIVED artifact and must be
  labelled as one, the same way a working copy is distinguished from the file
  as served.
- Transformation also caps at 25 MB and 50 megapixels, so a scanned TIFF would
  often fail it anyway -- which is a reason it would be noticed, not a reason
  it is safe.

Turning the feature off in the project settings is the other way to hold this,
and costs nothing while no image is ever transformed.
"""
from typing import Optional

from db_handler import DatabaseManager
from util.loggerfactory import LoggerFactory

LOGGER = LoggerFactory.create_logger(__name__)

BUCKET = "matter-documents"


class StorageService:
    """Thin wrapper over Supabase Storage for matter documents."""

    def __init__(self, manager: DatabaseManager):
        self._manager = manager

    def _client(self):
        return self._manager.client.storage.from_(BUCKET)

    def upload_pleading(self, matter_id: int, pleading_id: int, pdf_bytes: bytes) -> str:
        """Upload a pleading PDF. Returns the storage path."""
        path = f"matters/{matter_id}/pleadings/{pleading_id}.pdf"
        return self._upload(path, pdf_bytes)

    def upload_discovery(self, matter_id: int, document_id: int, pdf_bytes: bytes) -> str:
        """Upload a discovery document PDF. Returns the storage path."""
        path = f"matters/{matter_id}/discovery/{document_id}.pdf"
        return self._upload(path, pdf_bytes)

    def as_produced_path(self, matter_id: int, content_sha256: str,
                         extension: str = "pdf") -> str:
        """Where a received file lives. Content-addressed, so the path is known
        before the bytes are anywhere and a retry rewrites the same object."""
        return f"as-produced/{matter_id}/{content_sha256}.{extension}"

    def create_signed_upload_url(self, storage_path: str) -> dict:
        """
        A one-time URL the browser can PUT a file to, directly.

        **This is what keeps a large upload out of the API entirely.** Sent
        through the application, 74 MB moves twice inside one request — browser
        to the API, API to Storage — and haproxy cut that with a 504 while
        everything was still working. It also removes the memory ceiling, since
        every upload route reads the whole body into RAM, and the nginx body
        limit, which no longer sits in the path.

        Scoped to one path and short-lived, so it grants nothing beyond writing
        the object it was minted for.

        :return: ``{"path", "signed_url", "token"}``
        :rtype: dict
        """
        result = self._client().create_signed_upload_url(storage_path)
        LOGGER.info("storage_service.create_signed_upload_url: path=%s", storage_path)
        return {
            "path": storage_path,
            "signed_url": result.get("signed_url") or result.get("signedUrl"),
            "token": result.get("token"),
        }

    def stat(self, storage_path: str) -> Optional[dict]:
        """
        Does this object exist, and how big is it?

        The cheap half of verifying a direct upload. The expensive half -- does
        its content match the hash the client claimed -- is deliberately NOT
        done here: it would mean downloading the file back into the request this
        design exists to keep the file out of. The survey job checks the hash
        when it reads the file, which it does anyway.

        :return: ``{"name", "size"}``, or None if nothing is there.
        :rtype: Optional[dict]
        """
        folder, _, name = storage_path.rpartition("/")
        try:
            listing = self._client().list(folder, {"search": name})
        except Exception as e:  # noqa: BLE001
            LOGGER.warning("storage_service.stat: %s failed: %s", storage_path, str(e))
            return None
        for entry in listing or []:
            if entry.get("name") == name:
                meta = entry.get("metadata") or {}
                return {"name": name, "size": meta.get("size")}
        return None

    def upload_source_document(self, matter_id: int, content_sha256: str,
                               content: bytes, extension: str = "pdf") -> str:
        """
        Upload a file exactly as it was served on us. Returns the storage path.

        Kept under its own prefix rather than beside the working copies, because
        the whole value of this record is that nothing ever writes to it again:
        it is not OCR'd, not split, not re-saved. A path that says so is a
        reminder at the only moment anybody would be tempted.

        **Keyed on the content hash, not the row id, so the upload can happen
        BEFORE the row exists.** That ordering is the rule §11a paid for: a
        record whose file is still uploading fails for anything that reads it in
        that window. A hash also makes a retry write the same object rather than
        a second copy, and it is already the dedup key — so storage and the
        table agree by construction instead of by convention.

        :param matter_id: The matter the file was produced in.
        :param content_sha256: Hex digest of the bytes.
        :param content: The bytes as received, unaltered.
        :param extension: File extension as served — not always pdf.
        :return: Storage path.
        :rtype: str
        """
        path = f"as-produced/{matter_id}/{content_sha256}.{extension}"
        return self._upload(path, content)

    def upload_document(self, matter_id: int, document_id: int, pdf_bytes: bytes) -> str:
        """
        Upload a working copy split out of a source document.

        :param matter_id: The matter this document belongs to.
        :param document_id: Primary key of the documents row.
        :param pdf_bytes: The split PDF.
        :return: Storage path.
        :rtype: str
        """
        path = f"matters/{matter_id}/documents/{document_id}.pdf"
        return self._upload(path, pdf_bytes)

    def upload_intake(self, job_id: str, pdf_bytes: bytes) -> str:
        """
        Upload a pleading dropped for matter intake. Returns the storage path.

        Not matter-scoped like the others: at intake there is no matter yet, so
        the job id owns the file until a matter exists to move it under.

        :param job_id: The intake job this upload belongs to.
        :type job_id: str
        :param pdf_bytes: Raw PDF content.
        :type pdf_bytes: bytes
        :return: Storage path.
        :rtype: str
        """
        return self._upload(f"intake/{job_id}.pdf", pdf_bytes)

    def download(self, storage_path: str) -> Optional[bytes]:
        """
        Fetch a stored file's bytes.

        Used by the worker, which never sees the HTTP upload — it reads what the
        API stored.

        :param storage_path: The path returned by upload_*.
        :type storage_path: str
        :return: File bytes, or None when the object is missing or unreadable.
        :rtype: Optional[bytes]
        """
        try:
            return self._client().download(storage_path)
        except Exception as e:
            LOGGER.error("storage_service: download failed for %s: %s", storage_path, str(e))
            return None

    def move(self, from_path: str, to_path: str) -> Optional[str]:
        """
        Move a stored file, e.g. an intake upload into its new matter's folder.

        :param from_path: Current path.
        :type from_path: str
        :param to_path: Destination path.
        :type to_path: str
        :return: The destination path, or None if the move failed.
        :rtype: Optional[str]
        """
        try:
            self._client().move(from_path, to_path)
            return to_path
        except Exception as e:
            LOGGER.warning("storage_service: move failed %s -> %s: %s", from_path, to_path, str(e))
            return None

    def _upload(self, path: str, pdf_bytes: bytes) -> str:
        LOGGER.info("storage_service: uploading %s (%s bytes)", path, len(pdf_bytes))
        try:
            self._client().upload(
                path=path,
                file=pdf_bytes,
                file_options={"content-type": "application/pdf", "upsert": "true"},
            )
        except Exception as e:
            LOGGER.error("storage_service: upload failed for %s: %s", path, str(e))
            raise
        return path

    def get_signed_url(self, storage_path: str, expires_in: int = 3600) -> Optional[str]:
        """
        Generate a signed URL for downloading a file.

        **NEVER ADD A `transform` ARGUMENT TO THIS.** See the note on image
        transformation at the top of this module: a transformed URL serves a
        re-encode, not the file that was stored, and this is the one function
        every caller reaches for when it wants "the original".

        :param storage_path: The path returned by upload_*.
        :param expires_in: Seconds the URL is valid (default 1 hour).
        :return: Signed URL string, or None if generation failed.
        """
        try:
            result = self._client().create_signed_url(storage_path, expires_in)
            # The return shape varies by supabase-py version; normalize:
            if isinstance(result, dict):
                return result.get("signedURL") or result.get("signed_url")
            return str(result)
        except Exception as e:
            LOGGER.warning("storage_service: signed URL failed for %s: %s", storage_path, str(e))
            return None

    def delete(self, storage_path: str) -> None:
        """Delete a file from storage."""
        try:
            self._client().remove([storage_path])
        except Exception as e:
            LOGGER.warning("storage_service: delete failed for %s: %s", storage_path, str(e))
