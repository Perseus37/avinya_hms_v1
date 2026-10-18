from backend.core.database import Base
from backend.core.seed import pwd_context as seed_pwd_context
from backend.domains.billing.models import Invoice
from backend.domains.facility.models import Bed
from backend.domains.identity.auth_router import pwd_context as auth_pwd_context
from backend.domains.identity.models import User
from backend.domains.identity.passwords import pwd_context as identity_pwd_context
from backend.domains.patients.models import Patient
from backend.domains.patients.schemas import PatientResponse


def test_domain_models_share_tenant_metadata():
    for model in (Bed, Invoice, Patient, User):
        assert model.__table__.metadata is Base.metadata
    assert PatientResponse.__module__ == "backend.domains.patients.schemas"


def test_identity_password_hashing_uses_one_shared_context():
    assert seed_pwd_context is identity_pwd_context
    assert auth_pwd_context is identity_pwd_context
