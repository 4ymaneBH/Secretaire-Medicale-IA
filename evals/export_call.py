"""Export a recorded call into the JSON trace format used by evals.grade.

Patient and assistant text is excluded unless --include-text is explicitly set.
Keep exported files in evals/runs/ (gitignored), with appropriate access control.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from sqlalchemy import select

from app.db.session import get_session_context
from app.models.appointment import Appointment
from app.models.call_session import CallSession
from app.models.outcome import Outcome
from app.models.transcript import Transcript


async def export_call(call_sid: str, scenario_id: str, include_text: bool = False) -> dict:
    async with get_session_context() as db:
        call = (await db.execute(
            select(CallSession).where(CallSession.call_sid == call_sid)
        )).scalar_one_or_none()
        if call is None:
            raise ValueError(f"Call not found: {call_sid}")

        outcome = (await db.execute(
            select(Outcome).where(Outcome.call_session_id == call.id)
        )).scalar_one_or_none()
        turns = (await db.execute(
            select(Transcript)
            .where(Transcript.call_session_id == call.id)
            .order_by(Transcript.timestamp, Transcript.id)
        )).scalars().all()

        metadata = outcome.meta_data or {} if outcome else {}
        appointment_id = metadata.get("appointment_id")
        appointment = await db.get(Appointment, appointment_id) if appointment_id else None

        exported_turns = []
        for turn in turns:
            item = {
                "speaker": turn.speaker,
                "language": turn.language,
                "at": turn.timestamp.isoformat(),
            }
            if include_text:
                item["text"] = turn.text
            exported_turns.append(item)

        return {
            "scenario_id": scenario_id,
            "turns": exported_turns,
            "events": metadata.get("events", []),
            "final_state": {
                "appointment_confirmed": metadata.get("appointment_confirmed"),
                "appointment_id": appointment_id,
                "appointment_scheduled_at": (
                    appointment.scheduled_at.isoformat() if appointment else None
                ),
                "transfer_redirect_accepted": metadata.get("transfer_redirect_accepted"),
                "outcome_type": outcome.outcome_type.value if outcome else None,
            },
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a call for offline evaluation")
    parser.add_argument("--call-sid", required=True)
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--include-text", action="store_true",
                        help="Include patient and assistant text; handle as sensitive data")
    args = parser.parse_args()
    trace = asyncio.run(export_call(args.call_sid, args.scenario_id, args.include_text))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(trace, ensure_ascii=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
