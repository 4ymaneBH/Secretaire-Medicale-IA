"""Realtime API system prompt and tool definitions for the AI medical secretary.

Public API
----------
build_system_prompt(language, appointment_id=None) -> str
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
        "naturellement, et n'invente jamais d'information médicale."
    ),
    "en": (
        "You are the virtual medical secretary for a doctor's office. You answer "
        "patient calls professionally and warmly. You can: book, modify, or "
        "cancel an appointment; answer general questions about the practice; "
        "detect a medical emergency and immediately transfer the call to a human "
        "if needed. Stay concise, speak naturally, and never invent medical "
        "information."
    ),
}

_DEFAULT_LANGUAGE_INSTRUCTION = (
    "Conduct the entire conversation fluently in {language_name}, adapting tone "
    "and phrasing naturally to that language."
)


def build_system_prompt(language: str, appointment_id: str | None = None) -> str:
    """Build the system prompt injected once at Realtime session start."""
    base = _BASE_PROMPT.get(language, _BASE_PROMPT["en"])
    language_name = _LANGUAGE_NAMES.get(language, language)
    instruction = _DEFAULT_LANGUAGE_INSTRUCTION.format(language_name=language_name)

    parts = [base, instruction]
    if appointment_id:
        parts.append(
            f"This call relates to appointment reference {appointment_id}. "
            "Confirm the details naturally with the patient."
        )
    return " ".join(parts)


def get_tools(language: str) -> list[dict]:
    """Return the OpenAI Realtime API function-call tool definitions."""
    return [
        {
            "type": "function",
            "name": "propose_appointment_slot",
            "description": "Propose an available appointment slot to the patient.",
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
                "Flag this call for transfer to a human secretary or, for medical "
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
            "name": "end_call",
            "description": "End the call once the conversation is complete.",
            "parameters": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
            },
        },
    ]
