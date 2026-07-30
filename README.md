<div align="center">

# 🩺📞 Secrétaire Médicale IA — Module 3

### Téléphonie & Assistant vocal IA

Réception et émission d'appels téléphoniques automatisées, pipeline vocal temps réel (STT → dialogue → TTS), prise de rendez-vous et détection d'urgence avec transfert humain — pour cabinets médicaux.

[![Python](https://img.shields.io/badge/Python-3.11-3776AB?style=for-the-badge&logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.110-009688?style=for-the-badge&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-15-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)](https://www.postgresql.org/)
[![Redis](https://img.shields.io/badge/Redis-7-DC382D?style=for-the-badge&logo=redis&logoColor=white)](https://redis.io/)
[![Docker](https://img.shields.io/badge/Docker-Ready-2496ED?style=for-the-badge&logo=docker&logoColor=white)](https://www.docker.com/)
[![Twilio](https://img.shields.io/badge/Twilio-Voice-F22F46?style=for-the-badge&logo=twilio&logoColor=white)](https://www.twilio.com/)
[![OpenAI](https://img.shields.io/badge/OpenAI-Realtime%20API-412991?style=for-the-badge&logo=openai&logoColor=white)](https://platform.openai.com/)

[![License](https://img.shields.io/badge/license-Proprietary-lightgrey?style=flat-square)](#)
[![Status](https://img.shields.io/badge/status-in%20development-yellow?style=flat-square)](#-ce-qui-reste-à-construire)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](#)

</div>

---

## 📖 Sommaire

- [✨ Aperçu](#-aperçu)
- [🧠 Fonctionnement](#-fonctionnement)
- [🛠️ Stack technique](#️-stack-technique)
- [🚀 Démarrage rapide](#-démarrage-rapide)
- [⚙️ Variables d'environnement](#️-variables-denvironnement)
- [🗂️ Architecture](#️-architecture)
- [✅ Périmètre couvert](#-périmètre-couvert)
- [🧭 Ce qui reste à construire](#-ce-qui-reste-à-construire)
- [🧪 Tests](#-tests)

---

## ✨ Aperçu

**Secrétaire Médicale IA** est le backend FastAPI du **Module 3** d'une plateforme de secrétariat médical assistée par IA. Il gère de bout en bout les appels téléphoniques d'un cabinet médical :

| 🎯 Capacité | 📋 Détail |
|---|---|
| 📥 **Appels entrants** | Décroché automatique, dialogue naturel, prise de RDV |
| 📤 **Appels sortants** | Confirmation de RDV, rappel de RDV, enquête de satisfaction, rappel d'examens, relance administrative |
| 🎙️ **Pipeline vocal temps réel** | STT → LLM → TTS avec gestion de l'interruption (*barge-in*) |
| 🚨 **Détection d'urgence** | Transfert automatique vers un humain (transfert réel, pas un simple indicateur) |
| 📅 **Rendez-vous** | Proposition et confirmation de créneaux, persistés en base |
| 🌍 **Changement de langue à la volée** | FR/EN/AR/IT/ES, bascule en cours d'appel via un outil dédié |
| 🧠 **Mémoire de conversation** | Résumé de chaque appel embeddé (pgvector), rappelé au patient d'un appel à l'autre |

---

## 🧠 Fonctionnement

```mermaid
sequenceDiagram
    participant Patient
    participant Twilio
    participant WS as WebSocket /ws/media-stream
    participant Realtime as OpenAI Realtime API
    participant Tools as Function Calls

    Patient->>Twilio: 📞 Appel entrant
    Twilio->>WS: Media Stream (audio)
    WS->>Realtime: Flux audio + contexte
    Realtime-->>WS: Réponse vocale (streaming)
    Realtime->>Tools: propose_appointment_slot / confirm_appointment / switch_language / transfer_to_human
    Tools-->>Realtime: Résultat
    WS-->>Twilio: Audio TTS
    Twilio-->>Patient: 🔊 Réponse vocale
```

Un appel Twilio ouvre un **Media Stream WebSocket** vers `/api/v1/ws/media-stream`, qui relie l'audio en direct à l'**API Realtime d'OpenAI**. Le modèle gère la conversation et déclenche des *function calls* : recherche de créneaux, proposition/confirmation de rendez-vous, changement de langue, rappel de contexte (mémoire), transfert humain, fin d'appel.

Les appels sortants sont initiés via `POST /api/v1/voice/outbound/trigger/{appointment_id}` (protégé par une clé interne partagée, destiné à être appelé par un planificateur ou par le Module 1), qui déclenche un appel Twilio réel vers le patient.

---

## 🛠️ Stack technique

<div align="center">

| Catégorie | Technologies |
|---|---|
| **Framework** | ![FastAPI](https://img.shields.io/badge/-FastAPI-009688?style=flat-square&logo=fastapi&logoColor=white) ![SQLAlchemy](https://img.shields.io/badge/-SQLAlchemy%202.0-D71F00?style=flat-square) ![Alembic](https://img.shields.io/badge/-Alembic-4B8BBE?style=flat-square) |
| **Base de données** | ![PostgreSQL](https://img.shields.io/badge/-PostgreSQL%2015-4169E1?style=flat-square&logo=postgresql&logoColor=white) ![pgvector](https://img.shields.io/badge/-pgvector-336791?style=flat-square) |
| **Cache / Rate limit** | ![Redis](https://img.shields.io/badge/-Redis-DC382D?style=flat-square&logo=redis&logoColor=white) |
| **Téléphonie** | ![Twilio](https://img.shields.io/badge/-Twilio%20Media%20Streams-F22F46?style=flat-square&logo=twilio&logoColor=white) |
| **IA conversationnelle** | ![OpenAI](https://img.shields.io/badge/-OpenAI%20Realtime%20API-412991?style=flat-square&logo=openai&logoColor=white) |
| **STT** | ![Whisper](https://img.shields.io/badge/-Whisper%20(gpt--4o--transcribe)-412991?style=flat-square&logo=openai&logoColor=white) |
| **TTS** | ![ElevenLabs](https://img.shields.io/badge/-ElevenLabs%20streaming-000000?style=flat-square) |
| **Infra** | ![Docker](https://img.shields.io/badge/-Docker%20Compose-2496ED?style=flat-square&logo=docker&logoColor=white) ![Uvicorn](https://img.shields.io/badge/-Uvicorn-499848?style=flat-square) |
| **Observabilité** | ![structlog](https://img.shields.io/badge/-structlog-informational?style=flat-square) JSON logging |
| **Tests** | ![Pytest](https://img.shields.io/badge/-Pytest-0A9EDC?style=flat-square&logo=pytest&logoColor=white) |

</div>

---

## 🚀 Démarrage rapide

### Prérequis

- 🐍 Python 3.11+
- 🐳 Docker & Docker Compose
- 🔑 Clés API : Twilio, OpenAI, ElevenLabs

### Installation

```bash
# 1. Cloner le repo
git clone https://github.com/oumayma728/Secretaire-Medicale-IA.git
cd Secretaire-Medicale-IA

# 2. Configurer l'environnement
cp .env.example .env   # renseigner les clés Twilio / OpenAI / ElevenLabs

# 3. Lancer PostgreSQL (+pgvector) et Redis
docker compose up -d

# 4. Appliquer les migrations
alembic upgrade head

# 5. Démarrer le serveur
uvicorn app.main:app --reload
```

> 🌐 API disponible sur `http://localhost:8000` — docs interactives sur `http://localhost:8000/docs`

### Avec Docker uniquement

```bash
docker compose up --build
```

---

## ⚙️ Variables d'environnement

| Variable | Description |
|---|---|
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | Identifiants du compte Twilio |
| `TWILIO_PHONE_NUMBER` | Numéro Twilio utilisé pour les appels |
| `TWILIO_WEBHOOK_BASE_URL` | URL publique pour les webhooks Twilio |
| `HUMAN_TRANSFER_NUMBER` | Numéro à composer lors d'un transfert humain |
| `OPENAI_API_KEY` | Clé API OpenAI (Realtime, embeddings, Whisper) |
| `OPENAI_REALTIME_MODEL` | Modèle Realtime (`gpt-4o-realtime-preview`) |
| `ELEVENLABS_API_KEY` | Clé API ElevenLabs (TTS) |
| `ELEVENLABS_VOICE_ID_*` | ID de voix par langue (FR requis, EN/AR/IT/ES optionnels) |
| `DATABASE_URL` | Chaîne de connexion PostgreSQL (async) |
| `REDIS_URL` | Chaîne de connexion Redis |
| `SUPPORTED_LANGUAGES` / `DEFAULT_LANGUAGE` | Langues supportées par le pipeline vocal |
| `FEATURE_CALL_RECORDING` | Active/désactive l'enregistrement des appels |
| `RATE_LIMIT_PER_MINUTE` | Limite de requêtes par minute |
| `INTERNAL_API_KEY` | Clé partagée exigée pour déclencher un appel sortant (`/voice/outbound/trigger`) |
| `LOG_LEVEL` / `LOG_FORMAT` | Verbosité et format des logs |

📄 Voir [`.env.example`](.env.example) pour la liste complète.

---

## 🗂️ Architecture

```
app/
├── api/
│   ├── deps.py                  # Auth Twilio, clé interne, rate limit
│   └── v1/
│       ├── voice.py             # Webhooks Twilio (inbound, outbound, status, trigger)
│       └── voice_functions/     # Handlers par route (inbound, outbound, media_stream, status)
├── services/
│   ├── voice/                   # Pont OpenAI Realtime <-> Twilio Media Stream, outils, dispatcher sortant
│   ├── integrations/            # Client Twilio, TTS streaming ElevenLabs
│   └── ai/                      # Mémoire de conversation (pgvector)
├── models/                      # Patient, CallSession, Transcript, Outcome, Appointment, PatientMemory
├── db/                          # Session & base SQLAlchemy
├── core/                        # Config, logging
└── tests/                       # Scénarios de qualité de conversation
```

---

## ✅ Périmètre couvert

- ☎️ Décroché automatique des appels entrants, avec création/retrouvaille du patient par numéro
- 📤 Appels sortants réels via Twilio (`POST /voice/outbound/trigger/{appointment_id}`) : confirmation, rappel de RDV, enquête de satisfaction, rappel d'examens, relance administrative
- 🎙️ Pipeline vocal complet (STT → dialogue → TTS) avec gestion de l'interruption (*barge-in*)
- 🚨 Détection d'urgence → transfert humain réel (`transfer_to_human`, redirection Twilio `<Dial>` vers `HUMAN_TRANSFER_NUMBER`)
- 📅 Recherche de créneaux disponibles, proposition et confirmation de rendez-vous, persistés en base
- 🌍 Changement de langue à la volée (FR/EN/AR/IT/ES) via l'outil `switch_language`, propagé au modèle et à la voix TTS
- 🧠 Mémoire de conversation inter-appels (`PatientMemory`, pgvector) : résumé rappelé au début de l'appel et interrogeable via `recall_context`
- 🧪 Tests de scénarios (réservation, transfert d'urgence, changement de langue, fin d'appel)

## 🧭 Ce qui reste à construire

- [ ] 🌍 **Validation multilingue complète** : le mécanisme de changement de langue est en place pour les 5 langues, mais seuls les prompts et voix FR/EN sont validés à l'usage ; il faut fournir des voice IDs ElevenLabs pour AR/IT/ES et valider la qualité de conversation dans ces langues
- [ ] 🎓 **Apprentissage à partir des corrections humaines** : hors périmètre de cette itération (mentionné par le CDC comme axe d'évolution)
- [ ] 🔀 **Reprise d'appel en direct par une secrétaire** (bascule live) : pas encore implémentée — dépend du Module 1 (comptes/rôles) pour l'authentification
- [ ] 🔗 **Intégration Module 1** : ce module suppose une API/BDD centrale (comptes, rôles) exposée par le Module 1 ; aucune logique d'auth n'est dupliquée ici

---

## 🧪 Tests

```bash
pytest
```

Marqueurs disponibles : `unit`, `integration`, `architecture`, `slow`.

```bash
pytest -m unit          # tests unitaires uniquement
pytest -m integration   # tests d'intégration (API/DB)
```

---

<div align="center">

Fait avec ❤️ pour améliorer l'accueil téléphonique des cabinets médicaux

</div>
