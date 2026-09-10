**Cahier des Charges (CDC) Secrétaire Médicale IA** 

# **Module 3 — Téléphonie & Assistant vocal IA** 

## 1. Contexte général du projet 

Les cabinets médicaux, cliniques et centres de santé reçoivent quotidiennement un grand nombre d'appels concernant la prise de rendez-vous, les demandes d'informations, les confirmations, les annulations et le suivi administratif. L'objectif du projet est de développer une Secrétaire Médicale basée sur l'Intelligence Artificielle, capable d'automatiser ces tâches tout en restant disponible 24h/24 et 7j/7. 

### **Objectifs du projet** 

- Réduire la charge administrative des cabinets 

- Répondre automatiquement aux appels 

- Automatiser la prise de rendez-vous 

- Diminuer les appels manqués 

- Améliorer l'expérience patient 

- Intégrer la solution avec les logiciels médicaux existants 

- Respecter la confidentialité des données médicales 

### **Utilisateurs de la plateforme** 

- Patients : prendre, modifier, annuler un rendez-vous ; poser des questions ; demander des documents ; être rappelés automatiquement 

- Médecins : consulter leur planning ; valider certains rendez-vous ; consulter les conversations IA ; modifier leurs disponibilités 

- Secrétaires : superviser les conversations ; reprendre un appel en direct ; corriger les réponses de l'IA ; gérer les urgences 

- Administrateur : gestion des utilisateurs, paramétrage de l'IA, rapports, configuration générale 

### **Architecture générale** 

L'ensemble des modules s'appuie sur une API REST et une base de données centrales (Module 1 — Backend Core & API), qui portent les comptes utilisateurs, les rôles et permissions. Chaque module ci-dessous couvre un périmètre fonctionnel autonome du projet global ; ce document précise le contexte général puis le rôle spécifique de ce module dans l'ensemble. 

## 2. Présentation du module 

Ce module gère la réception et l'émission des appels téléphoniques ainsi que l'intelligence conversationnelle vocale : compréhension du langage naturel, synthèse vocale, mémoire de conversation, multilingue et détection d'urgence. C'est le module le plus complexe du projet. 

## 3. Fonctionnalités détaillées 

- Décroché automatique des appels entrants 

- Transfert à un humain si nécessaire 

- Appels sortants automatiques : confirmation de rendez-vous, rappel de rendez-vous, enquête satisfaction, rappel d'examens, relance administrative 

- Compréhension du langage naturel et des accents 

- Voix humaine (synthèse vocale naturelle) avec gestion de l'interruption naturelle 

- Mémoire de la conversation 

- Changement de langue à la volée — Langues supportées : Français, Anglais, Arabe, Italien, Espagnol 

- Compréhension du contexte et détection d'une urgence 

- Proposition de créneaux de rendez-vous 

- Apprentissage à partir des corrections humaines 

## 4. Stack technique 

- Twilio Voice (ou Asterisk/SIP en solution auto-hébergée) 

- Python 

- Whisper ou Deepgram (Speech-to-Text) 

- ElevenLabs ou Azure TTS (Text-to-Speech) 

- LangChain + API Claude/GPT (dialogue et NLU) 

- pgvector ou Pinecone (mémoire conversationnelle) 

## 5. Livrables attendus 

- Intégration téléphonique fonctionnelle (réception + émission) avec transfert vers un agent humain 

- Pipeline vocal complet (STT → NLU/dialogue → TTS) 

- Support multilingue validé sur les 5 langues cibles 

- Mécanisme de détection d'urgence avec transfert automatique 

- Tests de qualité de conversation (jeux de scénarios) 

## 6. Dépendances 

Module 1. Module dense — à confier à ton stagiaire le plus solide, idéalement en binôme. 

