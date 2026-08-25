#!/usr/bin/env python3
"""A small, self-hosted MessagePack backend for ACNH Custom Designs.

It deliberately stores the design body supplied by the game without attempting
to interpret its pixel format. That lets normal and Pro designs round-trip as
long as the game accepts the portal response schema.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import os
import sqlite3
import ssl
import time
import re
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any
from urllib.parse import parse_qs, urlparse

import msgpack


# ACNH's public MA/MO formatter uses a 30-character alphabet. The in-game
# binary contains this exact sequence; notably, ``Z`` is not a valid digit.
CODE_ALPHABET = "0123456789BCDFGHJKLMNPQRSTVWXY"
CODE_BASE = len(CODE_ALPHABET)

# Existing private catalogues began allocation at 31**11. Keep that floor so
# previously stored IDs (including the user's posted designs) remain stable.
# The public formatter itself is base-30, therefore its upper bound is 30**12.
CODE_FLOOR = 31 ** 11
CODE_CEILING = CODE_BASE ** 12 - 1


def encode_code(number: int) -> str:
    """Encode an API identifier as ACNH's 12-character public payload."""
    if not CODE_FLOOR <= number <= CODE_CEILING:
        raise ValueError("design ID is outside the 12-character portal range")

    digits: list[str] = []
    for _ in range(12):
        number, digit = divmod(number, CODE_BASE)
        digits.append(CODE_ALPHABET[digit])
    return "".join(reversed(digits))


def format_creator_id(player_id: int) -> str:
    """Return the public MA form for an already-valid portal player ID."""
    code = encode_code(player_id)
    return f"MA-{code[:4]}-{code[4:8]}-{code[8:]}"


