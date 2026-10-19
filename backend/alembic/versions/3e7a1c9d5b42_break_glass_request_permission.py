"""grant a dedicated break-glass request permission to clinical roles"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "3e7a1c9d5b42"
down_revision: Union[str, None] = "8a4f2d7c1e69"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            INSERT INTO permissions (
                code, description, module, created_at, updated_at
            )
            SELECT :code, :description, :module, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            WHERE NOT EXISTS (
                SELECT 1 FROM permissions WHERE code = :code
            )
            """
        ),
        {
            "code": "break_glass:request",
            "description": "Request audited emergency patient access",
            "module": "Privacy",
        },
    )
    connection.execute(
        sa.text(
            """
            INSERT INTO role_permissions (role_id, permission_id)
            SELECT roles.id, permissions.id
            FROM roles
            JOIN permissions ON permissions.code = :permission_code
            WHERE roles.code IN ('ROLE_ADMIN', 'ROLE_DOCTOR', 'ROLE_NURSE')
              AND NOT EXISTS (
                  SELECT 1
                  FROM role_permissions
                  WHERE role_permissions.role_id = roles.id
                    AND role_permissions.permission_id = permissions.id
              )
            """
        ),
        {"permission_code": "break_glass:request"},
    )


def downgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            DELETE FROM role_permissions
            WHERE permission_id IN (
                SELECT id FROM permissions WHERE code = :permission_code
            )
            """
        ),
        {"permission_code": "break_glass:request"},
    )
    connection.execute(
        sa.text("DELETE FROM permissions WHERE code = :permission_code"),
        {"permission_code": "break_glass:request"},
    )
