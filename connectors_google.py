"""Google Workspace read-only connectors for Gmail and Drive.

This module provides two simple connectors that adapt Google APIs to the
`Connector` protocol defined in `connectors.py`. It is read-only and safe to
run without credentials: the script will print clear instructions if tokens
or libraries are missing.

Usage (local test without modifying backend):

1. Install dependencies if you plan to call real Google APIs:

   pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2

2. Provide credentials via environment variables (one of the two flows):

   Service account (recommended for Drive read-sync with domain delegation):
     GOOGLE_SERVICE_ACCOUNT_JSON=/path/to/service-account.json

   Or OAuth2 refresh token flow:
     GOOGLE_OAUTH_CLIENT_ID=...
     GOOGLE_OAUTH_CLIENT_SECRET=...
     GOOGLE_OAUTH_REFRESH_TOKEN=...

3. Run the self-check (works without credentials; will explain missing items):

   python connectors_google.py

"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List

from connectors import (
    BaseConnector,
    ConnectorConfig,
    ConnectorResult,
    ConnectorMode,
    SourceType,
)


def _missing_google_libs_message() -> str:
    return (
        "Missing Google client libraries. Install with:\n"
        "pip install google-api-python-client google-auth google-auth-oauthlib google-auth-httplib2"
    )


class GoogleAuthHelper:
    SCOPES = [
        "https://www.googleapis.com/auth/drive.readonly",
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/drive.file",
    ]


    @staticmethod
    def load_credentials():
        try:
            from google.oauth2 import service_account
            from google.oauth2.credentials import Credentials as OAuthCredentials
            from google.auth.transport.requests import Request
        except Exception as exc:  # ImportError or similar
            raise RuntimeError(_missing_google_libs_message()) from exc

        # Prefer service account JSON path
        sa_path = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if sa_path and os.path.isfile(sa_path):
            creds = service_account.Credentials.from_service_account_file(
                sa_path, scopes=GoogleAuthHelper.SCOPES
            )
            return creds

        # Fallback to OAuth2 refresh token (requires client_id, client_secret, refresh_token)
        client_id = os.getenv("GOOGLE_OAUTH_CLIENT_ID")
        client_secret = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET")
        refresh_token = os.getenv("GOOGLE_OAUTH_REFRESH_TOKEN")
        token_uri = "https://oauth2.googleapis.com/token"

        if client_id and client_secret and refresh_token:
            creds = OAuthCredentials(
                token=None,
                refresh_token=refresh_token,
                token_uri=token_uri,
                client_id=client_id,
                client_secret=client_secret,
                scopes=GoogleAuthHelper.SCOPES,
            )
            # Attempt to refresh to populate access token (may fail if invalid)
            try:
                creds.refresh(Request())
            except Exception:
                # Don't fail hard here; caller will see missing/invalid credentials
                pass
            return creds

        raise RuntimeError("No Google credentials found in environment; set GOOGLE_SERVICE_ACCOUNT_JSON or OAuth vars.")


class GmailConnector(BaseConnector):
    def __init__(self) -> None:
        cfg = ConnectorConfig(
            name="gmail_read_only_runtime",
            source_type=SourceType.GMAIL,
            mode=ConnectorMode.READ_ONLY,
        )
        super().__init__(cfg)

    def _build_service(self):
        try:
            from googleapiclient.discovery import build
        except Exception as exc:
            raise RuntimeError(_missing_google_libs_message()) from exc

        creds = GoogleAuthHelper.load_credentials()
        service = build("gmail", "v1", credentials=creds)
        return service

    def search(self, query: str, *, limit: int = 10) -> ConnectorResult:
        try:
            service = self._build_service()
        except Exception as exc:
            return ConnectorResult(items=[], notes=str(exc), connector_name=self.config.name)

        try:
            resp = service.users().messages().list(userId="me", q=query, maxResults=limit).execute()
            msgs = resp.get("messages", [])
            results: List[Dict[str, Any]] = []
            for m in msgs:
                mid = m.get("id")
                msg = service.users().messages().get(userId="me", id=mid, format="metadata", metadataHeaders=["Subject","From","Date"]).execute()
                headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
                results.append({
                    "id": mid,
                    "threadId": msg.get("threadId"),
                    "subject": headers.get("Subject"),
                    "from": headers.get("From"),
                    "snippet": msg.get("snippet"),
                })
            return ConnectorResult(items=results, source=SourceType.GMAIL, connector_name=self.config.name)
        except Exception as exc:
            return ConnectorResult(items=[], notes=f"Gmail API error: {exc}", connector_name=self.config.name)

    def fetch(self, item_id: str) -> Dict[str, Any]:
        try:
            service = self._build_service()
        except Exception as exc:
            return {"error": str(exc)}

        try:
            msg = service.users().messages().get(userId="me", id=item_id, format="full").execute()
            return msg
        except Exception as exc:
            return {"error": str(exc)}


class DriveConnector(BaseConnector):
    def __init__(self) -> None:
        cfg = ConnectorConfig(
            name="google_drive_read_only_runtime",
            source_type=SourceType.GOOGLE_DRIVE,
            mode=ConnectorMode.READ_ONLY,
        )
        super().__init__(cfg)

    def _build_service(self):
        try:
            from googleapiclient.discovery import build
        except Exception as exc:
            raise RuntimeError(_missing_google_libs_message()) from exc

        creds = GoogleAuthHelper.load_credentials()
        service = build("drive", "v3", credentials=creds)
        return service

    def search(self, query: str, *, limit: int = 10) -> ConnectorResult:
        try:
            service = self._build_service()
        except Exception as exc:
            return ConnectorResult(items=[], notes=str(exc), connector_name=self.config.name)

        # Basic fullText/name contains search; for more complex queries, adapt q param
        safe_query = query.replace("'", "\\\\'")
        q = f"name contains '{safe_query}' or fullText contains '{safe_query}'"
        try:
            resp = service.files().list(q=q, pageSize=limit, fields="files(id,name,mimeType,webViewLink)").execute()
            files = resp.get("files", [])
            results = [{"id": f.get("id"), "name": f.get("name"), "mimeType": f.get("mimeType"), "link": f.get("webViewLink")} for f in files]
            return ConnectorResult(items=results, source=SourceType.GOOGLE_DRIVE, connector_name=self.config.name)
        except Exception as exc:
            return ConnectorResult(items=[], notes=f"Drive API error: {exc}", connector_name=self.config.name)

    def fetch(self, item_id: str) -> Dict[str, Any]:
        try:
            service = self._build_service()
        except Exception as exc:
            return {"error": str(exc)}

        try:
            meta = service.files().get(fileId=item_id, fields="id,name,mimeType,webViewLink,owners").execute()
            return meta
        except Exception as exc:
            return {"error": str(exc)}

    def upload_folder(self, local_folder: str, drive_parent_id: str | None = None) -> ConnectorResult:
        try:
            from googleapiclient.http import MediaFileUpload
        except Exception as exc:
            return ConnectorResult(items=[], notes=_missing_google_libs_message(), connector_name=self.config.name)

        try:
            service = self._build_service()
        except Exception as exc:
            return ConnectorResult(items=[], notes=str(exc), connector_name=self.config.name)

        uploaded = []
        errors = []
        for root, _, files in __import__("os").walk(local_folder):
            for fname in files:
                local_path = __import__("os").path.join(root, fname)
                mime = None
                media = MediaFileUpload(local_path, resumable=True)
                body = {"name": fname}
                if drive_parent_id:
                    body["parents"] = [drive_parent_id]
                try:
                    # When uploading into a shared drive, include supportsAllDrives
                    f = service.files().create(
                        body=body,
                        media_body=media,
                        fields="id,name,webViewLink",
                        supportsAllDrives=True,
                    ).execute()
                    uploaded.append({"local": local_path, "id": f.get("id"), "name": f.get("name"), "link": f.get("webViewLink")})
                except Exception as exc:
                    errors.append({"file": local_path, "error": str(exc)})

        notes = None
        if errors:
            notes = json.dumps(errors, indent=2)
        return ConnectorResult(items=uploaded, notes=notes, connector_name=self.config.name)


def _self_test():
    print("Google connector self-test starting...\n")

    gc = GmailConnector()
    dc = DriveConnector()

    print("Gmail connector search (demo):")
    gmail_result = gc.search("from:me has:attachment", limit=3)
    if gmail_result.items:
        print(f"Found {len(gmail_result.items)} messages (showing keys):")
        for i in gmail_result.items[:3]:
            print("-", {k: i.get(k) for k in ("id", "subject", "from")})
    else:
        print("Gmail connector returned no items.")
        if gmail_result.notes:
            print("Notes:", gmail_result.notes)

    print("\nDrive connector search (demo):")
    drive_result = dc.search("policy", limit=3)
    if drive_result.items:
        print(f"Found {len(drive_result.items)} files:")
        for f in drive_result.items[:3]:
            print("-", {"id": f.get("id"), "name": f.get("name")})
    else:
        print("Drive connector returned no items.")
        if drive_result.notes:
            print("Notes:", drive_result.notes)


if __name__ == "__main__":
    _self_test()
