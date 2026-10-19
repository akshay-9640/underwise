"""The web app: upload a packet, see fields with evidence and rule results, correct a field, rerun.

    POST   /api/packets                              upload files and/or pasted email, then process
    GET    /api/packets/{id}                         the result JSON
    POST   /api/packets/{id}/process                 process again (for example after a model failure)
    PUT    /api/packets/{id}/fields/{name}/override  correct a field and rerun the checks
    DELETE /api/packets/{id}/fields/{name}/override  remove a correction and rerun
    POST   /api/packets/{id}/ask                     ask about this packet, answered with verified citations
    GET    /api/packets/{id}/report                  the human-readable report (Markdown)
"""

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import store
from .ask import Answer, Turn, ask
from .cli import report
from .documents import MAX_FILE_BYTES, PacketError, check_upload, safe_name
from .llm import LLM, LLMError, from_env
from .log import log
from .models import PacketResult
from .pipeline import clear_override, process, rerun, set_override

WEB = Path(__file__).parent / "web"


class Correction(BaseModel):
    value: str
    reason: str


class Question(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    history: list[Turn] = Field(default=[], max_length=10)


def create_app(llm: LLM | None = None, use_env: bool = True) -> FastAPI:
    model_error = None
    if llm is None and use_env:
        try:
            llm = from_env()
        except LLMError as e:  # a broken model setting must not stop the deterministic checks
            model_error = str(e)
            log("config", status="model_error", error=model_error)
    app = FastAPI(title="Underwise submission review", docs_url="/api/docs", redoc_url=None)
    app.mount("/static", StaticFiles(directory=WEB), name="static")

    def get(pid: str) -> PacketResult:
        try:
            return store.load(pid)
        except KeyError:
            raise HTTPException(404, "No such packet.")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html", headers={"Cache-Control": "no-store"})

    @app.get("/api/health")
    def health():
        return {"model": llm.label if llm else None, "model_error": model_error}

    @app.post("/api/packets", response_model=PacketResult)
    def create(files: list[UploadFile] = File(default=[]), email_text: str = Form(default="")):
        files = [f for f in files if f.filename]
        pasted = email_text.strip()
        names = [safe_name(f.filename) for f in files]
        try:
            check_upload(names, [f.size or 0 for f in files], bool(pasted))
        except PacketError as e:
            raise HTTPException(400, str(e))
        pid, folder = store.new_packet()
        for f, name in zip(files, names):
            data = f.file.read(MAX_FILE_BYTES + 1)
            if len(data) > MAX_FILE_BYTES:  # the declared size can be wrong; check what actually arrived
                raise HTTPException(400, f"{name} is larger than 10 MB.")
            (folder / name).write_bytes(data)
        if pasted:
            (folder / "pasted_email.txt").write_text(pasted, encoding="utf-8")
        log("upload", packet=pid, files=len(names), pasted_email=bool(pasted))
        return _process(pid, folder)

    def _process(pid: str, folder: Path) -> PacketResult:
        try:
            result = process(pid, folder, llm)
        except PacketError as e:
            raise HTTPException(400, str(e))
        store.save(result)
        return result

    @app.get("/api/packets/{pid}", response_model=PacketResult)
    def read(pid: str):
        return get(pid)

    @app.post("/api/packets/{pid}/process", response_model=PacketResult)
    def reprocess(pid: str):
        get(pid)
        return _process(pid, store.folder(pid))

    @app.put("/api/packets/{pid}/fields/{name}/override", response_model=PacketResult)
    def correct(pid: str, name: str, body: Correction):
        result = get(pid)
        try:
            result = set_override(result, name, body.value, body.reason)
        except ValueError as e:
            raise HTTPException(422, str(e))
        result = rerun(result, llm)
        store.save(result)
        log("override", packet=pid, field=name)
        return result

    @app.delete("/api/packets/{pid}/fields/{name}/override", response_model=PacketResult)
    def uncorrect(pid: str, name: str):
        try:
            result = rerun(clear_override(get(pid), name), llm)
        except ValueError as e:
            raise HTTPException(422, str(e))
        store.save(result)
        return result

    @app.post("/api/packets/{pid}/ask", response_model=Answer)
    def question(pid: str, body: Question):
        result = get(pid)
        try:
            return ask(llm, result, store.folder(pid), body.question.strip(), body.history)
        except LLMError as e:
            raise HTTPException(503, str(e))

    @app.get("/api/packets/{pid}/report", response_class=PlainTextResponse)
    def markdown(pid: str):
        return report(get(pid))

    return app
