"""
app/services/storage_service.py - Wrapper over Supabase Storage.

Provides a uniform interface for uploading matter-scoped documents
(pleadings, discovery PDFs) and generating signed download URLs.

Path convention:
    matters/{matter_id}/pleadings/{pleading_id}.pdf
    matters/{matter_id}/discovery/{document_id}.pdf

Bucket: matter-documents (private, signed URLs only)
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
