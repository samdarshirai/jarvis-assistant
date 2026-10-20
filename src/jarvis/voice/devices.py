import hashlib
import hmac
import secrets
from typing import NamedTuple


def hash_token(token: str) -> str:
    # 256 random bits, so a fast hash is enough; a slow KDF would only add latency to every connect
    return hashlib.sha256(token.encode()).hexdigest()


class DeviceAuth(NamedTuple):
    device_id: int
    user_id: int


class Devices:
    def __init__(self, pool):
        self.pool = pool

    def create(self, user_id: int) -> tuple[int, str]:
        token = secrets.token_urlsafe(32)
        with self.pool.connection() as c:
            did = c.execute("INSERT INTO devices (user_id, token_hash) VALUES (%s, %s) RETURNING id",
                            (user_id, hash_token(token))).fetchone()[0]
        return did, token

    def verify(self, token: str) -> DeviceAuth | None:
        """The device and its user; None for an unknown token or a disabled user."""
        if not token:
            return None
        h = hash_token(token)
        with self.pool.connection() as c:
            rows = c.execute("SELECT d.id, d.user_id, d.token_hash, u.disabled FROM devices d"
                             " JOIN users u ON u.id = d.user_id").fetchall()
            found = None
            for did, uid, stored, disabled in rows:  # compare every row so timing does not reveal a prefix match
                if hmac.compare_digest(stored, h) and not disabled:
                    found = DeviceAuth(did, uid)
            if found is not None:
                c.execute("UPDATE devices SET last_seen = now() WHERE id = %s", (found.device_id,))
        return found

    def set_fcm(self, device_id: int, fcm_token: str) -> None:
        with self.pool.connection() as c:
            c.execute("UPDATE devices SET fcm_token = %s WHERE id = %s", (fcm_token, device_id))

    def fcm_tokens(self, user_id: int) -> list[str]:
        with self.pool.connection() as c:
            return [r[0] for r in c.execute("SELECT fcm_token FROM devices WHERE user_id = %s AND fcm_token IS NOT NULL",
                                            (user_id,)).fetchall()]

    def revoke(self, device_id: int) -> bool:
        with self.pool.connection() as c:
            return c.execute("DELETE FROM devices WHERE id = %s", (device_id,)).rowcount > 0

    def revoke_user(self, user_id: int) -> int:
        with self.pool.connection() as c:
            return c.execute("DELETE FROM devices WHERE user_id = %s", (user_id,)).rowcount
