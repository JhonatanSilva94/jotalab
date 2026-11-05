import logging
import os
import time
from decimal import Decimal

import boto3
from fastapi import FastAPI, HTTPException, Request

# Config 12-factor: tudo vem do ambiente. Credenciais são lidas pelo boto3
# a partir de AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY.
TABLE_NAME = os.environ["TABLE_NAME"]
AWS_REGION = os.environ["AWS_REGION"]

table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("service-products")

app = FastAPI(title="service-products")


def to_json(item: dict) -> dict:
    # DynamoDB devolve Number como Decimal; price é centavos, então vira int.
    return {k: int(v) if isinstance(v, Decimal) else v for k, v in item.items()}


@app.middleware("http")
async def log_request(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    log.info("%s %s %d %.1fms", request.method, request.url.path, response.status_code, ms)
    return response


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/products")
def list_products():
    resp = table.scan()
    items = resp["Items"]
    # Scan pagina a cada 1 MB; segue até o fim.
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items += resp["Items"]
    return [to_json(i) for i in items]


@app.get("/products/{product_id}")
def get_product(product_id: str):
    item = table.get_item(Key={"productId": product_id}).get("Item")
    if item is None:
        raise HTTPException(status_code=404, detail="product not found")
    return to_json(item)
