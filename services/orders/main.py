import logging
import os
import secrets
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal

import boto3
import httpx
import jwt
from boto3.dynamodb.conditions import Key
from fastapi import Depends, FastAPI, Header, HTTPException, Request

TABLE_NAME = os.environ["TABLE_NAME"]
AWS_REGION = os.environ["AWS_REGION"]
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    sys.exit("ERRO: variável de ambiente JWT_SECRET não definida. O service-orders não vai iniciar.")
# Endereços dos outros services: sempre do ambiente, nunca fixos no código.
PRODUCTS_URL = os.environ["PRODUCTS_URL"]
CART_URL = os.environ["CART_URL"]
INVENTORY_URL = os.environ["INVENTORY_URL"]
PAYMENTS_URL = os.environ["PAYMENTS_URL"]

table = boto3.resource("dynamodb", region_name=AWS_REGION).Table(TABLE_NAME)
http = httpx.Client(timeout=3.0)
# Chamadas a inventory e payments são idempotentes (chave = orderId), então
# repetir após um timeout é seguro: no máximo confirma o que já foi feito.
HTTP_ATTEMPTS = 2

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("service-orders")
logging.getLogger("httpx").setLevel(logging.WARNING)  # não repete cada URL interna no log

app = FastAPI(title="service-orders")


class Unavailable(Exception):
    """Outro service não respondeu (timeout, rede) ou respondeu 5xx.
    maybe_processed=False quando NENHUMA tentativa chegou a ser entregue
    (conexão recusada/sem conexão): aí é certo que o outro lado não fez nada."""

    def __init__(self, message: str, maybe_processed: bool = True):
        super().__init__(message)
        self.maybe_processed = maybe_processed


# Falhas na fase de CONEXÃO: a requisição nem saiu daqui.
NOT_SENT = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)


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


def to_json(value):
    # DynamoDB devolve Number como Decimal; aqui todo número é inteiro (centavos, quantidades).
    if isinstance(value, Decimal):
        return int(value)
    if isinstance(value, list):
        return [to_json(v) for v in value]
    if isinstance(value, dict):
        return {k: to_json(v) for k, v in value.items()}
    return value


def call(method: str, url: str, **kwargs) -> httpx.Response:
    path = httpx.URL(url).path
    maybe_processed = False  # alguma tentativa pode ter chegado ao outro service?
    for attempt in range(1, HTTP_ATTEMPTS + 1):
        try:
            response = http.request(method, url, **kwargs)
        except httpx.TransportError as e:  # timeout, conexão recusada…
            problem = type(e).__name__
            maybe_processed = maybe_processed or not isinstance(e, NOT_SENT)
        else:
            if response.status_code < 500:
                return response
            problem = f"HTTP {response.status_code}"
            maybe_processed = True
        if attempt < HTTP_ATTEMPTS:
            log.warning("%s %s: %s, repetindo (tentativa %d de %d)", method, path, problem, attempt + 1, HTTP_ATTEMPTS)
            time.sleep(0.2)
    raise Unavailable(f"{method} {path}: {problem}", maybe_processed)


def new_order_id() -> str:
    # Ordenável por tempo (o sort key ordena como texto): o-20261105T143012123Z-a1b2c3
    now = datetime.now(timezone.utc)
    return f"o-{now:%Y%m%dT%H%M%S}{now.microsecond // 1000:03d}Z-{secrets.token_hex(3)}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def set_status(user_id: str, order_id: str, status: str, **extra) -> dict:
    fields = {"status": status, "updatedAt": now_iso(), **extra}
    resp = table.update_item(
        Key={"userId": user_id, "orderId": order_id},
        UpdateExpression="SET " + ", ".join(f"#{k} = :{k}" for k in fields),
        ExpressionAttributeNames={f"#{k}": k for k in fields},
        ExpressionAttributeValues={f":{k}": v for k, v in fields.items()},
        ReturnValues="ALL_NEW",
    )
    return to_json(resp["Attributes"])


def release_all(order_id: str, reserved: list[tuple[str, int]]) -> None:
    # Compensação: devolve ao estoque tudo que este pedido PODE estar segurando.
    # O release é por orderId: se a reserva nunca aconteceu, não mexe em nada
    # (e bloqueia uma reserva atrasada), então liberar "na dúvida" é seguro.
    while reserved:
        product_id, qty = reserved.pop()
        try:
            r = call("POST", f"{INVENTORY_URL}/inventory/{product_id}/release", json={"orderId": order_id, "quantity": qty})
            ok = r.status_code == 200
        except Unavailable:
            ok = False
        if ok:
            log.info("order=%s etapa=release produto=%s qtd=%d", order_id, product_id, qty)
        else:
            log.error("order=%s etapa=release FALHOU produto=%s qtd=%d (reconciliar estoque)", order_id, product_id, qty)


