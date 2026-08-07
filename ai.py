import os
import json
from pathlib import Path
from dotenv import load_dotenv
from google import genai
from google.genai import types

import time

# Load .env using an absolute path relative to this file, not the current
# working directory. Without this, tools like VS Code's "Code Runner" can
# execute the script from a different cwd, load_dotenv() silently finds
# nothing, and GEMINI_API_KEY never gets set -- which is what causes
# "Missing key inputs argument!" from genai.Client().
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env")

_api_key = os.getenv("GEMINI_API_KEY")
if not _api_key:
    raise RuntimeError(
        "GEMINI_API_KEY is not set. Create a .env file next to app.py "
        "(copy .env.example to .env) and set GEMINI_API_KEY=your_key_here. "
        "Get a free key at https://aistudio.google.com/apikey"
    )

# Pass the key explicitly rather than relying on implicit env lookup --
# this is what actually fixes the "Missing key inputs argument!" error.
_client = genai.Client(api_key=_api_key)

# gemini-2.5-flash is free of charge on the Gemini API free tier
# (see https://ai.google.dev/gemini-api/docs/pricing) and is a stable
# (non-preview) model, which makes it a good fit for a student project.
_MODELS_TO_TRY = ["gemini-3.6-flash", "gemini-3.5-flash-lite"]

# JSON schema Gemini must follow. Using response_format with a schema means
# we get back valid, predictable JSON instead of having to hope the model
# wrapped its answer in the right markdown fences.
_RESUME_SCHEMA = {
    "type": "object",
    "properties": {
        "resume_score": {
            "type": "integer",
            "minimum": 0,
            "maximum": 100,
            "description": "Overall resume quality score out of 100, considering clarity, "
                            "relevance to the goal, and completeness.",
        },
        "skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Relevant skills detected in the resume.",
        },
        "missing_skills": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Important skills for the target role that are missing from the resume.",
        },
        "strengths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "What the resume does well.",
        },
        "weaknesses": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Weaknesses or gaps in the resume.",
        },
        "improvement_suggestions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Concrete, actionable suggestions to improve the resume.",
        },
        "recommended_roles": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Job titles/roles this resume is a good fit for, given the goal.",
        },
        "roadmap": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Step-by-step learning roadmap to close the skill gaps for the goal.",
        },
        "interview_questions": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Likely interview questions for the target role, based on the resume.",
        },
        "overall_feedback": {
            "type": "string",
            "description": "A short (3-5 sentence) overall summary of the analysis.",
        },
    },
    "required": [
        "resume_score",
        "skills",
        "missing_skills",
        "strengths",
        "weaknesses",
        "improvement_suggestions",
        "recommended_roles",
        "roadmap",
        "interview_questions",
        "overall_feedback",
    ],
}


def analyze_resume(resume_text, user_goal):
    """Send the resume to Gemini and return a dict matching _RESUME_SCHEMA.

    Always returns a dict. On any failure it returns {"error": "..."} so the
    caller (app.py) can render an error message instead of crashing.
    """
    if not resume_text or not resume_text.strip():
        return {"error": "No resume text was found to analyze."}

    prompt = f"""
You are a senior software engineer and hiring manager reviewing a resume.

User's career goal: "{user_goal}"

Evaluate the resume strictly based on this goal:
- Extract only skills that are actually present in the resume text.
- Ignore skills that are irrelevant to the goal (e.g. do not list Excel as a
  key skill for a backend engineering goal).
- Identify real, specific gaps between the resume and the goal.
- Give a resume_score out of 100 that reflects overall quality AND fit for the goal.
- Keep every list concise (roughly 3-8 items) and specific to this resume and goal.
- Base everything only on the resume text provided below. Do not invent
  experience or credentials that are not present in the text.

Resume:
\"\"\"
{resume_text}
\"\"\"
"""

    last_error = None

    for model_name in _MODELS_TO_TRY:
        # Retry each model up to 3 times with a short backoff before
        # giving up on it and moving to the next model in the list.
        for attempt in range(3):
            try:
                response = _client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=_RESUME_SCHEMA,
                    ),
                )
                return json.loads(response.text)

            except json.JSONDecodeError:
                return {"error": "Gemini returned a response that could not be parsed as JSON. Please try again."}

            except Exception as e:
                last_error = e
                message = str(e)
                is_overloaded = "503" in message or "UNAVAILABLE" in message or "overload" in message.lower()
                if is_overloaded and attempt < 2:
                    time.sleep(2 * (attempt + 1))  # 2s, then 4s
                    continue
                # Not an overload error (bad key, quota, etc.), or we're
                # out of retries for this model -- stop retrying and move
                # on to the next model (if any).
                break

    print(f"Error analyzing resume: {str(last_error)}")
    return {
        "error": "Gemini's servers are currently overloaded and the request failed after several retries. "
                 "Please try again in a minute."
                 if last_error and ("503" in str(last_error) or "UNAVAILABLE" in str(last_error))
                 else f"Error analyzing resume: {str(last_error)}"
    }