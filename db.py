import os
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import date

def get_db_connection():
    conn = psycopg2.connect(
        dbname=os.environ.get("DB_NAME"),
        user=os.environ.get("DB_USER"),
        password=os.environ.get("DB_PASSWORD"),
        host=os.environ.get("DB_HOST"),
        port=os.environ.get("DB_PORT", 5432)
    )
    return conn

def get_pedidos(data_ini=None, data_fim=None, f_pedido=None, f_cliente=None, f_nota=None, f_status=None,
                limit=None, offset=None, situacoes=None):
    conn = get_db_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)
    
    query = """
        SELECT
            p.id,
            p.n_pedido AS "Pedido",
            s.descricao AS "status_descricao",
            s.cor AS "status_cor",
            n.n_nota AS "Nota_Fiscal",
            n.nome_cliente AS "Cliente",
            p.data_pedido,
            p.data_expedicao,
            p.data_previsao,
            p.data_entrega,
            p.transportadora,
            p.cod_rastreamento,
            p.frete,
            sit.situacao AS "situacao_comercial"
        FROM pedidos_teste p
        LEFT JOIN status_logistico_teste s ON p.status_logistico_id = s.id
        LEFT JOIN notas_fiscais_teste n ON p.id_nf = n.id
        LEFT JOIN situacoes sit ON p.id_situacao = sit.id
        WHERE 1=1
    """

    params = []

    if situacoes:
        query += " AND sit.situacao = ANY(%s)"
        params.append(situacoes)

    if f_pedido:
        query += " AND CAST(p.n_pedido AS TEXT) ILIKE %s"
        params.append(f"%{f_pedido}%")
    if f_cliente:
        query += " AND n.nome_cliente ILIKE %s"
        params.append(f"%{f_cliente}%")
    if f_nota:
        query += " AND CAST(n.n_nota AS TEXT) ILIKE %s"
        params.append(f"%{f_nota}%")
    if f_status and f_status != "Todos":
        query += " AND s.descricao = %s"
        params.append(f_status)
    if data_ini and data_fim:
        query += " AND p.data_pedido BETWEEN %s AND %s"
        params.extend([data_ini, data_fim])

    query += " ORDER BY p.data_pedido DESC, p.id DESC"

    if limit is not None and offset is not None:
        query += " LIMIT %s OFFSET %s"
        params.extend([limit, offset])

    cur.execute(query, params)
    pedidos = cur.fetchall()
    cur.close()
    conn.close()
    return pedidos
