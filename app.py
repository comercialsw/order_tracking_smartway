from flask import Flask, render_template, request, redirect, url_for, session, flash, send_file
from forms import EditPedidoForm
import os
import io
from db import get_pedidos, get_db_connection
from werkzeug.security import check_password_hash, generate_password_hash
from psycopg2.extras import RealDictCursor
from datetime import date
import pandas as pd
from urllib.parse import urlencode
from math import ceil
from functools import wraps

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'usuario' not in session:
            flash("Por favor, faça login.", "warning")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('perfil') != 'admin':
            flash("Acesso restrito a administradores.", "danger")
            return redirect(url_for('order_tracking'))
        return f(*args, **kwargs)
    return decorated_function

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'default-insecure-key')

def count_pedidos(data_ini=None, data_fim=None, f_pedido=None, f_cliente=None, f_nota=None, f_status=None,
                  situacoes=None):
    conn = get_db_connection()
    cur = conn.cursor()

    query = """
        SELECT COUNT(*)
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

    cur.execute(query, params)
    total = cur.fetchone()[0]
    cur.close()
    conn.close()
    return total

def registrar_log_alteracao(conn, pedido_id, numero_pedido, campo, valor_antigo, valor_novo, usuario):
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO log_pedidos (id_pedido, numero_pedido, campo, valor_antigo, valor_novo, usuario)
        VALUES (%s, %s, %s, %s, %s, %s)
    """, (pedido_id, numero_pedido, campo, valor_antigo, valor_novo, usuario))
    conn.commit()
    cur.close()

def processa_pedidos(pedidos):
    hoje = date.today()
    for p in pedidos:
        p['entrega_atrasada'] = False
        if (p.get('status_descricao') != 'Entregue'
            and p.get('data_entrega')
            and p['data_entrega'] < date.today()):
            p['entrega_atrasada'] = True
        else:
            situacao = (p.get('situacao_comercial') or '').strip().lower()
            if situacao in ['atendido', '02 faturado mmvb']:
                if not p.get('status_descricao') or p.get('status_descricao') == 'Aguardando Envio':
                    p['status_descricao'] = 'Aguardando Envio'
            p['expedicao_atrasada'] = False
            status = p.get('status_descricao')
            if (
                status == 'Aguardando Envio'
                and p.get('data_expedicao')
                and str(p.get('data_expedicao')).strip() not in ['', 'None', 'null']
            ):
                data_exp = p['data_expedicao']
                if isinstance(data_exp, str):
                    try:
                        data_exp = pd.to_datetime(data_exp).date()
                    except:
                        data_exp = None
                if data_exp and data_exp < hoje:
                    p['expedicao_atrasada'] = True
            if p.get('status_descricao') == 'Aguardando Envio' and p.get('data_previsao'):
                data_prev = p['data_previsao']
                if isinstance(data_prev, str):
                    data_prev = pd.to_datetime(data_prev).date()
                if data_prev and data_prev < hoje:
                    p['status_descricao'] = 'Atrasado'
                    p['expedicao_atrasada'] = False
    return pedidos