def clear_cart(order_id: str, user_id: str) -> None:
    # Melhor esforço: o pedido já existe, então uma falha aqui só vira log.
    try:
        r = call("DELETE", f"{CART_URL}/internal/cart/{user_id}")
        if r.status_code != 204:
            raise Unavailable(f"cart HTTP {r.status_code}")
        log.info("order=%s etapa=carrinho-esvaziado", order_id)
    except Unavailable as e:
        log.error("order=%s etapa=carrinho-esvaziado FALHOU: %s", order_id, e)


def void_payment(order_id: str) -> dict | None:
    """Anula a cobrança se ela ainda não existe; senão devolve o status real.
    None = payments não respondeu nem ao void."""
    try:
        r = call("POST", f"{PAYMENTS_URL}/payments/{order_id}/void")
    except Unavailable:
        return None
    if r.status_code != 200:
        return None
    payment = r.json()
    log.info("order=%s etapa=void status=%s", order_id, payment["status"])
    return payment


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/orders", status_code=201)
def checkout(user_id: str = Depends(current_user)):
    order_id = new_order_id()
    reserved: list[tuple[str, int]] = []  # itens possivelmente reservados, ainda não confirmados nem liberados
    saved_status = None  # status gravado no DynamoDB (None = pedido ainda não gravado)
    try:
        # 1. Carrinho
        r = call("GET", f"{CART_URL}/internal/cart/{user_id}")
        if r.status_code != 200:
            raise Unavailable(f"cart HTTP {r.status_code}")
        cart = r.json()["items"]
        log.info("order=%s etapa=carrinho itens=%d", order_id, len(cart))
        if not cart:
            raise HTTPException(status_code=400, detail="carrinho vazio")

        # 2. Preços SEMPRE do service-products (nunca do cliente)
        items = []
        for c in cart:
            r = call("GET", f"{PRODUCTS_URL}/products/{c['productId']}")
            if r.status_code == 404:
                raise HTTPException(status_code=409, detail=f"produto {c['productId']} não existe mais")
            if r.status_code != 200:
                raise Unavailable(f"products HTTP {r.status_code}")
            product = r.json()
            price = int(product["price"])
            items.append({
                "productId": c["productId"],
                "name": product.get("name", ""),
                "quantity": int(c["quantity"]),
                "unitPrice": price,
                "subtotal": price * int(c["quantity"]),
            })
        total = sum(i["subtotal"] for i in items)
        log.info("order=%s etapa=precos total=%d", order_id, total)

        # 3. Reserva (se uma falhar, o finally libera as anteriores).
        # Entra na lista ANTES da chamada: se ela estourar o timeout, não dá
        # para saber se a reserva aconteceu, então ela também será liberada.
        for i in items:
            reserved.append((i["productId"], i["quantity"]))
            r = call("POST", f"{INVENTORY_URL}/inventory/{i['productId']}/reserve",
                     json={"orderId": order_id, "quantity": i["quantity"]})
            if r.status_code in (404, 409):
                reserved.pop()  # resposta definitiva: nada foi reservado
                log.info("order=%s etapa=reserva SEM_ESTOQUE produto=%s", order_id, i["productId"])
                raise HTTPException(status_code=409, detail=f"estoque insuficiente para {i['productId']}")
            if r.status_code != 200:
                raise Unavailable(f"inventory HTTP {r.status_code}")
            log.info("order=%s etapa=reserva produto=%s qtd=%d", order_id, i["productId"], i["quantity"])

        # 4. Pedido PENDING
        order = {
            "userId": user_id,
            "orderId": order_id,
            "status": "PENDING",
            "items": items,
            "total": total,
            "createdAt": now_iso(),
        }
        table.put_item(Item=order)
        saved_status = "PENDING"
        log.info("order=%s etapa=pedido status=PENDING", order_id)

        # 5. Pagamento (simulado)
        # Um pagamento por pedido (paymentId = pay-<orderId>): repetir não cobra de novo.
        try:
            r = call("POST", f"{PAYMENTS_URL}/payments", json={"orderId": order_id, "userId": user_id, "amount": total})
            if r.status_code not in (200, 201):  # 200 = pagamento já existia (repetição)
                raise Unavailable(f"payments HTTP {r.status_code}")
            payment = r.json()
        except Unavailable as e:
            if not e.maybe_processed:
                # Nenhuma tentativa chegou ao payments (ex.: service parado):
                # com certeza não houve cobrança. Cancela normalmente (finally).
                log.error("order=%s etapa=pagamento NAO_ENTREGUE (%s): sem cobrança, cancelando", order_id, e)
                raise
            # Sem resposta: a cobrança pode ter acontecido ou não. O void decide.
            log.error("order=%s etapa=pagamento SEM_RESPOSTA (%s): tentando void", order_id, e)
            payment = void_payment(order_id)
            if payment is None:
                # Nem o void respondeu: não dá para saber se houve cobrança. Cancelar
                # poderia cobrar por um pedido cancelado; liberar o estoque poderia
                # vender o que já foi pago. O pedido fica PENDING, reservado, para conferência.
                reserved.clear()
                set_status(user_id, order_id, "PENDING", reconcile=True)
                saved_status = "PENDING_RECONCILE"
                log.error("order=%s etapa=void SEM_RESPOSTA: pedido PENDING com estoque reservado (reconciliar)", order_id)
                # Os itens agora pertencem a este pedido: deixá-los no carrinho
                # faria o cliente comprar tudo de novo.
                clear_cart(order_id, user_id)
                raise HTTPException(
                    status_code=503,
                    detail=f"não conseguimos confirmar o pagamento; o pedido {order_id} ficou pendente para conferência",
                )
            if payment["status"] == "VOIDED":
                raise e  # garantido que não houve cobrança: desfaz tudo (finally) e responde 503
            # APPROVED ou DECLINED: a cobrança chegou; segue o fluxo normal.
        log.info("order=%s etapa=pagamento status=%s payment=%s", order_id, payment["status"], payment["paymentId"])

        # 6. Recusado: devolve o estoque e marca PAYMENT_FAILED
        if payment["status"] != "APPROVED":
            release_all(order_id, reserved)
            set_status(user_id, order_id, "PAYMENT_FAILED", paymentId=payment["paymentId"])
            saved_status = "PAYMENT_FAILED"
            log.info("order=%s etapa=pedido status=PAYMENT_FAILED", order_id)
            raise HTTPException(status_code=402, detail="pagamento recusado")

        # 7. Aprovado: confirma a venda no estoque. O dinheiro já foi cobrado,
        # então daqui em diante nada é desfeito: falhas só viram log para reconciliar.
        saved_status = "APPROVED"  # impede o finally de cancelar um pedido já cobrado
        while reserved:
            product_id, qty = reserved.pop()
            try:
                r = call("POST", f"{INVENTORY_URL}/inventory/{product_id}/commit", json={"orderId": order_id, "quantity": qty})
                ok = r.status_code == 200
            except Unavailable:
                ok = False
            if ok:
                log.info("order=%s etapa=commit produto=%s qtd=%d", order_id, product_id, qty)
            else:
                log.error("order=%s etapa=commit FALHOU produto=%s qtd=%d (reconciliar estoque)", order_id, product_id, qty)

        order = set_status(user_id, order_id, "PAID", paymentId=payment["paymentId"])
        saved_status = "PAID"
        log.info("order=%s etapa=pedido status=PAID", order_id)

        clear_cart(order_id, user_id)
        return order

    except Unavailable as e:
        log.error("order=%s service indisponível: %s", order_id, e)
        raise HTTPException(status_code=503, detail="serviço temporariamente indisponível, tente de novo")

    finally:
        # Desfaz o que sobrou em qualquer saída com erro (409, 503, 500…).
        if reserved:
            release_all(order_id, reserved)
        if saved_status == "PENDING":
            try:
                set_status(user_id, order_id, "CANCELLED")
                log.info("order=%s etapa=pedido status=CANCELLED", order_id)
            except Exception:
                log.exception("order=%s etapa=pedido FALHOU ao cancelar", order_id)


@app.get("/orders")
def list_orders(user_id: str = Depends(current_user)):
    query = {"KeyConditionExpression": Key("userId").eq(user_id), "ScanIndexForward": False}
    resp = table.query(**query)
    items = resp["Items"]
    while "LastEvaluatedKey" in resp:
        resp = table.query(**query, ExclusiveStartKey=resp["LastEvaluatedKey"])
        items += resp["Items"]
    return to_json(items)


@app.get("/orders/{order_id}")
def get_order(order_id: str, user_id: str = Depends(current_user)):
    # A chave inclui o userId do TOKEN: um usuário nunca lê o pedido de outro.
    item = table.get_item(Key={"userId": user_id, "orderId": order_id}).get("Item")
    if item is None:
        raise HTTPException(status_code=404, detail="pedido não encontrado")
    return to_json(item)
