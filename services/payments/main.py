import logging
import os
import random
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.exceptions import ClientError
from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import BaseModel, Field

# Service INTERNO e SIMULADO: nenhum dado de cartão entra aqui.
TABLE_NAME = os.environ["TABLE_NAME"]
AWS_REGION = os.environ["AWS_REGION"]
# Probabilidade de recusa (0.0 = sempre aprova, 1.0 = sempre recusa).
PAYMENT_FAILURE_RATE = float(os.environ.get("PAYMENT_FAILURE_RATE", "0.0"))
if not 0.0 <= PAYMENT_FAILURE_RATE <= 1.0:
    sys.exit("ERRO: PAYMENT_FAILURE_RATE precisa estar entre 0.0 e 1.0.")

table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("service-payments")

app = FastAPI(title="service-payments")


class NewPayment(BaseModel):
    orderId: str = Field(min_length=1, max_length=64)
    userId: str = Field(min_length=1, max_length=64)
    amount: int = Field(gt=0)  # centavos


def to_json(item: dict) -> dict:
    return {k: int(v) if isinstance(v, Decimal) else v for k, v in item.items()}


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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/payments", status_code=201)
def create_payment(body: NewPayment, response: Response):
    # Um pagamento por pedido: o id vem do orderId e a gravação é condicional.
    # Se o orders repetir a chamada (ex.: após um timeout), devolve o pagamento
    # que já existe em vez de cobrar de novo.
    payment_id = f"pay-{body.orderId}"
    status = "DECLINED" if random.random() < PAYMENT_FAILURE_RATE else "APPROVED"
    try:
        table.put_item(
            Item={
                "paymentId": payment_id,
                "orderId": body.orderId,
                "userId": body.userId,
                "amount": body.amount,
                "status": status,
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ConditionExpression="attribute_not_exists(paymentId)",
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        existing = table.get_item(Key={"paymentId": payment_id}, ConsistentRead=True)["Item"]
        if existing["status"] == "VOIDED":
            # O orders desistiu deste pedido antes da cobrança chegar: não cobra.
            log.info("payment=%s order=%s cobrança recusada: pedido anulado", payment_id, body.orderId)
            raise HTTPException(status_code=409, detail="payment voided for this order")
        if existing["userId"] != body.userId or int(existing["amount"]) != body.amount:
            raise HTTPException(status_code=409, detail="payment for this order exists with other data")
        log.info("payment=%s order=%s status=%s (repetido, sem nova cobrança)", payment_id, body.orderId, existing["status"])
        response.status_code = 200
        return {"paymentId": payment_id, "status": existing["status"]}
    log.info("payment=%s order=%s status=%s", payment_id, body.orderId, status)
    return {"paymentId": payment_id, "status": status}


@app.post("/payments/{order_id}/void")
def void_payment(order_id: str):
    """Chamado pelo orders quando desiste de um pedido sem saber se a cobrança
    aconteceu. Se ainda não há pagamento, grava VOIDED: uma cobrança atrasada
    vai encontrar esse registro e não cobra. Se já há, devolve o status real."""
    payment_id = f"pay-{order_id}"
    try:
        table.put_item(
            Item={
                "paymentId": payment_id,
                "orderId": order_id,
                "status": "VOIDED",
                "createdAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            },
            ConditionExpression="attribute_not_exists(paymentId)",
        )
        status = "VOIDED"
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        status = table.get_item(Key={"paymentId": payment_id}, ConsistentRead=True)["Item"]["status"]
    log.info("payment=%s order=%s void status=%s", payment_id, order_id, status)
    return {"paymentId": payment_id, "status": status}


@app.get("/payments/{payment_id}")
def get_payment(payment_id: str):
    item = table.get_item(Key={"paymentId": payment_id}).get("Item")
    if item is None:
        raise HTTPException(status_code=404, detail="payment not found")
    return to_json(item)
