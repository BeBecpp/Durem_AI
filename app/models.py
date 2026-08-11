from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

DecisionType = Literal["ALLOWED", "DENIED", "APPROVAL_REQUIRED", "NOT_FOUND"]
AnswerType = Literal["DECISION", "GUIDANCE", "ROUTING", "POLICY", "CHAT", "NOT_FOUND"]
AssistantMode = Literal["auto", "chat", "can_i", "how_to", "who", "policy"]
RouteType = Literal["policy", "chat"]
RouteMethod = Literal["explicit", "deterministic", "followup", "llm_classifier", "fallback", "memory"]


class UserPublic(BaseModel):
    id: int
    username: str
    name: str
    department: str = ""
    role: str = ""
    is_admin: bool = False


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=1, max_length=256)


class ApiLoginRequest(LoginRequest):
    device_name: str = Field(default="DUREM App", max_length=120)


class ApiTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: str
    user: UserPublic
    api_version: str = "v1"


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=5000)
    mode: AssistantMode = "auto"
    conversation_id: str | None = None


class RoutePreviewRequest(BaseModel):
    question: str = Field(min_length=2, max_length=5000)
    mode: AssistantMode = "auto"
    conversation_id: str | None = None


class RoutePreviewResponse(BaseModel):
    route: RouteType
    requested_mode: AssistantMode
    route_reason: str
    route_confidence: float = Field(ge=0.0, le=1.0)
    route_method: RouteMethod
    safety_override: bool = False
    classifier_invoked: bool = False
    signals: list[str] = Field(default_factory=list)


class SourceCard(BaseModel):
    id: str
    kind: Literal["rule", "document", "responsibility"]
    title: str
    section: str = ""
    snippet: str = ""
    score: float = 0.0
    document_id: str = ""


class AssistantResponse(BaseModel):
    answer_type: AnswerType
    decision: DecisionType = "NOT_FOUND"
    headline: str
    answer: str
    approver: str = ""
    next_steps: list[str] = Field(default_factory=list)
    sources: list[SourceCard] = Field(default_factory=list)
    confidence: Literal["confirmed", "partial", "unknown"] = "unknown"
    model: str
    company: str
    latency_ms: int
    conversation_id: str
    method: Literal["llm", "chat_llm", "mock", "safety_fallback", "rule_engine", "memory"] = "llm"
    route: RouteType = "policy"
    requested_mode: AssistantMode = "auto"
    route_reason: str = ""
    route_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    route_method: RouteMethod = "deterministic"
    safety_override: bool = False
    classifier_invoked: bool = False
    memory_used: bool = False


class RuleInput(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    title: str = Field(min_length=1, max_length=220)
    text: str = Field(min_length=2, max_length=6000)
    category: str = Field(default="general", max_length=80)
    keywords: str = Field(default="", max_length=500)
    decision_hint: DecisionType | Literal["AUTO"] = "AUTO"
    approver: str = Field(default="", max_length=220)
    role_scope: str = Field(default="", max_length=500)
    department_scope: str = Field(default="", max_length=500)
    priority: int = Field(default=100, ge=0, le=1000)
    metric: Literal["", "percent", "mnt", "number"] = ""
    min_value: float | None = None
    max_value: float | None = None
    min_inclusive: bool = True
    max_inclusive: bool = True
    source_document_id: str = Field(default="", max_length=100)
    source_section: str = Field(default="", max_length=300)
    active: bool = True

    @model_validator(mode="after")
    def validate_rule(self):
        if self.min_value is not None and self.max_value is not None and self.min_value > self.max_value:
            raise ValueError("Min value нь max value-аас их байж болохгүй.")
        if self.metric and self.min_value is None and self.max_value is None:
            raise ValueError("Deterministic metric rule-д min эсвэл max value шаардлагатай.")
        if self.decision_hint == "APPROVAL_REQUIRED" and not self.approver.strip():
            raise ValueError("APPROVAL_REQUIRED дүрэмд approver заавал оруулна.")
        return self


class DepartmentInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    active: bool = True


class RoleInput(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=1000)
    is_admin: bool = False
    active: bool = True


class UserInput(BaseModel):
    username: str = Field(min_length=2, max_length=120, pattern=r"^[\w.@+-]+$")
    name: str = Field(min_length=1, max_length=180)
    password: str = Field(default="", max_length=256)
    department_id: int | None = None
    role_id: int | None = None
    active: bool = True


class ResponsibilityInput(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9_.-]+$")
    topic: str = Field(min_length=1, max_length=220)
    keywords: str = Field(min_length=1, max_length=600)
    department_id: int | None = None
    user_id: int | None = None
    role_id: int | None = None
    instructions: str = Field(default="", max_length=2000)
    active: bool = True


class DocumentStatusInput(BaseModel):
    status: Literal["active", "archived"]


class SettingInput(BaseModel):
    company_name: str = Field(min_length=1, max_length=200)
    llm_model: str = Field(min_length=1, max_length=200)
    embedding_model: str = Field(min_length=1, max_length=200)
    embeddings_enabled: bool = True
    general_chat_enabled: bool = True
    auto_routing_enabled: bool = True
    hybrid_router_enabled: bool = True
    personal_memory_enabled: bool = True
    chat_history_messages: int = Field(default=16, ge=4, le=40)
    store_raw_chat_questions: bool = False
    api_access_enabled: bool = True
    api_token_ttl_days: int = Field(default=30, ge=1, le=365)


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: bool
    llm_reachable: bool
    llm_model: str
    embedding_model: str
    embeddings_enabled: bool
    company: str
    version: str


class PasswordChangeInput(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password: str = Field(min_length=1, max_length=256)


class FeedbackInput(BaseModel):
    conversation_id: str = Field(min_length=1, max_length=80)
    assistant_message_id: str = Field(default="", max_length=80)
    rating: Literal["up", "down"]
    note: str = Field(default="", max_length=1000)


class BackupInput(BaseModel):
    passphrase: str = Field(min_length=12, max_length=256)
