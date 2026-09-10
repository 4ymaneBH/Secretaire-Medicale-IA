# Intégration du Module 1 avec le Module 3

## Objectif

Connecter le backend central du **Module 1** (`NestJS`, authentification et rôles) au service de téléphonie du **Module 3** (`FastAPI`, Twilio et assistant vocal) afin que le Module 1 puisse déclencher un appel sortant pour un rendez-vous.

Le premier objectif fonctionnel est le suivant :

```text
Utilisateur autorisé
    -> Module 1
    -> requête interne authentifiée
    -> Module 3
    -> Twilio
    -> identifiant d'appel Twilio retourné au Module 1
```

## Décision d'architecture

Les deux modules doivent rester deux services séparés :

| Service | Responsabilité | Port local |
|---|---|---:|
| Module 1 | Utilisateurs, authentification, rôles et permissions | `3000` |
| Module 3 | Patients téléphoniques, rendez-vous vocaux et appels Twilio | `8000` |

Il ne faut pas fusionner directement le contenu des deux branches à la racine. Les branches n'ont pas d'ancêtre Git commun et contiennent deux applications différentes avec des fichiers racine incompatibles (`README.md`, `docker-compose.yml`, dépendances, etc.).

Pour le premier jalon :

- les bases de données restent séparées ;
- le Module 3 reste propriétaire de ses rendez-vous et de ses patients téléphoniques ;
- le Module 1 communique avec le Module 3 par HTTP ;
- les appels internes utilisent une clé secrète partagée ;
- le partage des JWT entre les modules sera traité dans un second jalon.

## Contrat HTTP déjà disponible dans le Module 3

Le Module 3 expose déjà la route suivante :

```http
POST /api/v1/voice/outbound/trigger/{appointmentId}?purpose={purpose}
X-Internal-API-Key: <clé-interne>
```

`appointmentId` doit être un entier correspondant à un rendez-vous existant dans la base du Module 3.

Valeurs autorisées pour `purpose` :

| Valeur | Utilisation |
|---|---|
| `confirmation` | Confirmation d'un rendez-vous |
| `reminder` | Rappel d'un rendez-vous |
| `satisfaction_survey` | Enquête de satisfaction |
| `exam_reminder` | Rappel d'examens |
| `admin_followup` | Relance administrative |

Réponse en cas de succès :

```json
{
  "success": true,
  "call_sid": "CAxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
}
```

### Codes d'erreur retournés par le Module 3

Ces codes sont figés et couverts par des tests (`app/tests/test_outbound_trigger_contract.py`).
Le corps d'erreur suit le format FastAPI standard : `{"detail": "<message>"}`.

| Code | Cause |
|---|---|
| `400` | `purpose` n'est pas l'une des cinq valeurs autorisées |
| `401` | En-tête `X-Internal-API-Key` absent ou invalide |
| `404` | Aucun rendez-vous ne correspond à `appointmentId` |
| `409` | Le rendez-vous existe mais n'a pas de patient avec un numéro de téléphone |
| `422` | `appointmentId` n'est pas un entier (validation FastAPI) |
| `502` | Twilio n'a pas créé l'appel |

Un `purpose` invalide ne déclenche **jamais** d'appel : il n'y a plus de repli
silencieux vers `reminder`, qui aurait fait lire le mauvais script au patient.

## Changements nécessaires dans le Module 1

### 1. Corriger les blocages de démarrage

Avant l'intégration, le Module 1 doit pouvoir démarrer de manière fiable.

- [ ] Utiliser Node.js `22` dans le Dockerfile. Node 18 est incompatible avec les versions verrouillées de NestJS et Prisma.
- [ ] Renommer `dockerfile` en `Dockerfile`, ou déclarer explicitement `dockerfile: dockerfile` dans Compose.
- [ ] Remplacer les imports tels que `src/cache/cache.service` par des imports relatifs, ou configurer un alias utilisable au runtime.
- [ ] Exécuter `npx prisma migrate deploy` avant le démarrage de l'API.
- [ ] Exécuter le seed Prisma dans un environnement contenant `ts-node`, ou compiler le seed pour l'image de production.
- [ ] Vérifier `GET /api/v1/health` avec PostgreSQL et Redis disponibles.

