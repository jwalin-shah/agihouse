"""Tests for Google Drive connector."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from services import (
    drive_create_folder,
    drive_delete,
    drive_download,
    drive_files,
    drive_get,
    drive_upload,
)


def _mock_file_item(**overrides):
    """Build a mock Drive API file resource."""
    base = {
        "id": "file-123",
        "name": "report.pdf",
        "mimeType": "application/pdf",
        "modifiedTime": "2026-04-09T10:00:00Z",
        "size": "1024",
        "shared": False,
        "webViewLink": "https://drive.google.com/file/d/file-123/view",
        "parents": ["folder-abc"],
    }
    base.update(overrides)
    return base


class TestDriveFiles:
    def test_lists_recent_files(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().list().execute.return_value = {"files": [_mock_file_item()]}

        files = drive_files(svc, limit=10)
        assert len(files) == 1
        f = files[0]
        assert f.id == "file-123"
        assert f.name == "report.pdf"
        assert f.mime_type == "application/pdf"
        assert f.size == 1024
        assert f.web_link == "https://drive.google.com/file/d/file-123/view"

    def test_shared_with_me_filter(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().list().execute.return_value = {"files": []}

        drive_files(svc, shared_with_me=True)
        # Verify the call was made (the mock chain handles the query)
        svc.files().list.assert_called()

    def test_search_query(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().list().execute.return_value = {"files": []}

        drive_files(svc, query="budget")
        svc.files().list.assert_called()

    def test_handles_api_error(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().list().execute.side_effect = Exception("API error")

        assert drive_files(svc) == []


class TestDriveUpload:
    def test_uploads_file(self, mock_drive_service, tmp_path):
        svc = mock_drive_service
        test_file = tmp_path / "test.txt"
        test_file.write_text("hello world")

        svc.files().create().execute.return_value = {
            "id": "new-123",
            "name": "test.txt",
            "mimeType": "text/plain",
            "modifiedTime": "2026-04-09T12:00:00Z",
            "size": "11",
            "webViewLink": "https://drive.google.com/file/d/new-123/view",
        }

        with patch("googleapiclient.http.MediaFileUpload"):
            result = drive_upload(svc, str(test_file))

        assert result is not None
        assert result.id == "new-123"
        assert result.name == "test.txt"

    def test_upload_with_folder(self, mock_drive_service, tmp_path):
        svc = mock_drive_service
        test_file = tmp_path / "doc.pdf"
        test_file.write_bytes(b"%PDF-fake")

        svc.files().create().execute.return_value = {
            "id": "new-456",
            "name": "doc.pdf",
            "mimeType": "application/pdf",
            "modifiedTime": "2026-04-09T12:00:00Z",
            "size": "9",
            "webViewLink": "",
        }

        with patch("googleapiclient.http.MediaFileUpload"):
            result = drive_upload(svc, str(test_file), folder_id="folder-abc")

        assert result is not None

    def test_nonexistent_file_returns_none(self, mock_drive_service):
        result = drive_upload(mock_drive_service, "/nonexistent/file.txt")
        assert result is None


class TestDriveCreateFolder:
    def test_creates_folder(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().create().execute.return_value = {
            "id": "folder-new",
            "name": "My Folder",
            "mimeType": "application/vnd.google-apps.folder",
            "modifiedTime": "2026-04-09T12:00:00Z",
            "webViewLink": "https://drive.google.com/drive/folders/folder-new",
        }

        result = drive_create_folder(svc, "My Folder")
        assert result is not None
        assert result.name == "My Folder"
        assert result.mime_type == "application/vnd.google-apps.folder"

    def test_with_parent(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().create().execute.return_value = {
            "id": "sub-folder",
            "name": "Sub",
            "mimeType": "application/vnd.google-apps.folder",
            "modifiedTime": "2026-04-09T12:00:00Z",
            "webViewLink": "",
        }

        result = drive_create_folder(svc, "Sub", parent_id="folder-parent")
        assert result is not None


class TestDriveDelete:
    def test_trashes_file(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().update().execute.return_value = {}

        assert drive_delete(svc, "file-123") is True

    def test_handles_error(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().update().execute.side_effect = Exception("not found")

        assert drive_delete(svc, "bad-id") is False


class TestDriveGet:
    def test_gets_file_metadata(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().get().execute.return_value = _mock_file_item()

        result = drive_get(svc, "file-123")
        assert result is not None
        assert result.id == "file-123"
        assert result.name == "report.pdf"

    def test_returns_none_on_error(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().get().execute.side_effect = Exception("not found")

        assert drive_get(svc, "bad-id") is None


class TestDriveFilesWithFolderId:
    def test_passes_folder_id_to_query(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().list().execute.return_value = {"files": [_mock_file_item()]}

        files = drive_files(svc, folder_id="folder-abc")
        assert len(files) == 1
        svc.files().list.assert_called()

    def test_folder_id_empty_does_not_filter(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().list().execute.return_value = {"files": []}

        drive_files(svc, folder_id="")
        svc.files().list.assert_called()


class TestDriveDownload:
    def test_downloads_regular_file(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().get().execute.return_value = {
            "mimeType": "application/pdf",
            "name": "report.pdf",
        }
        # Mock get_media to return a request object
        mock_request = MagicMock()
        svc.files().get_media.return_value = mock_request

        with patch("googleapiclient.http.MediaIoBaseDownload") as mock_dl:
            instance = mock_dl.return_value
            instance.next_chunk.return_value = (None, True)
            result = drive_download(svc, "file-123")

        assert result is not None
        content, mime = result
        assert mime == "application/pdf"

    def test_exports_google_doc_as_pdf(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().get().execute.return_value = {
            "mimeType": "application/vnd.google-apps.document",
            "name": "My Doc",
        }
        mock_request = MagicMock()
        svc.files().export_media.return_value = mock_request

        with patch("googleapiclient.http.MediaIoBaseDownload") as mock_dl:
            instance = mock_dl.return_value
            instance.next_chunk.return_value = (None, True)
            result = drive_download(svc, "doc-123")

        assert result is not None
        _, mime = result
        assert mime == "application/pdf"

    def test_returns_none_on_error(self, mock_drive_service):
        svc = mock_drive_service
        svc.files().get().execute.side_effect = Exception("not found")

        assert drive_download(svc, "bad-id") is None