def contar_pedidos_atrasados():
    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*) FROM pedidos_teste p
        JOIN status_logistico_teste s ON p.status_logistico_id = s.id
        WHERE s.descricao != 'Entregue'
          AND p.data_entrega < CURRENT_DATE
          AND p.data_entrega IS NOT NULL
    """)
    atraso_entrega = cur.fetchone()[0]
    cur.execute("""
        SELECT COUNT(*) FROM pedidos_teste p
        JOIN status_logistico_teste s ON p.status_logistico_id = s.id
        WHERE s.descricao != 'Entregue'
          AND p.data_expedicao < CURRENT_DATE
          AND p.data_expedicao IS NOT NULL
    """)
    atraso_expedicao = cur.fetchone()[0]
    cur.close()
    conn.close()
    return atraso_entrega, atraso_expedicao

@app.route('/', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        senha = request.form.get('senha')
        conn = get_db_connection()
        cur = conn.cursor(cursor_factory=RealDictCursor)
        cur.execute("SELECT * FROM usuarios WHERE email = %s AND ativo = TRUE", (email,))
        usuario = cur.fetchone()
        cur.close()
        conn.close()
        if usuario and check_password_hash(usuario['senha'], senha):
            session['usuario'] = usuario['nome']
            session['email'] = usuario['email']
            session['perfil'] = usuario['perfil']
            flash("Login realizado com sucesso!", "success")
            return redirect(url_for('order_tracking'))
        else:
            flash("Usuário ou senha inválidos.", "danger")
    return render_template('login.html')

@app.route('/order_tracking', methods=['GET', 'POST'])
@login_required
def order_tracking():
    form = EditPedidoForm()
    form.status_logistico_id.choices = [
        (1, 'Aguardando Envio'),
        (2, 'Em Trânsito'),
        (3, 'Entregue'),
        (4, 'Atrasado'),
    ]
    page = request.args.get('page', 1, type=int)
    per_page = 12
    offset = (page - 1) * per_page
    situacoes_desejadas = ['Atendido', '02 Faturado MMVB', 'Em aberto']
    f_pedido = request.values.get('f_pedido', '')
    f_cliente = request.values.get('f_cliente', '')
    f_status = request.values.get('f_status', 'Todos')
    f_data_ini = request.values.get('f_data_ini', '')
    f_data_fim = request.values.get('f_data_fim', '')
    pedidos = get_pedidos(
        data_ini=f_data_ini, data_fim=f_data_fim,
        f_pedido=f_pedido, f_cliente=f_cliente, f_status=f_status,
        limit=per_page, offset=offset,
        situacoes=situacoes_desejadas
    )
    total_count = count_pedidos(f_pedido=f_pedido, f_cliente=f_cliente, f_status=f_status, 
                                data_ini=f_data_ini, data_fim=f_data_fim)
    total_pages = (total_count + per_page - 1) // per_page
    pedidos = processa_pedidos(pedidos)
    atraso_entrega, atraso_expedicao = contar_pedidos_atrasados()
    usuario_perfil = session.get('perfil', 'visualizador')
    atraso_entrega = atraso_expedicao = 0
    if usuario_perfil in ['admin', 'editor']:
        atraso_entrega = sum(
            1 for p in pedidos 
            if p.get('status_descricao') == 'Atrasado' 
               or (p.get('data_entrega') and p.get('data_entrega') < date.today())
        )
        atraso_expedicao = sum(
            1 for p in pedidos
            if p.get('status_descricao') == 'Aguardando Envio' and p.get('expedicao_atrasada', False)
        )
    return render_template(
        'order_tracking.html',
        pedidos=pedidos,
        filtros={'f_pedido': f_pedido, 'f_cliente': f_cliente, 'f_status': f_status,
                 'f_data_ini': f_data_ini, 'f_data_fim': f_data_fim},
        form=form,
        active_page = 'order_tracking',
        atraso_entrega=atraso_entrega,
        atraso_expedicao=atraso_expedicao,
        page=page,
        total_pages = total_pages
    )


@app.route('/pedidos')
@login_required
def pedidos_tabela():
    page = int(request.args.get('page', 1))
    per_page = 30
    offset = (page - 1) * per_page

    f_pedido = request.args.get('f_pedido', '')
    f_cliente = request.args.get('f_cliente', '')
    f_status = request.args.get('f_status', 'Todos')
    f_data_ini = request.args.get('f_data_ini', '')
    f_data_fim = request.args.get('f_data_fim', '')

    situacoes_permitidas = ['Em aberto', '01 E-Bikes']  # Ajustado nome igual ao do banco

    pedidos = get_pedidos(
        f_pedido=f_pedido,
        f_cliente=f_cliente,
        f_status=f_status,
        data_ini=f_data_ini,
        data_fim=f_data_fim,
        situacoes=situacoes_permitidas,
        limit=per_page,
        offset=offset
    )

    total_count = count_pedidos(
        f_pedido=f_pedido,
        f_cliente=f_cliente,
        f_status=f_status,
        data_ini=f_data_ini,
        data_fim=f_data_fim,
        situacoes=situacoes_permitidas
    )

    total_pages = (total_count + per_page - 1) // per_page

    return render_template(
        'pedidos_tabela.html',
        pedidos=pedidos,
        filtros={'f_pedido': f_pedido, 'f_cliente': f_cliente, 'f_status': f_status,
                 'f_data_ini': f_data_ini, 'f_data_fim': f_data_fim},
        page=page,
        total_pages=total_pages,
        active_page='pedidos_tabela'
    )




@app.route('/editar_pedido', methods=['POST'])
@login_required
def editar_pedido():
    form = EditPedidoForm()
    form.status_logistico_id.choices = [
        (1, 'Aguardando Envio'),
        (2, 'Em Trânsito'),
        (3, 'Entregue'),
        (4, 'Atrasado'),
    ]
    if not form.validate_on_submit():
        flash("Formulário inválido!", "danger")
        return redirect(url_for('order_tracking'))
    
    if 'usuario' not in session:
        flash("Faça login primeiro!", "danger")
        return redirect(url_for('login'))

    if session.get('perfil') not in ['admin', 'editor']:
        flash("Você não tem permissão para editar!", "danger")
        return redirect(url_for('order_tracking'))
    
    pedido_id = request.form.get('id')
    status_id = request.form.get('status_logistico_id')
    data_exp = request.form.get('data_expedicao') or None
    data_prev = request.form.get('data_previsao') or None
    data_entr = request.form.get('data_entrega') or None
    transp = request.form.get('transportadora') or None
    rast = request.form.get('cod_rastreamento') or None
    frete = request.form.get('frete') or None

    conn = get_db_connection()
    cur = conn.cursor()

    # Buscar valor antigo de data_expedicao e número do pedido
    cur.execute("SELECT data_expedicao, n_pedido FROM pedidos_teste WHERE id=%s", (pedido_id,))
    result = cur.fetchone()
    data_exp_antiga = result[0] if result else None
    numero_pedido = result[1] if result else None

    # Atualizar pedido
    cur.execute("""
        UPDATE pedidos_teste SET
            status_logistico_id = %s,
            data_expedicao = %s,
            data_previsao = %s,
            data_entrega = %s,
            transportadora = %s,
            cod_rastreamento = %s,
            frete = %s
        WHERE id = %s
    """, (
        status_id, data_exp, data_prev, data_entr,
        transp, rast, frete, pedido_id
    ))

    # Registrar log se data_expedicao mudou
    if str(data_exp_antiga or '') != str(data_exp or ''):
        registrar_log_alteracao(
            conn,
            pedido_id,
            numero_pedido,
            'data_expedicao',
            str(data_exp_antiga),
            str(data_exp),
            session.get('usuario', 'desconhecido')
        )

    conn.commit()
    cur.close()
    conn.close()

    flash("Pedido atualizado com sucesso!", "success")

    # --- Mantendo filtros atuais após update ---
    filtros = {
        'f_pedido': request.form.get('f_pedido', ''),
        'f_cliente': request.form.get('f_cliente', ''),
        'f_status': request.form.get('f_status', 'Todos'),
        'f_data_ini': request.form.get('f_data_ini', ''),
        'f_data_fim': request.form.get('f_data_fim', '')
    }
    query_string = urlencode(filtros)
    return redirect(url_for('order_tracking') + '?' + query_string)


@app.route('/exportar_pedidos')
@login_required
def exportar_pedidos():
    # Recebe filtros
    f_pedido = request.args.get('f_pedido', '')
    f_cliente = request.args.get('f_cliente', '')
    f_status = request.args.get('f_status', 'Todos')
    f_data_ini = request.args.get('f_data_ini', '')
    f_data_fim = request.args.get('f_data_fim', '')

    # Busca pedidos filtrados
    pedidos = get_pedidos(
        data_ini=f_data_ini, data_fim=f_data_fim,
        f_pedido=f_pedido, f_cliente=f_cliente, f_status=f_status,
        limit=None, offset=None
    )
    pedidos = processa_pedidos(pedidos)

    # Criar DataFrame e remover colunas desnecessárias
    df = pd.DataFrame(pedidos)

    colunas_para_remover = ['status_cor', 'entrega_atrasada', 'expedicao_atrasada', 'id']
    for col in colunas_para_remover:
        if col in df.columns:
            df.drop(columns=col, inplace=True)

    df = df[['Cliente',
    'Nota_Fiscal',
    'data_pedido',
    'data_expedicao',
    'data_previsao',
    'data_entrega',
    'transportadora',
    'cod_rastreamento',
    'frete',
    'status_descricao',
    'situacao_comercial'
    ]]

    # Formatar datas para string dd/mm/yyyy
    for col in ['data_pedido', 'data_expedicao', 'data_previsao', 'data_entrega']:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%d/%m/%Y')

    # Gerar Excel em memória
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='openpyxl') as writer:
        df.to_excel(writer, index=False, sheet_name='Pedidos')
    output.seek(0)

    # Enviar arquivo para download
    return send_file(
        output,
        mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        as_attachment=True,
        download_name='pedidos_exportados.xlsx'
    )


USUARIOS_POR_PAGINA = 10

def admin_required():
    if session.get('perfil') != 'admin':
        flash("Acesso negado: somente administradores.", "danger")
        return False
    return True

@app.route('/usuarios')
@login_required
def listar_usuarios():
    if not admin_required():
        return redirect(url_for('order_tracking'))

    pagina = request.args.get('pagina', 1, type=int)
    offset = (pagina - 1) * USUARIOS_POR_PAGINA

    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("SELECT COUNT(*) FROM usuarios")
    total = cur.fetchone()[0]

    cur.execute("""
        SELECT id, nome, email, perfil, ativo
        FROM usuarios
        ORDER BY nome
        LIMIT %s OFFSET %s
    """, (USUARIOS_POR_PAGINA, offset))
    usuarios = cur.fetchall()

    cur.close()
    conn.close()

    total_paginas = ceil(total / USUARIOS_POR_PAGINA)

    return render_template('usuarios_listar.html',
                           usuarios=usuarios,
                           pagina=pagina,
                           total_paginas=total_paginas,
                           active_page='usuarios')

@app.route('/usuarios/novo', methods=['GET', 'POST'])
@login_required
def criar_usuario():
    if not admin_required():
        return redirect(url_for('order_tracking'))

    if request.method == 'POST':
        nome = request.form.get('nome')
        email = request.form.get('email')
        senha = request.form.get('senha')
        perfil = request.form.get('perfil')
        ativo = True

        if not (nome and email and senha and perfil):
            flash("Todos os campos são obrigatórios.", "danger")
            return redirect(url_for('criar_usuario'))

        senha_hash = generate_password_hash(senha)

        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO usuarios (nome, email, senha, perfil, ativo)
            VALUES (%s, %s, %s, %s, %s)
        """, (nome, email, senha_hash, perfil, ativo))
        conn.commit()
        cur.close()
        conn.close()

        flash("Usuário criado com sucesso!", "success")
        return redirect(url_for('listar_usuarios'))

    return render_template('usuarios_form.html', action='novo', active_page='usuarios')

