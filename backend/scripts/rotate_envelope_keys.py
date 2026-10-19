"""Re-encrypt tenant-registry secrets with the currently configured key."""

from sqlalchemy import select

from backend.control_plane.models import TenantRegistry
from backend.core.database import ControlSessionLocal
from backend.security.envelope import (
    decrypt_tenant_secret,
    encrypt_tenant_secret,
    needs_reencryption,
)


def rotate_tenant_secrets() -> int:
    rotated = 0
    with ControlSessionLocal() as db:
        tenants = db.scalars(select(TenantRegistry)).all()
        for tenant in tenants:
            for field in (
                "jwt_secret_key",
                "backup_encryption_key",
                "hl7_shared_secret_encrypted",
            ):
                stored_value = getattr(tenant, field)
                if stored_value is None:
                    continue
                if not needs_reencryption(stored_value):
                    continue
                if stored_value.startswith(("enc:v1:", "enc:v2:")):
                    plaintext = decrypt_tenant_secret(stored_value)
                else:
                    plaintext = stored_value
                encrypted_value = encrypt_tenant_secret(plaintext)
                if encrypted_value != stored_value:
                    setattr(tenant, field, encrypted_value)
                    rotated += 1
        db.commit()
    return rotated


def main() -> None:
    print(f"Re-encrypted {rotate_tenant_secrets()} tenant secret value(s).")


if __name__ == "__main__":
    main()
