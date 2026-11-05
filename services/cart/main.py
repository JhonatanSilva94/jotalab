import logging
import os
import sys
import time

import boto3
import jwt
from boto3.dynamodb.conditions import Key
from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

TABLE_NAME = os.environ["TABLE_NAME"]
AWS_REGION = os.environ["AWS_REGION"]
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    sys.exit("ERRO: variável de ambiente JWT_SECRET não definida. O service-cart não vai iniciar.")
CART_TTL_SECONDS = 7 * 24 * 3600  # carrinho expira 7 dias após a última alteração

table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("service-cart")

app = FastAPI(title="service-cart")


class CartItem(BaseModel):
    productId: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0, le=99)


@app.middleware("http")
async def log_request(request: Request, call_next):
    # Nunca loga headers: o Authorization carrega o token.
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        ms = (time.perf_counter() - start) * 1000
        log.info("%s %s %d %.1fms", request.method, request.url.path, status, ms)


def current_user(authorization: str | None = Header(default=None)) -> str:
    """Valida o JWT do service-login (HS256) e devolve o userId (claim sub)."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="token ausente", headers={"WWW-Authenticate": "Bearer"})
    try:
        claims = jwt.decode(authorization[7:], JWT_SECRET, algorithms=["HS256"], options={"require": ["sub", "exp"]})
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="token inválido", headers={"WWW-Authenticate": "Bearer"})
    return claims["sub"]


def query_items(user_id: str) -> list[dict]:
    resp = table.query(KeyConditionExpression=Key("userId").eq(user_id))
    items = resp["Items"]
    while "LastEvaluatedKey" in resp:
        resp = table.query(KeyConditionExpression=Key("userId").eq(user_id), ExclusiveStartKey=resp["LastEvaluatedKey"])
        items += resp["Items"]
    # O TTL do DynamoDB pode levar horas para apagar: filtra os já vencidos.
    now = int(time.time())
    return [i for i in items if int(i.get("expiresAt", 0)) > now]


def cart_json(items: list[dict]) -> dict:
    return {"items": [{"productId": i["productId"], "quantity": int(i["quantity"])} for i in items]}


def renew(user_id: str) -> None:
    # A cada alteração, o carrinho INTEIRO ganha mais 7 dias
    # (senão itens antigos expirariam antes dos novos).
    expires = int(time.time()) + CART_TTL_SECONDS
    for item in query_items(user_id):
        table.update_item(
            Key={"userId": user_id, "productId": item["productId"]},
            UpdateExpression="SET expiresAt = :e",
            ExpressionAttributeValues={":e": expires},
        )


@app.get("/health")
def health():
    return {"status": "ok"}


# ---------- Públicos (JWT) ----------

@app.get("/cart")
def get_cart(user_id: str = Depends(current_user)):
    return cart_json(query_items(user_id))


@app.post("/cart/items")
def set_item(body: CartItem, user_id: str = Depends(current_user)):
    table.put_item(
        Item={
            "userId": user_id,
            "productId": body.productId,
            "quantity": body.quantity,
            "expiresAt": int(time.time()) + CART_TTL_SECONDS,
        }
    )
    renew(user_id)
    return cart_json(query_items(user_id))


@app.delete("/cart/items/{product_id}", status_code=204)
def delete_item(product_id: str, user_id: str = Depends(current_user)):
    table.delete_item(Key={"userId": user_id, "productId": product_id})
    renew(user_id)
    return Response(status_code=204)


# ---------- Internos (rede do Compose, sem rota no gateway) ----------

@app.get("/internal/cart/{user_id}")
def internal_get_cart(user_id: str):
    return cart_json(query_items(user_id))


@app.delete("/internal/cart/{user_id}", status_code=204)
def internal_clear_cart(user_id: str):
    # DeleteItem item a item (carrinhos são pequenos): usa a mesma permissão IAM
    # do DELETE público, sem depender de BatchWriteItem. Inclui itens vencidos.
    query = {"KeyConditionExpression": Key("userId").eq(user_id), "ProjectionExpression": "productId"}
    resp = table.query(**query)
    keys = resp["Items"]
    while "LastEvaluatedKey" in resp:
        resp = table.query(**query, ExclusiveStartKey=resp["LastEvaluatedKey"])
        keys += resp["Items"]
    for item in keys:
        table.delete_item(Key={"userId": user_id, "productId": item["productId"]})
    log.info("carrinho esvaziado itens=%d", len(keys))
    return Response(status_code=204)
