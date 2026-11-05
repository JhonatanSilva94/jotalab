import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import boto3
import jwt
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, field_validator

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("service-login")

# Config 12-factor: tudo vem do ambiente. Credenciais AWS são lidas pelo boto3.
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    sys.exit("ERRO: variável de ambiente JWT_SECRET não definida. O service-login não vai iniciar.")
JWT_EXPIRES_MINUTES = int(os.environ.get("JWT_EXPIRES_MINUTES", "60"))
TABLE_NAME = os.environ["TABLE_NAME"]
AWS_REGION = os.environ["AWS_REGION"]

table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)

# Hash usado quando o e-mail não existe: o bcrypt roda do mesmo jeito,
# então o tempo de resposta não revela se o e-mail está cadastrado.
DUMMY_HASH = bcrypt.hashpw(b"dummy-password", bcrypt.gensalt())
INVALID_LOGIN = "e-mail ou senha inválidos"
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

app = FastAPI(title="service-login")


def clean_email(v: str) -> str:
    v = v.strip().lower()
    if not EMAIL_RE.match(v):
        raise ValueError("e-mail inválido")
    return v


class NewUser(BaseModel):
    email: str
    name: str
    password: str

    _email = field_validator("email")(clean_email)

    @field_validator("name")
    @classmethod
    def check_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("nome obrigatório")
        return v

    @field_validator("password")
    @classmethod
    def check_password(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("a senha precisa ter no mínimo 8 caracteres")
        if len(v.encode()) > 72:  # limite do bcrypt
            raise ValueError("a senha pode ter no máximo 72 bytes")
        return v


class Login(BaseModel):
    email: str
    password: str


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    # O 422 padrão do FastAPI ecoa o valor enviado ("input"), o que exporia a senha.
    errors = [{"field": e["loc"][-1], "msg": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content={"detail": errors})


@app.middleware("http")
async def log_request(request: Request, call_next):
    # Loga só método, caminho, status e tempo: nunca corpo (senha) nem headers (token).
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        ms = (time.perf_counter() - start) * 1000
        log.info("%s %s %d %.1fms", request.method, request.url.path, status, ms)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/users", status_code=201)
def create_user(body: NewUser):
    user_id = "u-" + uuid.uuid4().hex
    password_hash = bcrypt.hashpw(body.password.encode(), bcrypt.gensalt()).decode()
    try:
        table.put_item(
            Item={
                "email": body.email,
                "userId": user_id,
                "name": body.name,
                "passwordHash": password_hash,
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ConditionExpression="attribute_not_exists(email)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise HTTPException(status_code=409, detail="e-mail já cadastrado")
        raise
    return {"userId": user_id, "email": body.email, "name": body.name}


@app.post("/login")
def login(body: Login):
    email = body.email.strip().lower()
    item = table.get_item(Key={"email": email}).get("Item")
    stored = item["passwordHash"].encode() if item else DUMMY_HASH
    ok = bcrypt.checkpw(body.password.encode()[:72], stored)
    if not (item and ok):
        raise HTTPException(status_code=401, detail=INVALID_LOGIN)

    now = datetime.now(timezone.utc)
    token = jwt.encode(
        {"sub": item["userId"], "iat": now, "exp": now + timedelta(minutes=JWT_EXPIRES_MINUTES)},
        JWT_SECRET,
        algorithm="HS256",
    )
    return {"token": token, "expiresIn": JWT_EXPIRES_MINUTES * 60, "name": item["name"]}
