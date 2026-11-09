# Career Copilot AI - Career Intelligence Platform

Repository: https://github.com/tisha-varma/Career-Copilot
Live app: https://career-copilot-wbkk.onrender.com/

Tagline: Strategic Career Intelligence Suite: semantic resume auditing, predictive interview prep, and AI asset generation.

Repository: public, MIT license, 51 commits. Topics: ai, career-tech, fastapi, firebase, firestore, llama3, python, resume-analyzer.

Usage (as of 7 Nov 2026): 18 users, 31 resumes analyzed, 11 feedback entries.

Deployed on Render.

What it is: a career-optimisation platform that turns a static resume into strategic career assets using an LLM plus cloud infrastructure. It aims to understand the intent and impact of someone's experience, not just match keywords.

## Features

1. Resume auditing
   - Uses LLaMA 3.3 (llama-3.3-70b-versatile via Groq) to benchmark a resume against 12+ industry roles (AI Engineer, Product Manager, DevOps and more).
   - Affinity (role-fit) score: weighted match on technical skills, soft skills and leadership.
   - Skill-gap detection: critical missing keywords plus a prioritised learning roadmap with curated resources, including YouTube.
2. Interview preparation
   - Technical, behavioural and resume-based questions generated from the user's own projects and tenure, with STAR-framework guidance.
   - On-demand question generation through AJAX.
3. Professional assets
   - Context-aware cover letters generated from a pasted job description.
   - Downloadable PDF career analysis report with role-fit charts.
   - Integrated job search linking to LinkedIn, Indeed and Glassdoor.
4. Admin dashboard
   - Live view of user growth, uploads and interactions; audit logging of logins and system changes; feedback with CSV export; user, resume, feedback and audit-log management.

## Tech stack

- Languages: Python 3.12+, JavaScript (ES6+), HTML5/CSS3
- Frameworks and libraries: FastAPI, Tailwind CSS, Jinja2, ReportLab, Matplotlib, pdfplumber, Gunicorn
- Cloud and platforms: Firebase Authentication (Google Sign-In), Google Cloud Firestore, Cloudinary (resume storage), Render, Google Cloud Platform
- Databases: Firestore, ChromaDB (vector database), CSV/JSON local storage
- AI: Groq Cloud API, LLaMA models, BGE embeddings with ChromaDB retrieval, Firebase Admin SDK
- Dev tools: Git/GitHub, pip, python-dotenv, Mermaid.js

Resume parsing combines pdfplumber with regex section and skill detection and LLM structured extraction. Reliability features: automatic API-key rotation across multiple keys and multi-model fallback.

Architecture: candidate or admin -> FastAPI backend with Firebase auth middleware -> pdfplumber PDF extractor -> LLaMA engine returns JSON scoring and gap analysis -> results and file metadata cached in Firestore; the original resume is stored in Cloudinary; ReportLab generates the PDF report; local session and audit logs are reviewed by the admin.

## Core API endpoints

- POST /analyze - parse a resume and generate career insights
- GET /download-report - export the career analysis PDF
- GET /generate-resume-questions - AJAX interview-question generation
- POST /submit-feedback - user ratings
- GET /admin/dashboard - admin monitoring (admin only)
- DELETE /admin/delete-user/{uid} - remove a profile (admin only)

Folder structure: app/main.py (router and lifecycle), llama_analyzer.py (LLM logic), firestore_db.py, cloudinary_storage.py, report_generator.py, audit.py, static/, templates/; data/ for feedback and local sessions.

Planned improvements: an ATS optimisation score, mock voice interviews (ElevenLabs), a browser extension for LinkedIn job postings.
