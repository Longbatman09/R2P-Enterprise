---
name: R2P-Enterprise-Project-Goals
description: Core project goals and architectural decisions for the R2P-Enterprise SDK
type: project
---

# R2P-Enterprise — Project Goals

R2P-Enterprise is a Python SDK for AI-powered school analytics, built on top of an existing FastAPI backend. The product helps schools get actionable insights from student report cards.

## Product Vision
- School analytics platform powered by AI
- Ingest student report cards (PDFs) → parse with AI (Gemini/Google GenAI) → extract grades, attendance, subject performance
- Per-student RAG (Retrieval-Augmented Generation) so each student only sees their own history
- Every school, tenant, and student gets an isolated Pinecone namespace
- Stripe-backed invoicing per school/plan

## Deployment Model
- **School IT admins** integrate the SDK into any school app or website
- Integration should be dead-simple for schools (copy-paste snippet)
- After integration, everything must just work — rag, analytics, etc.

## Auth & Access Model
- No self-service signup. Admin manually creates accounts in Supabase Auth.
- Frontend is login-only (email + password via Supabase Auth).
- Only authorized logged-in users can generate API keys.
- API keys are the integration mechanism for the school apps.

## Frontend
- Separate frontend (friend is building it)
- Login page only (no register/signup)
- Dashboard for authorized users to view analytics and generate API keys
- Must integrate cleanly with the backend SDK
