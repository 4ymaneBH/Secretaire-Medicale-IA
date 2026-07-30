"""Realtime API system prompt and tool definitions for the AI medical secretary.

Public API
----------
build_system_prompt(language, appointment_id=None, purpose=None, memory_context=None) -> str
get_tools(language) -> list[dict]
"""

_LANGUAGE_NAMES = {
    "fr": "français",
    "en": "English",
    "ar": "arabe (Arabic)",
    "it": "italiano",
    "es": "español",
}

_BASE_PROMPT = {
    "fr": (
        "Tu es la secrétaire médicale virtuelle d'un cabinet médical. Tu réponds "
        "aux appels des patients avec professionnalisme et chaleur. Tu peux : "
        "prendre, modifier ou annuler un rendez-vous ; répondre aux questions "
        "générales sur le cabinet ; détecter une urgence médicale et transférer "
        "immédiatement l'appel à un humain si nécessaire. Reste concise, parle "
        "naturellement, et n'invente jamais d'information médicale. Si le patient "
        "change de langue en cours d'appel, utilise l'outil switch_language et "
        "poursuis la conversation dans cette nouvelle langue. Si le patient fait "
        "référence à un appel ou une visite précédente, utilise l'outil "
        "recall_context pour retrouver ce contexte avant de répondre."
    ),
    "en": (
        "You are the virtual medical secretary for a doctor's office. You answer "
        "patient calls professionally and warmly. You can: book, modify, or "
        "cancel an appointment; answer general questions about the practice; "
        "detect a medical emergency and immediately transfer the call to a human "
        "if needed. Stay concise, speak naturally, and never invent medical "
        "information. If the patient switches language mid-call, use the "
        "switch_language tool and continue the conversation in that language. If "
        "the patient references a previous call or visit, use the recall_context "
        "tool to retrieve that context before responding."
    ),
}

_DEFAULT_LANGUAGE_INSTRUCTION = (
    "Conduct the entire conversation fluently in {language_name}, adapting tone "
    "and phrasing naturally to that language."
)

# The five automatic outbound call types named in the CDC.
_OUTBOUND_PURPOSE_INSTRUCTIONS = {
    "confirmation": (
        "This is an outbound call to confirm an upcoming appointment. Introduce "
        "yourself, state the appointment date/time, and ask the patient to confirm "
        "or reschedule."
    ),
    "reminder": (
        "This is an outbound reminder call for an upcoming appointment. Introduce "
        "yourself and remind the patient of the date/time."
    ),
    "satisfaction_survey": (
        "This is an outbound satisfaction survey call following a recent visit. "
        "Ask briefly how the patient's experience was and thank them for their time."
    ),
    "exam_reminder": (
        "This is an outbound call reminding the patient to complete a prescribed "
        "exam or test. Explain what is needed and answer any questions."
    ),
    "admin_followup": (
        "This is an outbound administrative follow-up call (missing documents, "
        "billing, or other administrative matter). State the reason clearly."
    ),
}


def build_system_prompt(
    language: str,
    appointment_id: str | None = None,
    purpose: str | None = None,
    memory_context: str | None = None,
) -> str:
    """Build the system prompt injected once at Realtime session start."""
    base = _BASE_PROMPT.get(language, _BASE_PROMPT["en"])
    language_name = _LANGUAGE_NAMES.get(language, language)
    instruction = _DEFAULT_LANGUAGE_INSTRUCTION.format(language_name=language_name)

    parts = [base, instruction]

    if purpose and purpose in _OUTBOUND_PURPOSE_INSTRUCTIONS:
        parts.append(_OUTBOUND_PURPOSE_INSTRUCTIONS[purpose])
    if appointment_id:
        parts.append(f"This call relates to appointment reference {appointment_id}.")
    if memory_context:
        parts.append(f"Context from this patient's previous calls:\n{memory_context}")

    return " ".join(parts)


def get_tools(language: str) -> list[dict]:
    """Return the OpenAI Realtime API function-call tool definitions."""
    return [
        {
            "type": "function",
            "name": "get_available_slots",
            "description": "Look up real available appointment slots to offer the patient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {
                        "type": "integer",
                        "description": "How many days ahead to search (default 7)",
                    }
                },
            },
        },
        {
            "type": "function",
            "name": "propose_appointment_slot",
            "description": "Propose one of the available appointment slots to the patient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "slot": {
                        "type": "string",
                        "description": "ISO 8601 datetime of the proposed slot",
                    }
                },
                "required": ["slot"],
            },
        },
        {
            "type": "function",
            "name": "confirm_appointment",
            "description": "Confirm the previously proposed appointment slot with the patient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {
                        "type": "string",
                        "description": "Patient's stated reason for the visit",
                    }
                },
            },
        },
        {
            "type": "function",
            "name": "transfer_to_human",
            "description": (
                "Transfer this call to a human secretary or, for medical "
                "emergencies, immediate escalation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string"},
                    "urgent": {
                        "type": "boolean",
                        "description": "True if this is a medical emergency requiring immediate transfer",
                    },
                },
                "required": ["reason"],
            },
        },
        {
            "type": "function",
            "name": "switch_language",
            "description": "Switch the conversation language when the patient speaks in a different language.",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["fr", "en", "ar", "it", "es"],
                    }
                },
                "required": ["language"],
            },
        },
        {
            "type": "function",
            "name": "recall_context",
            "description": (
                "Recall relevant context from this patient's previous calls, e.g. when "
                "they reference a past visit or a previous conversation."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "What the patient just referenced",
                    }
                },
                "required": ["query"],
            },
        },
        {
            "type": "function",
            "name": "end_call",
            "description": "End the call once the conversation is complete.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
        },
    ]
