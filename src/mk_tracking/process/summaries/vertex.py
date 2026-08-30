
# TODO(postgres-migration): the Google Cloud inference APIs (Vertex AI / Gemini
# embeddings and summarisation) are deliberately OUT OF SCOPE for the PostgreSQL
# migration and are left as they are. Only the data store moved; the model calls
# did not. Revisit separately.
from __future__ import annotations

import time

from google import genai
from google.genai import errors, types

from .models import PoliticianAnalysis
from .pipeline import SYSTEM_INSTRUCTION


class VertexAnalyzer:
    def __init__(self, project: str, location: str, model: str) -> None:
        self.model = model
        self.client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(api_version="v1"),
        )

    def analyze(self, prompt: str) -> PoliticianAnalysis:
        response = None
        for attempt in range(3):
            try:
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=SYSTEM_INSTRUCTION,
                        response_mime_type="application/json",
                        response_schema=PoliticianAnalysis,
                        temperature=0,
                    ),
                )
                break
            except errors.ServerError:
                if attempt == 2:
                    raise
                time.sleep(2**attempt)
            except errors.ClientError as error:
                if getattr(error, "code", None) != 429 or attempt == 2:
                    raise
                time.sleep(2**attempt)
        if response is None:
            raise RuntimeError("Vertex AI request did not complete")
        if response.parsed is not None:
            return PoliticianAnalysis.model_validate(response.parsed)
        if not response.text:
            raise RuntimeError("Vertex AI returned no content")
        return PoliticianAnalysis.model_validate_json(response.text)
