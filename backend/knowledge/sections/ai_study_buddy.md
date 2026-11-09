# AI Study Buddy

Repository: https://github.com/tisha-varma/AI-study-Buddy

A local multi-agent learning assistant built with CrewAI, Ollama (Mistral) and Streamlit. It runs fully on the user's machine (private, zero-cost, offline inference). Python 3.12+; setup uses `ollama pull mistral` with CrewAI pointed at the local Ollama endpoint through environment variables.

Three agents run in a strict sequence:

- Topic Agent: a structured, beginner-friendly explanation of any topic.
- Quiz Agent: reads that explanation and writes 3 multiple-choice questions with the answers hidden.
- Feedback Agent: takes the material, the questions and the user's answers; grades them, explains each option and returns a score such as 2/3.

Three-phase pipeline: explanation -> auto-generated quiz -> grading with personalised feedback. Interfaces: Streamlit web app (app.py) and a command-line tool (main.py), both over the same backend logic.

Public repository, 3 commits, demo video in the README.