@app.route('/usuarios/editar/<int:id>', methods=['GET', 'POST'])
@login_required
def editar_usuario(id):
    if not admin_required():
        return redirect(url_for('order_tracking'))

    conn = get_db_connection()
    cur = conn.cursor()

    if request.method == 'POST':
        nome = request.form.get('nome')
        email = request.form.get('email')
        senha = request.form.get('senha')
        perfil = request.form.get('perfil')
        ativo = request.form.get('ativo') == 'on'

        if not (nome and email and perfil):
            flash("Nome, email e perfil são obrigatórios.", "danger")
            return redirect(url_for('editar_usuario', id=id))

        if senha:
            senha_hash = generate_password_hash(senha)
            cur.execute("""
                UPDATE usuarios SET nome=%s, email=%s, senha=%s, perfil=%s, ativo=%s WHERE id=%s
            """, (nome, email, senha_hash, perfil, ativo, id))
        else:
            cur.execute("""
                UPDATE usuarios SET nome=%s, email=%s, perfil=%s, ativo=%s WHERE id=%s
            """, (nome, email, perfil, ativo, id))
        conn.commit()
        cur.close()
        conn.close()

        flash("Usuário atualizado com sucesso!", "success")
        return redirect(url_for('listar_usuarios'))

    # GET - buscar dados para preencher o formulário
    cur.execute("SELECT nome, email, perfil, ativo FROM usuarios WHERE id=%s", (id,))
    usuario = cur.fetchone()
    cur.close()
    conn.close()

    if not usuario:
        flash("Usuário não encontrado.", "danger")
        return redirect(url_for('listar_usuarios'))

    return render_template('usuarios_form.html', usuario=usuario, action='editar', active_page='usuarios')

@app.route('/usuarios/excluir/<int:id>', methods=['POST'])
@login_required
def excluir_usuario(id):
    if not admin_required():
        return redirect(url_for('order_tracking'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM usuarios WHERE id=%s", (id,))
    conn.commit()
    cur.close()
    conn.close()

    flash("Usuário excluído com sucesso!", "success")
    return redirect(url_for('listar_usuarios'))

@app.route('/usuarios/desativar/<int:id>', methods=['POST'])
@login_required
def desativar_usuario(id):
    if not admin_required():
        return redirect(url_for('listar_usuarios'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("UPDATE usuarios SET ativo=FALSE WHERE id=%s", (id,))
    conn.commit()
    cur.close()
    conn.close()

    flash("Usuário desativado com sucesso!", "success")
    return redirect(url_for('listar_usuarios'))



@app.route('/logout')
@login_required
def logout():
    session.clear()  # Remove todos os dados do usuário na sessão
    flash('Você saiu do sistema.', 'success')
    return redirect(url_for('login'))

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute(query, params)
    return cur.fetchone()[0]



if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get('PORT', 5000)), debug=True)
