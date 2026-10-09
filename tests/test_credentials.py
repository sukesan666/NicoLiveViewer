import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from nicoview.credentials import CredentialStore, protect

@unittest.skipUnless(os.name=='nt','Windows DPAPI integration')
class CredentialTests(unittest.TestCase):
    def test_encrypted_roundtrip_and_delete(self):
        try:protect(b'nonsecret-capability-probe')
        except OSError:self.skipTest('DPAPI CurrentUser unavailable under sandbox account (no loaded user profile)')
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'credentials.dat';store=CredentialStore(path)
            self.assertIsNone(store.load())
            store.save('test@example.invalid','sample-password-日本語')
            raw=path.read_bytes()
            self.assertNotIn(b'test@example.invalid',raw)
            self.assertNotIn(b'sample-password',raw)
            self.assertEqual(CredentialStore(path).load(),{'email':'test@example.invalid','password':'sample-password-日本語'})
            store.delete();self.assertFalse(path.exists());self.assertIsNone(store.load())

    def test_storage_roundtrip_with_crypto_boundary(self):
        with tempfile.TemporaryDirectory() as d, \
             patch('nicoview.credentials.protect',side_effect=lambda value:b'encrypted:'+value[::-1]), \
             patch('nicoview.credentials.unprotect',side_effect=lambda value:value[10:][::-1]):
            store=CredentialStore(Path(d)/'credentials.dat')
            store.save('mail','sample');self.assertEqual(store.load(),{'email':'mail','password':'sample'})
            store.delete();self.assertIsNone(store.load())

    def test_corrupt_blob_fails_without_plaintext_fallback(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'credentials.dat';path.write_bytes(b'{"password":"plaintext"}')
            with self.assertRaises(ValueError):CredentialStore(path).load()

    def test_encryption_failure_never_writes_plaintext(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'credentials.dat';store=CredentialStore(path)
            with patch('nicoview.credentials.protect',side_effect=OSError('DPAPI failed')):
                with self.assertRaises(OSError):store.save('user','secret')
            self.assertFalse(path.exists());self.assertEqual(list(Path(d).iterdir()),[])
