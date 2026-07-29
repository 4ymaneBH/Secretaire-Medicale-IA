# Secretaire-Medicale-IA — Module 3 : Téléphonie & Assistant vocal IA

Backend FastAPI pour la secrétaire médicale virtuelle : réception et émission
d'appels téléphoniques, pipeline vocal temps réel (STT → dialogue → TTS),
prise de rendez-vous, et détection d'urgence avec transfert humain.

## Stack

- **FastAPI** + SQLAlchemy 2.0 (async) + Alembic
- **PostgreSQL 15 + pgvector** (base de données + mémoire de recherche pour le RAG)
- **Redis** (rate limiting, cache TTS)
- **Twilio** (téléphonie, Media Streams)
- **OpenAI Realtime API** (dialogue vocal temps réel, function calling)
- **OpenAI Whisper** (STT) / **ElevenLabs** (TTS streaming)

## Setup

```bash
cp .env.example .env   # renseigner les clés Twilio / OpenAI / ElevenLabs
docker compose up -d
alembic upgrade head
uvicorn app.main:app --reload
```

## Architecture

```
app/api/v1/voice.py            Webhooks Twilio (inbound, outbound, status)
app/api/v1/voice_functions/    Handlers par route
app/realtime/                  WebSocket routes + connection manager
app/services/voice/            Pont OpenAI Realtime <-> Twilio Media Stream
app/services/integrations/     Clients Twilio, Whisper (STT), ElevenLabs (TTS)
app/services/ai/               RAG (pgvector) pour les questions générales
app/models/                    CallSession, Transcript, Outcome, Appointment, Patient
```

Le flux principal : un appel Twilio ouvre un Media Stream WebSocket vers
`/api/v1/ws/media-stream`, qui relie l'audio à l'API Realtime d'OpenAI. Le
modèle gère la conversation et déclenche des function calls (proposition de
créneau, confirmation de rendez-vous, transfert humain, fin d'appel).

## Périmètre couvert

- Décroché automatique des appels entrants / appels sortants (confirmation,
  rappel de rendez-vous)
- Pipeline vocal complet (STT → dialogue → TTS) avec gestion de l'interruption
  (barge-in)
- Détection d'urgence → transfert humain (`transfer_to_human` tool)
- Proposition et confirmation de créneaux de rendez-vous
- Base de connaissances (RAG) pour les questions générales sur le cabinet

## Ce qui reste à construire

- **Multilingue** : la configuration supporte FR/EN/AR/IT/ES
  (`SUPPORTED_LANGUAGES`), mais seuls le prompt et les voix FR/EN sont
  validés ; il faut fournir des voice IDs ElevenLabs pour AR/IT/ES et valider
  la qualité de conversation dans ces langues.
- **Apprentissage à partir des corrections humaines** : pas encore implémenté.
- **Reprise d'appel en direct par une secrétaire** (bascule live) : pas encore
  implémentée — dépend du Module 1 (comptes/rôles) pour l'authentification.
- **Tests de qualité de conversation** (jeux de scénarios) : à écrire.
- **Intégration Module 1** : ce module suppose une API/BDD centrale
  (comptes, rôles) exposée par le Module 1 ; aucune logique d'auth n'est
  dupliquée ici.
