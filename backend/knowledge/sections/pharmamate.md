# PharmaMate - Pharmacist's Assistant

Repository: https://github.com/tisha-varma/PharmaMate

Google Girl Hackathon 2025 finalist. Built solo.

Public repository, 7 commits; README screenshots dated 25-26 Feb 2025.

Stack: Python, Tkinter GUI, Pandas, Pillow, Google Generative AI (Gemini API).

What it does: a pharmacist's assistant that matches orders against handwritten prescriptions, creates the order, and offers a symptom-to-possible-condition assistant.

- The user uploads a prescription image. It is sent directly to Gemini (gemini-2.0-pro-exp-02-05, via the google.generativeai library) as image bytes, so no separate OCR library is used.
- Medicine names are extracted from Gemini's reply with a regular expression and checked against a pandas-backed inventory file (medicine_database.csv).
- If a medicine is unavailable, a second Gemini call suggests 3 alternatives and the first one found in the inventory is used.
- Price, frequency and special instructions come from the inventory file; the final order with total cost can be saved as a text file.
- Processing runs in a background thread so the Tkinter interface stays responsive. The Gemini API key is configured by pasting it into main.py, as described in the README.
- The symptom module (systoms.py) asks for symptoms through an interface and suggests a possible condition.
