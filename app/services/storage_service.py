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
