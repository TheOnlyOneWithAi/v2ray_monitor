from cryptography.fernet import Fernet
from .config import get_settings

class SecretBox:
    def __init__(self) -> None:
        key = get_settings().encryption_key.encode()
        if not key:
            raise RuntimeError('ENCRYPTION_KEY is required')
        self._fernet = Fernet(key)
    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()
    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode()).decode()
