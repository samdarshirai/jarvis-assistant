import hashlib
import hmac
import secrets


def hash_token(token: str) -> str:
    # 256 random bits, so a fast hash is enough; a slow KDF would only add latency to every connect
    return hashlib.sha256(token.encode()).hexdigest()


class Devices:
    def __init__(self, pool):
        self.pool = pool

    def create(self) -> tuple[int, str]:
        token = secrets.token_urlsafe(32)
        with self.pool.connection() as c:
            did = c.execute("INSERT INTO devices (token_hash) VALUES (%s) RETURNING id", (hash_token(token),)).fetchone()[0]
        return did, token

    def verify(self, token: str) -> int | None:
        if not token:
            return None
        h = hash_token(token)
        with self.pool.connection() as c:
            rows = c.execute("SELECT id, token_hash FROM devices").fetchall()
            found = None
            for did, stored in rows:  # compare every row so timing does not reveal a prefix match
                if hmac.compare_digest(stored, h):
                    found = did
            if found is not None:
                c.execute("UPDATE devices SET last_seen = now() WHERE id = %s", (found,))
        return found

    def set_fcm(self, device_id: int, fcm_token: str) -> None:
        with self.pool.connection() as c:
            c.execute("UPDATE devices SET fcm_token = %s WHERE id = %s", (fcm_token, device_id))

    def fcm_tokens(self) -> list[str]:
        with self.pool.connection() as c:
            return [r[0] for r in c.execute("SELECT fcm_token FROM devices WHERE fcm_token IS NOT NULL").fetchall()]

    def revoke(self, device_id: int) -> bool:
        with self.pool.connection() as c:
            return c.execute("DELETE FROM devices WHERE id = %s", (device_id,)).rowcount > 0
