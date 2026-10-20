"""Versioned, declarative schemas for hospital-authored privacy policies."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from backend.domains.privacy.data_use_points import (
    validate_data_use_point_registry,
)


class StrictPolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


PurposeScope = Literal[
    "patient", "episode", "recipient", "study", "channel", "item"
]
Validity = Literal[
    "until_withdrawn", "fixed_term", "per_episode"
]
NoRecordBehavior = Literal["deny", "allow", "ask"]
ConsentCapture = Literal[
    "WRITTEN",
    "E_SIGNATURE",
    "OTP",
    "DIGITAL_OPT_IN",
    "WITNESSED_VERBAL",
    "GUARDIAN_SIGNATURE",
    "ABDM_ARTIFACT",
]
class PurposeRule(StrictPolicyModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    display_name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    consent_required: bool
    legal_basis: str = Field(min_length=1, max_length=500)
    required_before: list[Literal["collect", "use", "disclose"]] = Field(
        default_factory=list
    )
    scope: PurposeScope = "patient"
    validity: Validity = "until_withdrawn"
    capture: list[ConsentCapture] = Field(default_factory=list)
    consenters: list[Literal["patient", "guardian", "representative"]] = Field(
        default_factory=lambda: ["patient"]
    )
    default_when_no_record: NoRecordBehavior = "deny"

    @model_validator(mode="after")
    def validate_consent_fields(self):
        if self.consent_required:
            if not self.required_before:
                raise ValueError(
                    f"purpose {self.code}: required_before must not be empty "
                    "when consent is required"
                )
            if not self.capture:
                raise ValueError(
                    f"purpose {self.code}: capture methods must be specified "
                    "when consent is required"
                )
            if not self.consenters:
                raise ValueError(
                    f"purpose {self.code}: at least one consenter is required"
                )
        if self.validity == "per_episode" and self.scope != "episode":
            raise ValueError(
                f"purpose {self.code}: per_episode validity requires episode scope"
            )
        if self.validity == "fixed_term" and not self.consent_required:
            raise ValueError(
                f"purpose {self.code}: fixed_term validity requires consent"
            )
        return self


class ExceptionRule(StrictPolicyModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    covers: list[str] = Field(min_length=1)
    invoker_permission: str = Field(
        min_length=3, max_length=100, pattern=r"^[a-z][a-z0-9:_-]*$"
    )
    reason_required: bool = True
    review_hours: int = Field(ge=1, le=8760)


class PatientGroupRule(StrictPolicyModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]{1,63}$")
    status: Literal["configured", "deferred"]
    condition: dict[str, str | int | bool] | None = None
    consenter: Literal["patient", "guardian", "representative"] | None = None

    @model_validator(mode="after")
    def validate_group(self):
        if self.status == "configured" and (
            self.condition is None or self.consenter is None
        ):
            raise ValueError(
                f"group {self.code}: configured groups need condition and consenter"
            )
        if self.status == "deferred" and self.condition is not None:
            raise ValueError(
                f"group {self.code}: deferred groups must not define a condition"
            )
        if self.condition is not None:
            keys = set(self.condition)
            if keys == {"age_lt"}:
                age_limit = self.condition["age_lt"]
                if type(age_limit) is not int or age_limit < 1:
                    raise ValueError(
                        f"group {self.code}: age_lt must be a positive integer"
                    )
            else:
                raise ValueError(
                    f"group {self.code}: unsupported condition; only age_lt is supported"
                )
        return self


class WithdrawalRule(StrictPolicyModel):
    effective: Literal["immediate"]
    cancel_queued: bool
    cancel_in_flight: bool
    notify_prior_recipients: Literal["never", "contract_required", "always"]
    statutory_retention_continues: bool
    patient_notice: str = Field(min_length=1, max_length=2000)
    clinician_alert_purposes: list[str] = Field(default_factory=list)
    does_not_affect_treatment: bool


class ConsentPolicyV1(StrictPolicyModel):
    schema_version: Literal[1]
    policy_code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    purposes: list[PurposeRule] = Field(min_length=1, max_length=100)
    data_use_points: dict[str, str] = Field(default_factory=dict, max_length=200)
    exceptions: list[ExceptionRule] = Field(default_factory=list, max_length=50)
    never_override: list[str] = Field(default_factory=list, max_length=100)
    groups: list[PatientGroupRule] = Field(default_factory=list, max_length=100)
    withdrawal: WithdrawalRule

    @model_validator(mode="after")
    def validate_references_and_conflicts(self):
        purpose_codes = [purpose.code for purpose in self.purposes]
        if len(purpose_codes) != len(set(purpose_codes)):
            raise ValueError("purpose codes must be unique")
        purpose_set = set(purpose_codes)

        if len(self.exceptions) != len({item.code for item in self.exceptions}):
            raise ValueError("exception codes must be unique")
        for exception in self.exceptions:
            unknown = set(exception.covers) - purpose_set
            if unknown:
                raise ValueError(
                    f"exception {exception.code} references unknown purposes: "
                    f"{', '.join(sorted(unknown))}"
                )
        unknown_never_override = set(self.never_override) - purpose_set
        if unknown_never_override:
            raise ValueError(
                "never_override references unknown purposes: "
                + ", ".join(sorted(unknown_never_override))
            )
        overlap = set(self.never_override).intersection(
            code for item in self.exceptions for code in item.covers
        )
        if overlap:
            raise ValueError(
                "never_override purposes cannot be covered by exceptions: "
                + ", ".join(sorted(overlap))
            )
        unknown_mappings = set(self.data_use_points.values()) - purpose_set
        if unknown_mappings:
            raise ValueError(
                "data_use_points reference unknown purposes: "
                + ", ".join(sorted(unknown_mappings))
            )
        unsupported_points = validate_data_use_point_registry(self.data_use_points)
        if unsupported_points:
            raise ValueError(
                "unsupported data-use points: "
                + ", ".join(unsupported_points)
            )
        if len(self.groups) != len({item.code for item in self.groups}):
            raise ValueError("patient group codes must be unique")
        for purpose in self.purposes:
            if purpose.consent_required and (
                purpose.code not in self.never_override
                and purpose.default_when_no_record not in {"deny", "allow", "ask"}
            ):
                raise ValueError(
                    f"purpose {purpose.code}: a valid no-record behavior is required"
                )
        return self


class RetentionCategoryRule(StrictPolicyModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    system_of_record: str = Field(min_length=1, max_length=120)
    locations: list[str] = Field(min_length=1, max_length=100)
    retention_days: int = Field(gt=0, le=36500)
    clock_start: Literal[
        "creation", "last_encounter", "discharge", "last_activity", "invoice",
        "contract_end", "date_of_death", "age_of_majority",
    ]
    action: Literal["retain", "archive", "delete", "anonymise"]
    legal_hold_triggers: list[str] = Field(default_factory=list, max_length=100)
    backup_treatment: str = Field(min_length=1, max_length=1000)
    export_treatment: str = Field(min_length=1, max_length=1000)


class RetentionPolicyV1(StrictPolicyModel):
    schema_version: Literal[1]
    policy_code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,63}$")
    categories: list[RetentionCategoryRule] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_category_codes(self):
        codes = [category.code for category in self.categories]
        if len(codes) != len(set(codes)):
            raise ValueError("retention category codes must be unique")
        return self


def validate_policy_document(policy_type: str, document: dict):
    """Parse and validate a versioned policy without applying its behavior."""
    if policy_type == "CONSENT":
        return ConsentPolicyV1.model_validate(document)
    if policy_type == "RETENTION":
        return RetentionPolicyV1.model_validate(document)
    raise ValueError("policy_type must be CONSENT or RETENTION")


def questionnaire_answers_to_policy_document(
    policy_type: str, answers: dict
) -> dict:
    """Wrap normalized questionnaire section answers in the current schema version."""
    if not isinstance(answers, dict):
        raise ValueError("questionnaire answers must be a JSON object")
    if "schema_version" in answers:
        raise ValueError("questionnaire answers must not provide schema_version")
    document = {"schema_version": 1, **answers}
    validate_policy_document(policy_type, document)
    return document