class Catalog:
    def __init__(self, database: Path, secret: bytes) -> None:
        database.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(database, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = Lock()
        self._secret = secret
        self._db.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS creators (
                subject TEXT PRIMARY KEY,
                player_id INTEGER NOT NULL UNIQUE,
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS profiles (
                game_user_id TEXT PRIMARY KEY,
                subject TEXT NOT NULL UNIQUE REFERENCES creators(subject),
                created_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS resort_planner_profiles (
                player_id INTEGER PRIMARY KEY REFERENCES creators(player_id),
                payload BLOB NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS design_players (
                player_id INTEGER PRIMARY KEY REFERENCES creators(player_id),
                payload BLOB NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS designs (
                id INTEGER PRIMARY KEY,
                player_id INTEGER NOT NULL REFERENCES creators(player_id),
                is_pro INTEGER NOT NULL,
                payload BLOB NOT NULL,
                header_meta,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sequences (
                name TEXT PRIMARY KEY,
                value INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS request_trace (
                id INTEGER PRIMARY KEY,
                method TEXT NOT NULL,
                path TEXT NOT NULL,
                fields TEXT NOT NULL,
                created_at INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS designs_by_player ON designs(player_id, is_pro, created_at DESC);
            CREATE INDEX IF NOT EXISTS request_trace_by_time ON request_trace(created_at DESC);
            """
        )
        # Existing private catalogues were created before the game-supplied
        # list metadata was retained.  SQLite has no conditional ADD COLUMN.
        columns = {row[1] for row in self._db.execute("PRAGMA table_info(designs)")}
        if "header_meta" not in columns:
            self._db.execute("ALTER TABLE designs ADD COLUMN header_meta")
        self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    def trace_request(self, method: str, path: str, payload: dict[str, Any] | None = None) -> None:
        """Keep a privacy-preserving record of the client contract in use.

        The game is updated independently from this local service.  Keeping
        only HTTP method, route and top-level MessagePack field *names* makes
        missing endpoints diagnosable on the next portal session without ever
        writing account identifiers, bearer tokens, or save data to the log.
        """
        fields = "" if payload is None else ",".join(sorted(map(str, payload.keys())))
        with self._lock:
            self._db.execute(
                "INSERT INTO request_trace(method, path, fields, created_at) VALUES (?, ?, ?, ?)",
                (method, path, fields, int(time.time())),
            )
            self._db.commit()

    @staticmethod
    def _subject_from_login(payload: dict[str, Any]) -> str:
        subject = payload.get("id")
        if subject is None:
            raise ValueError("auth_token request has no id")
        return str(subject)

    def _token(self, subject: str) -> str:
        encoded = base64.urlsafe_b64encode(subject.encode()).rstrip(b"=").decode()
        signature = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
        return f"v1.{encoded}.{signature}"

    def _subject_from_token(self, authorization: str | None) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            raise PermissionError("missing bearer token")
        try:
            version, encoded, signature = authorization[7:].split(".", 2)
            expected = hmac.new(self._secret, encoded.encode(), hashlib.sha256).hexdigest()
            if version != "v1" or not hmac.compare_digest(signature, expected):
                raise ValueError
            padding = "=" * (-len(encoded) % 4)
            return base64.urlsafe_b64decode(encoded + padding).decode()
        except (UnicodeDecodeError, ValueError):
            raise PermissionError("invalid bearer token") from None

    def login(self, payload: dict[str, Any]) -> str:
        subject = self._subject_from_login(payload)
        now = int(time.time())
        with self._lock:
            row = self._db.execute("SELECT player_id FROM creators WHERE subject = ?", (subject,)).fetchone()
            if row is None:
                # SQLite row IDs are small, so offset them into the exact range the
                # game's 12-character base-30 formatter supports.
                player_id = CODE_FLOOR + self._db.execute("SELECT COUNT(*) FROM creators").fetchone()[0] + 1
                self._db.execute(
                    "INSERT INTO creators(subject, player_id, created_at) VALUES (?, ?, ?)",
                    (subject, player_id, now),
                )
                self._db.commit()
        return self._token(subject)

    def authentication(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Build the complete auth_token response expected by ACNH 3.0.3."""
        return {
            "token": self.login(payload),
            # The client parses this integer even though the local bearer
            # token is self-contained. Keep it comfortably in the future so
            # the authenticated profile tasks are not treated as expired.
            "expire_at": int(time.time()) + 6 * 60 * 60,
        }

    def _player_id(self, authorization: str | None) -> int:
        subject = self._subject_from_token(authorization)
        row = self._db.execute("SELECT player_id FROM creators WHERE subject = ?", (subject,)).fetchone()
        if row is None:
            raise PermissionError("unknown portal account")
        return int(row["player_id"])

    def profile(self, authorization: str | None) -> dict[str, Any]:
        subject = self._subject_from_token(authorization)
        row = self._db.execute(
            "SELECT player_id, created_at FROM creators WHERE subject = ?", (subject,)
        ).fetchone()
        if row is None:
            raise PermissionError("unknown portal account")
        player_id = int(row["player_id"])
        # ACNH validates this field as a short string. It is an opaque server
        # digest, so make it deterministic without storing account secrets.
        digest = hashlib.sha1(f"{subject}:{player_id}".encode()).hexdigest()
        # ``mMyDesignAuthorId`` is the field ACNH caches for the Creator ID
        # shown on the passport and by "Check creator ID info".  The 3.0.3
        # executable deserializes it as a string, so expose the normal public
        # MA representation rather than a private numeric database key.
        #
        # Keep the original compact fields too: older client paths use them
        # while the profile object has additional optional fields that ACNH
        # safely defaults when the private service does not manage them.
        return {
            "id": player_id,
            "digest": digest,
            "created_at": int(row["created_at"]),
            "mMyDesignAuthorId": format_creator_id(player_id),
        }

    def land(self, authorization: str | None) -> dict[str, Any]:
        """Return the companion land credential required by ACNH 3.0.3.

        The game deserializes ``id`` as an unsigned portal identifier and
        ``password`` as a string.  A private service does not need to expose a
        reusable secret, so a deterministic opaque value is sufficient.
        """
        subject = self._subject_from_token(authorization)
        player_id = self._player_id(authorization)
        password = hmac.new(self._secret, f"land:{subject}".encode(), hashlib.sha256).hexdigest()
        return {"id": player_id, "password": password}

    @staticmethod
    def icon() -> dict[str, bytes]:
        # The icon task requires a binary MessagePack body. An empty icon is
        # valid and lets the game continue with its local player portrait.
        return {"body": b""}

    def profile_status(self, game_user_id: str, authorization: str | None) -> dict[str, str]:
        """Acknowledge the portal profile resources required at startup.

        ACNH checks this before its optional Custom Designs tasks.  In 3.0.3,
        returning ``ng`` stops that initialization sequence rather than asking
        the client to register a missing profile, so a self-hosted portal must
        report the available local resources as ready.
        """
        subject = self._subject_from_token(authorization)
        if subject != game_user_id:
            raise PermissionError("profile status does not belong to the authenticated user")
        return {"user_profile": "ok", "land_profile": "ok"}

    @staticmethod
    def message_cards(query: dict[str, list[str]]) -> dict[str, Any]:
        """Return an empty, paginated message-card inbox.

        ACNH requests this resource before it starts the creator-profile
        synchronization.  Message cards are optional portal notifications, so
        a private service can safely expose an empty collection.  Returning a
        successful MessagePack response here is important: a 404 makes the
        client stop the remaining initialization tasks.
        """
        try:
            offset = max(0, int(query.get("offset", ["0"])[0]))
        except ValueError:
            raise ValueError("offset must be an integer") from None
        return {"offset": offset, "total": 0, "count": 0, "message_cards": []}

    def register_profile(self, game_user_id: str, authorization: str | None) -> dict[str, Any]:
        subject = self._subject_from_token(authorization)
        now = int(time.time())
        with self._lock:
            self._db.execute(
                "INSERT INTO profiles(game_user_id, subject, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(game_user_id) DO UPDATE SET subject = excluded.subject, created_at = excluded.created_at",
                (game_user_id, subject, now),
            )
            self._db.commit()
        return self.profile(authorization)

    def register_design_player(self, authorization: str | None, payload: dict[str, Any]) -> dict[str, Any]:
        """Create or update the author's Custom Designs portal identity.

        ACNH 3.0.3 contains a dedicated ``/api/v1/design_players`` resource.
        This is distinct from the Nintendo-account subject used to obtain the
        bearer token: it owns the numeric identifier rendered as an MA code.
        Preserve the game's version-specific registration payload verbatim,
        while returning the canonical numeric ID in the conventional aliases
        used by the portal's list and profile objects.
        """
        player_id = self._player_id(authorization)
        packed_payload = msgpack.packb(payload, use_bin_type=True)
        with self._lock:
            self._db.execute(
                "INSERT INTO design_players(player_id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(player_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (player_id, sqlite3.Binary(packed_payload), int(time.time())),
            )
            self._db.commit()

        creator_id = format_creator_id(player_id)
        return {
            "id": player_id,
            "player_id": player_id,
            "design_player_id": player_id,
            "display_id": player_id,
            "design_author_id": creator_id,
            "mMyDesignAuthorId": creator_id,
        }

    def design_player(self, authorization: str | None) -> dict[str, Any]:
        """Return the authenticated author's persistent portal identity."""
        return self.register_design_player(authorization, {})

    def register_resort_planner_profile(
        self, player_id: int, authorization: str | None, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist ACNH's creator-profile upload.

        The 3.0.3 client performs this write after its initial account flow.
        Its local Creator ID is not finalized when this endpoint is missing,
        even if the earlier authentication and profile-status requests succeed.
        The uploaded schema is game-version-specific, so retain it losslessly
        and acknowledge the successful update rather than reinterpret it.
        """
        authenticated_player_id = self._player_id(authorization)
        if player_id != authenticated_player_id:
            raise PermissionError("cannot update another creator profile")

        packed_payload = msgpack.packb(payload, use_bin_type=True)
        with self._lock:
            self._db.execute(
                "INSERT INTO resort_planner_profiles(player_id, payload, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(player_id) DO UPDATE SET payload = excluded.payload, updated_at = excluded.updated_at",
                (player_id, sqlite3.Binary(packed_payload), int(time.time())),
            )
            subject = self._subject_from_token(authorization)
            self._db.execute(
                "INSERT INTO profiles(game_user_id, subject, created_at) VALUES (?, ?, ?) "
                "ON CONFLICT(game_user_id) DO UPDATE SET subject = excluded.subject, created_at = excluded.created_at",
                (subject, subject, int(time.time())),
            )
            self._db.commit()

        # This endpoint acknowledges an update; profile data is retrieved via
        # the user/profile endpoints, not in the PUT response.
        return {}

    def adopt_game_player_id(self, authorization: str | None, player_id: int) -> None:
        """Adopt the creator ID reported by ACNH during its own-profile sync."""
        if not CODE_FLOOR <= player_id <= CODE_CEILING:
            raise ValueError("game reported an invalid creator ID")
        subject = self._subject_from_token(authorization)
        with self._lock:
            row = self._db.execute(
                "SELECT player_id FROM creators WHERE subject = ?", (subject,)
            ).fetchone()
            if row is None:
                raise PermissionError("unknown portal account")
            previous_id = int(row["player_id"])
            if previous_id == player_id:
                return
            # Bootstrap IDs are assigned only before the game supplies its
            # canonical creator ID. Never replace an established profile.
            if previous_id != CODE_FLOOR + 1:
                return
            occupied = self._db.execute(
                "SELECT 1 FROM creators WHERE player_id = ?", (player_id,)
            ).fetchone()
            if occupied is not None:
                raise ValueError("creator ID is already assigned")
            self._db.execute("UPDATE designs SET player_id = ? WHERE player_id = ?", (player_id, previous_id))
            self._db.execute("UPDATE creators SET player_id = ? WHERE subject = ?", (player_id, subject))
            self._db.commit()

    def create_design(self, authorization: str | None, payload: dict[str, Any]) -> int:
        player_id = self._player_id(authorization)
        body = payload.get("body")
        if not isinstance(body, (bytes, bytearray)):
            raise ValueError("design request has no MessagePack body")
        header_meta = payload.get("meta")
        try:
            decoded_body = msgpack.unpackb(body, raw=False, strict_map_key=False)
            is_pro = bool(decoded_body.get("mMeta", {}).get("mMtPro", False))
        except (msgpack.ExtraData, msgpack.FormatError, ValueError, TypeError) as error:
            raise ValueError("design body is not valid MessagePack") from error

        now = int(time.time())
        with self._lock:
            row = self._db.execute("SELECT value FROM sequences WHERE name = 'design'").fetchone()
            design_id = CODE_FLOOR + 1 if row is None else int(row["value"]) + 1
            if design_id > CODE_CEILING:
                raise OverflowError("portal ID space exhausted")
            self._db.execute(
                "INSERT INTO sequences(name, value) VALUES ('design', ?) "
                "ON CONFLICT(name) DO UPDATE SET value = excluded.value",
                (design_id,),
            )
            self._db.execute(
                "INSERT INTO designs(id, player_id, is_pro, payload, header_meta, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (design_id, player_id, is_pro, bytes(body), header_meta, now, now),
            )
            self._db.commit()
        return design_id

    @staticmethod
    def _header(row: sqlite3.Row, origin: str) -> dict[str, Any]:
        player_id = int(row["player_id"])
        header_meta = row["header_meta"]
        # The client accepts this field as text, but its thumbnail decoder is
        # strict about the server-side representation.  Until that converter
        # is fully reproduced, return the safe empty value rather than feed a
        # malformed preview payload into the emulated game.  The original
        # MessagePack metadata remains retained in ``header_meta``.
        header_meta_text = ""
        # ACNH 3.0.3 validates this complete address structure while parsing a
        # list entry, even though a private portal does not use the social
        # address for routing.  Keep its integer fields in their valid ranges.
        address = {
            "user_id": player_id,
            "id": player_id,
            "name": "Nextendo player",
            # ``display_id`` is the public author identifier exposed by the
            # list API. Returning zero makes ACNH render MA-0000 even though
            # the same entry already has a valid ``design_player_id``.
            "display_id": player_id,
            "in_app_id": 0,
        }
        return {
            "id": int(row["id"]),
            "design_player_id": player_id,
            "design_player_name": "Nextendo player",
            "address": address,
            "digest": hashlib.sha1(bytes(row["payload"])).hexdigest(),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
            "meta": header_meta_text,
            "body": f"{origin}/api/v1/designs/{int(row['id'])}",
        }

    def list_designs(self, query: dict[str, list[str]], origin: str) -> dict[str, Any]:
        try:
            offset = max(0, int(query.get("offset", ["0"])[0]))
            limit = min(120, max(1, int(query.get("limit", ["120"])[0])))
        except ValueError:
            raise ValueError("offset and limit must be integers") from None

        clauses: list[str] = []
        values: list[Any] = []
        if design_id := query.get("q[design_id]", [None])[0]:
            clauses.append("id = ?")
            values.append(int(design_id))
        if player_id := query.get("q[player_id]", [None])[0]:
            clauses.append("player_id = ?")
            values.append(int(player_id))
        if pro := query.get("q[pro]", [None])[0]:
            if pro.lower() not in {"true", "false"}:
                raise ValueError("q[pro] must be true or false")
            clauses.append("is_pro = ?")
            values.append(pro.lower() == "true")

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            total = self._db.execute(f"SELECT COUNT(*) FROM designs{where}", values).fetchone()[0]
            rows = self._db.execute(
                f"SELECT * FROM designs{where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                [*values, limit, offset],
            ).fetchall()
        headers = [self._header(row, origin) for row in rows]
        return {"offset": offset, "total": total, "count": len(headers), "headers": headers}

    def design_body(self, design_id: int) -> bytes | None:
        with self._lock:
            row = self._db.execute("SELECT payload FROM designs WHERE id = ?", (design_id,)).fetchone()
        return None if row is None else bytes(row["payload"])

    def delete_design(self, authorization: str | None, design_id: int) -> bool:
        player_id = self._player_id(authorization)
        with self._lock:
            result = self._db.execute("DELETE FROM designs WHERE id = ? AND player_id = ?", (design_id, player_id))
            self._db.commit()
        return result.rowcount == 1


class PortalHandler(BaseHTTPRequestHandler):
    catalog: Catalog

    server_version = "NextendoACNHDesigns/0.1"

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)

    def _origin(self) -> str:
        host = self.headers.get("Host", "api.hac.lp1.acbaa.srv.nintendo.net").split(":", 1)[0]
        return f"https://{host}"

    def _read_msgpack(self) -> dict[str, Any]:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = msgpack.unpackb(self.rfile.read(length), raw=False, strict_map_key=False)
        except (ValueError, msgpack.ExtraData, msgpack.FormatError) as error:
            raise ValueError("request body is not valid MessagePack") from error
        if not isinstance(data, dict):
            raise ValueError("request MessagePack root must be a map")
        return data

    def _respond(self, status: HTTPStatus, body: Any | None = None) -> None:
        encoded = b"" if body is None else msgpack.packb(body, use_bin_type=True)
        self.send_response(status)
        self.send_header("Content-Type", "application/x-msgpack")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(encoded)

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._respond(status, {"error": message, "status": int(status)})

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        self.catalog.trace_request("GET", parsed.path)
        if parsed.path == "/healthz":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        if parsed.path == "/api/v2/designs":
            try:
                # On portal startup ACNH asks for its own designs with this
                # marker and supplies its canonical Creator ID in q[player_id].
                if parsed.query and parse_qs(parsed.query).get("with_binaries") == ["false"]:
                    player_id = parse_qs(parsed.query).get("q[player_id]", [None])[0]
                    if player_id is not None:
                        self.catalog.adopt_game_player_id(self.headers.get("Authorization"), int(player_id))
                self._respond(HTTPStatus.OK, self.catalog.list_designs(parse_qs(parsed.query), self._origin()))
            except PermissionError as error:
                self._error(HTTPStatus.UNAUTHORIZED, str(error))
            except (ValueError, OverflowError) as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if parsed.path == "/api/v1/message_cards":
            try:
                self.catalog._player_id(self.headers.get("Authorization"))
                self._respond(HTTPStatus.OK, self.catalog.message_cards(parse_qs(parsed.query)))
            except PermissionError as error:
                self._error(HTTPStatus.UNAUTHORIZED, str(error))
            except ValueError as error:
                self._error(HTTPStatus.BAD_REQUEST, str(error))
            return
        if parsed.path == "/api/v1/design_players":
            try:
                self._respond(HTTPStatus.OK, self.catalog.design_player(self.headers.get("Authorization")))
            except PermissionError as error:
                self._error(HTTPStatus.UNAUTHORIZED, str(error))
            return
        status_match = re.fullmatch(r"/api/v1/users/(\d+)/profile_status", parsed.path)
        if status_match:
            # ACNH 3.0.3 deserializes these exact keys and accepts only the
            # string values "ok" and "ng".  The portal requires both the
            # creator profile and land profile to have been registered.
            try:
                self._respond(
                    HTTPStatus.OK,
                    self.catalog.profile_status(status_match.group(1), self.headers.get("Authorization")),
                )
            except PermissionError as error:
                self._error(HTTPStatus.UNAUTHORIZED, str(error))
            return
        if re.fullmatch(r"/api/v1/users/\d+/profile", parsed.path):
            try:
                self._respond(HTTPStatus.OK, self.catalog.profile(self.headers.get("Authorization")))
            except PermissionError as error:
                self._error(HTTPStatus.UNAUTHORIZED, str(error))
            return
        if re.fullmatch(r"/api/v1/users/\d+/land", parsed.path):
            try:
                self._respond(HTTPStatus.OK, self.catalog.land(self.headers.get("Authorization")))
            except PermissionError as error:
                self._error(HTTPStatus.UNAUTHORIZED, str(error))
            return
        if re.fullmatch(r"/api/v1/users/\d+/icon", parsed.path):
            try:
                self.catalog._player_id(self.headers.get("Authorization"))
                self._respond(HTTPStatus.OK, self.catalog.icon())
            except PermissionError as error:
                self._error(HTTPStatus.UNAUTHORIZED, str(error))
            return
        if parsed.path.startswith("/api/v1/designs/"):
            try:
                design_id = int(parsed.path.rsplit("/", 1)[1])
            except ValueError:
                self._error(HTTPStatus.NOT_FOUND, "unknown design")
                return
            body = self.catalog.design_body(design_id)
            if body is None:
                self._error(HTTPStatus.NOT_FOUND, "unknown design")
            else:
                # Since ACNH 3.0.3 this is not the design MessagePack itself:
                # the client first validates the requested numeric id and then
                # unpacks the binary body in this response envelope.
                self._respond(HTTPStatus.OK, {"id": design_id, "body": body})
            return
        self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")

    def do_POST(self) -> None:
        try:
            payload = self._read_msgpack()
            self.catalog.trace_request("POST", urlparse(self.path).path, payload)
            if self.path == "/api/v1/auth_token":
                self._respond(HTTPStatus.OK, self.catalog.authentication(payload))
            elif self.path == "/api/v1/notification_tokens":
                # ACNH registers a push-notification token immediately after
                # authenticating. A self-hosted portal has no push service, but
                # the game only needs this registration to succeed before it
                # continues to the designs endpoints.
                self.catalog._player_id(self.headers.get("Authorization"))
                self._respond(HTTPStatus.OK, {})
            elif self.path == "/api/v1/design_players":
                # This 3.0.3 registration is the portal-side owner of the MA
                # identifier. Log only the top-level keys: they are enough to
                # diagnose schema changes without recording player/save data.
                print(
                    f"design_players registration fields={','.join(sorted(map(str, payload.keys())))}",
                    flush=True,
                )
                self._respond(
                    HTTPStatus.CREATED,
                    self.catalog.register_design_player(self.headers.get("Authorization"), payload),
                )
            elif self.path == "/api/v1/designs":
                design_id = self.catalog.create_design(self.headers.get("Authorization"), payload)
                self._respond(HTTPStatus.CREATED, {"id": design_id})
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except PermissionError as error:
            self._error(HTTPStatus.UNAUTHORIZED, str(error))
        except (ValueError, OverflowError) as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))

    def do_PUT(self) -> None:
        try:
            # The 3.0.3 client records unlock state through this endpoint
            # before opening the portal. It only checks the HTTP result, so a
            # private portal can acknowledge the resource without a remote
            # web-service account.
            payload = self._read_msgpack()
            self.catalog.trace_request("PUT", urlparse(self.path).path, payload)
            if re.fullmatch(r"/api/v1/web_service_resources/[A-Za-z0-9_-]+", self.path):
                self.catalog._player_id(self.headers.get("Authorization"))
                self._respond(HTTPStatus.OK, {})
            elif resort_profile_match := re.fullmatch(r"/api/v1/resort_planners/(\d+)/profile", self.path):
                self._respond(
                    HTTPStatus.OK,
                    self.catalog.register_resort_planner_profile(
                        int(resort_profile_match.group(1)), self.headers.get("Authorization"), payload
                    ),
                )
            elif profile_match := re.fullmatch(r"/api/v1/users/(\d+)/profile", self.path):
                self._respond(
                    HTTPStatus.OK,
                    self.catalog.register_profile(profile_match.group(1), self.headers.get("Authorization")),
                )
            elif re.fullmatch(r"/api/v1/users/\d+/land", self.path):
                self.catalog._player_id(self.headers.get("Authorization"))
                self._respond(HTTPStatus.OK, self.catalog.land(self.headers.get("Authorization")))
            elif re.fullmatch(r"/api/v1/users/\d+/icon", self.path):
                self.catalog._player_id(self.headers.get("Authorization"))
                self._respond(HTTPStatus.OK, self.catalog.icon())
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
        except PermissionError as error:
            self._error(HTTPStatus.UNAUTHORIZED, str(error))
        except ValueError as error:
            self._error(HTTPStatus.BAD_REQUEST, str(error))

    def do_DELETE(self) -> None:
        self.catalog.trace_request("DELETE", urlparse(self.path).path)
        if not self.path.startswith("/api/v1/designs/"):
            self._error(HTTPStatus.NOT_FOUND, "unknown endpoint")
            return
        try:
            design_id = int(self.path.rsplit("/", 1)[1])
            if self.catalog.delete_design(self.headers.get("Authorization"), design_id):
                self._respond(HTTPStatus.NO_CONTENT)
            else:
                self._error(HTTPStatus.NOT_FOUND, "unknown design")
        except PermissionError as error:
            self._error(HTTPStatus.UNAUTHORIZED, str(error))
        except ValueError:
            self._error(HTTPStatus.NOT_FOUND, "unknown design")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, default=Path("acnh-designs.sqlite3"))
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=443)
    parser.add_argument("--certfile", type=Path, help="PEM certificate for direct TLS")
    parser.add_argument("--keyfile", type=Path, help="PEM key for direct TLS")
    args = parser.parse_args()

    secret = os.environ.get("ACNH_DESIGNS_AUTH_SECRET")
    if not secret:
        parser.error("ACNH_DESIGNS_AUTH_SECRET must be set to a long random value")
    if bool(args.certfile) != bool(args.keyfile):
        parser.error("--certfile and --keyfile must be supplied together")

    PortalHandler.catalog = Catalog(args.database, secret.encode())
    server = ThreadingHTTPServer((args.host, args.port), PortalHandler)
    if args.certfile:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(args.certfile, args.keyfile)
        context.set_alpn_protocols(["http/1.1"])
        server.socket = context.wrap_socket(server.socket, server_side=True)
    scheme = "https" if args.certfile else "http"
    print(f"ACNH Designs portal listening on {scheme}://{args.host}:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
