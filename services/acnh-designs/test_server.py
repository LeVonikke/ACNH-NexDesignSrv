import http.client
import tempfile
import threading
import unittest
from pathlib import Path

import msgpack

from server import CODE_FLOOR, Catalog, PortalHandler, ThreadingHTTPServer, encode_code, format_creator_id


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.catalog = Catalog(Path(self.tempdir.name) / "catalog.sqlite3", b"test-secret")
        self.token = self.catalog.login({"id": 42})
        self.authorization = f"Bearer {self.token}"
        PortalHandler.catalog = self.catalog
        self.http_server = ThreadingHTTPServer(("127.0.0.1", 0), PortalHandler)
        self.server_thread = threading.Thread(target=self.http_server.serve_forever)
        self.server_thread.start()

    def tearDown(self):
        self.http_server.shutdown()
        self.http_server.server_close()
        self.server_thread.join()
        self.catalog.close()
        self.tempdir.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.http_server.server_port)
        conn.request(method, path, body, headers or {})
        response = conn.getresponse()
        status, content = response.status, response.read()
        conn.close()
        return status, content

    def test_created_design_has_a_non_null_twelve_character_code(self):
        payload = {"body": msgpack.packb({"mMeta": {"mMtPro": False}, "mData": {}}, use_bin_type=True)}
        design_id = self.catalog.create_design(self.authorization, payload)

        self.assertGreaterEqual(design_id, CODE_FLOOR)
        self.assertEqual(len(encode_code(design_id)), 12)
        result = self.catalog.list_designs({"q[design_id]": [str(design_id)]}, "https://portal.test")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["offset"], 0)
        self.assertEqual(result["headers"][0]["id"], design_id)
        self.assertEqual(len(result["headers"][0]["digest"]), 40)
        self.assertEqual(
            result["headers"][0]["address"],
            {"user_id": CODE_FLOOR + 1, "id": CODE_FLOOR + 1, "name": "Nextendo player", "display_id": CODE_FLOOR + 1, "in_app_id": 0},
        )
        self.assertEqual(result["headers"][0]["meta"], "")
        self.assertEqual(self.catalog.design_body(design_id), payload["body"])

    def test_creator_id_uses_the_public_ma_format(self):
        self.assertEqual(format_creator_id(CODE_FLOOR + 1), "MA-1F0V-HWR5-JTC2")
        with self.assertRaises(ValueError):
            format_creator_id(0)

    def test_design_can_only_be_deleted_by_its_creator(self):
        design_id = self.catalog.create_design(
            self.authorization,
            {"body": msgpack.packb({"mMeta": {"mMtPro": True}, "mData": {}}, use_bin_type=True)},
        )
        other = self.catalog.login({"id": 7})

        self.assertFalse(self.catalog.delete_design(f"Bearer {other}", design_id))
        self.assertTrue(self.catalog.delete_design(self.authorization, design_id))

    def test_http_upload_list_and_download_round_trip(self):
        login_status, login_content = self.request(
            "POST",
            "/api/v1/auth_token",
            msgpack.packb({"id": 99}, use_bin_type=True),
            {"Content-Type": "application/x-msgpack"},
        )
        token = msgpack.unpackb(login_content, raw=False)["token"]
        body = msgpack.packb({"mMeta": {"mMtPro": False}, "mData": {"mPalette": {}}}, use_bin_type=True)
        upload_status, upload_content = self.request(
            "POST",
            "/api/v1/designs",
            msgpack.packb(
                {"body": body, "meta": msgpack.packb({"mMtPro": False}, use_bin_type=True)},
                use_bin_type=True,
            ),
            {"Authorization": f"Bearer {token}", "Content-Type": "application/x-msgpack"},
        )
        design_id = msgpack.unpackb(upload_content, raw=False)["id"]
        list_status, list_content = self.request("GET", f"/api/v2/designs?q[design_id]={design_id}")
        listing = msgpack.unpackb(list_content, raw=False)
        download_status, download_content = self.request("GET", f"/api/v1/designs/{design_id}")

        self.assertEqual((login_status, upload_status, list_status, download_status), (200, 201, 200, 200))
        self.assertEqual(listing["offset"], 0)
        self.assertEqual(listing["headers"][0]["id"], design_id)
        self.assertEqual(len(listing["headers"][0]["digest"]), 40)
        self.assertEqual(
            listing["headers"][0]["address"]["display_id"],
            listing["headers"][0]["design_player_id"],
        )
        self.assertEqual(listing["headers"][0]["meta"], "")
        self.assertEqual(msgpack.unpackb(download_content, raw=False), {"id": design_id, "body": body})

    def test_http_notification_token_registration_is_accepted(self):
        status, content = self.request(
            "POST",
            "/api/v1/notification_tokens",
            msgpack.packb({"token": "local-only"}, use_bin_type=True),
            {"Authorization": self.authorization, "Content-Type": "application/x-msgpack"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(msgpack.unpackb(content, raw=False), {})

    def test_http_design_player_registration_returns_a_nonzero_creator_id(self):
        status, content = self.request(
            "POST",
            "/api/v1/design_players",
            msgpack.packb({"name": "local player"}, use_bin_type=True),
            {"Authorization": self.authorization, "Content-Type": "application/x-msgpack"},
        )
        response = msgpack.unpackb(content, raw=False)

        self.assertEqual(status, 201)
        self.assertEqual(response["id"], self.catalog._player_id(self.authorization))
        self.assertEqual(response["display_id"], response["id"])
        self.assertEqual(response["mMyDesignAuthorId"], format_creator_id(response["id"]))
        stored = self.catalog._db.execute(
            "SELECT payload FROM design_players WHERE player_id = ?", (response["id"],)
        ).fetchone()
        self.assertEqual(msgpack.unpackb(stored["payload"], raw=False), {"name": "local player"})

    def test_request_trace_records_only_contract_field_names(self):
        status, _ = self.request(
            "POST",
            "/api/v1/design_players",
            msgpack.packb({"name": "private", "token": "must not be logged"}, use_bin_type=True),
            {"Authorization": self.authorization, "Content-Type": "application/x-msgpack"},
        )
        trace = self.catalog._db.execute(
            "SELECT method, path, fields FROM request_trace ORDER BY id DESC LIMIT 1"
        ).fetchone()

        self.assertEqual(status, 201)
        self.assertEqual(tuple(trace), ("POST", "/api/v1/design_players", "name,token"))

    def test_http_message_cards_returns_an_empty_paginated_inbox(self):
        status, content = self.request(
            "GET",
            "/api/v1/message_cards?offset=0&limit=30",
            headers={"Authorization": self.authorization},
        )

        self.assertEqual(status, 200)
        self.assertEqual(
            msgpack.unpackb(content, raw=False),
            {"offset": 0, "total": 0, "count": 0, "message_cards": []},
        )

    def test_auth_token_includes_a_future_expiry(self):
        status, content = self.request(
            "POST",
            "/api/v1/auth_token",
            msgpack.packb({"id": 123}, use_bin_type=True),
            {"Content-Type": "application/x-msgpack"},
        )
        response = msgpack.unpackb(content, raw=False)

        self.assertEqual(status, 200)
        self.assertIsInstance(response["token"], str)
        self.assertGreater(response["expire_at"], 1_700_000_000)

    def test_http_profile_status_allows_the_initialization_sequence(self):
        user_id = "6134456094606385495"
        token = self.catalog.login({"id": user_id})
        status, content = self.request(
            "GET",
            f"/api/v1/users/{user_id}/profile_status",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(status, 200)
        response = msgpack.unpackb(content, raw=False)
        self.assertEqual(response, {"user_profile": "ok", "land_profile": "ok"})

    def test_profile_status_rejects_a_different_authenticated_user(self):
        token = self.catalog.login({"id": 6134456094606385495})

        status, content = self.request(
            "GET",
            "/api/v1/users/1/profile_status",
            headers={"Authorization": f"Bearer {token}"},
        )

        self.assertEqual(status, 401)
        self.assertEqual(msgpack.unpackb(content, raw=False)["status"], 401)

    def test_http_web_service_resource_update_is_accepted(self):
        status, content = self.request(
            "PUT",
            "/api/v1/web_service_resources/resort_unlock",
            msgpack.packb({"value": True}, use_bin_type=True),
            {"Authorization": self.authorization, "Content-Type": "application/x-msgpack"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(msgpack.unpackb(content, raw=False), {})

    def test_http_profile_registration_returns_a_persistent_creator_id(self):
        status, content = self.request(
            "PUT",
            "/api/v1/users/42/profile",
            msgpack.packb({"nickname": "Bianca"}, use_bin_type=True),
            {"Authorization": self.authorization, "Content-Type": "application/x-msgpack"},
        )
        response = msgpack.unpackb(content, raw=False)

        self.assertEqual(status, 200)
        self.assertEqual(response["id"], self.catalog._player_id(self.authorization))
        self.assertEqual(len(response["digest"]), 40)
        self.assertIsInstance(response["created_at"], int)
        self.assertEqual(response["mMyDesignAuthorId"], format_creator_id(response["id"]))
        status, content = self.request(
            "GET",
            "/api/v1/users/42/profile_status",
            headers={"Authorization": self.authorization},
        )
        self.assertEqual(status, 200)
        self.assertEqual(msgpack.unpackb(content, raw=False), {"user_profile": "ok", "land_profile": "ok"})

    def test_http_resort_planner_profile_update_is_persisted(self):
        player_id = self.catalog._player_id(self.authorization)
        payload = {"name": "LeVon", "gender": 0, "user_icon": b"local-icon"}

        status, content = self.request(
            "PUT",
            f"/api/v1/resort_planners/{player_id}/profile",
            msgpack.packb(payload, use_bin_type=True),
            {"Authorization": self.authorization, "Content-Type": "application/x-msgpack"},
        )

        self.assertEqual(status, 200)
        self.assertEqual(msgpack.unpackb(content, raw=False), {})
        stored = self.catalog._db.execute(
            "SELECT payload FROM resort_planner_profiles WHERE player_id = ?", (player_id,)
        ).fetchone()
        self.assertEqual(msgpack.unpackb(stored["payload"], raw=False), payload)
        status, content = self.request(
            "GET",
            "/api/v1/users/42/profile_status",
            headers={"Authorization": self.authorization},
        )
        self.assertEqual(status, 200)
        self.assertEqual(msgpack.unpackb(content, raw=False), {"user_profile": "ok", "land_profile": "ok"})

    def test_http_land_and_icon_contracts_are_available(self):
        land_status, land_content = self.request(
            "GET",
            "/api/v1/users/6134456094606385495/land",
            headers={"Authorization": self.authorization},
        )
        icon_status, icon_content = self.request(
            "GET",
            "/api/v1/users/6134456094606385495/icon",
            headers={"Authorization": self.authorization},
        )
        land = msgpack.unpackb(land_content, raw=False)
        icon = msgpack.unpackb(icon_content, raw=False)

        self.assertEqual(land_status, 200)
        self.assertEqual(land["id"], self.catalog._player_id(self.authorization))
        self.assertEqual(len(land["password"]), 64)
        self.assertEqual((icon_status, icon), (200, {"body": b""}))

    def test_startup_sync_adopts_the_game_creator_id_and_migrates_designs(self):
        design_id = self.catalog.create_design(
            self.authorization,
            {"body": msgpack.packb({"mMeta": {"mMtPro": False}, "mData": {}}, use_bin_type=True)},
        )
        game_player_id = CODE_FLOOR + 100
        status, content = self.request(
            "GET",
            f"/api/v2/designs?offset=0&limit=256&with_binaries=false&q[player_id]={game_player_id}",
            headers={"Authorization": self.authorization},
        )
        response = msgpack.unpackb(content, raw=False)

        self.assertEqual(status, 200)
        self.assertEqual(self.catalog._player_id(self.authorization), game_player_id)
        self.assertEqual(response["headers"][0]["id"], design_id)
        self.assertEqual(response["headers"][0]["design_player_id"], game_player_id)


if __name__ == "__main__":
    unittest.main()
