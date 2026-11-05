import logging
import os
import time

import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field

# Service INTERNO: sem rota no gateway, só os outros services o chamam.
TABLE_NAME = os.environ["TABLE_NAME"]
AWS_REGION = os.environ["AWS_REGION"]

table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("service-inventory")

app = FastAPI(title="service-inventory")

# Cada item guarda as reservas por pedido: reservations = {orderId: quantity}.
# Isso torna reserve/release/commit idempotentes (repetir não duplica).
# Valor 0 = "lápide": o pedido foi liberado sem nunca ter reservado; impede
# que uma reserva atrasada (ex.: chegou depois de um timeout) seja aplicada.
TOMBSTONE = 0


class Movement(BaseModel):
    orderId: str = Field(min_length=1, max_length=64)
    quantity: int = Field(gt=0)


def to_json(item: dict) -> dict:
    return {"productId": item["productId"], "available": int(item["available"]), "reserved": int(item["reserved"])}


@app.middleware("http")
async def log_request(request: Request, call_next):
    start = time.perf_counter()
    status = 500
    try:
        response = await call_next(request)
        status = response.status_code
        return response
    finally:
        ms = (time.perf_counter() - start) * 1000
        log.info("%s %s %d %.1fms", request.method, request.url.path, status, ms)


def get_item(product_id: str) -> dict:
    item = table.get_item(Key={"productId": product_id}, ConsistentRead=True).get("Item")
    if item is None:
        raise HTTPException(status_code=404, detail="product not found")
    return item


def update(product_id: str, order_id: str, quantity: int, expression: str, condition: str) -> dict | None:
    """UpdateItem condicional. Devolve o item novo, ou None se a condição falhou."""
    names = {"#a": "available", "#r": "reserved", "#m": "reservations", "#o": order_id}
    text = expression + condition
    kwargs = dict(
        Key={"productId": product_id},
        UpdateExpression=expression,
        ConditionExpression=condition,
        # Só os nomes/valores usados: o DynamoDB recusa os que sobram.
        ExpressionAttributeNames={k: v for k, v in names.items() if k in text},
        ReturnValues="ALL_NEW",
    )
    values = {k: v for k, v in {":q": quantity, ":z": TOMBSTONE}.items() if k in text}
    if values:
        kwargs["ExpressionAttributeValues"] = values
    for attempt in range(2):
        try:
            return table.update_item(**kwargs)["Attributes"]
        except ClientError as e:
            code = e.response["Error"]["Code"]
            if code == "ConditionalCheckFailedException":
                return None
            # Itens antigos ainda sem o mapa "reservations": cria vazio e tenta de novo.
            if code == "ValidationException" and attempt == 0 and "document path" in str(e):
                try:
                    table.update_item(
                        Key={"productId": product_id},
                        UpdateExpression="SET reservations = :empty",
                        ConditionExpression="attribute_exists(productId) AND attribute_not_exists(reservations)",
                        ExpressionAttributeValues={":empty": {}},
                    )
                except ClientError as e2:
                    if e2.response["Error"]["Code"] != "ConditionalCheckFailedException":
                        raise
                continue
            raise
    return None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/inventory/{product_id}")
def get_inventory(product_id: str):
    return to_json(get_item(product_id))


@app.post("/inventory/{product_id}/reserve")
def reserve(product_id: str, body: Movement):
    item = update(
        product_id, body.orderId, body.quantity,
        "SET #a = #a - :q, #r = #r + :q, #m.#o = :q",
        "attribute_exists(productId) AND #a >= :q AND attribute_not_exists(#m.#o)",
    )
    if item:
        return to_json(item)
    # A condição falhou: descobre por quê.
    item = get_item(product_id)
    existing = item.get("reservations", {}).get(body.orderId)
    if existing is not None and int(existing) == body.quantity:
        return to_json(item)  # repetição da mesma reserva: nada muda
    if existing is not None:
        raise HTTPException(status_code=409, detail="order already released or reserved with another quantity")
    raise HTTPException(status_code=409, detail="insufficient quantity")


@app.post("/inventory/{product_id}/release")
def release(product_id: str, body: Movement):
    item = update(
        product_id, body.orderId, body.quantity,
        "SET #a = #a + :q, #r = #r - :q REMOVE #m.#o",
        "#m.#o = :q",
    )
    if item:
        return to_json(item)
    item = get_item(product_id)
    existing = item.get("reservations", {}).get(body.orderId)
    if existing is None:
        # Nunca reservou (ou a reserva ainda está a caminho): grava a lápide.
        item = update(product_id, body.orderId, body.quantity, "SET #m.#o = :z", "attribute_not_exists(#m.#o)")
        if item:
            return to_json(item)
        return release(product_id, body)  # a reserva chegou neste meio-tempo: libera ela
    if int(existing) == TOMBSTONE:
        return to_json(item)  # já liberado antes
    raise HTTPException(status_code=409, detail="reservation has another quantity")


@app.post("/inventory/{product_id}/commit")
def commit(product_id: str, body: Movement):
    # Venda confirmada: a unidade reservada sai de vez do estoque.
    item = update(
        product_id, body.orderId, body.quantity,
        "SET #r = #r - :q REMOVE #m.#o",
        "#m.#o = :q",
    )
    if item:
        return to_json(item)
    item = get_item(product_id)
    existing = item.get("reservations", {}).get(body.orderId)
    if existing is None:
        return to_json(item)  # já confirmado antes (repetição)
    raise HTTPException(status_code=409, detail="no active reservation for this order")
