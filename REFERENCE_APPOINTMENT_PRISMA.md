# Référence des rendez-vous pour le module Prisma

Ce document décrit uniquement les informations nécessaires pour référencer les rendez-vous existants depuis un modèle Prisma, notamment depuis un modèle `Payment`.

## Résumé

| Élément | Valeur |
|---|---|
| Modèle SQLAlchemy | `Appointment` |
| Table PostgreSQL | `appointments` |
| Clé primaire | `id` |
| Type de la clé primaire | `INTEGER` |
| Équivalent Prisma | `Int @id @default(autoincrement())` |
| Référence du patient | `patient_id` |
| Type de `patient_id` | `INTEGER`, nullable |
| Table patient référencée | `patients.id` |

## Modèle SQLAlchemy existant

Le modèle se trouve dans `app/models/appointment.py` :

```python
class Appointment(Base):
    __tablename__ = "appointments"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(
        Integer,
        ForeignKey("patients.id"),
        nullable=True,
        index=True,
    )

    scheduled_at = Column(DateTime(timezone=True), nullable=False, index=True)
    duration_minutes = Column(Integer, default=30)
    reason = Column(String(255), nullable=True)
    status = Column(
        Enum(AppointmentStatus),
        nullable=False,
        default=AppointmentStatus.PROPOSED,
    )
    notes = Column(Text, nullable=True)

    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    patient = relationship("Patient", back_populates="appointments")
    call_sessions = relationship("CallSession", back_populates="appointment")
```

Sous PostgreSQL, une clé primaire SQLAlchemy de type `Integer` est auto-incrémentée par défaut. Elle peut donc être représentée dans Prisma par :

```prisma
id Int @id @default(autoincrement())
```

## Colonnes de la table `appointments`

| Colonne PostgreSQL | Type | Nullable | Valeur par défaut | Description |
|---|---|---:|---|---|
| `id` | `INTEGER` | Non | Auto-incrémentée | Identifiant du rendez-vous |
| `patient_id` | `INTEGER` | Oui | — | Clé étrangère vers `patients.id` |
| `scheduled_at` | `TIMESTAMP WITH TIME ZONE` | Non | — | Date et heure du rendez-vous |
| `duration_minutes` | `INTEGER` | Oui | `30` | Durée prévue en minutes |
| `reason` | `VARCHAR(255)` | Oui | — | Motif du rendez-vous |
| `status` | `appointmentstatus` | Non | `proposed` | Statut PostgreSQL du rendez-vous |
| `notes` | `TEXT` | Oui | — | Notes complémentaires |
| `created_at` | `TIMESTAMP WITH TIME ZONE` | Non | `now()` | Date de création |
| `updated_at` | `TIMESTAMP WITH TIME ZONE` | Oui | — | Date de dernière modification |

## Statuts disponibles

Le type enum PostgreSQL s'appelle `appointmentstatus` et accepte les valeurs suivantes :

```text
proposed
confirmed
canceled
rescheduled
completed
no_show
```

## Relations existantes

### Relation avec les patients

```text
appointments.patient_id -> patients.id
```

- `patient_id` est nullable : un rendez-vous peut temporairement ne pas être associé à un patient.
- Un patient peut avoir plusieurs rendez-vous.
- Un rendez-vous appartient au maximum à un patient.

### Relation avec les sessions d'appel

La table `call_sessions` contient la clé étrangère suivante :

```text
call_sessions.appointment_id -> appointments.id
```

- `call_sessions.appointment_id` est de type `INTEGER` et nullable.
- Une session d'appel peut être associée à zéro ou un rendez-vous.
- Un rendez-vous peut être associé à plusieurs sessions d'appel.

### Suppression et mise à jour

Les clés étrangères existantes ne déclarent pas de règle `ON DELETE CASCADE` ou `ON UPDATE CASCADE`. PostgreSQL utilise donc son comportement par défaut (`NO ACTION`).

## Référence recommandée dans `Payment`

La colonne de référence doit avoir le même type que `appointments.id`, donc `Int` dans Prisma.

### Plusieurs paiements possibles par rendez-vous

Utiliser cette version lorsqu'un rendez-vous peut avoir plusieurs paiements, tentatives ou remboursements :

```prisma
model Payment {
  id            Int         @id @default(autoincrement())
  appointmentId Int         @map("appointment_id")
  appointment   Appointment @relation(
    fields: [appointmentId],
    references: [id],
    onDelete: NoAction,
    onUpdate: NoAction
  )

  @@index([appointmentId])
  @@map("payments")
}
```

Ajouter la relation inverse au modèle `Appointment` :

```prisma
model Appointment {
  id       Int       @id @default(autoincrement())
  payments Payment[]

  @@map("appointments")
}
```

### Un seul paiement par rendez-vous

Si chaque rendez-vous ne peut avoir qu'un seul paiement, ajouter `@unique` :

```prisma
appointmentId Int @unique @map("appointment_id")
```

Dans ce cas, la relation inverse dans `Appointment` devient facultative et singulière :

```prisma
payment Payment?
```

## Modèle Prisma complet de référence

Si le module Prisma doit représenter toute la table existante, le modèle peut être écrit ainsi :

```prisma
enum AppointmentStatus {
  PROPOSED    @map("proposed")
  CONFIRMED   @map("confirmed")
  CANCELED    @map("canceled")
  RESCHEDULED @map("rescheduled")
  COMPLETED   @map("completed")
  NO_SHOW     @map("no_show")

  @@map("appointmentstatus")
}

model Appointment {
  id              Int               @id @default(autoincrement())
  patientId       Int?              @map("patient_id")
  scheduledAt     DateTime          @map("scheduled_at") @db.Timestamptz(6)
  durationMinutes Int?              @default(30) @map("duration_minutes")
  reason          String?           @db.VarChar(255)
  status          AppointmentStatus @default(PROPOSED)
  notes           String?
  createdAt       DateTime          @default(now()) @map("created_at") @db.Timestamptz(6)
  updatedAt       DateTime?         @map("updated_at") @db.Timestamptz(6)

  patient         Patient?          @relation(fields: [patientId], references: [id])
  payments        Payment[]

  @@index([patientId])
  @@index([scheduledAt])
  @@map("appointments")
}
```

Le modèle `Patient` doit contenir la relation inverse correspondante :

```prisma
model Patient {
  id           Int           @id @default(autoincrement())
  appointments Appointment[]

  @@map("patients")
}
```

Le modèle Prisma complet doit être adapté si `Patient` ou `Appointment` existe déjà dans le schéma du Module 8. Il ne faut pas créer une deuxième table : `@@map("appointments")` permet de cibler la table PostgreSQL existante.

## Point à décider avant la migration

La seule décision fonctionnelle nécessaire concerne la cardinalité du paiement :

- sans `@unique` sur `Payment.appointmentId` : plusieurs paiements sont autorisés pour un rendez-vous ;
- avec `@unique` : un seul paiement est autorisé pour un rendez-vous.

Pour éviter que Prisma tente de recréer ou de modifier incorrectement les tables existantes, il est recommandé de récupérer d'abord leur définition avec :

```bash
npx prisma db pull
```

Puis ajouter le modèle `Payment` et sa relation au schéma obtenu.

