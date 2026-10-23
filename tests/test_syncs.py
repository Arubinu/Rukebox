"""Folders filled from another machine: the sealed passwords and what a sync may hold."""
import os
import shutil
import stat
import tempfile
import unittest
from unittest import mock

import _path
import secret_box
import syncs


class SecretBoxTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.machine = mock.patch.object(secret_box, "_machine_id", return_value=b"machine-one")
        self.machine.start()
        self.addCleanup(mock.patch.stopall)

    def test_a_password_comes_back_and_is_not_kept_in_clear(self):
        token = secret_box.seal("mot de passe é", self.dir)
        self.assertNotIn("mot", token)
        self.assertEqual(secret_box.unseal(token, self.dir), "mot de passe é")
        self.assertNotEqual(token, secret_box.seal("mot de passe é", self.dir), "a fresh nonce each time")

    def test_another_machine_cannot_open_it(self):
        token = secret_box.seal("secret", self.dir)
        self.machine.stop()
        with mock.patch.object(secret_box, "_machine_id", return_value=b"machine-two"):
            with self.assertRaises(secret_box.SecretLost):
                secret_box.unseal(token, self.dir)
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        with mock.patch.object(secret_box, "_machine_id", return_value=b"machine-one"):
            with self.assertRaises(secret_box.SecretLost, msg="the same machine id, but not its key"):
                secret_box.unseal(token, other)

    def test_a_changed_token_is_refused(self):
        token = secret_box.seal("secret", self.dir)
        broken = token[:-6] + ("A" if token[-6] != "A" else "B") + token[-5:]
        for bad in (broken, "", "!!!", token[:20]):
            with self.assertRaises(secret_box.SecretLost):
                secret_box.unseal(bad, self.dir)

    @unittest.skipUnless(os.name == "posix", "file modes are a POSIX matter")
    def test_the_key_and_the_store_are_the_service_accounts_alone(self):
        store = secret_box.Store(os.path.join(self.dir, "secrets.json"), self.dir)
        store.put("nas", "pw")
        for name in ("secret.key", "secrets.json"):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.dir, name)).st_mode), 0o600, name)

    def test_the_store_says_which_secrets_this_machine_lost(self):
        store = secret_box.Store(os.path.join(self.dir, "secrets.json"), self.dir)
        store.put("nas", "pw")
        store.put("cloud", "token")
        self.assertEqual((store.get("nas"), store.lost()), ("pw", []))
        os.remove(os.path.join(self.dir, "secret.key"))
        self.assertEqual(store.lost(), ["cloud", "nas"], "a backup restored where the key is not")
        with self.assertRaises(secret_box.SecretLost):
            store.get("nas")
        store.put("nas", "typed again")
        self.assertEqual((store.get("nas"), store.lost()), ("typed again", ["cloud"]))
        store.drop("cloud")
        self.assertEqual(store.names(), ["nas"])
        self.assertIsNone(store.get("cloud"))


