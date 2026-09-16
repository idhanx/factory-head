"""SQLAlchemy models. Import from here so Alembic sees every table."""

from .actions import (
    Action,
    ActionEvent,
    ActionTrigger,
    MemoryEntry,
    PdcaState,
    RecoveredProfitEntry,
    RootCauseAnalysis,
)
from .base import Base
from .delivery import (
    Attachment,
    HoldStatus,
    Notification,
    NotificationChannel,
    NotificationStatus,
    PushSubscription,
    SyncHold,
)
from .events import (
    DataSource,
    DispatchRecord,
    Disposition,
    DowntimeEvent,
    DowntimeStatus,
    MaterialStaging,
    ProductionCount,
    QualityRecord,
    StageCompletion,
    StagingStatus,
)
from .masters import (
    AlertRecipient,
    CostFamily,
    CostRate,
    DefectCode,
    EscalationRoute,
    MarginBand,
    MarginBandValue,
    OeeTreatment,
    Operator,
    Part,
    ProductionTarget,
    RateSource,
    ReasonCode,
    Recoverability,
    RoutingOperation,
    Section,
    Shift,
    Station,
)
from .ops import (
    CoachBriefing,
    CopilotQuery,
    Escalation,
    EscalationChannel,
    EscalationStatus,
    ReadinessAssessment,
    TemplateUpload,
)
from .plugins import (
    AuditPack,
    ClauseStatus,
    ComplianceClause,
    KaizenIdea,
    KaizenStatus,
    OperatorSkill,
    SkillLevel,
    Standard,
    TrainingNeed,
)
from .spine import (
    JobOrder,
    JobOrderStatus,
    JobSession,
    Lot,
    LotGranularity,
    ShiftSession,
    ShiftSessionStatus,
)
from .tenancy import AuditLog, CostingMode, Plant, Role, User, UserPlantRole

__all__ = [n for n in dir() if not n.startswith("_")]
