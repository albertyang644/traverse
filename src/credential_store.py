"""Secure credential storage backed by KDE Wallet."""

import json
import base64


class CredentialStore:
    """Store SMB credentials in the desktop's encrypted KDE Wallet."""

    _WALLET = "kdewallet"
    _FOLDER = "Traverse"

    @staticmethod
    def _entry(server: str, share: str) -> str:
        return f"smb://{server.rstrip('.').casefold()}/{share.casefold()}"

    @classmethod
    def available(cls) -> bool:
        try:
            import dbus
            bus = dbus.SessionBus()
            return bool(bus.name_has_owner("org.kde.kwalletd5"))
        except (ImportError, Exception):
            return False

    @staticmethod
    def _interface():
        import dbus
        bus = dbus.SessionBus()
        proxy = bus.get_object("org.kde.kwalletd5", "/modules/kwalletd5")
        return dbus.Interface(proxy, "org.kde.KWallet")

    def _open_wallet(self) -> int | None:
        try:
            import dbus
            return int(self._interface().open(self._WALLET, dbus.Int64(0), "Traverse"))
        except Exception:
            return None

    def _ensure_folder(self) -> bool:
        handle = self._open_wallet()
        if handle is None or handle < 0:
            return False
        try:
            import dbus
            interface = self._interface()
            if interface.hasFolder(dbus.Int32(handle), self._FOLDER, "Traverse"):
                return True
            return bool(interface.createFolder(dbus.Int32(handle), self._FOLDER, "Traverse"))
        except Exception:
            return False

    def get(self, server: str, share: str) -> dict | None:
        if not self.available():
            return None
        try:
            import dbus
            handle = self._open_wallet()
            if handle is None or handle < 0:
                return None
            stored = str(self._interface().readPassword(
                dbus.Int32(handle), self._FOLDER, self._entry(server, share), "Traverse"
            ))
            if not stored.startswith("traverse-v1:"):
                return None
            raw = base64.urlsafe_b64decode(stored[len("traverse-v1:"):].encode()).decode()
            data = json.loads(raw)
            if all(isinstance(data.get(key), str) for key in ("username", "domain", "password")):
                return data
        except (Exception, json.JSONDecodeError, ValueError, UnicodeDecodeError):
            pass
        return None

    def save(self, server: str, share: str, username: str, domain: str, password: str) -> bool:
        if not self.available() or not self._ensure_folder():
            return False
        raw = json.dumps({"username": username, "domain": domain, "password": password}).encode()
        payload = "traverse-v1:" + base64.urlsafe_b64encode(raw).decode()
        try:
            import dbus
            handle = self._open_wallet()
            if handle is None or handle < 0:
                return False
            result = self._interface().writePassword(
                dbus.Int32(handle), self._FOLDER, self._entry(server, share), payload, "Traverse"
            )
            return int(result) == 0
        except Exception:
            return False

    def delete(self, server: str, share: str) -> bool:
        """Remove one saved SMB login from KDE Wallet."""
        if not self.available():
            return False
        handle = self._open_wallet()
        if handle is None or handle < 0:
            return False
        try:
            import dbus
            result = self._interface().removeEntry(
                dbus.Int32(handle), self._FOLDER, self._entry(server, share), "Traverse"
            )
            return int(result) == 0
        except Exception:
            return False
