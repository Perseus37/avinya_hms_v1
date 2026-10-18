"""Password hashing configuration shared by identity and tenant bootstrap."""

from passlib.context import CryptContext

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