### 2. Ajouter les variables d'environnement

Ajouter dans `.env.example` du Module 1 :

```env
# Connexion au service de téléphonie du Module 3
VOICE_API_BASE_URL=http://localhost:8000/api/v1
VOICE_API_INTERNAL_KEY=change-me
VOICE_API_TIMEOUT_MS=10000
```

Dans un réseau Docker Compose commun, utiliser le nom du service au lieu de `localhost` :

```env
VOICE_API_BASE_URL=http://voice-api:8000/api/v1
```

Dans le Module 3, la même clé doit être configurée :

```env
INTERNAL_API_KEY=change-me
```

La vraie clé de production ne doit pas être enregistrée dans Git.

### 3. Ajouter le client HTTP NestJS

Installer le client HTTP NestJS :

```bash
npm install @nestjs/axios axios
```

Créer un module d'intégration, par exemple :

```text
src/integrations/voice/
  voice.module.ts
  voice-client.service.ts
  voice.controller.ts
  dto/trigger-outbound-call.dto.ts
```

Le service doit :

- lire `VOICE_API_BASE_URL`, `VOICE_API_INTERNAL_KEY` et `VOICE_API_TIMEOUT_MS` avec `ConfigService` ;
- envoyer `X-Internal-API-Key` au Module 3 ;
- transmettre `appointmentId` et `purpose` ;
- appliquer un timeout ;
- journaliser l'échec sans afficher la clé secrète ;
- retourner le `call_sid` reçu ;
- convertir les erreurs du Module 3 en réponses HTTP cohérentes.

Correspondance recommandée des erreurs :

| Erreur du Module 3 | Réponse du Module 1 |
|---|---|
| `400` valeur `purpose` invalide | `400 Bad Request` |
| `401` clé interne rejetée | `502 Bad Gateway` avec journal d'erreur interne |
| `404` rendez-vous absent | `404 Not Found` |
| `409` rendez-vous sans patient joignable | `409 Conflict` — le rendez-vous existe mais n'est pas appelable |
| `502` échec Twilio | `502 Bad Gateway` |
| Timeout ou service indisponible | `503 Service Unavailable` |
| Autre erreur distante | `502 Bad Gateway` |

### 4. Exposer un endpoint dans le Module 1

Route recommandée :

```http
POST /api/v1/appointments/{appointmentId}/outbound-call
Authorization: Bearer <access-token>
Content-Type: application/json

{
  "purpose": "reminder"
}
```

Cette route doit :

- être protégée par `JwtAuthGuard` ;
- être protégée par `PermissionsGuard` ;
- exiger la permission `calls:trigger` ;
- valider `appointmentId` comme entier positif ;
- valider `purpose` avec une liste fermée ;
- appeler `VoiceClientService` ;
- retourner le résultat du Module 3.

### 5. Ajouter la permission `calls:trigger`

Ajouter au seed Prisma :

```ts
{ name: 'calls:trigger', resource: 'calls' }
```

Attribution initiale recommandée :

| Rôle | Permission `calls:trigger` |
|---|---:|
| `admin` | Oui |
| `secretary` | Oui |
| `doctor` | À décider fonctionnellement |
| `patient` | Non |

Les routes utilisateurs du Module 1 doivent également recevoir des contrôles de permission. Dans l'état actuel, elles utilisent seulement `JwtAuthGuard`, ce qui permet à tout utilisateur authentifié d'accéder à des opérations sensibles.

## Changements nécessaires dans le Module 3

### Premier jalon

La route de déclenchement et la vérification de `X-Internal-API-Key` existent déjà.

Statut : **terminé**. Détail de ce qui a été fait :

