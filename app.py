import os
from flask import (
    Flask,
    request,
    render_template,
    redirect,
    url_for,
    flash
)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

from datetime import datetime

app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET', 'replace-with-secure-key')

# Use Postgres when DATABASE_URL is set, otherwise SQLite
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv(
    'DATABASE_URL',
    'sqlite:///pallets.db'
)
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db      = SQLAlchemy(app)
migrate = Migrate(app, db)
# ── Models ────────────────────────────────────────────────────────────────────

class Pallet(db.Model):
    id          = db.Column(db.String, primary_key=True)
    rm_number   = db.Column(db.String, nullable=False)
    location    = db.Column(db.String, nullable=False)
    received_at = db.Column(db.DateTime, default=datetime.utcnow)
    lots        = db.relationship('Lot', backref='pallet', cascade='all, delete-orphan')

class Lot(db.Model):
    id              = db.Column(db.Integer, primary_key=True)
    pallet_id       = db.Column(db.String, db.ForeignKey('pallet.id'), nullable=False)
    lot             = db.Column(db.String, nullable=False)
    qty             = db.Column(db.Integer, nullable=False)
    unit            = db.Column(db.String, nullable=False)   # LBS or KGS
    expiration_date = db.Column(db.Date, nullable=False)

# ── Utility ───────────────────────────────────────────────────────────────────

def generate_pallet_id():
    last = Pallet.query.order_by(Pallet.id.desc()).first()
    if not last:
        return 'PAL00001'
    num = int(last.id.replace('PAL','')) + 1
    return f'PAL{num:05d}'

# ── Routes ───────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    pallets = Pallet.query.order_by(Pallet.id).all()
    return render_template('index.html', pallets=pallets)

@app.route('/pallets/new', methods=['GET','POST'])
def create_pallet():
    next_id = generate_pallet_id()
    if request.method == 'POST':
        pallet_id = request.form.get('pallet_id', next_id).strip()
        rm_number = request.form.get('rm_number','').strip()
        location  = request.form.get('location','').strip()
        if not rm_number or not location:
            flash("RM# and Location are required.", "error")
            return render_template('create_pallet.html', next_id=next_id), 400

        p = Pallet(id=pallet_id, rm_number=rm_number, location=location)
        db.session.add(p)
        db.session.commit()

        lots  = request.form.getlist('lot')
        qtys  = request.form.getlist('qty')
        units = request.form.getlist('unit')
        exps  = request.form.getlist('expiration_date')
        for lot_code, qty, unit, exp_str in zip(lots, qtys, units, exps):
            l = Lot(
                pallet_id       = p.id,
                lot             = lot_code.strip(),
                qty             = int(qty),
                unit            = unit,
                expiration_date = datetime.fromisoformat(exp_str).date()
            )
            db.session.add(l)
        db.session.commit()

        flash(f"Pallet {p.id} created.", "success")
        return redirect(url_for('index'))
    return render_template('create_pallet.html', next_id=next_id)

@app.route('/pallets/<pallet_id>')
def pallet_detail(pallet_id):
    p = Pallet.query.get_or_404(pallet_id)
    return render_template('pallet_detail.html', pallet=p)

@app.route('/pallets/<pallet_id>/edit', methods=['GET','POST'])
def edit_pallet(pallet_id):
    p = Pallet.query.get_or_404(pallet_id)
    if request.method == 'POST':
        p.rm_number = request.form.get('rm_number','').strip()
        p.location  = request.form.get('location','').strip()
        db.session.commit()
        flash("Pallet info updated.", "success")
        return redirect(url_for('pallet_detail', pallet_id=p.id))
    return render_template('edit_pallet.html', pallet=p)

@app.route('/pallets/<pallet_id>/return', methods=['GET','POST'])
def return_to_pallet(pallet_id):
    p = Pallet.query.get_or_404(pallet_id)
    if request.method == 'POST':
        lot_id       = int(request.form['lot_id'])
        returned_qty = int(request.form['qty'])
        new_lot_code = request.form['lot'].strip()
        new_unit     = request.form['unit']
        new_exp      = datetime.fromisoformat(request.form['expiration_date']).date()

        base_lot = Lot.query.get_or_404(lot_id)
        if base_lot.lot == new_lot_code and base_lot.unit == new_unit:
            base_lot.qty += returned_qty
        else:
            mixed = Lot(
                pallet_id       = p.id,
                lot             = new_lot_code,
                qty             = returned_qty,
                unit            = new_unit,
                expiration_date = new_exp
            )
            db.session.add(mixed)
        db.session.commit()
        flash("Return/mix-lot recorded.", "success")
        return redirect(url_for('pallet_detail', pallet_id=p.id))
    return render_template('return_pallet.html', pallet=p)

@app.route('/pallets/<pallet_id>/pick', methods=['GET','POST'])
def pick_from_pallet(pallet_id):
    p = Pallet.query.get_or_404(pallet_id)
    lots = sorted(p.lots, key=lambda l: l.expiration_date)
    if request.method == 'POST':
        lot_id   = int(request.form['lot_id'])
        pick_qty = int(request.form['qty'])
        lot = Lot.query.get_or_404(lot_id)
        if pick_qty < 1 or pick_qty > lot.qty:
            flash("Invalid pick quantity.", "error")
            return render_template('pick_pallet.html', pallet=p, lots=lots), 400
        lot.qty -= pick_qty
        if lot.qty == 0:
            db.session.delete(lot)
        db.session.commit()
        flash(f"Picked {pick_qty}{lot.unit} from lot {lot.lot}.", "success")
        return redirect(url_for('pallet_detail', pallet_id=p.id))
    return render_template('pick_pallet.html', pallet=p, lots=lots)

# Optional: debug listing of routes
@app.route('/routes')
def list_routes():
    out = []
    for rule in sorted(app.url_map.iter_rules(), key=lambda r: r.rule):
        methods = ','.join(sorted(rule.methods - {'HEAD','OPTIONS'}))
        out.append(f"{rule.rule} → [{methods}]")
    return '<br>'.join(out)

@app.route('/pallets/<pallet_id>/tag')
def pallet_tag(pallet_id):
    p = Pallet.query.get_or_404(pallet_id)
    return render_template('pallet_tag.html', pallet=p)

@app.route('/scan')
def scan_pallet():
    return render_template('scan_pallet.html')


if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(host='0.0.0.0', port=5000, debug=True)
