"""The verification harness: code checks before a tool call (gate A, `verify.plan`) and before an answer is
delivered (gate C, `verify.answer`), with every number traced to the register, field and period it came from
(gate B, `verify.provenance`). See docs/verification_harness_plan.md.

Each check returns Findings; each gate emits one `verification` event with what failed and what it did about it
(deliver, repaired, fallback, warn), so the trace shows why an answer passed or was changed.
"""
from dataclasses import asdict, dataclass

STAGES = ('plan', 'answer')
ACTIONS = ('deliver', 'repaired', 'fallback', 'warn')


@dataclass
class Finding:
    name: str             # the check, e.g. 'unjustified_table', 'misattributed'
    ok: bool
    severity: str = 'warn'  # 'block' | 'repair' | 'warn'
    detail: str = ''      # for the trace, in English
    feedback: str = ''    # for an LLM rewrite, when one is allowed

    def public(self):
        return {k: v for k, v in asdict(self).items() if v not in ('', None)}


def failed(findings):
    return [f for f in findings if not f.ok]


def emit(ctx, stage, findings, action, checks=None):
    """One `verification` event per gate pass. Quiet when every check passed and nothing was done."""
    bad = failed(findings)
    if not bad and action == 'deliver':
        ctx.metrics.setdefault('verification', {})[stage] = 'passed'
        return
    ctx.metrics.setdefault('verification', {})[stage] = action
    ctx.emit('verification', stage=stage, action=action, checks=checks or sorted({f.name for f in findings}),
             findings=[f.public() for f in bad], position=ctx.run['position'])