- [x] valider explicitement `purpose` au niveau de l'API et retourner `400` si la valeur est invalide ;
      le repli silencieux vers `reminder` a été supprimé ;
- [x] retourner `404` lorsqu'un rendez-vous n'existe pas et `409` lorsqu'il n'a pas de patient
      joignable, au lieu d'une réponse générique `200 {"success": false}` ;
- [x] conserver une réponse stable contenant `success` et `call_sid` (inchangée) ;
- [x] ajouter un test d'intégration pour la clé interne
      (`app/tests/test_outbound_trigger_contract.py`) ;
- [x] protéger réellement les webhooks Twilio avec la validation de signature ;
      le contournement `SKIP_TWILIO_SIGNATURE_VERIFICATION` est désormais refusé quand
      `APP_ENV=production` et documenté dans `.env.example` ;
- [x] documenter la route dans OpenAPI : `purpose` est publié avec la liste fermée de ses
      cinq valeurs, et les codes `400/401/404/422/502` sont décrits.

Correction de sécurité supplémentaire (hors liste initiale) : la route utilisait
`Depends(rate_limit)` directement, ce qui exposait `limit`, `window_seconds` et `key_prefix`
comme paramètres de requête. N'importe quel appelant pouvait donc envoyer
`?limit=999999` pour désactiver sa propre limite de débit. La dépendance est maintenant
encapsulée dans `rate_limit_internal_trigger`, et un test vérifie que `purpose` est le seul
paramètre de requête publié.

### Deuxième jalon : identité patient

Les identifiants ne sont pas compatibles actuellement :

| Module 1 | Module 3 |
|---|---|
| `User.id` : texte au format UUID | `Patient.id` : entier |
| Aucun numéro de téléphone | Numéro de téléphone obligatoire |

Changements recommandés :

- [ ] ajouter un numéro de téléphone normalisé au format E.164 dans le profil patient du Module 1 ;
- [ ] ajouter `core_user_id` de type texte, nullable et unique dans `patients` du Module 3 ;
- [ ] créer une API ou un événement de synchronisation des patients ;
- [ ] ne jamais supposer que `User.id` et `Patient.id` ont la même valeur ;
- [ ] définir la gestion des modifications de téléphone et des doublons.

## Gestion des rendez-vous

Le Module 1 possède actuellement des permissions `appointments:*`, mais ne possède ni modèle ni API de rendez-vous. Le Module 3 utilise ses propres rendez-vous avec une clé primaire entière.

Pour le premier jalon, le Module 1 doit recevoir ou stocker l'identifiant du rendez-vous créé dans le Module 3. Il ne faut pas créer immédiatement une deuxième table `appointments` indépendante dans le Module 1.

Une décision d'architecture sera ensuite nécessaire :

1. conserver le Module 3 comme source de vérité des rendez-vous ; ou
2. créer un service central de rendez-vous avec un identifiant stable partagé par les modules.

## Réseau Docker recommandé

Les deux APIs doivent être accessibles sur un même réseau Docker :

```yaml
services:
  core-api:
    # Module 1
    ports:
      - "3000:3000"
    networks:
      - secretaire-network

  voice-api:
    # Module 3
    ports:
      - "8000:8000"
    networks:
      - secretaire-network

networks:
  secretaire-network:
    driver: bridge
```

Les bases PostgreSQL peuvent rester séparées. Si Redis est partagé plus tard, chaque module doit utiliser des préfixes de clés différents afin d'éviter les collisions.

## Tests obligatoires

### Tests du Module 1

- [ ] Un utilisateur autorisé peut déclencher un appel.
- [ ] Un utilisateur sans `calls:trigger` reçoit `403`.
- [ ] Une requête sans JWT reçoit `401`.
- [ ] Un `purpose` invalide reçoit `400`.
- [ ] Une indisponibilité du Module 3 reçoit `503`.
- [ ] Un refus de la clé interne n'expose pas la clé dans les logs ou la réponse.

### Tests du Module 3