class ValidationTest(unittest.TestCase):
    def connection(self, **fields):
        data = {"name": "NAS", "type": "sftp", "host": "nas.local", "user": "pi", "path": "music"}
        data.update(fields)
        return syncs.validate_connection(data)

    def test_nothing_typed_can_pass_for_an_option(self):
        for host in ("-oProxyCommand=sh", "nas;rm", "a b", "nas..local", "", "x" * 300):
            with self.assertRaises(ValueError, msg=host):
                self.connection(host=host)
        for user in ("-x", "a b", "pi;id"):
            with self.assertRaises(ValueError, msg=user):
                self.connection(user=user)
        for path in ("../etc", "music/../../x", "-rf", "a\nb"):
            with self.assertRaises(ValueError, msg=path):
                self.connection(path=path)
        self.assertEqual(self.connection(host="[fe80::1]")["host"], "fe80::1")
        self.assertEqual(self.connection(path="/Music//Rock/./")["path"], "Music/Rock")

    def test_a_connection_gets_its_type_defaults(self):
        self.assertEqual(self.connection()["port"], 22)
        self.assertEqual(self.connection(type="smb", tls="none")["tls"], "verify", "nothing to choose for SMB")
        self.assertEqual(self.connection(type="webdav")["vendor"], "other")
        with self.assertRaises(ValueError):
            self.connection(type="webdav", auth="key")
        with self.assertRaises(ValueError):
            self.connection(port=70000)
        with self.assertRaises(ValueError):
            self.connection(type="nfs")

    def test_a_sync_fetches_only_the_files_its_folder_needs(self):
        conns = [{"id": "nas"}]
        job = syncs.validate_job({"name": "Music", "connection": "nas", "kind": "music", "target": "/srv/music/"}, conns)
        self.assertIn("flac", job["extensions"])
        self.assertIn("jpg", job["extensions"], "covers come with the music")
        self.assertEqual(job["target"], "/srv/music")
        ann = syncs.validate_job({"name": "A", "connection": "nas", "kind": "announcement", "target": "/a"}, conns)
        self.assertNotIn("jpg", ann["extensions"])
        own = syncs.validate_job({"name": "A", "connection": "nas", "target": "/a", "extensions": ".MP3, flac"}, conns)
        self.assertEqual(own["extensions"], ["mp3", "flac"])
        for bad in ({"extensions": "exe;rm"}, {"target": "relative"}, {"target": "/a/../etc"},
                    {"connection": "gone"}, {"every_hours": 500}, {"window_start": "25:00"},
                    {"wifi": "ssid", "ssid": ""}, {"internet": "maybe"}):
            data = {"name": "A", "connection": "nas", "target": "/a"}
            data.update(bad)
            with self.assertRaises(ValueError, msg=bad):
                syncs.validate_job(data, conns)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = _path.document(self.dir)

    def test_a_server_key_is_pinned_until_the_server_changes(self):
        made = syncs.save_connection({"name": "NAS", "type": "sftp", "host": "nas.local"}, path=self.path)
        syncs.pin_host_key(made["id"], "nas.local ssh-ed25519 AAAA", path=self.path)
        changed = syncs.save_connection({"user": "music", "host_key": "forged"}, made["id"], path=self.path)
        self.assertEqual(changed["host_key"], "nas.local ssh-ed25519 AAAA", "never typed in through an edit")
        moved = syncs.save_connection({"host": "nas2.local"}, made["id"], path=self.path)
        self.assertEqual(moved["host_key"], "", "another server is accepted again from scratch")

    def test_a_connection_a_sync_uses_stays(self):
        made = syncs.save_connection({"name": "NAS", "host": "nas.local"}, path=self.path)
        job = syncs.save_job({"name": "Music", "connection": made["id"], "target": "/m"}, path=self.path)
        with self.assertRaises(ValueError):
            syncs.delete_connection(made["id"], path=self.path)
        syncs.delete_job(job["id"], path=self.path)
        syncs.delete_connection(made["id"], path=self.path)
        self.assertEqual(syncs.load(self.path), {"connections": [], "jobs": []})

    def test_a_mirror_never_shares_its_folder(self):
        made = syncs.save_connection({"name": "NAS", "host": "nas.local"}, path=self.path)
        copy = syncs.save_job({"name": "Music", "connection": made["id"], "target": "/m"}, path=self.path)
        self.assertEqual(copy["mode"], "copy", "a sync removes nothing unless asked")
        for target in ("/m", "/m/Rock", "/"):
            with self.assertRaises(ValueError) as caught:
                syncs.save_job({"name": "Mirror", "connection": made["id"], "target": target, "mode": "mirror"},
                               path=self.path)
            self.assertEqual(str(caught.exception), "sync_mirror_shared", target)
        alone = syncs.save_job({"name": "Mirror", "connection": made["id"], "target": "/memes", "mode": "mirror"},
                               path=self.path)
        self.assertEqual(alone["mode"], "mirror")
        with self.assertRaises(ValueError):
            syncs.save_job({"target": "/memes/x"}, copy["id"], path=self.path)
        self.assertEqual(syncs.save_job({"name": "Music 2"}, copy["id"], path=self.path)["target"], "/m",
                         "a sync beside a mirror is still edited")

    def test_an_imported_mirror_on_a_shared_folder_only_copies(self):
        conn = {"id": "nas", "name": "NAS", "type": "smb", "host": "nas.local"}
        jobs = [{"id": "a", "name": "A", "connection": "nas", "target": "/m"},
                {"id": "b", "name": "B", "connection": "nas", "target": "/m/x", "mode": "mirror"}]
        with mock.patch.object(syncs, "save_all") as saved:
            syncs.import_block({"connections": [conn], "jobs": jobs})
        self.assertEqual([job["mode"] for job in saved.call_args[0][0]["jobs"]], ["copy", "copy"])


if __name__ == "__main__":
    unittest.main()
