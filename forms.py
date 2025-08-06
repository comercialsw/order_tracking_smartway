from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField, DateField, SelectField
from wtforms.validators import DataRequired, Optional, Length

class EditPedidoForm(FlaskForm):
    id = HiddenField('id', validators=[DataRequired()])
    status_logistico_id = SelectField('Status', coerce=int, validators=[DataRequired()])
    data_expedicao = DateField('Expedição', format='%Y-%m-%d', validators=[Optional()])
    data_previsao = DateField('Previsão', format='%Y-%m-%d', validators=[Optional()])
    data_entrega = DateField('Entregue', format='%Y-%m-%d', validators=[Optional()])
    transportadora = StringField('Transportadora', validators=[Length(max=80)])
    cod_rastreamento = StringField('Rastreamento', validators=[Length(max=80)])
    frete = StringField('Frete', validators=[Length(max=20)])
