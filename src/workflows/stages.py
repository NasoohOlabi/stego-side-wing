"""Single source of truth for the context-stage names and their artifact steps.

The sender and the receiver must agree on *which* stages build the shared context and on
*which* step directory each stage reads and writes. That agreement used to be re-spelled by
hand at every call site as string literals, so a rename could land in one place and be missed
in another -- exactly the kind of drift the sender/receiver contract cannot absorb.

Stage names (``data_load``/``research``/``gen_angles``) are the internal identifiers used in
progress events, reports, and summary dicts. Step names (``filter-url-unresolved`` etc.) are
the on-disk artifact steps registered in :data:`infrastructure.config.STEPS`.
"""

from types import MappingProxyType
from typing import Final

DATA_LOAD_STEP: Final = "filter-url-unresolved"
RESEARCH_STEP: Final = "filter-researched"
ANGLES_STEP: Final = "angles-step"
FINAL_STEP: Final = "final-step"

#: Ordered ``stage name -> artifact step`` map for the three context-building stages.
#: Order is significant: it is the order the stages run in on both sides.
CONTEXT_STAGE_STEPS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "data_load": DATA_LOAD_STEP,
        "research": RESEARCH_STEP,
        "gen_angles": ANGLES_STEP,
    }
)

#: The context stages in run order, without their steps.
CONTEXT_STAGES: Final[tuple[str, ...]] = tuple(CONTEXT_STAGE_STEPS)

#: Every step a prep run materializes, in pipeline order (context stages plus the final step).
PREP_RUN_STEPS: Final[tuple[str, ...]] = (*CONTEXT_STAGE_STEPS.values(), FINAL_STEP)