Tous couverts dans `app/tests/test_outbound_trigger_contract.py` — `python -m pytest` doit passer.

- [x] Une clé interne correcte autorise la requête.
- [x] Une clé absente ou incorrecte reçoit `401`, et la clé rejetée n'apparaît pas dans la réponse.
- [x] Un rendez-vous inexistant reçoit `404`.
- [x] Un rendez-vous sans patient ou sans téléphone ne déclenche aucun appel (`409`),
      code distinct du `422` que FastAPI renvoie pour un `appointmentId` non entier.
- [x] Un appel réussi retourne un `call_sid`.
- [x] Un `purpose` invalide reçoit `400` et ne déclenche aucun appel.
- [x] La limite de débit n'est pas modifiable par l'appelant.

### Test manuel de bout en bout

Préparer dans le Module 3 un patient avec un numéro valide et un rendez-vous associé, puis tester directement :

```bash
curl -X POST "http://localhost:8000/api/v1/voice/outbound/trigger/1?purpose=reminder" \
  -H "X-Internal-API-Key: development-secret"
```

Après validation du client Module 1, tester la route publique du Module 1 :

```bash
curl -X POST "http://localhost:3000/api/v1/appointments/1/outbound-call" \
  -H "Authorization: Bearer <access-token>" \
  -H "Content-Type: application/json" \
  -d '{"purpose":"reminder"}'
```

## Limites connues du Module 3 à la date de la remise

À signaler aux équipes qui intègrent, pour éviter de mauvaises surprises :

- **Identité patient** : le Module 3 crée automatiquement un `Patient` à partir du numéro
  appelant si aucun ne correspond. Il n'y a pas encore de lien vers `User.id` du Module 1
  (voir « Deuxième jalon »). Les deux systèmes n'ont donc pas de patient commun.
- **Rendez-vous** : le Module 3 reste la source de vérité. `appointmentId` est un entier
  propre au Module 3 et n'a aucune signification dans le Module 1.
- **Langues** : les cinq langues sont configurées côté dialogue, mais
  `ELEVENLABS_VOICE_ID_AR/IT/ES` sont optionnelles et retombent sur la voix française
  si elles ne sont pas renseignées. Pour une validation réelle sur cinq langues,
  ces trois identifiants de voix doivent être fournis.
- **Apprentissage à partir des corrections humaines** (CDC §3) : non implémenté.
- **Couverture de tests** : le contrat d'intégration et les outils de dialogue sont testés.
  Le cœur temps réel (`media_stream.py`, `realtime_session.py`, `tts_bridge.py`) n'a pas
  de test automatisé et n'est validé que par des appels manuels.
- **Aucun webhook sortant** : le Module 3 ne notifie pas le Module 1 de la fin d'un appel
  ni d'un rendez-vous confirmé. Si le Module 1 en a besoin, il faut le prévoir explicitement.

## Critères d'acceptation du premier jalon

Le premier jalon est terminé lorsque :

- [ ] les deux services démarrent et leurs endpoints de santé répondent ;
- [ ] le Module 1 appelle le Module 3 avec une clé interne ;
- [ ] seuls les rôles autorisés peuvent demander un appel ;
- [ ] le rendez-vous et le patient existent dans le Module 3 ;
- [ ] Twilio crée effectivement l'appel ;
- [ ] le `call_sid` est retourné jusqu'au client du Module 1 ;
- [ ] les scénarios d'erreur sont testés ;
- [ ] `.env.example` et Swagger sont à jour ;
- [ ] aucun secret réel n'est enregistré dans Git.

## Livrables demandés au développeur du Module 1

- le commit ou la pull request contenant l'intégration ;
- le nouveau module/client HTTP ;
- l'endpoint protégé du Module 1 ;
- la permission `calls:trigger` et son seed ;
- les tests unitaires et d'intégration ;
- la mise à jour de `.env.example` ;
- la documentation Swagger ;
- une preuve de test montrant un `call_sid` retourné par le flux Module 1 vers Module 3.

