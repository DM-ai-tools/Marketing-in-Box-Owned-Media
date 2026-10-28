"""`POST /pipeline/files/extract`: the clip button on a file question. Thin; see
`app/services/document_text.py`.

Returns the file's text for the input box. Stores nothing and costs nothing. A file that cannot be
read is a 422 with one sentence saying what to upload instead.
"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.services import document_text

router = APIRouter()


@router.post("/files/extract")
async def extract_file_text(file: UploadFile = File(...)) -> dict:
    data = await file.read(document_text.MAX_UPLOAD_BYTES + 1)
    try:
        result = document_text.extract(file.filename or "", data)
    except document_text.DocumentTextError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result.to_dict()
